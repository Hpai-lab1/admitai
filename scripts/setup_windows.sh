#!/usr/bin/env bash
# Windows (Git Bash) version of scripts/setup_new_mac.sh. Run once from a fresh clone:
#
#     scripts/setup_windows.sh      # ~20-30 min, mostly downloads; safe to run again
#     scripts/up_windows.sh         # starts everything and opens the widget and the console
#
# Needs Python 3.12 (`py -3.12`), Node 20+, Ollama and Git for Windows; about 8 GB free.
# PostgreSQL is a portable copy inside the git-ignored .local/pgsql (no installer, no Windows
# service); its data is in .local/pgdata, served on 127.0.0.1:5440 only.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
step() { printf '\n==> %s\n' "$1"; }
PG_ZIP="https://get.enterprisedb.com/postgresql/postgresql-17.6-1-windows-x64-binaries.zip"

step "Checking the tools"
py -3.12 --version > /dev/null 2>&1 || { echo "Install Python 3.12 (py -3.12 must work)"; exit 1; }
command -v node > /dev/null && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] ||
  { echo "Install Node 20 or newer"; exit 1; }
command -v ollama > /dev/null || { echo "Install Ollama from https://ollama.com"; exit 1; }
mkdir -p .local/logs

step "PostgreSQL (portable, .local/pgsql)"
if [ ! -x .local/pgsql/bin/pg_ctl.exe ]; then
  curl -sfL -o .local/pg.zip "$PG_ZIP"
  unzip -q .local/pg.zip -d .local && rm .local/pg.zip
fi
# shellcheck source=/dev/null
source local.env.sh  # PATH (.venv/Scripts, .local/pgsql/bin), secrets and keys on first use
if [ ! -f "$ADMITAI_PGDATA/PG_VERSION" ]; then
  initdb -D "$ADMITAI_PGDATA" -U postgres --auth=trust --encoding=UTF8 --locale=C > /dev/null
fi
pg_isready -q -h 127.0.0.1 -p 5440 ||
  pg_ctl -D "$ADMITAI_PGDATA" -o "-p 5440 -c listen_addresses=127.0.0.1" \
    -l "$ADMITAI_PGDATA/server.log" -w start > /dev/null
psql_admin() { psql -h 127.0.0.1 -p 5440 -U postgres -d postgres -qAt "$@"; }
for db in admitai_local admitai_local_test; do
  [ "$(psql_admin -c "select 1 from pg_database where datname = '$db'")" = 1 ] ||
    psql_admin -c "create database $db"
done
psql_admin -c "do \$\$ begin if not exists (select 1 from pg_roles where rolname = 'admitai_app')
  then create role admitai_app login nobypassrls nosuperuser nocreatedb nocreaterole; end if; end \$\$"

step "Python environment (.venv)"
[ -x .venv/Scripts/python.exe ] || py -3.12 -m venv .venv
.venv/Scripts/python -m pip install -q --upgrade pip
.venv/Scripts/python -m pip install -q --default-timeout=300 --retries 8 \
  -r knowledge/requirements.txt -r assistant/requirements.txt \
  -r experience/requirements.txt -r experience/requirements-dev.txt

step "Web app (npm)"
(cd experience/web && npm ci --no-audit --no-fund --loglevel=error)

step "Ollama models (about 3.5 GB)"
curl -sf -m 2 http://127.0.0.1:11434/api/tags > /dev/null ||
  { (nohup ollama serve > .local/logs/ollama.log 2>&1 &); sleep 5; }
for m in qwen2.5:3b nomic-embed-text bge-m3; do ollama pull "$m"; done

step "Reranker and voice models (about 0.5 GB, into .local/models)"
.venv/Scripts/python - << 'PY'
from huggingface_hub import hf_hub_download

def get(repo, files, folder):
    for f in files:
        hf_hub_download(repo, f, local_dir=folder)

get("Xenova/bge-reranker-base",
    ["onnx/model_int8.onnx", "tokenizer.json", "tokenizer_config.json", "config.json"],
    ".local/models/reranker/bge-reranker-base")
get("rhasspy/piper-voices",
    ["en/en_US/lessac/medium/en_US-lessac-medium.onnx",
     "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"], ".local/models/piper-download")

from faster_whisper import WhisperModel  # downloads Whisper "base" into the model folder
WhisperModel("base", device="cpu", compute_type="int8", download_root=".local/models/whisper")
PY
mkdir -p .local/models/piper
cp .local/models/piper-download/en/en_US/lessac/medium/en_US-lessac-medium.onnx* .local/models/piper/
rm -rf .local/models/piper-download

step "Colleges, staff and knowledge (database bootstrap)"
# shellcheck source=/dev/null
source local.env.sh  # again, so KB_RERANKER sees the model just downloaded
.venv/Scripts/python scripts/bootstrap_local.py --fresh

step "Done"
cat << MSG
Start and open it:   scripts/up_windows.sh
Stop it:             scripts/up_windows.sh stop
RUAS website KB:     see LOCAL.md ("On Windows")
Your staff password is DEMO_PASSWORD in local.secrets.sh; the sign-in code is 123456.
MSG
