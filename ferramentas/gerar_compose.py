#!/usr/bin/env python3
"""
gerar_compose.py — gera docker-compose.distribuido.yaml (Cenário B).

POR QUE UM GERADOR
------------------
O regulamento da APS exige que toda extensão de engenharia (workers, proxy
reverso, bancos de apoio) conste "estritamente dentro do arquivo complementar
docker-compose.distribuido.yaml". O arquivo entregue precisa, portanto, ser
AUTOSSUFICIENTE: funcionar quando copiado sozinho para a pasta do kit, sem
depender de diretórios de código ao lado.

Para isso o código Go é embutido no próprio YAML (build.dockerfile_inline com
heredocs), e a configuração do nginx e o esquema SQL entram por `configs`
com conteúdo inline. Manter esse texto à mão, com `$` duplicado e recuo de
YAML, seria frágil. O gerador mantém os fontes legíveis em fontes/ e produz o
YAML de forma determinística.

Uso:
    python3 ferramentas/gerar_compose.py                 # 6 APIs + 2 workers
    python3 ferramentas/gerar_compose.py --api 8 --workers 2

O gerador recusa configurações cuja soma de limits.cpus passe de 0.80 e
imprime o orçamento serviço a serviço.
"""
import argparse
import sys
from decimal import Decimal
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
FONTES = RAIZ / "fontes"

TETO = Decimal("0.80")
COTA_GO = Decimal("0.05")  # cota individual fixa de toda instância Go (regulamento)

ARQUIVOS_GO = ["main.go", "infra.go", "cache.go", "dominio.go", "api.go", "worker.go"]


def escapar_compose(texto: str) -> str:
    """O Compose interpola $VAR em qualquer valor; '$$' produz um '$' literal."""
    return texto.replace("$", "$$")


def indentar(texto: str, n: int) -> str:
    pad = " " * n
    return "\n".join((pad + linha) if linha.strip() else "" for linha in texto.rstrip("\n").split("\n"))


def dockerfile_inline() -> str:
    """Dockerfile multi-stage com todos os fontes embutidos por heredoc."""
    partes = [
        "FROM golang:1.25-alpine AS builder",
        "WORKDIR /src",
    ]
    # go.mod e go.sum primeiro: a camada de dependências fica em cache.
    for nome in ["go.mod", "go.sum"]:
        tag = "EOF_" + nome.replace(".", "_").upper()
        partes += [f"COPY <<'{tag}' /src/{nome}", (FONTES / "app" / nome).read_text().rstrip("\n"), tag]
    partes.append("RUN go mod download")
    for nome in ARQUIVOS_GO:
        tag = "EOF_" + nome.replace(".", "_").upper()
        partes += [f"COPY <<'{tag}' /src/{nome}", (FONTES / "app" / nome).read_text().rstrip("\n"), tag]
    partes += [
        'RUN CGO_ENABLED=0 GOOS=linux go build -trimpath -ldflags="-s -w" -o /out/app .',
        "",
        "FROM alpine:3.20",
        "COPY --from=builder /out/app /usr/local/bin/app",
        'ENTRYPOINT ["/usr/local/bin/app"]',
    ]
    return "\n".join(partes)


def servico_go(nome, papel, extras, depende):
    deps = "\n".join(
        f"      {d}:\n        condition: service_healthy" for d in depende
    )
    saude = ""
    if papel == "api":
        saude = """
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/health >/dev/null 2>&1 || exit 1"]
      interval: 20s
      timeout: 5s
      retries: 3
      start_period: 20s
      start_interval: 1s"""
    return f"""
  {nome}:
    <<: *servico-go
    environment:
      <<: *ambiente-go
      ROLE: {papel}
      CONSUMER: {nome}
{extras}    deploy:
      replicas: 1
      resources:
        limits:
          cpus: '{COTA_GO}'
          memory: 64M{saude}
    depends_on:
{deps}
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--api", type=int, default=6, help="instâncias da API (padrão 6)")
    ap.add_argument("--workers", type=int, default=2, help="instâncias de worker (padrão 2)")
    ap.add_argument("--cpu-gateway", default="0.10")
    ap.add_argument("--cpu-redis", default="0.10")
    ap.add_argument("--cpu-postgres", default="0.15")
    ap.add_argument("--saida", default=str(RAIZ / "docker-compose.distribuido.yaml"))
    a = ap.parse_args()

    cpu = {
        "gateway (nginx)": Decimal(a.cpu_gateway),
        "redis": Decimal(a.cpu_redis),
        "postgres": Decimal(a.cpu_postgres),
    }
    apis = [f"api-{i}" for i in range(1, a.api + 1)]
    workers = [f"worker-{i}" for i in range(1, a.workers + 1)]
    for n in apis + workers:
        cpu[n] = COTA_GO
    total = sum(cpu.values())
    total_float = 0.0
    for v in cpu.values():
        total_float += float(v)

    print("Orçamento de CPU (limits.cpus):")
    for k, v in cpu.items():
        print(f"  {k:<18} {v}")
    print(f"  {'TOTAL':<18} {total}   (soma em ponto flutuante: {total_float!r}; teto {TETO})")
    if total > TETO or total_float > float(TETO):
        sys.exit(f"ERRO: soma {total} ultrapassa o teto de {TETO} vCPU.")
    if total > Decimal("0.78"):
        print("  AVISO: margem inferior a 0.02 vCPU ao teto; arredondamento de um auditor pode estourar.")

    # ---- nginx ----
    servidores = "\n".join(f"        server {n}:8080 max_fails=0;" for n in apis)
    nginx = (FONTES / "nginx" / "nginx.conf.tpl").read_text().replace("@@SERVIDORES@@", servidores)
    schema = (FONTES / "sql" / "schema.sql").read_text()

    dep_infra = ["postgres", "redis"]
    blocos = []
    for n in apis:
        blocos.append(servico_go(n, "api", "      DB_MAX_CONNS: \"3\"\n", dep_infra))
    for n in workers:
        blocos.append(
            servico_go(n, "worker", "      DB_MAX_CONNS: \"2\"\n      WORKER_BATCH_MSGS: \"100\"\n", dep_infra)
        )
    dep_gateway = "\n".join(f"      {n}:\n        condition: service_healthy" for n in apis)

    linhas_orc = "\n".join(f"#   {k:<18} {v}" for k, v in cpu.items())

    yaml = f"""# =============================================================================
# docker-compose.distribuido.yaml — CENÁRIO B (arquitetura distribuída)
#
# ARQUIVO AUTOSSUFICIENTE, GERADO por ferramentas/gerar_compose.py a partir dos
# fontes em fontes/. Não depende de nenhum arquivo além do kit base: o código Go
# vai embutido em build.dockerfile_inline; nginx e esquema SQL, em `configs`.
#
# Topologia:   k6 -> aps-app:8080 (nginx, least_conn)
#                     -> {a.api} instâncias da API (Go, sem estado)
#                          -> Redis Streams (fila durável, AOF) + cache de índices
#                               -> {a.workers} workers (Go) -> PostgreSQL (gravação em lote)
#
# Orçamento de CPU (soma de deploy.resources.limits.cpus; teto 0.80):
{linhas_orc}
#   TOTAL              {total}
#
# Regras do regulamento atendidas:
#   * toda instância Go (API e worker) declara exatamente cpus: '0.05';
#   * porta pública 8080 e alias de rede aps-app na rede externa aps-net;
#   * nenhum volume do tipo external (só a rede aps-net é externa);
#   * PostgreSQL e Redis ficam na rede interna: a plataforma de teste só fala
#     com a aplicação pelo contrato HTTP (rotas /audit/*), nunca com o banco.
# =============================================================================
name: aps-sistema

x-ambiente-go: &ambiente-go
  DATABASE_URL: postgres://iot:iot@postgres:5432/telemetria?sslmode=disable
  REDIS_ADDR: redis:6379
  GOMAXPROCS: "1"        # com 0.05 vCPU, uma só thread evita estrangulamento do CFS
  GOMEMLIMIT: 48MiB      # abaixo do limite de 64M do contêiner: o GC age antes do OOM

x-servico-go: &servico-go
  image: aps-distribuido-app:1.0
  build:
    context: .
    dockerfile_inline: |
{indentar(escapar_compose(dockerfile_inline()), 6)}
  restart: on-failure
  stop_grace_period: 20s

services:
  # ---------------------------------------------------------------- dados ---
  postgres:
    image: postgres:16-alpine
    environment:
      POSTGRES_USER: iot
      POSTGRES_PASSWORD: iot
      POSTGRES_DB: telemetria
    command:
      - postgres
      - -c
      - max_connections=60
      - -c
      - shared_buffers=96MB
      - -c
      - wal_compression=on
      - -c
      - checkpoint_timeout=15min
      - -c
      - max_wal_size=1GB
    configs:
      - source: esquema_sql
        target: /docker-entrypoint-initdb.d/01-esquema.sql
    deploy:
      replicas: 1
      resources:
        limits:
          cpus: '{cpu["postgres"]}'
          memory: 384M
    restart: on-failure
    healthcheck:
      # -h 127.0.0.1 força TCP: durante o initdb o servidor temporário escuta só
      # no socket Unix, e um pg_isready sem host declararia "pronto" cedo demais.
      test: ["CMD-SHELL", "pg_isready -h 127.0.0.1 -U iot -d telemetria"]
      interval: 5s
      timeout: 5s
      retries: 30
      start_period: 5s
      start_interval: 1s

  redis:
    image: redis:7.4-alpine
    command:
      - redis-server
      - --appendonly
      - "yes"
      - --appendfsync
      - everysec
      - --auto-aof-rewrite-min-size
      - 512mb
      - --save
      - ""
      - --maxmemory
      - 200mb
      - --maxmemory-policy
      - noeviction
    deploy:
      replicas: 1
      resources:
        limits:
          cpus: '{cpu["redis"]}'
          memory: 256M
    restart: on-failure
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 20
      start_period: 2s
      start_interval: 1s

  # ----------------------------------------------------------- aplicação ---
{"".join(blocos)}
  # ---------------------------------------------------------------- borda ---
  gateway:
    image: nginx:1.27-alpine
    ports:
      - "8080:8080"
    configs:
      - source: nginx_conf
        target: /etc/nginx/nginx.conf
    networks:
      default:
      aps-net:
        aliases: [aps-app]
    deploy:
      replicas: 1
      resources:
        limits:
          cpus: '{cpu["gateway (nginx)"]}'
          memory: 128M
    restart: on-failure
    healthcheck:
      test: ["CMD-SHELL", "wget -qO- http://127.0.0.1:8080/health >/dev/null 2>&1 || exit 1"]
      interval: 20s
      timeout: 5s
      retries: 3
      start_period: 20s
      start_interval: 1s
    depends_on:
{dep_gateway}

configs:
  esquema_sql:
    content: |
{indentar(escapar_compose(schema), 6)}
  nginx_conf:
    content: |
{indentar(escapar_compose(nginx), 6)}

networks:
  aps-net:
    external: true
"""
    Path(a.saida).write_text(yaml)
    print(f"\nGerado: {a.saida} ({len(yaml.splitlines())} linhas)")


if __name__ == "__main__":
    main()
