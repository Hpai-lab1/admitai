#!/usr/bin/env bash
# Start the whole local prototype: Module A (8100), Module B (8150), Module C API (8200) and the
# web app (8210), all on 127.0.0.1. Logs go to .local/logs/. Stop with: scripts/run_local.sh stop
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/.local/logs"
mkdir -p "$LOGS"
cd "$ROOT"
# shellcheck source=/dev/null
source local.env.sh
# Lab-server overrides (git-ignored): the LiteLLM gateway, LiveKit and this server's models.
# shellcheck source=/dev/null
[ -f .local/lab.env.sh ] && source .local/lab.env.sh

stop() {
  pkill -f "uvicorn knowledge_api.main:app" || true
  pkill -f "uvicorn assistant_api.main:app" || true
  pkill -f "uvicorn experience_api.main:app" || true
  pkill -f "experience_api.webhooks run" || true
  pkill -f "experience_api.messaging run" || true
  pkill -f "next dev -H 127.0.0.1 -p 8210" || true
  [ -f "$ROOT/.local/voice_worker.pid" ] && kill "$(cat "$ROOT/.local/voice_worker.pid")" 2>/dev/null; rm -f "$ROOT/.local/voice_worker.pid"
  # Wait until the old servers have released their ports, or the new ones cannot bind.
  for _ in $(seq 1 40); do
    pgrep -f "uvicorn (knowledge|assistant|experience)_api.main:app|next dev -H 127.0.0.1 -p 8210" > /dev/null || break
    sleep 0.25
  done
}
if [ "${1:-}" = "stop" ]; then stop; echo "stopped"; exit 0; fi
stop

# The project's own PostgreSQL (port 5440) does not start by itself after a reboot.
PGDATA_DIR="${ADMITAI_PGDATA:-$ROOT/.local/pgdata}"
if ! pg_isready -q -h 127.0.0.1 -p 5440 && [ -d "$PGDATA_DIR" ]; then
  pg_ctl -D "$PGDATA_DIR" -o "-p 5440 -k /tmp -c listen_addresses=127.0.0.1" \
    -l "$PGDATA_DIR/server.log" -w start > /dev/null
fi

# LiveKit (8170-8172) and the LiteLLM gateway (8190), when this machine has them (.local/infra.sh).
[ -x .local/infra.sh ] && .local/infra.sh start
(cd assistant && python -m assistant_api.migrate > "$LOGS/migrate.log" 2>&1) || echo "chat migrations failed; see $LOGS/migrate.log"
(cd knowledge && python -m knowledge_api.migrate > "$LOGS/migrate-kb.log" 2>&1) || echo "kb/learn migrations failed; see $LOGS/migrate-kb.log"
(cd knowledge && nohup uvicorn knowledge_api.main:app --host 127.0.0.1 --port 8100 > "$LOGS/knowledge.log" 2>&1 < /dev/null &)
(cd assistant && nohup uvicorn assistant_api.main:app --host 127.0.0.1 --port 8150 > "$LOGS/assistant.log" 2>&1 < /dev/null &)
(cd experience && nohup uvicorn experience_api.main:app --host 127.0.0.1 --port 8200 > "$LOGS/experience.log" 2>&1 < /dev/null &)
# Outbound webhook worker (PRD 12.2): sends due deliveries every 15 s.
(cd experience && nohup python -m experience_api.webhooks run --every 15 > "$LOGS/webhooks.log" 2>&1 < /dev/null &)
# E-mail worker and overdue callbacks (PRD F3.4, F3.6, F5.3, F7.5): every 60 s. Without SMTP_*
# settings it only escalates overdue callbacks and leaves e-mails queued.
(cd experience && nohup python -m experience_api.messaging run --every 60 > "$LOGS/messaging.log" 2>&1 < /dev/null &)
(cd experience/web && NEXT_PUBLIC_API_URL=http://127.0.0.1:8200 NEXT_PUBLIC_ASSISTANT_URL=http://127.0.0.1:8150 \
  nohup npm run dev > "$LOGS/web.log" 2>&1 < /dev/null &)

# LiveKit voice worker (PRD F2.1, 9.3), when LiveKit is configured.
if [ -n "${LIVEKIT_URL:-}" ]; then
  (cd assistant && nohup python -m assistant_api.voice_worker --pid-file "$ROOT/.local/voice_worker.pid" > "$LOGS/voice_worker.log" 2>&1 < /dev/null &)
fi
for port in 8100 8150 8200; do
  for _ in $(seq 1 40); do curl -sf "http://127.0.0.1:$port/health" > /dev/null && break; sleep 0.5; done
  curl -sf "http://127.0.0.1:$port/health" > /dev/null || { echo "service on $port did not start; see $LOGS"; exit 1; }
done
# Warm the answer model so the first visitor does not wait for it to load.
# In the background: the pages open at once, and the first answer is fast once it has loaded.
(curl -s -m 300 "${LLM_BASE_URL}/chat/completions" -H 'Content-Type: application/json' \
  -H "Authorization: Bearer ${LLM_API_KEY:-local}" \
  -d "{\"model\":\"${ANSWER_MODEL:-qwen2.5:3b}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1}" \
  > /dev/null 2>&1 &)
echo "Module A  http://127.0.0.1:8100/docs"
echo "Module B  http://127.0.0.1:8150/docs"
echo "Module C  http://127.0.0.1:8200/docs"
echo "Web       http://127.0.0.1:8210  (widget demo: /demo-site, console: /console)"
