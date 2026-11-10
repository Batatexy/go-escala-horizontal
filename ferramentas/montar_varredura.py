#!/usr/bin/env python3
"""
montar_varredura.py — consolida a análise de sensibilidade à carga.

Lê resultados_varredura/x<k>/<variante>/<carimbo>/ (gerado por
scripts/executar_varredura.sh) e, como fator x1, as rodadas da matriz principal
(resultados/CenarioA, CenarioB) e do experimento A' em experimentos/.
Escreve resultados_varredura/varredura.md e varredura.csv. Só biblioteca padrão.
"""
import csv
import statistics
import sys
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))
import montar_matriz as mm  # noqa: E402

COMPOSE = {
    "A": RAIZ / "docker-compose.monolito.yaml",
    "Aprime": RAIZ / "experimentos" / "A_postgres_008" / "docker-compose.monolito.yaml",
    "B": RAIZ / "docker-compose.distribuido.yaml",
}
NOME = {"A": "A (PG 0,8)", "Aprime": "A′ (PG 0,08)", "B": "B (distribuído)"}
SAIDA = RAIZ / "resultados_varredura"


def coletar():
    """{(k, variante): [rodada, ...]}"""
    dados = {}

    def add(k, v, pasta):
        r = mm.ler_rodada("A" if v != "B" else "B", pasta, COMPOSE[v])
        if r:
            dados.setdefault((k, v), []).append(r)

    for v, cen in (("A", "A"), ("B", "B")):  # fator x1 = matriz principal
        base = RAIZ / "resultados" / f"Cenario{cen}"
        if base.is_dir():
            for p in sorted(x for x in base.iterdir() if x.is_dir()):
                add(1, v, p)
    base = RAIZ / "experimentos" / "A_postgres_008" / "resultados" / "CenarioA"  # A' no fator x1
    if base.is_dir():
        for p in sorted(x for x in base.iterdir() if x.is_dir()):
            add(1, "Aprime", p)
    if SAIDA.is_dir():
        for kdir in sorted(SAIDA.glob("x*"), key=lambda p: int(p.name[1:])):
            for vdir in sorted(x for x in kdir.iterdir() if x.is_dir()):
                for p in sorted(x for x in vdir.iterdir() if x.is_dir()):
                    add(int(kdir.name[1:]), vdir.name, p)
    return dados


def m(vals):
    vals = list(vals)
    return statistics.fmean(vals) if vals else 0.0


def gargalo(R):
    """Serviço mais saturado (maior fração de amostras >= 90% da cota), média entre rodadas."""
    sat = {}
    for r in R:
        for s, v in r["stats"].items():
            sat.setdefault(s, []).append(v["saturacao"])
    if not sat:
        return "—"
    s, v = max(((s, m(v)) for s, v in sat.items()), key=lambda x: x[1])
    return f"{s} ({100 * v:.0f}%)" if v > 0 else "nenhum"


def main():
    dados = coletar()
    if not dados:
        sys.exit("sem dados")
    cab = ["Carga", "Variante", "Rodadas", "Req/s ofertadas", "POST ok (%)", "GET ok (%)", "Carga atendida (%)",
           "p95 (ms)", "Persistência (%)", "Perdidas+Dup+Fant+Diverg", "Nota", "CPU consumida (vCPU)", "Serviço mais saturado"]
    linhas = []
    for (k, v), R in sorted(dados.items(), key=lambda kv: (kv[0][0], ["A", "Aprime", "B"].index(kv[0][1]))):
        integr = sum(r["diverg"] + r["fant"] + r["perd"] + r["dup"] for r in R)
        linhas.append([f"x{k}", NOME[v], len(R), f"{m(r['vazao_req_s'] for r in R):.1f}",
                       f"{m(r['post_ok_pct'] for r in R):.2f}", f"{m(r['get_ok_pct'] for r in R):.2f}",
                       f"{m(r['atendida_pct'] for r in R):.1f}", f"{m(r['p95_ms'] for r in R):.1f}",
                       f"{m(r['taxa_pct'] for r in R):.2f}", integr, f"{m(r['nota'] for r in R):.2f}",
                       f"{m(r['cpu_usada'] for r in R):.3f}", gargalo(R)])
    md = ["# Análise de sensibilidade à carga\n",
          "Fator xk = taxas basais do kit multiplicadas por k (WRITE_RATE=4k, READ_RATE=50k, PEAK_FACTOR=3).",
          "Cada linha é a média das rodadas indicadas. Carga atendida = iterações concluídas ÷ (concluídas + descartadas pelo k6).\n",
          "| " + " | ".join(cab) + " |", "|" + "---|" * len(cab)]
    md += ["| " + " | ".join(str(c) for c in l) + " |" for l in linhas]
    SAIDA.mkdir(exist_ok=True)
    (SAIDA / "varredura.md").write_text("\n".join(md) + "\n")
    with (SAIDA / "varredura.csv").open("w", newline="") as f:
        w = csv.writer(f, delimiter=";")
        w.writerow(cab)
        w.writerows(linhas)
    print("\n".join(md))


if __name__ == "__main__":
    main()
