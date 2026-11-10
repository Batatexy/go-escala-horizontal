package main

// cache.go — cache do índice de calor por sensor.
//
// A fonte da verdade é a tabela agregados_sensor (soma e número de amostras,
// atualizados na mesma transação que grava as medições). O Redis guarda uma
// cópia "soma:amostras" para que a rota de leitura, que domina o tráfego,
// responda em O(1) sem consultar o PostgreSQL nem calcular média sobre um
// conjunto de linhas crescente.
//
// Duas escritas concorrentes sobre a mesma chave (worker e preenchimento por
// leitura, ou dois workers) poderiam deixar um valor antigo por cima de um
// novo. O número de amostras só cresce, então o script Lua abaixo recusa
// qualquer escrita com menos amostras do que a já armazenada.

import (
	"context"
	"strconv"
	"strings"

	"github.com/redis/go-redis/v9"
)

const ttlCacheSegundos = 3600

// gravarSeMaisNovo: KEYS[1]=chave; ARGV[1]="soma:amostras"; ARGV[2]=amostras;
// ARGV[3]=TTL em segundos. Retorna 1 se gravou e 0 se descartou.
var scriptGravarIndice = redis.NewScript(`
local atual = redis.call('GET', KEYS[1])
if atual then
  local n = tonumber(string.match(atual, ':(%d+)$'))
  if n and n > tonumber(ARGV[2]) then return 0 end
end
redis.call('SET', KEYS[1], ARGV[1], 'EX', ARGV[3])
return 1
`)

func chaveIndice(sensorID string) string { return "idx:" + sensorID }

func codificarAgregado(soma float64, amostras int64) string {
	return strconv.FormatFloat(soma, 'g', -1, 64) + ":" + strconv.FormatInt(amostras, 10)
}

func decodificarAgregado(v string) (soma float64, amostras int64, ok bool) {
	i := strings.LastIndexByte(v, ':')
	if i <= 0 {
		return 0, 0, false
	}
	s, err1 := strconv.ParseFloat(v[:i], 64)
	n, err2 := strconv.ParseInt(v[i+1:], 10, 64)
	if err1 != nil || err2 != nil || n <= 0 {
		return 0, 0, false
	}
	return s, n, true
}

// gravarIndiceCache escreve (de forma monotônica) o agregado de um sensor.
func gravarIndiceCache(ctx context.Context, rdb redis.Scripter, sensorID string, soma float64, amostras int64) error {
	return scriptGravarIndice.Run(ctx, rdb, []string{chaveIndice(sensorID)},
		codificarAgregado(soma, amostras), amostras, ttlCacheSegundos).Err()
}
