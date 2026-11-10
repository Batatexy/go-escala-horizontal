package main

// infra.go — configuração por variáveis de ambiente e abertura das conexões
// com PostgreSQL e Redis. As duas funções de abertura tentam por até 60 s: em
// Compose, "container iniciado" não significa "dependência pronta".

import (
	"context"
	"fmt"
	"log"
	"os"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

func envString(chave, padrao string) string {
	if v := os.Getenv(chave); v != "" {
		return v
	}
	return padrao
}

func envInt(chave string, padrao int) int {
	v, err := strconv.Atoi(os.Getenv(chave))
	if err != nil || v <= 0 {
		return padrao
	}
	return v
}

const (
	// chaveStream é a fila durável de lotes aceitos e ainda não gravados.
	chaveStream = "telemetria:lotes"
	// chaveMortos recebe mensagens ilegíveis (veneno) para inspeção posterior,
	// em vez de travar o consumidor em um laço infinito de reentrega.
	chaveMortos = "telemetria:mortos"
	// grupoWorkers é o grupo de consumidores do stream.
	grupoWorkers = "workers"
)

// abrirPostgres cria o pool de conexões e só retorna quando o banco responde.
func abrirPostgres(ctx context.Context, maxConns int32) (*pgxpool.Pool, error) {
	cfg, err := pgxpool.ParseConfig(envString("DATABASE_URL",
		"postgres://iot:iot@postgres:5432/telemetria?sslmode=disable"))
	if err != nil {
		return nil, fmt.Errorf("DATABASE_URL invalida: %w", err)
	}
	cfg.MaxConns = maxConns
	cfg.MinConns = 0
	cfg.MaxConnLifetime = 10 * time.Minute

	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	limite := time.Now().Add(60 * time.Second)
	for {
		pctx, cancel := context.WithTimeout(ctx, 3*time.Second)
		err = pool.Ping(pctx)
		cancel()
		if err == nil {
			return pool, nil
		}
		if time.Now().After(limite) || ctx.Err() != nil {
			pool.Close()
			return nil, fmt.Errorf("postgres indisponivel: %w", err)
		}
		log.Printf("aguardando postgres: %v", err)
		time.Sleep(time.Second)
	}
}

// abrirRedis cria o cliente e só retorna quando o Redis responde ao PING.
func abrirRedis(ctx context.Context, poolSize int) (*redis.Client, error) {
	rdb := redis.NewClient(&redis.Options{
		Addr:         envString("REDIS_ADDR", "redis:6379"),
		PoolSize:     poolSize,
		DialTimeout:  2 * time.Second,
		ReadTimeout:  3 * time.Second,
		WriteTimeout: 3 * time.Second,
	})
	limite := time.Now().Add(60 * time.Second)
	for {
		pctx, cancel := context.WithTimeout(ctx, 2*time.Second)
		err := rdb.Ping(pctx).Err()
		cancel()
		if err == nil {
			return rdb, nil
		}
		if time.Now().After(limite) || ctx.Err() != nil {
			_ = rdb.Close()
			return nil, fmt.Errorf("redis indisponivel: %w", err)
		}
		log.Printf("aguardando redis: %v", err)
		time.Sleep(time.Second)
	}
}
