# APS 2026/2: escalabilidade horizontal × vertical sob restrição de recursos

Arquitetura distribuída (**Cenário B**) para o serviço de telemetria microclimática, mais as ferramentas para medi-la contra o monolito do kit (**Cenário A**). O relatório ABNT está em `../DocumentoCriado/`.

## Arquitetura em uma frase

`k6 → nginx (aps-app:8080) → 6 APIs Go (0,05 vCPU) → Redis Streams (fila durável) → 2 workers Go (0,05 vCPU) → PostgreSQL (gravação em lote)`, com cache de índices no Redis. A API responde **202** assim que o Redis confirma o `XADD`; o worker grava depois, em lote, em uma transação.

Soma de `limits.cpus`: **0,75** (teto: 0,80). Toda instância Go = `cpus: '0.05'`.

## Conteúdo

| Caminho | O que é | Mexer? |
|---|---|---|
| `main.go`, `avaliar_justo.go`, `script.js`, `init.sql`, `Dockerfile.monolito`, `docker-compose.monolito.yaml`, `docker-compose.teste.yaml`, `run_benchmark.sh/.bat` | **Kit do professor, cópia byte a byte** (imutável pelo regulamento) | **Não** |
| `docker-compose.distribuido.yaml` | **O entregável do Cenário B.** Autossuficiente: código Go embutido (`dockerfile_inline`), nginx e SQL em `configs`. **Gerado** | Não editar à mão; regenerar |
| `fontes/app/` | Fontes Go legíveis + testes (`dominio_test.go`) + `go.mod/go.sum` | Sim |
| `fontes/nginx/nginx.conf.tpl`, `fontes/sql/schema.sql` | Configuração do gateway e esquema do banco | Sim |
| `ferramentas/gerar_compose.py` | Gera o `docker-compose.distribuido.yaml` a partir de `fontes/` | — |
| `ferramentas/verificar_compose.py` | Auditor: confere regulamento e Anexo C no arquivo pronto | — |
| `ferramentas/montar_matriz.py` | Consolida `resultados/` na matriz comparativa | — |
| `ferramentas/montar_varredura.py` | Consolida a análise de sensibilidade à carga | — |
| `ferramentas/demonstrar.py` | Sobe o Cenário B e exercita cada funcionalidade com chamadas reais (grava `resultados/demonstracao.json`) | — |
| `scripts/executar_matriz.sh` | N rodadas de A e B + coleta de `docker stats` + matriz | — |
| `scripts/executar_varredura.sh` | Multiplica a carga (×2, ×4, …) e mede A, A′ e B | — |
| `scripts/coletar_docker_stats.sh` | Amostrador de `docker stats` em CSV | — |
| `experimentos/A_postgres_008/` | **Variante A′:** cópia do kit com **uma linha** alterada (PostgreSQL `0.8` → `0.08`, o valor do enunciado). Análise de sensibilidade, **não** é entregável | — |
| `resultados/` | Matriz principal (`CenarioA/`, `CenarioB/`, `matriz_comparativa.*`, `demonstracao.json`); `arquivo_compose_anterior/` guarda rodadas feitas antes de uma correção só de healthcheck | Gerado |
| `resultados_varredura/` | Rodadas por fator de carga (`x2/`, `x4/`, …) e `varredura.md/.csv` | Gerado |

## Pré-requisitos (Arch / CachyOS)

```bash
sudo pacman -S --needed docker docker-compose docker-buildx go
sudo systemctl enable --now docker
sudo usermod -aG docker $USER      # sair e entrar na sessão (ou: newgrp docker)
```

O k6 e o avaliador rodam em contêineres (o kit usa `grafana/k6:0.50.0` e `golang:1.25-alpine`); **não é preciso instalar k6 no hospedeiro**. O build das imagens precisa de acesso à rede (módulos Go e imagens base).

## Como rodar

```bash

# 1) conferir a conformidade e os testes (não precisa de Docker)
python3 ferramentas/verificar_compose.py
(cd fontes/app && go test ./...)

# 2) benchmark oficial do kit: Cenário A e depois B
./run_benchmark.sh              # ou: ./run_benchmark.sh B

# 3) matriz comparativa com docker stats, 3 rodadas de cada cenário (~35 min)
./scripts/executar_matriz.sh 3
#    → resultados/matriz_comparativa.md e .csv  (a variante A′ vem de resultados_varredura/x1/Aprime)

# 4) demonstração funcional do Cenário B (~2 min)
python3 ferramentas/demonstrar.py

# 5) sensibilidade à carga: fatores de carga x variantes (A, Aprime, B), N rodadas
./scripts/executar_varredura.sh "1" "Aprime" 3        # A′ na carga padrão
./scripts/executar_varredura.sh "2 4 8" "A Aprime B"  # ×2, ×4, ×8
./scripts/executar_varredura.sh "16 32" "B" 2         # onde o B começa a ceder
python3 ferramentas/montar_varredura.py
```

Em sessões em que o grupo `docker` ainda não foi herdado, envolva os comandos em `newgrp docker <<'EOF' ... EOF`.

Para regenerar o arquivo distribuído após mexer em `fontes/`:

```bash
python3 ferramentas/gerar_compose.py                    # 6 APIs + 2 workers
python3 ferramentas/gerar_compose.py --api 8 --workers 2   # variação de dimensionamento
python3 ferramentas/verificar_compose.py
```

O gerador **recusa** configurações cuja soma passe de 0,80 vCPU.

## Contrato implementado

| Rota | Resposta |
|---|---|
| `GET /health` | 200 `{"status":"ok"}`; 503 se PostgreSQL ou Redis estiverem fora |
| `POST /telemetria` | **202** `{status, sensor_id, total_recebido, total_aceito, total_persistido, total_rejeitado, humidex_medio}`; 400 lote vazio / JSON inválido; 503 fila indisponível |
| `GET /indice-calor/{id}` | 200 `{sensor_id, humidex_medio, amostras}`; **404** sem medição |
| `GET /audit/contagem` | 200 `{total_medicoes, total_indices}` (sempre iguais) |
| `GET /audit/medidas?limit=&cursor=` | 200 `{medidas:[{id,sensor_id,timestamp,raw_packet,hash}], proximo_cursor}` |

`total_persistido` na resposta do POST significa **"persistido no log durável (Redis AOF)"**; a gravação no PostgreSQL é assíncrona (ver relatório, Seção 5.7).

## Resultados (execução de 6 nov. 2026, 16 núcleos, Docker Engine nativo)

| Carga | A (kit, PG 0,8) | A′ (PG 0,08) | B (distribuído) |
|---|---|---|---|
| ×1 (padrão) | 100% persistido, p95 131 ms | **99,48%**, 463 perdidas, p95 1,7 s | 100%, p95 0,5 ms |
| ×2 | 91% (3 rodadas, muita variação) | 72% | 100% |
| ×4 | 33% | 27% | 100% |
| ×8 | 13% | 10% (1 rodada) | 100% |
| ×16 | — | — | 100% persistido, 99,6% da carga atendida (3 rodadas) |
| ×32 | — | — | 100% persistido, **82,5% da carga atendida** (3 rodadas): ponto de ruptura |

**Falha sob carga** (`scripts/teste_falha_sob_carga.sh`): worker-1 parado e api-3 morta (SIGKILL) por 40 s durante o benchmark → 100% persistido, 0 perdidas, 0 descartes.

Leitura honesta: com o banco do kit (0,8 vCPU), o baseline **não** colapsa na carga padrão; com o do enunciado (0,08), perde dados já nela. A nota do avaliador não penaliza iterações que o k6 descartou: veja a coluna "Carga atendida". Detalhes e limitações no relatório (Seção 7).

## Garantias e limites

- **Nenhuma perda silenciosa:** o 202 só sai depois do `XADD`; fila cheia ou fora do ar → 503.
- **Idempotência:** `UNIQUE(sensor_id, timestamp)` + `ON CONFLICT DO NOTHING`; o agregado só soma linhas realmente inseridas.
- **Recuperação:** pendentes relidos na partida; `XAUTOCLAIM` para consumidores mortos; mensagem ilegível vai para `telemetria:mortos`.
- **Paginação segura:** gravação serializada por *advisory lock* → ids visíveis em prefixo contínuo.
- **Limite conhecido:** Redis com `appendfsync everysec`. Queda do *processo* não perde dado; queda do *hospedeiro* pode perder até ~1 s de mensagens já confirmadas ao hub.

## Problemas conhecidos

- **Linux, "permission denied" ao gravar `report.json`:** a imagem do k6 roda sem ser root. Use `./scripts/executar_matriz.sh` (aplica `umask 000`) ou `chmod -R a+rwX resultados` antes do `run_benchmark.sh`.
- **`docker: permission denied`:** o usuário não está no grupo `docker` (veja pré-requisitos).
- **Divergências entre o enunciado e o kit** (0,08 × 0,8 vCPU no PostgreSQL do baseline etc.): ver `../DocumentoCriado/00_Avaliacao_do_Enunciado_e_do_Kit.md`.
