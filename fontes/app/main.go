package main

// main.go — ponto de entrada único das duas funções da arquitetura
// distribuída. A variável ROLE escolhe o papel do contêiner:
//
//	ROLE=api     instância HTTP sem estado (atrás do nginx)
//	ROLE=worker  consumidor da fila durável que grava no PostgreSQL
//
// Usar um só binário para os dois papéis mantém uma única imagem (um único
// build) e garante que API e worker compartilhem exatamente o mesmo código de
// domínio (hash, Humidex, timestamp canônico).

import (
	"context"
	"log"
	"os/signal"
	"syscall"
)

func main() {
	ctx, parar := signal.NotifyContext(context.Background(), syscall.SIGINT, syscall.SIGTERM)
	defer parar()

	switch papel := envString("ROLE", "api"); papel {
	case "api":
		executarAPI(ctx)
	case "worker":
		executarWorker(ctx)
	default:
		log.Fatalf("ROLE invalido: %q (use api ou worker)", papel)
	}
}
