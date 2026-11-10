#!/usr/bin/env python3
"""
verificar_compose.py — confere docker-compose.distribuido.yaml contra o
regulamento (Seção IV) e a lista de conformidade (Anexo C) da APS.

Não usa o gerador: lê o YAML pronto, como faria um auditor. Requer PyYAML.
Uso: python3 ferramentas/verificar_compose.py [caminho]
Sai com código 1 se algum item falhar.
"""
import re
import sys
from decimal import Decimal
from pathlib import Path

import yaml

RAIZ = Path(__file__).resolve().parent.parent
alvo = Path(sys.argv[1]) if len(sys.argv) > 1 else RAIZ / "docker-compose.distribuido.yaml"
d = yaml.safe_load(alvo.read_text())
servicos = d["services"]

falhas = 0


def item(ok, texto):
    global falhas
    print(("  [OK]    " if ok else "  [FALHA] ") + texto)
    falhas += 0 if ok else 1


def cpus(s):
    return Decimal(str(s["deploy"]["resources"]["limits"]["cpus"]))


def replicas(s):
    return int(s["deploy"].get("replicas", 1))


print(f"Arquivo: {alvo}\n")

# -- teto agregado ------------------------------------------------------------
total = sum(cpus(s) * replicas(s) for s in servicos.values())
total_sem_replicas = sum(cpus(s) for s in servicos.values())
item(total <= Decimal("0.80"), f"soma de limits.cpus (com réplicas) = {total} <= 0.80")
item(total_sem_replicas <= Decimal("0.80"), f"soma de limits.cpus (sem multiplicar réplicas) = {total_sem_replicas} <= 0.80")
item(all(replicas(s) == 1 for s in servicos.values()), "nenhum serviço usa replicas > 1 (soma é inequívoca)")
item(all("limits" in s.get("deploy", {}).get("resources", {}) and "cpus" in s["deploy"]["resources"]["limits"]
         for s in servicos.values()), "todo serviço declara limits.cpus")

# -- cota individual das instâncias Go ----------------------------------------
go = {n: s for n, s in servicos.items() if n.startswith(("api-", "worker-"))}
item(len(go) > 0 and all(cpus(s) == Decimal("0.05") for s in go.values()),
     f"as {len(go)} instâncias Go (API e worker) declaram exatamente cpus: '0.05'")
item(len(go) <= 16, f"{len(go)} instâncias da aplicação <= 16 (máximo do enunciado)")

# -- contrato de rede ----------------------------------------------------------
gw = servicos["gateway"]
item("8080:8080" in [str(p) for p in gw.get("ports", [])], "porta 8080 publicada")
alias = (gw.get("networks", {}).get("aps-net") or {}).get("aliases", [])
item("aps-app" in alias, "alias aps-app registrado na rede aps-net")
item(d["networks"]["aps-net"].get("external") is True, "aps-net é a rede externa do contrato")
publicados = [n for n, s in servicos.items() if s.get("ports")]
item(publicados == ["gateway"], f"apenas o gateway publica porta (publicam: {publicados})")
na_rede_teste = [n for n, s in servicos.items() if isinstance(s.get("networks"), dict) and "aps-net" in s["networks"]]
item(na_rede_teste == ["gateway"], f"apenas o gateway entra em aps-net; banco e fila ficam inacessíveis à plataforma (em aps-net: {na_rede_teste})")

# -- volumes -------------------------------------------------------------------
vols = d.get("volumes") or {}
item(not any((v or {}).get("external") for v in vols.values()), "nenhum volume declarado como external")
item(not any(s.get("volumes") for s in servicos.values()), "nenhum serviço monta volume ou pasta do host (dados nascem e morrem com o contêiner)")

# -- código embutido idêntico aos fontes --------------------------------------
df = d["x-servico-go"]["build"]["dockerfile_inline"]
embutidos = {nome: corpo for _tag, nome, corpo in
             re.findall(r"COPY <<'(EOF_[A-Z0-9_]+)' /src/([\w.]+)\n(.*?)\n\1\n", df, flags=re.S)}
ok_todos = True
for nome, corpo in embutidos.items():
    fonte = (RAIZ / "fontes" / "app" / nome).read_text().rstrip("\n")
    if corpo.replace("$$", "$") != fonte:
        ok_todos = False
        print(f"         divergência em {nome}")
item(len(embutidos) >= 8 and ok_todos, f"{len(embutidos)} arquivos embutidos idênticos a fontes/app/ (após desfazer $$)")

esq = d["configs"]["esquema_sql"]["content"].replace("$$", "$")
item(esq.rstrip("\n") == (RAIZ / "fontes" / "sql" / "schema.sql").read_text().rstrip("\n"), "esquema SQL embutido idêntico a fontes/sql/schema.sql")

# -- arquivos imutáveis do kit -------------------------------------------------
import hashlib
ref = RAIZ.parent / "codes"
if ref.is_dir():
    iguais = all(hashlib.sha256((RAIZ / f.name).read_bytes()).digest() == hashlib.sha256(f.read_bytes()).digest()
                 for f in ref.iterdir() if f.is_file())
    item(iguais, "arquivos do núcleo base (kit) byte a byte iguais aos originais em codes/")

print(f"\n{'CONFORME' if falhas == 0 else str(falhas) + ' ITEM(NS) COM FALHA'}")
sys.exit(1 if falhas else 0)
