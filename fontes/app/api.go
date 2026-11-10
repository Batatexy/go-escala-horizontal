package main

// api.go — papel "api": instância sem estado atrás do balanceador.
//
// Caminho de escrita:  valida -> calcula hash e Humidex -> XADD no Redis ->
//                      responde 202. Não toca o PostgreSQL.
// Caminho de leitura:  GET no Redis (O(1)); em falta, consulta o agregado no
//                      PostgreSQL e preenche o cache.
// Auditoria:           lê o PostgreSQL, que é a fonte da verdade.

import (
	"context"
	"encoding/json"
	"errors"
	"log"
	"net/http"
	"strconv"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

type servidorAPI struct {
	pg  *pgxpool.Pool
	rdb *redis.Client
}

type respostaIngestao struct {
	Status          string  `json:"status"`
	SensorID        string  `json:"sensor_id"`
	TotalRecebido   int     `json:"total_recebido"`
	TotalAceito     int     `json:"total_aceito"`
	TotalPersistido int     `json:"total_persistido"`
	TotalRejeitado  int     `json:"total_rejeitado"`
	HumidexMedio    float64 `json:"humidex_medio"`
}

type medidaAuditoria struct {
	ID        int64  `json:"id"`
	SensorID  string `json:"sensor_id"`
	Timestamp string `json:"timestamp"`
	RawPacket string `json:"raw_packet"`
	Hash      string `json:"hash"`
}

func escreverJSON(w http.ResponseWriter, status int, corpo any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	if err := json.NewEncoder(w).Encode(corpo); err != nil {
		log.Printf("aviso: falha ao serializar resposta: %v", err)
	}
}

// ---------- GET /health ----------

// health responde 200 quando a instância consegue falar com as duas
// dependências e 503 caso contrário. A plataforma de teste só começa a injetar
// carga depois de receber 200.
func (s *servidorAPI) health(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	if err := s.pg.Ping(ctx); err != nil {
		http.Error(w, "Banco de dados indisponivel", http.StatusServiceUnavailable)
		return
	}
	if err := s.rdb.Ping(ctx).Err(); err != nil {
		http.Error(w, "Fila indisponivel", http.StatusServiceUnavailable)
		return
	}
	escreverJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

// ---------- POST /telemetria ----------

func (s *servidorAPI) telemetria(w http.ResponseWriter, r *http.Request) {
	r.Body = http.MaxBytesReader(w, r.Body, 8<<20)
	var lote loteEntrada
	if err := json.NewDecoder(r.Body).Decode(&lote); err != nil {
		http.Error(w, "Payload de telemetria invalido", http.StatusBadRequest)
		return
	}
	recebidas := len(lote.Measurements)
	if recebidas == 0 {
		http.Error(w, "Lote de medidas vazio", http.StatusBadRequest)
		return
	}

	aceitas, soma := processarLote(lote.Measurements)
	rejeitadas := recebidas - len(aceitas)
	if len(aceitas) == 0 {
		escreverJSON(w, http.StatusBadRequest, map[string]any{
			"status":          "rejeitado",
			"total_recebido":  recebidas,
			"total_aceito":    0,
			"total_rejeitado": rejeitadas,
		})
		return
	}

	carga, err := json.Marshal(aceitas)
	if err != nil {
		http.Error(w, "Falha ao serializar lote", http.StatusInternalServerError)
		return
	}

	// O 202 só é emitido depois que o Redis confirma o XADD (com AOF ligado, a
	// escrita já está no log). Se a fila estiver indisponível ou cheia, o hub
	// recebe 503 e retransmite: a perda nunca é silenciosa.
	ctx, cancel := context.WithTimeout(r.Context(), 2*time.Second)
	defer cancel()
	err = s.rdb.XAdd(ctx, &redis.XAddArgs{
		Stream: chaveStream,
		Values: []any{"d", carga},
	}).Err()
	if err != nil {
		log.Printf("aviso: falha ao enfileirar lote: %v", err)
		http.Error(w, "Fila indisponivel", http.StatusServiceUnavailable)
		return
	}

	escreverJSON(w, http.StatusAccepted, respostaIngestao{
		Status:          "aceito",
		SensorID:        aceitas[0].Sensor,
		TotalRecebido:   recebidas,
		TotalAceito:     len(aceitas),
		TotalPersistido: len(aceitas), // persistido no log durável; gravação no banco é assíncrona
		TotalRejeitado:  rejeitadas,
		HumidexMedio:    soma / float64(len(aceitas)),
	})
}

// ---------- GET /indice-calor/{sensor_id} ----------

func (s *servidorAPI) indiceCalor(w http.ResponseWriter, r *http.Request) {
	sensorID := r.PathValue("sensor_id")
	if sensorID == "" || len(sensorID) > 64 {
		http.Error(w, "sensor_id obrigatorio", http.StatusBadRequest)
		return
	}
	ctx, cancel := context.WithTimeout(r.Context(), 3*time.Second)
	defer cancel()

	// 1) cache. Qualquer erro do Redis degrada para o banco, nunca para 5xx.
	if v, err := s.rdb.Get(ctx, chaveIndice(sensorID)).Result(); err == nil {
		if soma, n, ok := decodificarAgregado(v); ok {
			responderIndice(w, sensorID, soma, n)
			return
		}
	}

	// 2) fonte da verdade
	var soma float64
	var amostras int64
	err := s.pg.QueryRow(ctx,
		`SELECT soma, amostras FROM agregados_sensor WHERE sensor_id = $1`, sensorID,
	).Scan(&soma, &amostras)
	if errors.Is(err, pgx.ErrNoRows) {
		http.Error(w, "Nenhuma medicao encontrada para o sensor", http.StatusNotFound)
		return
	}
	if err != nil {
		http.Error(w, "Falha ao consultar indice", http.StatusInternalServerError)
		return
	}
	_ = gravarIndiceCache(ctx, s.rdb, sensorID, soma, amostras) // melhor esforço
	responderIndice(w, sensorID, soma, amostras)
}

func responderIndice(w http.ResponseWriter, sensorID string, soma float64, amostras int64) {
	escreverJSON(w, http.StatusOK, map[string]any{
		"sensor_id":     sensorID,
		"humidex_medio": soma / float64(amostras),
		"amostras":      amostras,
	})
}

// ---------- GET /audit/contagem ----------

// total_indices é o número de medições cujo índice já compõe o agregado do
// sensor (soma de "amostras"). Como medição e agregado são gravados na mesma
// transação, os dois totais coincidem em qualquer instante observável.
func (s *servidorAPI) auditContagem(w http.ResponseWriter, r *http.Request) {
	ctx, cancel := context.WithTimeout(r.Context(), 10*time.Second)
	defer cancel()
	var medicoes, indices int64
	err := s.pg.QueryRow(ctx, `
		SELECT (SELECT count(*) FROM medicoes),
		       (SELECT COALESCE(sum(amostras), 0)::bigint FROM agregados_sensor)`,
	).Scan(&medicoes, &indices)
	if err != nil {
		http.Error(w, "erro de auditoria", http.StatusInternalServerError)
		return
	}
	escreverJSON(w, http.StatusOK, map[string]int64{
		"total_medicoes": medicoes,
		"total_indices":  indices,
	})
}

// ---------- GET /audit/medidas ----------

// O cursor é o id da última linha da página anterior (opaco para o cliente).
// Os workers serializam a gravação por advisory lock, então os ids visíveis
// formam um prefixo contínuo e a paginação por id não pula linhas.
func (s *servidorAPI) auditMedidas(w http.ResponseWriter, r *http.Request) {
	limite := 1000
	if v, err := strconv.Atoi(r.URL.Query().Get("limit")); err == nil && v > 0 && v <= 5000 {
		limite = v
	}
	var cursor int64
	if c := r.URL.Query().Get("cursor"); c != "" {
		n, err := strconv.ParseInt(c, 10, 64)
		if err != nil || n < 0 {
			http.Error(w, "cursor invalido", http.StatusBadRequest)
			return
		}
		cursor = n
	}

	ctx, cancel := context.WithTimeout(r.Context(), 30*time.Second)
	defer cancel()
	linhas, err := s.pg.Query(ctx, `
		SELECT id, sensor_id, timestamp, raw_packet, hash
		  FROM medicoes
		 WHERE id > $1
		 ORDER BY id
		 LIMIT $2`, cursor, limite)
	if err != nil {
		http.Error(w, "erro de auditoria", http.StatusInternalServerError)
		return
	}
	defer linhas.Close()

	medidas := make([]medidaAuditoria, 0, limite)
	for linhas.Next() {
		var m medidaAuditoria
		var ts time.Time
		if err := linhas.Scan(&m.ID, &m.SensorID, &ts, &m.RawPacket, &m.Hash); err != nil {
			http.Error(w, "erro de auditoria", http.StatusInternalServerError)
			return
		}
		m.Timestamp = timestampCanonico(ts)
		medidas = append(medidas, m)
	}
	if err := linhas.Err(); err != nil {
		http.Error(w, "erro de auditoria", http.StatusInternalServerError)
		return
	}

	proximo := ""
	if len(medidas) == limite {
		proximo = strconv.FormatInt(medidas[len(medidas)-1].ID, 10)
	}
	escreverJSON(w, http.StatusOK, map[string]any{
		"medidas":        medidas,
		"proximo_cursor": proximo,
	})
}

// ---------- bootstrap ----------

func executarAPI(ctx context.Context) {
	pg, err := abrirPostgres(ctx, int32(envInt("DB_MAX_CONNS", 3)))
	if err != nil {
		log.Fatalf("api: %v", err)
	}
	defer pg.Close()
	rdb, err := abrirRedis(ctx, envInt("REDIS_POOL", 8))
	if err != nil {
		log.Fatalf("api: %v", err)
	}
	defer rdb.Close()

	s := &servidorAPI{pg: pg, rdb: rdb}
	mux := http.NewServeMux()
	mux.HandleFunc("GET /health", s.health)
	mux.HandleFunc("POST /telemetria", s.telemetria)
	mux.HandleFunc("GET /indice-calor/{sensor_id}", s.indiceCalor)
	mux.HandleFunc("GET /audit/contagem", s.auditContagem)
	mux.HandleFunc("GET /audit/medidas", s.auditMedidas)

	srv := &http.Server{
		Addr:              envString("LISTEN_ADDR", ":8080"),
		Handler:           mux,
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       15 * time.Second,
		WriteTimeout:      35 * time.Second,
		IdleTimeout:       120 * time.Second,
	}
	go func() {
		<-ctx.Done()
		sctx, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		_ = srv.Shutdown(sctx)
	}()

	log.Printf("api ouvindo em %s", srv.Addr)
	if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
		log.Fatalf("api: %v", err)
	}
}
