# Matriz comparativa (carga padrão do kit) — A × A′ × B

Gerada por `ferramentas/montar_matriz.py`. Valores com ± são média ± desvio-padrão entre rodadas.

| Métrica | Cenário A (kit, PG 0,8) | Variante A′ (PG 0,08) | Cenário B (distribuído) |
|---|---|---|---|
| Rodadas consideradas | 3 | 3 | 3 |
| Soma de limits.cpus (vCPU) | 0.85 | 0.13 | 0.75 |
| Vazão do gerador (req/s) | 78.54 ± 0.02 | 77.12 ± 0.06 | 78.58 ± 0.00 |
| Taxa de sucesso POST /telemetria (%) | 100.00 ± 0.00 | 99.53 ± 0.21 | 100.00 ± 0.00 |
| Taxa de sucesso GET /indice-calor (%) | 100.00 ± 0.00 | 99.63 ± 0.07 | 100.00 ± 0.00 |
| Iterações descartadas pelo k6 | 5 ± 3 | 212 ± 10 | 0 ± 0 |
| Carga atendida (%) | 100.0 ± 0.0 | 98.1 ± 0.1 | 100.0 ± 0.0 |
| Latência p95 (ms) [informativo] | 130.7 ± 38.8 | 1676.7 ± 255.9 | 0.5 ± 0.0 |
| Registros enviados | 29871 ± 55 | 29486 ± 324 | 29617 ± 226 |
| Registros gravados | 29871 ± 55 | 29332 ± 369 | 29617 ± 226 |
| Taxa de persistência (%) | 100.00 ± 0.00 | 99.48 ± 0.16 | 100.00 ± 0.00 |
| Divergentes / Fantasmas / Perdidas / Duplicatas | 0 / 0 / 0 / 0 (soma) | 0 / 0 / 463 / 0 (soma) | 0 / 0 / 0 / 0 (soma) |
| Leituras sem dado (404) | 0 ± 0 | 7 ± 9 | 0 ± 0 |
| Vazão útil (registros gravados/s) | 206.0 ± 0.4 | 202.3 ± 2.5 | 204.3 ± 1.6 |
| IEV nominal (registros/s por vCPU alocada) | 242.4 ± 0.4 | 1556.0 ± 19.6 | 272.3 ± 2.1 |
| CPU efetivamente consumida (vCPU, docker stats) | 0.075 ± 0.007 | 0.075 ± 0.002 | 0.050 ± 0.002 |
| IEV medido (registros/s por vCPU consumida) | 2757 ± 257 | 2713 ± 64 | 4117 ± 191 |
| Nota do avaliador (0–10) | 10.00 ± 0.00 | 9.95 ± 0.02 | 10.00 ± 0.00 |

**IEV nominal B/A = 1.12×; IEV medido B/A = 1.49×**

## docker stats — Cenário A (kit, PG 0,8) (rodada 20261106_172141, janela de estresse)

| Serviço | Cota (% de 1 CPU) | CPU média (%) | CPU pico (%) | Saturação (≥90% da cota) | Memória pico (MiB) | Amostras |
|---|---|---|---|---|---|---|
| postgres | 80 | 4.97 | 14.17 | 0% | 42.0 | 36 |
| sistema-monolito | 5 | 2.96 | 5.03 | 14% | 18.5 | 36 |

## docker stats — Variante A′ (PG 0,08) (rodada 20261106_173450, janela de estresse)

| Serviço | Cota (% de 1 CPU) | CPU média (%) | CPU pico (%) | Saturação (≥90% da cota) | Memória pico (MiB) | Amostras |
|---|---|---|---|---|---|---|
| postgres | 8 | 4.55 | 8.41 | 25% | 50.8 | 36 |
| sistema-monolito | 5 | 3.08 | 5.15 | 17% | 28.9 | 36 |

## docker stats — Cenário B (distribuído) (rodada 20261106_181736, janela de estresse)

| Serviço | Cota (% de 1 CPU) | CPU média (%) | CPU pico (%) | Saturação (≥90% da cota) | Memória pico (MiB) | Amostras |
|---|---|---|---|---|---|---|
| api-1 | 5 | 0.16 | 0.32 | 0% | 9.2 | 36 |
| api-2 | 5 | 0.18 | 0.62 | 0% | 9.4 | 36 |
| api-3 | 5 | 0.50 | 2.37 | 0% | 9.9 | 36 |
| api-4 | 5 | 0.33 | 2.20 | 0% | 9.2 | 36 |
| api-5 | 5 | 0.51 | 2.40 | 0% | 16.2 | 36 |
| api-6 | 5 | 0.36 | 2.47 | 0% | 17.7 | 36 |
| gateway | 10 | 0.56 | 1.24 | 0% | 9.6 | 36 |
| postgres | 15 | 1.53 | 7.22 | 0% | 42.6 | 36 |
| redis | 10 | 0.90 | 2.86 | 0% | 14.6 | 36 |
| worker-1 | 5 | 0.11 | 0.28 | 0% | 9.6 | 36 |
| worker-2 | 5 | 0.11 | 0.29 | 0% | 9.6 | 36 |

## Rodadas individuais

| Variante | Rodada | Enviados | Gravados | Persistência (%) | POST ok (%) | GET ok (%) | Descartadas | Nota |
|---|---|---|---|---|---|---|---|---|
| A | 20261106_170944 | 29933 | 29933 | 100.00 | 100.00 | 100.00 | 8 | 10.00 |
| A | 20261106_171550 | 29849 | 29849 | 100.00 | 100.00 | 100.00 | 2 | 10.00 |
| A | 20261106_172141 | 29830 | 29830 | 100.00 | 100.00 | 100.00 | 5 | 10.00 |
| Aprime | 20261106_172853 | 29542 | 29408 | 99.55 | 99.64 | 99.70 | 208 | 9.95 |
| Aprime | 20261106_173152 | 29138 | 28931 | 99.29 | 99.29 | 99.57 | 205 | 9.93 |
| Aprime | 20261106_173450 | 29779 | 29657 | 99.59 | 99.64 | 99.62 | 223 | 9.96 |
| B | 20261106_181134 | 29358 | 29358 | 100.00 | 100.00 | 100.00 | 0 | 10.00 |
| B | 20261106_181435 | 29773 | 29773 | 100.00 | 100.00 | 100.00 | 0 | 10.00 |
| B | 20261106_181736 | 29721 | 29721 | 100.00 | 100.00 | 100.00 | 0 | 10.00 |
