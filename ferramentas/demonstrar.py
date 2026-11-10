#!/usr/bin/env python3
"""
demonstrar.py — sobe o Cenário B, exercita cada funcionalidade com chamadas HTTP
reais e grava resultados/demonstracao.json (usado na Seção 7.3 do relatório).

Cobre as cinco rotas do contrato e os comportamentos extras do projeto:
idempotência, rejeição parcial, 404, verificação independente do hash,
contrapressão (Redis fora do ar -> 503) e desacoplamento aceitar/persistir
(workers parados -> 202 sem gravar; workers de volta -> dado aparece).

Uso:  python3 ferramentas/demonstrar.py        (precisa de Docker; derruba tudo ao final)
Só usa a biblioteca padrão.
"""
import hashlib
import json
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
COMPOSE = RAIZ / "docker-compose.distribuido.yaml"
BASE = "http://localhost:8080"
PROJ = ["-p", "aps-sistema", "-f", str(COMPOSE)]
itens = []


def dc(*args, check=True):
    return subprocess.run(["docker", "compose", *PROJ, *args], check=check, cwd=RAIZ,
                          capture_output=True, text=True)


def http(metodo, caminho, corpo=None, bruto=None):
    dados = bruto if bruto is not None else (json.dumps(corpo).encode() if corpo is not None else None)
    req = urllib.request.Request(BASE + caminho, data=dados, method=metodo,
                                 headers={"Content-Type": "application/json"} if dados is not None else {})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:  # conexão recusada etc.
        return 0, f"{type(e).__name__}: {e}"


def registrar(func, metodo, caminho, status, corpo_resp, chamada=None):
    resp = " ".join(corpo_resp.split())
    if len(resp) > 150:
        resp = resp[:147] + "..."
    itens.append({"funcionalidade": func, "chamada": chamada or f"{metodo} {caminho}", "resposta": f"HTTP {status} {resp}"})
    print(f"  [{status}] {func}")


def contagem():
    s, b = http("GET", "/audit/contagem")
    return json.loads(b)["total_medicoes"] if s == 200 else -1


def contagem_estavel(leituras=4, intervalo=1.0, limite=60):
    """Espera total_medicoes ficar igual em `leituras` leituras seguidas (fila drenada).
    Como a gravação é assíncrona, uma leitura isolada pode pegar o sistema no meio do caminho."""
    anterior, iguais, fim = None, 0, time.time() + limite
    while time.time() < fim:
        atual = contagem()
        iguais = iguais + 1 if atual == anterior else 1
        if iguais >= leituras:
            return atual
        anterior = atual
        time.sleep(intervalo)
    return anterior


def aguardar(cond, limite=30):
    fim = time.time() + limite
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.5)
    return False


def medicao(sensor, ts, raw, t=27.4, u=68.2):
    return {"sensor_id": sensor, "timestamp": ts,
            "metrics": {"microclimate": {"temperature_celsius": t, "relative_humidity_percentage": u}},
            "raw_telemetry_packet": raw}


def main():
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        sys.exit("Docker indisponível.")
    subprocess.run(["docker", "network", "create", "aps-net"], capture_output=True)
    print("Subindo o Cenário B ...")
    dc("down", "-v", "--remove-orphans", check=False)
    r = dc("up", "-d", "--build", check=False)
    if r.returncode != 0:
        sys.exit("falha ao subir:\n" + r.stderr[-2000:])
    try:
        if not aguardar(lambda: http("GET", "/health")[0] == 200, 120):
            sys.exit("aplicação não ficou pronta")
        print("Executando a demonstração ...")

        s, b = http("GET", "/health")
        registrar("Prontidão (RF1)", "GET", "/health", s, b)

        sensor = "SNS-5G-SEQ-000001"
        lote = {"measurements": [medicao(sensor, "2026-10-23T14:32:05.123456Z", "k3j9x2ab7c1", 27.4, 68.2),
                                 medicao(sensor, "2026-10-23T14:33:05.123456Z", "m8p2v4cd9e5", 27.9, 66.8)]}
        s, b = http("POST", "/telemetria", lote)
        registrar("Ingestão de lote com 2 medições (RF2)", "POST", "/telemetria", s, b, "POST /telemetria (2 medições)")

        aguardar(lambda: contagem() == 2)
        s, b = http("GET", "/audit/contagem")
        registrar("Persistência assíncrona: o lote aparece no banco (RF4)", "GET", "/audit/contagem", s, b)

        s, b = http("POST", "/telemetria", lote)
        time.sleep(2)
        s2, b2 = http("GET", "/audit/contagem")
        registrar("Idempotência: reenvio do mesmo lote não duplica", "POST", "/telemetria", s2, b2,
                  "POST /telemetria (mesmo lote) e depois GET /audit/contagem")

        aguardar(lambda: http("GET", f"/indice-calor/{sensor}")[0] == 200)
        s, b = http("GET", f"/indice-calor/{sensor}")
        registrar("Índice de calor: média e amostras (RF3)", "GET", f"/indice-calor/{sensor}", s, b)

        s, b = http("GET", "/indice-calor/SNS-5G-SEQ-999999")
        registrar("Sensor sem medição: 404 é resposta válida (RF3)", "GET", "/indice-calor/SNS-5G-SEQ-999999", s, b)

        s, b = http("GET", "/audit/medidas?limit=2&cursor=0")
        pag = json.loads(b)
        registrar("Listagem paginada por cursor (RF5)", "GET", "/audit/medidas?limit=2&cursor=0", s, b)
        confere = all(hashlib.sha256((m["sensor_id"] + m["timestamp"] + m["raw_packet"]).encode()).hexdigest() == m["hash"]
                      for m in pag["medidas"])
        itens.append({"funcionalidade": "Verificação independente do hash (SHA-256)",
                      "chamada": "sha256(sensor_id + timestamp + raw_packet) por fora da aplicação",
                      "resposta": f"hash recalculado == hash da API para as {len(pag['medidas'])} medições: {confere}"})
        print(f"  [{'ok' if confere else 'FALHA'}] hash confere")

        parcial = {"measurements": [medicao("SNS-5G-SEQ-000002", "2026-10-23T14:40:00.000000Z", "a1"),
                                    medicao("SNS-5G-SEQ-000002", "nao-e-data", "a2")]}
        s, b = http("POST", "/telemetria", parcial)
        registrar("Rejeição parcial: medição inválida é contada, o resto segue", "POST", "/telemetria", s, b,
                  "POST /telemetria (1 válida + 1 timestamp inválido)")

        s, b = http("POST", "/telemetria", {"measurements": []})
        registrar("Lote vazio", "POST", "/telemetria", s, b, "POST /telemetria (lote vazio)")
        s, b = http("POST", "/telemetria", bruto=b"{nao e json")
        registrar("JSON malformado", "POST", "/telemetria", s, b, "POST /telemetria (corpo inválido)")

        # --- desacoplamento: aceitar != persistir ---
        antes = contagem_estavel()  # só mede depois que a fila drenou: a gravação é assíncrona
        dc("stop", "worker-1", "worker-2")
        s, b = http("POST", "/telemetria", {"measurements": [medicao("SNS-5G-SEQ-000003", "2026-10-23T15:00:00.000000Z", "w1")]})
        time.sleep(3)
        durante = contagem()
        registrar("Workers parados: a API continua aceitando (202)", "POST", "/telemetria", s, b,
                  "docker compose stop worker-1 worker-2; POST /telemetria")
        itens.append({"funcionalidade": "Workers parados: nada é gravado enquanto isso",
                      "chamada": "GET /audit/contagem (workers parados)",
                      "resposta": f"total_medicoes antes={antes}, durante={durante} (inalterado: {antes == durante})"})
        dc("start", "worker-1", "worker-2")
        recuperou = aguardar(lambda: contagem() == antes + 1, 60)
        itens.append({"funcionalidade": "Workers de volta: a fila é drenada, sem perda",
                      "chamada": "docker compose start worker-1 worker-2; GET /audit/contagem",
                      "resposta": f"total_medicoes passou a {contagem()} (esperado {antes + 1}): {recuperou}"})
        print(f"  [{'ok' if recuperou else 'FALHA'}] recuperação de fila")

        # --- contrapressão: fila fora do ar ---
        dc("stop", "redis")
        time.sleep(2)
        s, b = http("POST", "/telemetria", {"measurements": [medicao("SNS-5G-SEQ-000004", "2026-10-23T16:00:00.000000Z", "r1")]})
        registrar("Fila fora do ar: 503 explícito, sem perda silenciosa", "POST", "/telemetria", s, b,
                  "docker compose stop redis; POST /telemetria")
        dc("start", "redis")
        aguardar(lambda: http("GET", "/health")[0] == 200, 60)
        s, b = http("POST", "/telemetria", {"measurements": [medicao("SNS-5G-SEQ-000004", "2026-10-23T16:00:00.000000Z", "r1")]})
        registrar("Fila de volta: o hub retransmite e o lote é aceito", "POST", "/telemetria", s, b,
                  "docker compose start redis; POST /telemetria (retransmissão)")
    finally:
        (RAIZ / "resultados").mkdir(exist_ok=True)
        (RAIZ / "resultados" / "demonstracao.json").write_text(json.dumps(itens, ensure_ascii=False, indent=2))
        print("Derrubando o cenário ...")
        dc("down", "-v", "--remove-orphans", check=False)
        subprocess.run(["docker", "network", "rm", "aps-net"], capture_output=True)
    print(f"Escrito resultados/demonstracao.json ({len(itens)} itens)")


if __name__ == "__main__":
    main()
