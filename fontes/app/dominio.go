package main

// dominio.go — regras de negócio puras (sem I/O): timestamp canônico, âncora
// criptográfica (hash) e índice Humidex. Nada aqui toca rede ou banco, o que
// permite testar o contrato da auditoria em milissegundos (dominio_test.go).

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"math"
	"time"
)

// layoutCanonico é a forma obrigatória do timestamp: UTC com exatamente seis
// casas decimais. O sufixo "Z" é acrescentado à parte para garantir que nunca
// apareça um deslocamento de fuso ("+00:00") no lugar dele.
const layoutCanonico = "2006-01-02T15:04:05.000000"

// timestampCanonico serializa um instante na forma que entra no hash e que a
// rota /audit/medidas devolve. Qualquer outra representação (três casas, fuso
// local, sem sufixo) produziria hash diferente para a mesma medição.
func timestampCanonico(t time.Time) string {
	return t.UTC().Format(layoutCanonico) + "Z"
}

// interpretarTimestamp aceita RFC 3339 com qualquer número de casas decimais e
// qualquer deslocamento de fuso, e devolve o instante em UTC truncado em
// microssegundos (a precisão do tipo timestamptz do PostgreSQL). O truncamento
// garante que o valor lido do banco serialize exatamente como o valor que
// entrou no hash.
func interpretarTimestamp(s string) (time.Time, error) {
	t, err := time.Parse(time.RFC3339Nano, s)
	if err != nil {
		return time.Time{}, fmt.Errorf("timestamp fora do formato RFC 3339: %q", s)
	}
	return t.UTC().Truncate(time.Microsecond), nil
}

// hashMedicao é a âncora de integridade de UMA medição:
// SHA-256(sensor_id + timestamp_canônico + raw_packet), em hexadecimal
// minúsculo, com concatenação direta e sem separadores. É idêntica à função
// computeHash do avaliador (avaliar_justo.go).
func hashMedicao(sensorID, tsCanonico, rawPacket string) string {
	soma := sha256.Sum256([]byte(sensorID + tsCanonico + rawPacket))
	return hex.EncodeToString(soma[:])
}

// humidex calcula o índice de calor percebido a partir da temperatura do ar
// (°C) e da umidade relativa (%). A pressão de vapor é estimada pela fórmula
// de Magnus, com os mesmos coeficientes do kit base (Anexo D.1 do enunciado).
// É a parte intensiva em ponto flutuante da carga: não pode ser descartada.
func humidex(tempC, umidadePct float64) float64 {
	e := 6.112 * math.Exp((17.62*tempC)/(243.12+tempC)) * (umidadePct / 100.0)
	return tempC + 0.5555*(e-10.0)
}

// ---------- contratos de entrada ----------

type medicaoEntrada struct {
	SensorID  string `json:"sensor_id"`
	Timestamp string `json:"timestamp"`
	Metrics   struct {
		Microclimate struct {
			Temperatura float64 `json:"temperature_celsius"`
			Umidade     float64 `json:"relative_humidity_percentage"`
		} `json:"microclimate"`
	} `json:"metrics"`
	RawPacket string `json:"raw_telemetry_packet"`
}

type loteEntrada struct {
	Measurements []medicaoEntrada `json:"measurements"`
}

// medicaoProcessada é a medição já validada e enriquecida (hash e índice
// calculados na API). É o formato que trafega pela fila; os nomes curtos
// reduzem os bytes por medição no Redis e o custo de serialização.
type medicaoProcessada struct {
	Sensor  string  `json:"s"`
	TsMicro int64   `json:"t"` // instante em microssegundos desde a época Unix
	Raw     string  `json:"r"`
	Hash    string  `json:"h"`
	Temp    float64 `json:"c"`
	Umid    float64 `json:"u"`
	Humidex float64 `json:"x"`
}

// processarLote valida cada medição e calcula hash e Humidex. Medições
// inválidas (sensor vazio, pacote vazio, timestamp ilegível) são contadas como
// rejeitadas e não entram na fila; o restante do lote segue normalmente.
func processarLote(entrada []medicaoEntrada) (aceitas []medicaoProcessada, somaHumidex float64) {
	aceitas = make([]medicaoProcessada, 0, len(entrada))
	for i := range entrada {
		m := &entrada[i]
		if m.SensorID == "" || m.RawPacket == "" || len(m.SensorID) > 64 {
			continue
		}
		ts, err := interpretarTimestamp(m.Timestamp)
		if err != nil {
			continue
		}
		t := m.Metrics.Microclimate.Temperatura
		u := m.Metrics.Microclimate.Umidade
		if math.IsNaN(t) || math.IsInf(t, 0) || math.IsNaN(u) || math.IsInf(u, 0) {
			continue
		}
		x := humidex(t, u)
		if math.IsNaN(x) || math.IsInf(x, 0) {
			continue
		}
		aceitas = append(aceitas, medicaoProcessada{
			Sensor:  m.SensorID,
			TsMicro: ts.UnixMicro(),
			Raw:     m.RawPacket,
			Hash:    hashMedicao(m.SensorID, timestampCanonico(ts), m.RawPacket),
			Temp:    t,
			Umid:    u,
			Humidex: x,
		})
		somaHumidex += x
	}
	return aceitas, somaHumidex
}
