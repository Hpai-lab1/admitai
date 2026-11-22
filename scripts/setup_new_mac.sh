#!/usr/bin/env bash
# Set up the AdmitAI local prototype on a new Mac (Apple Silicon or Intel), from a fresh clone:
#
#     git clone https://github.com/ramitharavichandra/admitai-prototype.git admitai-local
#       (or: git clone admitai-prototype.bundle admitai-local)
#     cd admitai-local
#     scripts/setup_new_mac.sh       # ~20-30 min, mostly model downloads; safe to run again
#     scripts/up.sh                  # opens the widget and the staff console
#
# Needs Homebrew (https://brew.sh) and about 8 GB free. Installs, if missing: PostgreSQL 14,
# Python 3.12, Node (20 or newer) and Ollama. Everything else stays inside this folder (git-ignored .local/
# and .venv), except Ollama's models, which go to ~/.ollama as usual.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
step() { printf '\n==> %s\n' "$1"; }

step "Checking the Mac"
command -v brew > /dev/null || { echo "Install Homebrew first: https://brew.sh"; exit 1; }
free_gb=$(df -g "$ROOT" | tail -1 | awk '{print $4}')
[ "$free_gb" -ge 8 ] || echo "Warning: only ${free_gb} GB free; about 8 GB is needed."

step "Tools (Homebrew)"
brew list postgresql@14 > /dev/null 2>&1 || brew install postgresql@14
PYTHON_FORMULA="python@""3.12"
brew list "$PYTHON_FORMULA" > /dev/null 2>&1 || brew install "$PYTHON_FORMULA"
command -v node > /dev/null && [ "$(node -p 'process.versions.node.split(".")[0]')" -ge 20 ] ||
  brew install node
command -v ollama > /dev/null || brew install ollama
PG="$(brew --prefix postgresql@14)/bin"
PY="$(brew --prefix "$PYTHON_FORMULA")/bin/python3.12"
export PATH="$PG:$PATH"

# Pushes only to the team's private copy, and only while it is private: this repository mixes
# all three modules and holds RUAS content that must not go public. A clone from a bundle file
# has no remote to keep.
cp scripts/git-hooks/pre-push .git/hooks/pre-push && chmod +x .git/hooks/pre-push
case "$(git remote get-url origin 2> /dev/null)" in
  *github.com*ramitharavichandra/admitai-prototype*) ;;
  *) git remote remove origin 2> /dev/null || true ;;
esac

# Generates this Mac's own secrets and keys (local.secrets.sh, .local/keys) on first use.
# shellcheck source=/dev/null
source local.env.sh
mkdir -p .local/logs

step "PostgreSQL on 127.0.0.1:5440 (data in $ADMITAI_PGDATA)"
if [ ! -f "$ADMITAI_PGDATA/PG_VERSION" ]; then
  "$PG/initdb" -D "$ADMITAI_PGDATA" -U postgres --auth=trust --encoding=UTF8 > /dev/null
fi
"$PG/pg_isready" -q -h 127.0.0.1 -p 5440 ||
  "$PG/pg_ctl" -D "$ADMITAI_PGDATA" -o "-p 5440 -k /tmp -c listen_addresses=127.0.0.1" \
    -l "$ADMITAI_PGDATA/server.log" -w start > /dev/null
psql_admin() { "$PG/psql" -h 127.0.0.1 -p 5440 -U postgres -d postgres -qAt "$@"; }
for db in admitai_local admitai_local_test; do
  [ "$(psql_admin -c "select 1 from pg_database where datname = '$db'")" = 1 ] ||
    psql_admin -c "create database $db"
done
# Module C's restricted login role (A's and B's are created by their migrations).
psql_admin -c "do \$\$ begin if not exists (select 1 from pg_roles where rolname = 'admitai_app')
  then create role admitai_app login nobypassrls nosuperuser nocreatedb nocreaterole; end if; end \$\$"

step "Python environment (.venv)"
[ -x .venv/bin/python ] || "$PY" -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r knowledge/requirements.txt -r assistant/requirements.txt \
  -r experience/requirements.txt -r experience/requirements-dev.txt

step "Web app (npm)"
(cd experience/web && npm ci --no-audit --no-fund --loglevel=error)

step "Ollama models (about 3.5 GB)"
curl -sf -m 2 http://127.0.0.1:11434/api/tags > /dev/null ||
  { (open -a Ollama 2> /dev/null || (nohup ollama serve > .local/logs/ollama.log 2>&1 &)); sleep 5; }
for m in qwen2.5:3b nomic-embed-text bge-m3; do ollama pull "$m"; done

step "Voice and reranker models (about 0.5 GB, into .local/models)"
.venv/bin/python - << 'PY'
from huggingface_hub import hf_hub_download

def get(repo, files, folder):
    for f in files:
        hf_hub_download(repo, f, local_dir=folder)

get("rhasspy/piper-voices",
    ["en/en_US/lessac/medium/en_US-lessac-medium.onnx",
     "en/en_US/lessac/medium/en_US-lessac-medium.onnx.json"], ".local/models/piper-download")
get("Xenova/bge-reranker-base",
    ["onnx/model_int8.onnx", "tokenizer.json", "tokenizer_config.json", "config.json"],
    ".local/models/reranker/bge-reranker-base")

from faster_whisper import WhisperModel  # downloads Whisper "base" into the model folder
WhisperModel("base", device="cpu", compute_type="int8", download_root=".local/models/whisper")
PY
mkdir -p .local/models/piper
cp .local/models/piper-download/en/en_US/lessac/medium/en_US-lessac-medium.onnx* .local/models/piper/
rm -rf .local/models/piper-download

step "Colleges, staff and knowledge (database bootstrap)"
# shellcheck source=/dev/null
source local.env.sh  # again, so KB_RERANKER sees the model just downloaded
.venv/bin/python scripts/bootstrap_local.py --fresh

step "Done"
cat << MSG
Start and open it:   scripts/up.sh
Stop it:             scripts/run_local.sh stop
Everything checked:  scripts/check_all.sh    (about 25 minutes)
Third college:       python scripts/onboard_college.py packs/riverside   (after scripts/up.sh)
Your staff password is DEMO_PASSWORD in local.secrets.sh; the sign-in code is 123456.
MSG
