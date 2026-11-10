#!/usr/bin/env bash
# ============================================================
# executar_varredura.sh — análise de sensibilidade à carga.
#
# O enunciado diz que a carga é configurável por variáveis de ambiente
# (WRITE_RATE, READ_RATE, PEAK_FACTOR, ...). Esta varredura multiplica as taxas
# basais do kit (WRITE_RATE=4, READ_RATE=50) por um fator k e mede cada variante,
# para achar o ponto em que cada arquitetura deixa de absorver a demanda.
#
# Uso:  ./scripts/executar_varredura.sh "<fatores>" "<variantes>" [rodadas]
#   variantes:  A       monolito do kit, como entregue (PostgreSQL 0,8 vCPU)
#               Aprime  monolito com PostgreSQL a 0,08 vCPU (valor do enunciado),
#                       em experimentos/A_postgres_008 (cópia do kit, 1 linha mudada)
#               B       arquitetura distribuída (docker-compose.distribuido.yaml)
# Exemplo:  ./scripts/executar_varredura.sh "2 4 8" "A Aprime B"
#
# Saída: resultados_varredura/x<k>/<variante>/<carimbo>/ (separada da matriz principal).
# Depois:  python3 ferramentas/montar_varredura.py
# ============================================================
set -uo pipefail
cd "$(dirname "$0")/.."
umask 000   # o k6 roda como uid 12345 e precisa gravar nas pastas de resultado

FATORES="${1:-2 4 8}"
VARIANTES="${2:-A Aprime B}"
RODADAS="${3:-1}"
export DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-180}"
DEST="resultados_varredura"

for k in $FATORES; do
    export WRITE_RATE=$((4 * k)) READ_RATE=$((50 * k))
    for v in $VARIANTES; do
        case "$v" in
            A)      dir=.;                            cen=A ;;
            Aprime) dir=experimentos/A_postgres_008;  cen=A ;;
            B)      dir=.;                            cen=B ;;
            *) echo "variante desconhecida: $v"; continue ;;
        esac
        for r in $(seq 1 "$RODADAS"); do
            echo
            echo "########## carga x$k (WRITE_RATE=$WRITE_RATE READ_RATE=$READ_RATE) — variante $v — rodada $r/$RODADAS ##########"
            stats="/tmp/.stats_varredura_${k}_${v}_${r}.csv"
            ./scripts/coletar_docker_stats.sh "$stats" 2 &
            coletor=$!
            (cd "$dir" && ./run_benchmark.sh "$cen")
            codigo=$?
            kill "$coletor" 2>/dev/null; wait "$coletor" 2>/dev/null
            ultimo="$(ls -1 "$dir/resultados/Cenario${cen}" 2>/dev/null | sort | tail -n1)"
            if [ -n "$ultimo" ]; then
                alvo="$DEST/x${k}/${v}/${ultimo}"
                mkdir -p "$(dirname "$alvo")"
                mv "$dir/resultados/Cenario${cen}/${ultimo}" "$alvo"
                mv "$stats" "$alvo/docker_stats.csv"
                echo "  guardado em $alvo"
            else
                echo "  AVISO: sem pasta de resultado (run_benchmark.sh terminou com $codigo)"
            fi
        done
    done
done
echo
echo "Varredura concluída. Consolide com: python3 ferramentas/montar_varredura.py"
