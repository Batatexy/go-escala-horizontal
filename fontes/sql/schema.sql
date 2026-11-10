-- ============================================================
-- schema.sql — esquema do Cenário B (arquitetura distribuída)
--
-- Executado uma única vez pelo próprio contêiner do PostgreSQL, na primeira
-- inicialização (diretório /docker-entrypoint-initdb.d). Não altera o init.sql
-- do kit base, que é imutável e continua servindo ao Cenário A.
--
-- Diferenças em relação ao esquema do monolito:
--   * medicoes guarda também temperatura, umidade e o Humidex de cada medição;
--   * agregados_sensor mantém soma e contagem por sensor, atualizadas na mesma
--     transação da inserção. A rota de leitura deixa de calcular AVG() sobre
--     um conjunto de linhas que cresce a cada ingestão.
-- ============================================================

CREATE TABLE IF NOT EXISTS medicoes (
    id           BIGSERIAL        PRIMARY KEY,
    sensor_id    TEXT             NOT NULL,
    timestamp    TIMESTAMPTZ      NOT NULL,
    raw_packet   TEXT             NOT NULL,
    hash         TEXT             NOT NULL,
    temperatura  DOUBLE PRECISION NOT NULL,
    umidade      DOUBLE PRECISION NOT NULL,
    humidex      DOUBLE PRECISION NOT NULL,
    criado_em    TIMESTAMPTZ      NOT NULL DEFAULT now(),
    -- Regra de unicidade do contrato: um par (sensor, instante) por linha.
    -- O ON CONFLICT do worker depende desta restrição para ser idempotente.
    CONSTRAINT medicoes_sensor_timestamp_uniq UNIQUE (sensor_id, timestamp)
);

CREATE TABLE IF NOT EXISTS agregados_sensor (
    sensor_id  TEXT             PRIMARY KEY,
    soma       DOUBLE PRECISION NOT NULL,
    amostras   BIGINT           NOT NULL CHECK (amostras > 0)
);
