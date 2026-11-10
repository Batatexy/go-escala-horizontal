#!/usr/bin/env python3
"""
montar_matriz.py — consolida resultados/ na matriz comparativa da APS.

Lê, para cada rodada em resultados/Cenario{A,B}/<carimbo>/:
  report.json              resumo do k6 (vazão, taxas, descartes, latência)
  resultado_registros.txt  saída do avaliador (enviados;rejeitados;gravados;...)
  docker_stats.csv         amostras de `docker stats` (coletor em scripts/)

e escreve resultados/matriz_comparativa.md e resultados/matriz_comparativa.csv.
Só usa a biblioteca padrão.

DEFINIÇÕES (o enunciado cita o "Índice de Eficiência de Vazão" mas não o define;
esta é a definição adotada pela equipe e declarada no relatório):

  vazão útil (registros/s)  = registros gravados / duração da execução
  IEV                        = vazão útil / soma de limits.cpus do cenário
                               [registros·s⁻¹·vCPU⁻¹]
  taxa de sucesso POST/GET   = aceitas / (aceitas + perdidas)   (contadores do k6)
  nota (avaliador)           = 10 × gravados / enviados
  saturação de um serviço    = fração das amostras em que o uso de CPU atingiu
                               pelo menos 90% da sua cota (contenção)
"""
import csv
import json
import re
import statistics
import sys
from datetime import datetime
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
RESULT = RAIZ / "resultados"
COMPOSES = {"A": RAIZ / "docker-compose.monolito.yaml",
            "Aprime": RAIZ / "experimentos" / "A_postgres_008" / "docker-compose.monolito.yaml",
            "B": RAIZ / "docker-compose.distribuido.yaml"}
TITULOS = {"A": "Cenário A (kit, PG 0,8)", "Aprime": "Variante A′ (PG 0,08)", "B": "Cenário B (distribuído)"}


# ---------------------------------------------------------------- compose ----
def cotas_cpu(arquivo: Path) -> dict:
    """service -> cpus (float), lendo só linhas de configuração, não comentários."""
    cotas, secao, servico = {}, False, None
    for linha in arquivo.read_text().splitlines():
        if linha.startswith("services:"):
            secao = True
            continue
        if secao and re.match(r"^\S", linha) and not linha.startswith("#"):
            secao = False
        if not secao or linha.lstrip().startswith("#"):
            continue
        m = re.match(r"^  ([A-Za-z0-9_-]+):\s*(#.*)?$", linha)
        if m:
            servico = m.group(1)
            continue
        m = re.match(r"^\s+cpus:\s*['\"]?([0-9.]+)", linha)
        if m and servico:
            cotas[servico] = float(m.group(1))
    return cotas


# ------------------------------------------------------------ docker stats ----
def para_mib(texto: str) -> float:
    m = re.match(r"([0-9.]+)\s*([A-Za-z]+)", texto.strip())
    if not m:
        return 0.0
    v, u = float(m.group(1)), m.group(2).lower()
    return v * {"b": 1 / 1048576, "kb": 1 / 1024, "kib": 1 / 1024, "mb": 0.9537, "mib": 1, "gb": 953.7, "gib": 1024}.get(u, 0)


def servico_de(container: str):
    m = re.match(r"^aps-(?:sistema|teste)-(.+)-\d+$", container)
    return m.group(1) if m else None


def ler_stats(arquivo: Path, cotas: dict) -> dict:
    """Retorna, por serviço do sistema sob teste, CPU média/pico, memória pico e saturação,
    restritos à janela em que o contêiner stress-test esteve ativo."""
    if not arquivo.exists():
        return {}
    linhas = []
    with arquivo.open() as f:
        for r in csv.DictReader(f, delimiter=";"):
            try:
                linhas.append((datetime.fromisoformat(r["timestamp"]), r["container"],
                               float(r["cpu_perc"].rstrip("%")), para_mib(r["mem_uso"].split("/")[0])))
            except (ValueError, KeyError):
                continue
    tk6 = [t for t, c, *_ in linhas if "stress-test" in c]
    if not tk6:
        return {}
    ini, fim = min(tk6), max(tk6)
    por = {}
    for t, c, cpu, mem in linhas:
        s = servico_de(c)
        if s is None or s not in cotas or not (ini <= t <= fim):
            continue
        por.setdefault(s, []).append((cpu, mem))
    saida = {}
    for s, v in por.items():
        quota_pct = cotas[s] * 100
        saida[s] = {
            "cpu_media": statistics.fmean(x[0] for x in v),
            "cpu_pico": max(x[0] for x in v),
            "mem_pico_mib": max(x[1] for x in v),
            "saturacao": sum(1 for x in v if x[0] >= 0.9 * quota_pct) / len(v),
            "cota_pct": quota_pct,
            "amostras": len(v),
        }
    return saida


# ------------------------------------------------------------------- rodada ----
def ler_rodada(cen: str, pasta: Path, compose: Path | None = None) -> dict | None:
    rep, res = pasta / "report.json", pasta / "resultado_registros.txt"
    if not rep.exists() or not res.exists():
        return None
    linha = res.read_text().strip()
    campos = linha.split(";")
    if len(campos) != 8:
        print(f"  aviso: {res} sem resultado válido ({linha!r}); ignorado")
        return None
    enviados, rejeitados, gravados = int(campos[0]), int(campos[1]), int(campos[2])
    diverg, fant, perd, dup = (int(x) for x in campos[4:8])
    m = json.loads(rep.read_text())["metrics"]

    def cnt(nome):
        return m.get(nome, {}).get("count", m.get(nome, {}).get("value", 0)) or 0

    reqs, taxa = cnt("http_reqs"), m.get("http_reqs", {}).get("rate", 0) or 0
    duracao = reqs / taxa if taxa else 0
    cotas = cotas_cpu(compose or COMPOSES[cen])
    cpu_total = sum(cotas.values())
    post_ok, post_perd = cnt("post_aceitos"), cnt("post_perdidos")
    get_ok, get_perd = cnt("get_aceitos"), cnt("get_perdidos")
    vazao_reg = gravados / duracao if duracao else 0
    concluidas, descartadas = cnt("iterations"), cnt("dropped_iterations")
    # carga atendida: fração das iterações previstas que o gerador conseguiu disparar.
    # A nota do avaliador só conta o que foi ENVIADO; iterações descartadas por falta de
    # capacidade não entram no denominador, então esta métrica complementa a persistência.
    atendida = 100 * concluidas / (concluidas + descartadas) if concluidas + descartadas else 0
    stats = ler_stats(pasta / "docker_stats.csv", cotas)
    # CPU efetivamente consumida (vCPU): soma das médias de uso de cada serviço na janela de estresse
    cpu_usada = sum(v["cpu_media"] for v in stats.values()) / 100
    return {
        "cenario": cen, "pasta": pasta.name,
        "reqs": reqs, "vazao_req_s": taxa, "duracao_s": duracao,
        "post_ok_pct": 100 * post_ok / (post_ok + post_perd) if post_ok + post_perd else 0,
        "get_ok_pct": 100 * get_ok / (get_ok + get_perd) if get_ok + get_perd else 0,
        "post_total": post_ok + post_perd, "get_total": get_ok + get_perd,
        "descartadas": descartadas, "iteracoes": concluidas, "atendida_pct": atendida,
        "p95_ms": m.get("http_req_duration", {}).get("p(95)", 0) or 0,
        "enviados": enviados, "rejeitados": rejeitados, "gravados": gravados,
        "taxa_pct": 100 * gravados / enviados if enviados else 0,
        "diverg": diverg, "fant": fant, "perd": perd, "dup": dup,
        "sem_dado": cnt("leitura_sem_dado"),
        "nota": 10 * gravados / enviados if enviados else 0,
        "cpu_total": cpu_total, "vazao_reg_s": vazao_reg,
        "iev": vazao_reg / cpu_total if cpu_total else 0,
        "stats": stats, "cpu_usada": cpu_usada,
        "iev_medido": vazao_reg / cpu_usada if cpu_usada else 0,
    }


def media_dp(valores):
    valores = list(valores)
    if not valores:
        return 0.0, 0.0
    return statistics.fmean(valores), (statistics.stdev(valores) if len(valores) > 1 else 0.0)


def fmt(valores, casas=2, sufixo=""):
    m, dp = media_dp(valores)
    if len(list(valores)) > 1:
        return f"{m:.{casas}f}{sufixo} ± {dp:.{casas}f}"
    return f"{m:.{casas}f}{sufixo}"


# --------------------------------------------------------------------- main ----
LINHAS = [
    ("Rodadas consideradas", lambda R: str(len(R))),
    ("Soma de limits.cpus (vCPU)", lambda R: f"{R[0]['cpu_total']:.2f}"),
    ("Vazão do gerador (req/s)", lambda R: fmt([r["vazao_req_s"] for r in R])),
    ("Taxa de sucesso POST /telemetria (%)", lambda R: fmt([r["post_ok_pct"] for r in R])),
    ("Taxa de sucesso GET /indice-calor (%)", lambda R: fmt([r["get_ok_pct"] for r in R])),
    ("Iterações descartadas pelo k6", lambda R: fmt([r["descartadas"] for r in R], 0)),
    ("Carga atendida (%)", lambda R: fmt([r["atendida_pct"] for r in R], 1)),
    ("Latência p95 (ms) [informativo]", lambda R: fmt([r["p95_ms"] for r in R], 1)),
    ("Registros enviados", lambda R: fmt([r["enviados"] for r in R], 0)),
    ("Registros gravados", lambda R: fmt([r["gravados"] for r in R], 0)),
    ("Taxa de persistência (%)", lambda R: fmt([r["taxa_pct"] for r in R])),
    ("Divergentes / Fantasmas / Perdidas / Duplicatas",
     lambda R: " / ".join(str(sum(r[k] for r in R)) for k in ("diverg", "fant", "perd", "dup")) + " (soma)"),
    ("Leituras sem dado (404)", lambda R: fmt([r["sem_dado"] for r in R], 0)),
    ("Vazão útil (registros gravados/s)", lambda R: fmt([r["vazao_reg_s"] for r in R], 1)),
    ("IEV nominal (registros/s por vCPU alocada)", lambda R: fmt([r["iev"] for r in R], 1)),
    ("CPU efetivamente consumida (vCPU, docker stats)", lambda R: fmt([r["cpu_usada"] for r in R], 3)),
    ("IEV medido (registros/s por vCPU consumida)", lambda R: fmt([r["iev_medido"] for r in R], 0)),
    ("Nota do avaliador (0–10)", lambda R: fmt([r["nota"] for r in R])),
]


def coletar_matriz():
    """Rodadas de carga padrão (x1) das três variantes."""
    out = {"A": [], "Aprime": [], "B": []}
    fontes = {"A": RESULT / "CenarioA", "B": RESULT / "CenarioB",
              "Aprime": RAIZ / "resultados_varredura" / "x1" / "Aprime"}
    for v, base in fontes.items():
        if base.is_dir():
            for pasta in sorted(p for p in base.iterdir() if p.is_dir()):
                r = ler_rodada("A" if v != "B" else "B", pasta, COMPOSES[v])
                if r:
                    out[v].append(r)
    return out


def main():
    rod = coletar_matriz()
    vs = [v for v in ("A", "Aprime", "B") if rod[v]]
    if not vs:
        sys.exit("Nenhuma rodada em resultados/Cenario*/ . Rode ./scripts/executar_matriz.sh primeiro.")

    md = ["# Matriz comparativa (carga padrão do kit) — A × A′ × B\n",
          "Gerada por `ferramentas/montar_matriz.py`. Valores com ± são média ± desvio-padrão entre rodadas.\n",
          "| Métrica | " + " | ".join(TITULOS[v] for v in vs) + " |", "|---|" + "---|" * len(vs)]
    csv_linhas = [["metrica"] + [TITULOS[v] for v in vs]]
    for nome, f in LINHAS:
        cel = [f(rod[v]) for v in vs]
        md.append(f"| {nome} | " + " | ".join(cel) + " |")
        csv_linhas.append([nome] + cel)

    if rod["A"] and rod["B"]:
        ia, _ = media_dp(r["iev"] for r in rod["A"])
        ib, _ = media_dp(r["iev"] for r in rod["B"])
        ma, _ = media_dp(r["iev_medido"] for r in rod["A"])
        mb, _ = media_dp(r["iev_medido"] for r in rod["B"])
        md.append(f"\n**IEV nominal B/A = {ib / ia:.2f}×; IEV medido B/A = {mb / ma:.2f}×**")

    for v in vs:
        R = rod[v]
        if not R[-1]["stats"]:
            continue
        md += [f"\n## docker stats — {TITULOS[v]} (rodada {R[-1]['pasta']}, janela de estresse)\n",
               "| Serviço | Cota (% de 1 CPU) | CPU média (%) | CPU pico (%) | Saturação (≥90% da cota) | Memória pico (MiB) | Amostras |",
               "|---|---|---|---|---|---|---|"]
        for s, d in sorted(R[-1]["stats"].items()):
            md.append(f"| {s} | {d['cota_pct']:.0f} | {d['cpu_media']:.2f} | {d['cpu_pico']:.2f} | "
                      f"{100 * d['saturacao']:.0f}% | {d['mem_pico_mib']:.1f} | {d['amostras']} |")

    md.append("\n## Rodadas individuais\n")
    md += ["| Variante | Rodada | Enviados | Gravados | Persistência (%) | POST ok (%) | GET ok (%) | Descartadas | Nota |", "|---|---|---|---|---|---|---|---|---|"]
    for v in vs:
        for r in rod[v]:
            md.append(f"| {v} | {r['pasta']} | {r['enviados']} | {r['gravados']} | {r['taxa_pct']:.2f} | "
                      f"{r['post_ok_pct']:.2f} | {r['get_ok_pct']:.2f} | {r['descartadas']} | {r['nota']:.2f} |")

    (RESULT / "matriz_comparativa.md").write_text("\n".join(md) + "\n")
    with (RESULT / "matriz_comparativa.csv").open("w", newline="") as f:
        csv.writer(f, delimiter=";").writerows(csv_linhas)
    print("\n".join(md))
    print(f"\nEscrito: {RESULT / 'matriz_comparativa.md'} e .csv")


if __name__ == "__main__":
    main()
