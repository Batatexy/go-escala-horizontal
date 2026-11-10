package main

import (
	"math"
	"strings"
	"testing"
	"time"
)

// Referência calculada por ferramenta independente do código testado:
//
//	printf '%s' "SNS-5G-SEQ-0000012026-10-23T14:32:05.123456Zk3j9x2ab7c1" | sha256sum
const hashReferencia = "bbcf77efecb3d9f696e8b81932d26cb6df9b803e89ef26cbd163e6d03348565b"

func TestHashMedicaoConfereComSha256sum(t *testing.T) {
	got := hashMedicao("SNS-5G-SEQ-000001", "2026-10-23T14:32:05.123456Z", "k3j9x2ab7c1")
	if got != hashReferencia {
		t.Fatalf("hash = %s, esperado %s", got, hashReferencia)
	}
}

func TestTimestampCanonicoTemSeisCasasEZ(t *testing.T) {
	casos := map[string]string{
		"2026-10-23T14:32:05.123456Z":      "2026-10-23T14:32:05.123456Z",
		"2026-10-23T14:32:05.123Z":         "2026-10-23T14:32:05.123000Z", // 3 casas -> 6
		"2026-10-23T14:32:05Z":             "2026-10-23T14:32:05.000000Z", // sem fração
		"2026-10-23T11:32:05.123456-03:00": "2026-10-23T14:32:05.123456Z", // fuso local -> UTC
		"2026-10-23T14:32:05.123456789Z":   "2026-10-23T14:32:05.123456Z", // nanos truncados
	}
	for entrada, esperado := range casos {
		ts, err := interpretarTimestamp(entrada)
		if err != nil {
			t.Fatalf("%q: %v", entrada, err)
		}
		if got := timestampCanonico(ts); got != esperado {
			t.Errorf("%q -> %q, esperado %q", entrada, got, esperado)
		}
	}
}

func TestTimestampInvalidoEhRejeitado(t *testing.T) {
	for _, s := range []string{"", "ontem", "2026-13-40T00:00:00Z", "2026-10-23 14:32:05"} {
		if _, err := interpretarTimestamp(s); err == nil {
			t.Errorf("%q deveria ser rejeitado", s)
		}
	}
}

// O round-trip pelo banco guarda microssegundos (timestamptz). O valor lido
// deve serializar exatamente como o valor que entrou no hash.
func TestRoundTripMicrossegundosPreservaHash(t *testing.T) {
	ts, _ := interpretarTimestamp("2026-10-23T14:32:05.123456789Z")
	micro := ts.UnixMicro()
	lido := time.UnixMicro(micro).UTC() // o que o worker grava e a API relê
	if timestampCanonico(lido) != timestampCanonico(ts) {
		t.Fatalf("round-trip alterou o timestamp: %s != %s", timestampCanonico(lido), timestampCanonico(ts))
	}
}

// Valores de referência obtidos em Python com a mesma fórmula do Anexo D.1.
func TestHumidexReferencia(t *testing.T) {
	casos := []struct{ temp, umid, esperado float64 }{
		{27.4, 68.2, 35.6400292626048},
		{27.9, 66.8, 36.25740893343531},
	}
	for _, c := range casos {
		if got := humidex(c.temp, c.umid); math.Abs(got-c.esperado) > 1e-9 {
			t.Errorf("humidex(%v,%v) = %v, esperado %v", c.temp, c.umid, got, c.esperado)
		}
	}
}

func TestHumidexEhMonotonicoNaUmidade(t *testing.T) {
	if humidex(30, 80) <= humidex(30, 50) {
		t.Fatal("mais umidade deveria elevar a sensação térmica")
	}
}

func novaEntrada(sensor, ts, raw string, temp, umid float64) medicaoEntrada {
	var m medicaoEntrada
	m.SensorID, m.Timestamp, m.RawPacket = sensor, ts, raw
	m.Metrics.Microclimate.Temperatura = temp
	m.Metrics.Microclimate.Umidade = umid
	return m
}

func TestProcessarLoteSeparaValidasDeInvalidas(t *testing.T) {
	lote := []medicaoEntrada{
		novaEntrada("SNS-5G-SEQ-000001", "2026-10-23T14:32:05.123456Z", "k3j9x2ab7c1", 27.4, 68.2),
		novaEntrada("", "2026-10-23T14:33:05.123456Z", "x", 20, 50),                      // sensor vazio
		novaEntrada("SNS-5G-SEQ-000001", "lixo", "x", 20, 50),                            // timestamp ruim
		novaEntrada("SNS-5G-SEQ-000001", "2026-10-23T14:34:05.123456Z", "", 20, 50),      // pacote vazio
		novaEntrada(strings.Repeat("A", 65), "2026-10-23T14:35:05.123456Z", "x", 20, 50), // id longo demais
		novaEntrada("SNS-5G-SEQ-000001", "2026-10-23T14:33:05.123456Z", "m8p2v4cd9e5", 27.9, 66.8),
	}
	aceitas, soma := processarLote(lote)
	if len(aceitas) != 2 {
		t.Fatalf("aceitas = %d, esperado 2", len(aceitas))
	}
	if aceitas[0].Hash != hashReferencia {
		t.Errorf("hash da primeira = %s", aceitas[0].Hash)
	}
	if esperado := 35.6400292626048 + 36.25740893343531; math.Abs(soma-esperado) > 1e-9 {
		t.Errorf("soma = %v, esperado %v", soma, esperado)
	}
}

func TestProcessarLoteRejeitaNaN(t *testing.T) {
	lote := []medicaoEntrada{novaEntrada("S", "2026-10-23T14:32:05Z", "x", math.NaN(), 50)}
	if aceitas, _ := processarLote(lote); len(aceitas) != 0 {
		t.Fatal("NaN deveria ser rejeitado")
	}
}

func TestAgregadoCodificaEDecodifica(t *testing.T) {
	v := codificarAgregado(1234.5678, 35)
	soma, n, ok := decodificarAgregado(v)
	if !ok || soma != 1234.5678 || n != 35 {
		t.Fatalf("round-trip falhou: %q -> %v %v %v", v, soma, n, ok)
	}
	for _, ruim := range []string{"", "abc", "1.0:", ":3", "1.0:0", "1.0:-2"} {
		if _, _, ok := decodificarAgregado(ruim); ok {
			t.Errorf("%q deveria ser invalido", ruim)
		}
	}
}
