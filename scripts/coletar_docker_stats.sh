#!/usr/bin/env bash
# ============================================================
# coletar_docker_stats.sh — amostra `docker stats` em CSV até ser encerrado.
#
# Uso:  ./scripts/coletar_docker_stats.sh <saida.csv> [intervalo_em_segundos]
#
# O enunciado exige métricas de contenção e uso de recursos coletadas com
# `docker stats` durante todas as janelas ativas de estresse. Este coletor
# roda ao lado do run_benchmark.sh (que é imutável) e registra todos os
# contêineres, inclusive o k6; montar_matriz.py usa a presença do contêiner
# stress-test para recortar a janela de estresse.
#
# Atenção: CPUPerc do docker stats é relativo a UM núcleo do host (100% = 1
# vCPU). Um contêiner limitado a 0.05 vCPU satura, portanto, perto de 5%.
# ============================================================
set -u
saida="${1:?informe o arquivo CSV de saída}"
intervalo="${2:-2}"

echo "timestamp;container;cpu_perc;mem_uso;mem_perc;net_io;block_io;pids" > "$saida"
while true; do
    agora="$(date +%Y-%m-%dT%H:%M:%S.%3N)"
    docker stats --no-stream \
        --format '{{.Name}};{{.CPUPerc}};{{.MemUsage}};{{.MemPerc}};{{.NetIO}};{{.BlockIO}};{{.PIDs}}' \
        2>/dev/null | sed "s|^|${agora};|" >> "$saida"
    sleep "$intervalo"
done
