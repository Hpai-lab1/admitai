#!/usr/bin/env bash
# Restart Module B (8150) after a code change without breaking voice. The other services and the
# cloudflared tunnel are left running (the tunnel only forwards to port 8150, so its address and
# the Retell / Vapi settings stay valid). If the tunnel is down or unreachable, it is reopened with
# scripts/voice_tunnel.sh in the background, which also points Retell and Vapi at the new address.
#
#     scripts/restart_assistant.sh
#
# A call that was open during the restart is cut: hang up and start a new call.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOGS="$ROOT/.local/logs"
mkdir -p "$LOGS"
cd "$ROOT"
# shellcheck source=/dev/null
source local.env.sh
# shellcheck source=/dev/null
[ -f .local/lab.env.sh ] && source .local/lab.env.sh

pkill -f "uvicorn assistant_api.main:app" || true
for _ in $(seq 1 40); do
  pgrep -f "uvicorn assistant_api.main:app" > /dev/null || break
  sleep 0.25
done
(cd assistant && python -m assistant_api.migrate > "$LOGS/migrate.log" 2>&1) || echo "chat migrations failed; see $LOGS/migrate.log"
(cd assistant && nohup uvicorn assistant_api.main:app --host 127.0.0.1 --port 8150 > "$LOGS/assistant.log" 2>&1 < /dev/null &) > /dev/null 2>&1 < /dev/null
for _ in $(seq 1 60); do curl -sf -o /dev/null http://127.0.0.1:8150/health && break; sleep 0.5; done
curl -sf -o /dev/null http://127.0.0.1:8150/health || { echo "Module B did not start; see $LOGS/assistant.log"; exit 1; }
echo "Module B restarted."

URL=$(grep -ao "https://[a-z0-9-]*\.trycloudflare\.com" "$LOGS/voice_tunnel.log" 2>/dev/null | head -1)
if [ -n "$URL" ] && pgrep -f "cloudflared tunnel --url http://127.0.0.1:8150" > /dev/null &&
   curl -sf -o /dev/null -m 10 "$URL/health"; then
  echo "Voice tunnel still open ($URL): start a new call to test."
  exit 0
fi
echo "Voice tunnel is down; reopening it (about 30 s)..."
pkill -f "cloudflared tunnel --url http://127.0.0.1:8150" || true
nohup scripts/voice_tunnel.sh > "$LOGS/voice_tunnel_run.log" 2>&1 < /dev/null &
for _ in $(seq 1 60); do
  grep -q "Voice is ready\|Phone calls are ready" "$LOGS/voice_tunnel_run.log" && break
  grep -q "not reachable\|No tunnel address\|could be connected" "$LOGS/voice_tunnel_run.log" && break
  sleep 2
done
cat "$LOGS/voice_tunnel_run.log"
