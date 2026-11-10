# Experimento A′ — monolito com PostgreSQL a 0,08 vCPU (valor do enunciado)

Cópia do kit com **uma única linha alterada** em `docker-compose.monolito.yaml`:
`cpus: '0.8'` → `cpus: '0.08'` no serviço `postgres`.

Motivo: o enunciado (Seção III e Anexo D.6) dá 0,08 vCPU ao banco do baseline, mas o arquivo
do kit entrega traz 0,8. Este experimento mede o monolito com o valor descrito no enunciado.

Não é entregável e não substitui o Cenário A oficial: é uma análise de sensibilidade.
Execução: `cd experimentos/A_postgres_008 && ./run_benchmark.sh A`
