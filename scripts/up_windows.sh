#!/usr/bin/env bash
# Windows (Git Bash) version of scripts/up.sh + scripts/run_local.sh: starts PostgreSQL (5440),
# Ollama, Module A (8100), Module B (8150), Module C (8200), its workers and the web app (8210),
# all on 127.0.0.1, then opens the widget and the staff console. Logs go to .local/logs/.
#
#     scripts/up_windows.sh          # start whatever is not running
#     scripts/up_windows.sh stop     # stop the services (PostgreSQL and Ollama keep running)
#     scripts/up_windows.sh restart
#
# Set up once with scripts/setup_windows.sh.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source local.env.sh
LOGS="$ROOT/.local/logs"
PIDS="$ROOT/.local/pids"
mkdir -p "$LOGS" "$PIDS"

up() { curl -sf -m 2 "$1" > /dev/null; }

stop() {
  # Each service was started from this script and wrote its Windows process id; taskkill /T
  # also ends the processes it started (uvicorn's workers, next dev's server).
  for f in "$PIDS"/*.pid; do
    [ -f "$f" ] || continue
    taskkill //F //T //PID "$(cat "$f")" > /dev/null 2>&1 || true
    rm -f "$f"
  done
  # Anything still holding one of the project's ports (a service started by hand).
  for port in 8100 8150 8200 8210; do
    for pid in $(netstat -ano | awk -v p=":$port" '$2 ~ p"$" && $4 == "LISTENING" {print $5}' | sort -u); do
      taskkill //F //T //PID "$pid" > /dev/null 2>&1 || true
    done
  done
}

start() {  # start <name> <folder> <command...>: in the background, logging to .local/logs/<name>.log
  local name=$1 dir=$2
  shift 2
  (cd "$dir" && nohup "$@" > "$LOGS/$name.log" 2>&1 < /dev/null &
    echo $! > "$PIDS/$name.bashpid")
  # Git Bash's $! is its own id; taskkill needs the Windows one.
  local bpid
  bpid=$(cat "$PIDS/$name.bashpid")
  rm -f "$PIDS/$name.bashpid"
  local wpid
  wpid=$(ps -p "$bpid" -o winpid= 2> /dev/null | tr -d ' ')
  [ -z "$wpid" ] && wpid=$(ps | awk -v p="$bpid" '$1 == p {print $4}')
  [ -n "$wpid" ] && echo "$wpid" > "$PIDS/$name.pid"
}

case "${1:-}" in
  stop) stop; echo "stopped"; exit 0 ;;
  restart) stop ;;
esac

if ! pg_isready -q -h 127.0.0.1 -p 5440; then
  echo "Starting PostgreSQL ..."
  pg_ctl -D "$ADMITAI_PGDATA" -o "-p 5440 -c listen_addresses=127.0.0.1" \
    -l "$ADMITAI_PGDATA/server.log" -w start > /dev/null || { echo "PostgreSQL did not start; see $ADMITAI_PGDATA/server.log"; exit 1; }
fi
if ! up http://127.0.0.1:11434/api/tags; then
  echo "Starting Ollama ..."
  (nohup ollama serve > "$LOGS/ollama.log" 2>&1 < /dev/null &)
  for _ in $(seq 1 30); do up http://127.0.0.1:11434/api/tags && break; sleep 1; done
fi

if up http://127.0.0.1:8100/health && up http://127.0.0.1:8150/health &&
  up http://127.0.0.1:8200/health && up http://127.0.0.1:8210/demo-site; then
  echo "Already running."
else
  echo "Starting the services (about a minute) ..."
  stop
  (cd assistant && python -m assistant_api.migrate > "$LOGS/migrate.log" 2>&1) ||
    echo "chat migrations failed; see $LOGS/migrate.log"
  (cd knowledge && python -m knowledge_api.migrate > "$LOGS/migrate-kb.log" 2>&1) ||
    echo "kb/learn migrations failed; see $LOGS/migrate-kb.log"
  start knowledge knowledge uvicorn knowledge_api.main:app --host 127.0.0.1 --port 8100
  start assistant assistant uvicorn assistant_api.main:app --host 127.0.0.1 --port 8150
  start experience experience uvicorn experience_api.main:app --host 127.0.0.1 --port 8200
  start webhooks experience python -m experience_api.webhooks run --every 15
  start messaging experience python -m experience_api.messaging run --every 60
  NEXT_PUBLIC_API_URL=http://127.0.0.1:8200 NEXT_PUBLIC_ASSISTANT_URL=http://127.0.0.1:8150 \
    start web experience/web npm run dev
  for port in 8100 8150 8200; do
    for _ in $(seq 1 60); do up "http://127.0.0.1:$port/health" && break; sleep 1; done
    up "http://127.0.0.1:$port/health" || { echo "service on $port did not start; see $LOGS"; exit 1; }
  done
  echo "APIs are up; waiting for the web app (the first start compiles it) ..."
  for _ in $(seq 1 180); do up http://127.0.0.1:8210/demo-site && break; sleep 1; done
  # Warm the answer model so the first visitor does not wait for it to load.
  (curl -s -m 300 "${LLM_BASE_URL}/chat/completions" -H 'Content-Type: application/json' \
    -H "Authorization: Bearer ${LLM_API_KEY:-local}" \
    -d "{\"model\":\"${ANSWER_MODEL:-qwen2.5:3b}\",\"messages\":[{\"role\":\"user\",\"content\":\"hi\"}],\"max_tokens\":1}" \
    > /dev/null 2>&1 &)
fi

start_url() { cmd //c start "" "$1" 2> /dev/null || true; }
[ "${NO_BROWSER:-}" = 1 ] || { start_url "http://127.0.0.1:8210/demo-site"; start_url "http://127.0.0.1:8210/console"; }
cat << MSG

Widget:   http://127.0.0.1:8210/demo-site
Console:  http://127.0.0.1:8210/console
          college demo-college · admin@example.com   or   college ruas · admin@ruas.example
          password $DEMO_PASSWORD · code 123456
APIs:     http://127.0.0.1:8100/docs (A) · 8150/docs (B) · 8200/docs (C)
Stop:     scripts/up_windows.sh stop
(The first answer may take a minute while the language model loads.)
MSG
