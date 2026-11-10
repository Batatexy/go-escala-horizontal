# ============================================================
# nginx.conf — borda do Cenário B (balanceador de carga)
#
# Termina o HTTP na porta 8080 (a única publicada) e distribui as requisições
# entre as instâncias da API. Gerado por ferramentas/gerar_compose.py: a lista
# "upstream" acompanha o número de instâncias declarado no compose.
# ============================================================
worker_processes 1;
worker_rlimit_nofile 16384;
pid /tmp/nginx.pid;
error_log /dev/stderr warn;

events {
    worker_connections 8192;
    multi_accept on;
}

http {
    access_log off;                 # log por requisição custaria CPU da cota
    server_tokens off;
    sendfile on;
    tcp_nodelay on;

    client_max_body_size 8m;
    client_body_buffer_size 1m;     # lotes de 10 a 60 medições (~5 a 15 KB) ficam em memória
    keepalive_timeout 120s;
    keepalive_requests 100000;

    upstream api {
        # least_conn: a próxima requisição vai para a instância com menos
        # conexões ativas. Com cota de 0.05 vCPU por instância, round-robin
        # puro empilharia trabalho em uma instância já estrangulada pelo CFS.
        least_conn;
@@SERVIDORES@@
        keepalive 128;              # reaproveita conexões nginx -> API
    }

    server {
        listen 8080 backlog=4096;

        location / {
            proxy_pass http://api;
            proxy_http_version 1.1;
            proxy_set_header Connection "";
            proxy_set_header Host $host;
            proxy_buffering on;

            proxy_connect_timeout 1s;
            proxy_send_timeout 10s;
            proxy_read_timeout 10s;

            # Se uma instância falhar, outra atende. A ingestão é idempotente
            # (UNIQUE sensor+timestamp), então repetir um POST é seguro.
            proxy_next_upstream error timeout http_502 http_503 non_idempotent;
            proxy_next_upstream_tries 3;
        }
    }
}
