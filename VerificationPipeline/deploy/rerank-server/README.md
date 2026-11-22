# rerank-server: reranker na GPU

`bge-reranker-v2-m3` w fp16 na GPU serwera z Bielikiem. Ta sama skala 0–1 co reranker lokalny
(`prawdowid.retrieval.reranker.CrossEncoderReranker`), więc progi (`rerank_min`) i wyniki ewaluacji się nie zmieniają.
Nasłuchuje tylko na `127.0.0.1:8081`; pipeline łączy się przez tunel SSH.

## Instalacja na serwerze (bez sudo)

```bash
# z maszyny z repo
scp server.py requirements.txt <user>@<serwer>:rerank-server/
scp rerank-server.service <user>@<serwer>:.config/systemd/user/

# na serwerze
cd ~/rerank-server
python3 -m venv .venv
.venv/bin/pip install torch --index-url https://download.pytorch.org/whl/cu128   # RTX 50xx (Blackwell) wymaga cu128+
.venv/bin/pip install -r requirements.txt
systemctl --user daemon-reload && systemctl --user enable --now rerank-server
curl -s localhost:8081/health          # {"status":"ok",...,"device":"cuda"}
journalctl --user -u rerank-server -f   # logi
```

Usługa `systemd --user` startuje z sesją użytkownika. Żeby startowała przy uruchomieniu systemu bez logowania:
`loginctl enable-linger <user>`.

Zasoby: ~1,2 GB VRAM (fp16), ~5 GB na dysku (venv + model w `~/.cache/huggingface`).

## Tunel i pipeline

```bash
ssh -N -L 127.0.0.1:11435:127.0.0.1:11434 -L 172.17.0.1:11435:127.0.0.1:11434 \
       -L 127.0.0.1:8081:127.0.0.1:8081   -L 172.17.0.1:8081:127.0.0.1:8081 <user>@<serwer>
```

`.env` w katalogu głównym monorepo:

```
PRAWDOWID_RERANKER_ENABLED=true
PRAWDOWID_RERANKER_PROVIDER=remote
PRAWDOWID_RERANKER_API_BASE=http://host.docker.internal:8081
PRAWDOWID_RERANKER_FALLBACK=local   # CPU, gdy serwer nie odpowiada (obraz z WITH_RERANK=true)
```

Ślad sprawdzenia pokazuje, gdzie liczono: `reranker_remote` (device, server_ms) albo `event: remote_failed` z powodem.
