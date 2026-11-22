#!/usr/bin/env bash
# Voice through Retell AI, in one command: opens a cloudflared quick tunnel to Module B (8150),
# waits for its public address, points the Retell agent at it (assistant/scripts/retell_agent.py)
# and, when Vapi is set up (VAPI_API_KEY, VAPI_PHONE_NUMBER_ID, VAPI_SERVER_SECRET, VAPI_TENANT_ID),
# the Vapi phone number too; then keeps the tunnel open until Ctrl+C. A quick tunnel gets a new
# address every time, so run
# this again after any restart (Mac asleep, network change, window closed).
#
#     scripts/voice_tunnel.sh
#
# Needs: brew install cloudflared; RETELL_API_KEY (and RETELL_AGENT_ID once created) in
# local.secrets.sh; Module B running (scripts/run_local.sh).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source local.env.sh
command -v cloudflared > /dev/null || { echo "Install cloudflared first: brew install cloudflared"; exit 1; }
curl -sf -o /dev/null http://127.0.0.1:8150/health || { echo "Module B is not running: scripts/run_local.sh"; exit 1; }
[ -n "${RETELL_API_KEY:-}${VAPI_API_KEY:-}" ] ||
  { echo "Add RETELL_API_KEY or VAPI_API_KEY to local.secrets.sh first"; exit 1; }

mkdir -p .local/logs
LOG=.local/logs/voice_tunnel.log
: > "$LOG"
cloudflared tunnel --url http://127.0.0.1:8150 >> "$LOG" 2>&1 &
TUNNEL=$!
trap 'kill $TUNNEL 2>/dev/null; echo; echo "Tunnel closed: voice is off until you run this again."' EXIT INT TERM

echo "Opening the tunnel..."
URL=""
for _ in $(seq 1 60); do
  URL=$(grep -ao "https://[a-z0-9-]*\.trycloudflare\.com" "$LOG" | head -1)
  [ -n "$URL" ] && grep -aq "Registered tunnel connection" "$LOG" && break
  sleep 1
done
[ -n "$URL" ] || { echo "No tunnel address after 60 s; see $LOG"; exit 1; }
# A new address takes a few seconds to exist in DNS, and asking too early makes macOS remember
# "not found" for a while (seen: the agent update then failed). So wait first, then keep trying.
echo "Tunnel $URL opened; waiting for it to be reachable..."
sleep 8
reachable=""
for _ in $(seq 1 30); do
  if curl -sf -o /dev/null -m 8 "$URL/health"; then reachable=1; break; fi
  sleep 3
done
if [ -z "$reachable" ]; then
  echo "The tunnel is not reachable after about 90 s (DNS or network)."
  echo "Press Ctrl+C and run scripts/voice_tunnel.sh again; a new address usually works."
  exit 1
fi

# Retell and Vapi are set up independently: one failing (a key without write access, say) must
# not leave the other one off.
retell_ready=""
if python assistant/scripts/retell_agent.py --public-url "$URL"; then
  retell_ready=1
else
  echo "Retell: the agent could not be updated (website voice calls stay off until it can)."
fi
# Vapi phone calls, when set up: the number's server URL must follow the tunnel's new address,
# so incoming calls reach this service (assistant/scripts/vapi_setup.py connect).
vapi_ready=""
if [ -n "${VAPI_API_KEY:-}" ] && [ -n "${VAPI_PHONE_NUMBER_ID:-}" ] &&
   [ -n "${VAPI_SERVER_SECRET:-}" ] && [ -n "${VAPI_TENANT_ID:-}" ]; then
  if python assistant/scripts/vapi_setup.py connect --phone-number-id "$VAPI_PHONE_NUMBER_ID" \
       --public-url "$URL" --tenant-id "$VAPI_TENANT_ID"; then
    vapi_ready=1
  else
    echo "Vapi: could not connect the phone number (website voice still works)."
  fi
fi
echo
[ -n "$retell_ready" ] && echo "Voice is ready: open the demo site, tap the microphone once and just talk."
[ -n "$vapi_ready" ] && echo "Phone calls are ready: call the college's Vapi number."
[ -z "$retell_ready$vapi_ready" ] && { echo "Neither Retell nor Vapi could be connected (see above)."; exit 1; }
echo "Keep this window open during the demo. Ctrl+C closes the tunnel."
wait $TUNNEL
