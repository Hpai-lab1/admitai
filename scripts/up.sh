#!/usr/bin/env bash
# One command to use the prototype: starts whatever is not running (nothing, if all is up) and
# opens the widget and the staff console in the browser.
#
#     ~/Projects/admitai-local/scripts/up.sh
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source local.env.sh

up() { curl -sf -m 2 "$1" > /dev/null; }
all_up() {
  up http://127.0.0.1:8100/health && up http://127.0.0.1:8150/health &&
    up http://127.0.0.1:8200/health && up http://127.0.0.1:8210/demo-site &&
    pgrep -f "experience_api.webhooks run" > /dev/null && pgrep -f "experience_api.messaging run" > /dev/null
}

if ! curl -sf -m 2 http://127.0.0.1:11434/api/tags > /dev/null; then
  echo "Starting Ollama ..."
  (open -a Ollama 2>/dev/null || (nohup ollama serve > .local/logs/ollama.log 2>&1 &))
fi

if all_up; then
  echo "Already running."
else
  echo "Starting the services (about a minute) ..."
  scripts/run_local.sh > .local/logs/up.log 2>&1 || { echo "Start failed; see .local/logs/up.log"; exit 1; }
  for _ in $(seq 1 90); do up http://127.0.0.1:8210/demo-site && break; sleep 1; done
fi

open "http://127.0.0.1:8210/demo-site"
open "http://127.0.0.1:8210/console"
cat <<MSG

Widget:   http://127.0.0.1:8210/demo-site
Console:  http://127.0.0.1:8210/console
          college demo-college · admin@example.com · password $DEMO_PASSWORD · code 123456
Stop:     $ROOT/scripts/run_local.sh stop
(The first answer may take a minute while the language model loads.)
MSG
