package main

// worker.go — papel "worker": consome a fila durável e grava no PostgreSQL.
//
// Garantias:
//   - Entrega "pelo menos uma vez": a mensagem só sai da fila (XACK + XDEL)
//     depois que a transação do banco confirmou.
//   - Idempotência: ON CONFLICT (sensor_id, timestamp) DO NOTHING impede
//     duplicata; o agregado só é atualizado com as linhas realmente
//     inseridas, então uma reentrega não conta a mesma medição duas vezes.
//   - Recuperação: mensagens pendentes do próprio consumidor são relidas na
//     partida, e as de consumidores mortos são resgatadas por XAUTOCLAIM.
//   - Ordem de ids: a transação de gravação adquire um advisory lock, de modo
//     que os ids da tabela são atribuídos e confirmados na mesma ordem. A
//     paginação por cursor de id, em /audit/medidas, depende disso.

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"log"
	"os"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
	"github.com/redis/go-redis/v9"
)

// chaveLockGravacao identifica o advisory lock que serializa as gravações.
const chaveLockGravacao = 7301

// Uma única instrução: insere as medições (ignorando duplicatas) e soma ao
// agregado do sensor apenas o que foi realmente inserido. unnest transforma
// sete arrays em linhas, trocando N INSERTs por uma ida ao banco.
const sqlInserirLote = `
WITH entrada AS (
    SELECT * FROM unnest(
        $1::text[], $2::timestamptz[], $3::text[], $4::text[],
        $5::float8[], $6::float8[], $7::float8[]
    ) AS t(sensor_id, ts, raw_packet, hash, temperatura, umidade, humidex)
), novas AS (
    INSERT INTO medicoes (sensor_id, timestamp, raw_packet, hash, temperatura, umidade, humidex)
    SELECT sensor_id, ts, raw_packet, hash, temperatura, umidade, humidex FROM entrada
    ON CONFLICT (sensor_id, timestamp) DO NOTHING
    RETURNING sensor_id, humidex
)
INSERT INTO agregados_sensor AS a (sensor_id, soma, amostras)
SELECT sensor_id, sum(humidex), count(*) FROM novas GROUP BY sensor_id
ON CONFLICT (sensor_id) DO UPDATE
    SET soma = a.soma + EXCLUDED.soma,
        amostras = a.amostras + EXCLUDED.amostras`

// Lê o agregado final de todos os sensores do lote, inclusive os que só
// tiveram duplicatas (reentrega), para manter o cache sempre coerente.
const sqlLerAgregados = `
SELECT sensor_id, soma, amostras FROM agregados_sensor WHERE sensor_id = ANY($1::text[])`

type colunasLote struct {
	sensores []string
	ts       []time.Time
	raws     []string
	hashes   []string
	temps    []float64
	umids    []float64
	humidex  []float64
}

func (c *colunasLote) adicionar(m medicaoProcessada) {
	c.sensores = append(c.sensores, m.Sensor)
	c.ts = append(c.ts, time.UnixMicro(m.TsMicro).UTC())
	c.raws = append(c.raws, m.Raw)
	c.hashes = append(c.hashes, m.Hash)
	c.temps = append(c.temps, m.Temp)
	c.umids = append(c.umids, m.Umid)
	c.humidex = append(c.humidex, m.Humidex)
}

type agregado struct {
	sensor   string
	soma     float64
	amostras int64
}

type trabalhador struct {
	pg         *pgxpool.Pool
	rdb        *redis.Client
	consumidor string
	maxMsgs    int64

	lotes, medicoes int64
	ultimoLog       time.Time
}

func executarWorker(ctx context.Context) {
	pg, err := abrirPostgres(ctx, int32(envInt("DB_MAX_CONNS", 2)))
	if err != nil {
		log.Fatalf("worker: %v", err)
	}
	defer pg.Close()
	rdb, err := abrirRedis(ctx, 4)
	if err != nil {
		log.Fatalf("worker: %v", err)
	}
	defer rdb.Close()

	nome, _ := os.Hostname()
	w := &trabalhador{
		pg:         pg,
		rdb:        rdb,
		consumidor: envString("CONSUMER", nome),
		maxMsgs:    int64(envInt("WORKER_BATCH_MSGS", 100)),
		ultimoLog:  time.Now(),
	}

	// Cria o grupo (e o stream, se ainda não existir). BUSYGROUP significa que
	// outro worker já o criou, o que é esperado.
	err = rdb.XGroupCreateMkStream(ctx, chaveStream, grupoWorkers, "0").Err()
	if err != nil && err.Error() != "BUSYGROUP Consumer Group name already exists" {
		log.Fatalf("worker: falha ao criar grupo: %v", err)
	}

	log.Printf("worker %s pronto (lote de ate %d mensagens)", w.consumidor, w.maxMsgs)
	w.laco(ctx)
	log.Printf("worker %s encerrado", w.consumidor)
}

func (w *trabalhador) laco(ctx context.Context) {
	pendentes := true // na partida, primeiro relê o que ficou sem XACK
	ultimoResgate := time.Now()
	falhas := 0

	for ctx.Err() == nil {
		var (
			msgs []redis.XMessage
			err  error
		)
		switch {
		case pendentes:
			msgs, err = w.lerMensagens(ctx, "0", -1)
			if err == nil && len(msgs) == 0 {
				pendentes = false
				continue
			}
		case time.Since(ultimoResgate) > 15*time.Second:
			ultimoResgate = time.Now()
			msgs, err = w.resgatarOciosos(ctx)
			if err == nil && len(msgs) == 0 {
				continue
			}
		default:
			msgs, err = w.lerMensagens(ctx, ">", time.Second)
		}

		if err == nil && len(msgs) > 0 {
			err = w.processar(ctx, msgs)
			if err == nil {
				falhas = 0
			}
		}
		if err != nil {
			if ctx.Err() != nil {
				return
			}
			falhas++
			espera := time.Duration(min(falhas, 5)) * time.Second
			log.Printf("worker %s: falha (tentativa %d), nova tentativa em %s: %v",
				w.consumidor, falhas, espera, err)
			pendentes = true // reler as pendentes: nada foi confirmado
			select {
			case <-ctx.Done():
				return
			case <-time.After(espera):
			}
		}
	}
}

// lerMensagens lê do grupo. Com id "0" devolve as pendentes do consumidor;
// com ">" devolve as novas, bloqueando até "bloqueio" (negativo = não bloqueia).
func (w *trabalhador) lerMensagens(ctx context.Context, id string, bloqueio time.Duration) ([]redis.XMessage, error) {
	res, err := w.rdb.XReadGroup(ctx, &redis.XReadGroupArgs{
		Group:    grupoWorkers,
		Consumer: w.consumidor,
		Streams:  []string{chaveStream, id},
		Count:    w.maxMsgs,
		Block:    bloqueio,
	}).Result()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	if err != nil {
		return nil, err
	}
	var msgs []redis.XMessage
	for _, s := range res {
		msgs = append(msgs, s.Messages...)
	}
	return msgs, nil
}

// resgatarOciosos assume mensagens entregues a outro consumidor que não deu
// XACK há mais de 30 s (consumidor morto ou travado).
func (w *trabalhador) resgatarOciosos(ctx context.Context) ([]redis.XMessage, error) {
	msgs, _, err := w.rdb.XAutoClaim(ctx, &redis.XAutoClaimArgs{
		Stream:   chaveStream,
		Group:    grupoWorkers,
		Consumer: w.consumidor,
		MinIdle:  30 * time.Second,
		Start:    "0-0",
		Count:    w.maxMsgs,
	}).Result()
	if errors.Is(err, redis.Nil) {
		return nil, nil
	}
	return msgs, err
}

func (w *trabalhador) processar(ctx context.Context, msgs []redis.XMessage) error {
	// O lote em curso termina mesmo que o contêiner receba SIGTERM.
	pctx, cancel := context.WithTimeout(context.WithoutCancel(ctx), 30*time.Second)
	defer cancel()

	var cols colunasLote
	ids := make([]string, 0, len(msgs))
	for _, m := range msgs {
		ids = append(ids, m.ID)
		bruto, _ := m.Values["d"].(string)
		var itens []medicaoProcessada
		if err := json.Unmarshal([]byte(bruto), &itens); err != nil || len(itens) == 0 {
			// Mensagem veneno: guarda para inspeção e segue, em vez de travar.
			log.Printf("worker %s: mensagem %s ilegivel, movida para %s", w.consumidor, m.ID, chaveMortos)
			if e := w.rdb.XAdd(pctx, &redis.XAddArgs{
				Stream: chaveMortos, Values: []any{"id_origem", m.ID, "d", bruto},
			}).Err(); e != nil {
				return fmt.Errorf("mover mensagem veneno: %w", e)
			}
			continue
		}
		for _, it := range itens {
			cols.adicionar(it)
		}
	}

	var agregados []agregado
	if len(cols.sensores) > 0 {
		var err error
		agregados, err = w.gravar(pctx, &cols)
		if err != nil {
			return fmt.Errorf("gravar no postgres: %w", err)
		}
	}

	// Só agora, com o banco confirmado: atualiza o cache e retira da fila.
	pipe := w.rdb.Pipeline()
	for _, a := range agregados {
		scriptGravarIndice.Eval(pctx, pipe, []string{chaveIndice(a.sensor)},
			codificarAgregado(a.soma, a.amostras), a.amostras, ttlCacheSegundos)
	}
	pipe.XAck(pctx, chaveStream, grupoWorkers, ids...)
	pipe.XDel(pctx, chaveStream, ids...)
	if _, err := pipe.Exec(pctx); err != nil {
		return fmt.Errorf("confirmar na fila: %w", err)
	}

	w.lotes += int64(len(msgs))
	w.medicoes += int64(len(cols.sensores))
	if time.Since(w.ultimoLog) >= 10*time.Second {
		log.Printf("worker %s: %d requisicoes / %d medicoes processadas", w.consumidor, w.lotes, w.medicoes)
		w.ultimoLog = time.Now()
	}
	return nil
}

// gravar executa a transação do lote: lock, inserção em massa e leitura dos
// agregados atualizados, tudo em uma única ida ao banco (pgx.Batch) e um
// único COMMIT (um fsync para centenas de medições).
func (w *trabalhador) gravar(ctx context.Context, c *colunasLote) ([]agregado, error) {
	tx, err := w.pg.Begin(ctx)
	if err != nil {
		return nil, err
	}
	defer tx.Rollback(ctx)

	unicos := make(map[string]struct{}, 64)
	for _, s := range c.sensores {
		unicos[s] = struct{}{}
	}
	lista := make([]string, 0, len(unicos))
	for s := range unicos {
		lista = append(lista, s)
	}

	b := &pgx.Batch{}
	b.Queue("SELECT pg_advisory_xact_lock($1)", int64(chaveLockGravacao))
	b.Queue(sqlInserirLote, c.sensores, c.ts, c.raws, c.hashes, c.temps, c.umids, c.humidex)
	b.Queue(sqlLerAgregados, lista)
	br := tx.SendBatch(ctx, b)

	if _, err := br.Exec(); err != nil {
		_ = br.Close()
		return nil, err
	}
	if _, err := br.Exec(); err != nil {
		_ = br.Close()
		return nil, err
	}
	linhas, err := br.Query()
	if err != nil {
		_ = br.Close()
		return nil, err
	}
	agregados := make([]agregado, 0, len(lista))
	for linhas.Next() {
		var a agregado
		if err := linhas.Scan(&a.sensor, &a.soma, &a.amostras); err != nil {
			linhas.Close()
			_ = br.Close()
			return nil, err
		}
		agregados = append(agregados, a)
	}
	linhas.Close()
	if err := br.Close(); err != nil {
		return nil, err
	}
	if err := tx.Commit(ctx); err != nil {
		return nil, err
	}
	return agregados, nil
}
