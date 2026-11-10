#!/usr/bin/env bash
# ============================================================
# executar_matriz.sh — roda a matriz comparativa (Cenário A x Cenário B).
#
# Uso:
#   ./scripts/executar_matriz.sh            # 1 rodada de A e de B
#   ./scripts/executar_matriz.sh 3          # 3 rodadas de cada (média e desvio)
#   ./scripts/executar_matriz.sh 3 "B"      # só o Cenário B, 3 rodadas
#
# Chama o run_benchmark.sh do kit sem modificá-lo, mantém um coletor de
# `docker stats` rodando em segundo plano durante cada cenário e, ao final,
# chama ferramentas/montar_matriz.py.
#
# DRAIN_TIMEOUT vale para os dois cenários (o enunciado recomenda 180 a 300 s
# para arquiteturas assíncronas). Use a mesma régua nos dois: a diferença de
# resultado deve vir da arquitetura, e não da paciência do avaliador.
# ============================================================
set -uo pipefail
cd "$(dirname "$0")/.."

# No Linux, a imagem grafana/k6 roda com um usuário sem privilégios (uid 12345) e
# precisa escrever em resultados/..., criado pelo run_benchmark.sh com o uid do
# host. Com umask 000 as pastas nascem com permissão total e o k6 consegue gravar
# report.json e stream.json. (No Docker Desktop do Windows/macOS isso é irrelevante.)
umask 000

RODADAS="${1:-1}"
CENARIOS="${2:-A B}"
export DRAIN_TIMEOUT="${DRAIN_TIMEOUT:-180}"

[ -f docker-compose.distribuido.yaml ] || python3 ferramentas/gerar_compose.py

mkdir -p resultados
for rodada in $(seq 1 "$RODADAS"); do
    for c in $CENARIOS; do
        echo
        echo "########## Rodada $rodada/$RODADAS — Cenário $c (DRAIN_TIMEOUT=${DRAIN_TIMEOUT}s) ##########"
        stats="resultados/.docker_stats_${c}_${rodada}.csv"
        ./scripts/coletar_docker_stats.sh "$stats" 2 &
        coletor=$!

        ./run_benchmark.sh "$c"
        codigo=$?

        kill "$coletor" 2>/dev/null; wait "$coletor" 2>/dev/null
        ultimo="$(ls -1 "resultados/Cenario${c}" 2>/dev/null | sort | tail -n1)"
        if [ -n "$ultimo" ]; then
            mv "$stats" "resultados/Cenario${c}/${ultimo}/docker_stats.csv"
        else
            echo "AVISO: nenhuma pasta de resultado do Cenário $c; stats ficou em $stats"
        fi
        [ "$codigo" -ne 0 ] && echo "AVISO: run_benchmark.sh $c terminou com código $codigo"
    done
done

echo
python3 ferramentas/montar_matriz.py
