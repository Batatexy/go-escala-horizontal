#!/usr/bin/env bash
# Injeção de falha sob carga (Cenário B, carga padrão): ~30 s após o início do k6,
# para o worker-1 (docker stop) e mata a api-3 (docker kill, SIGKILL); religa ambos 40 s depois.
# O resultado vai para resultados_falhas/ (fora da matriz principal).
set -uo pipefail
cd "$(dirname "$0")/.."
umask 000
export DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-180}"
( sleep 60
  echo ">>> FALHA: docker stop worker-1; docker kill api-3  ($(date +%T))"
  docker stop aps-sistema-worker-1-1 >/dev/null; docker kill aps-sistema-api-3-1 >/dev/null
  sleep 40
  echo ">>> RECUPERAÇÃO: docker start worker-1 api-3  ($(date +%T))"
  docker start aps-sistema-worker-1-1 aps-sistema-api-3-1 >/dev/null ) &
./run_benchmark.sh B
wait
ult="$(ls -1 resultados/CenarioB | sort | tail -n1)"
mkdir -p resultados_falhas && mv "resultados/CenarioB/$ult" resultados_falhas/
echo "guardado em resultados_falhas/$ult"
