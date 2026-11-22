# Local-only prototype settings. Source this before running anything: `source local.env.sh`.
# Real environment variables override each module's .env, so the public repo's databases stay untouched.
export DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=admitai_app"
export ADMIN_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=postgres"
# Background jobs (outbox, webhooks, retention, onboarding) as a worker role, not the superuser.
export WORKER_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=admitai_app_worker"
export TEST_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local_test user=admitai_app"
export TEST_ADMIN_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local_test user=postgres"
export PATH="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/.venv/bin:$PATH"
# Windows (Git Bash): the virtual environment keeps its programs in Scripts/, and the project's
# portable PostgreSQL (scripts/setup_windows.sh) lives in .local/pgsql.
_win_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
[ -d "$_win_root/.venv/Scripts" ] && export PATH="$_win_root/.venv/Scripts:$PATH"
[ -d "$_win_root/.local/pgsql/bin" ] && export PATH="$_win_root/.local/pgsql/bin:$PATH"
# Tools downloaded into the project (cloudflared for the Retell voice tunnel).
[ -d "$_win_root/.local/bin" ] && export PATH="$_win_root/.local/bin:$PATH"
# Python on Windows writes its console output as cp1252 by default; the scripts print ✓, ₹ and Kannada.
export PYTHONIOENCODING=utf-8 PYTHONUTF8=1
# On a laptop CPU the local fallback model needs more than its 20 s for a long prompt, so giving
# up on Gemini after 6 s (the lab default) often ends in "I'm having trouble". 15 s was used until
# 19 Nov 2026; then a stalled Gemini meant 24.6 s of "…" (15 s wait + 9 s local answer). 8 s keeps
# most slow Gemini answers and caps a dead one at about 16 s; the widget now says it is checking.
export GEMINI_FIRST_TOKEN_S="${GEMINI_FIRST_TOKEN_S:-8}"
# PRD 11.4: Modules B and C sweep expired transcripts, audio, leads and audit rows every 24 hours
# (the code default 0 keeps test apps from starting the thread).
export RETENTION_SWEEP_HOURS="${RETENTION_SWEEP_HOURS:-24}"
# Module A (knowledge): schemas kb + learn, RLS role admitai_kb.
export KB_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=admitai_kb"
export KB_ADMIN_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=postgres"
export KB_WORKER_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=admitai_kb_worker"
export KB_TEST_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local_test user=admitai_kb"
export KB_TEST_ADMIN_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local_test user=postgres"
export OLLAMA_URL="http://127.0.0.1:11434"
# Module B (assistant): schema chat, RLS role admitai_chat.
export CHAT_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=admitai_chat"
export CHAT_ADMIN_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=postgres"
export CHAT_WORKER_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local user=admitai_chat_worker"
export CHAT_TEST_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local_test user=admitai_chat"
export CHAT_TEST_ADMIN_DATABASE_URL="host=127.0.0.1 port=5440 dbname=admitai_local_test user=postgres"
export KNOWLEDGE_API_URL="http://127.0.0.1:8100"
export EXPERIENCE_API_URL="http://127.0.0.1:8200"
# Module C calls the real Modules A and B (instead of its mocks on 8230).
export ASSISTANT_API_URL="http://127.0.0.1:8150"
export WEB_ORIGIN="http://127.0.0.1:8210,http://localhost:8210"
export DEV_ECHO_CODES=1
export LLM_BASE_URL="http://127.0.0.1:11434/v1"
# Secrets, generated once into a git-ignored file so they survive restarts and never reach git.
# Each caller has its own key per callee (KEY_<from>_TO_<to>); KEY_OPS is for operator scripts.
_root="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)"
_secrets="$_root/local.secrets.sh"
[ -f "$_secrets" ] || { : > "$_secrets"; chmod 600 "$_secrets"; }
grep -q '^export DEMO_PASSWORD=' "$_secrets" ||
  printf 'export DEMO_PASSWORD="Demo-%s-2026"\n' "$(openssl rand -hex 4)" >> "$_secrets"
for v in JWT_SECRET SERVICE_KEY CONTACT_SALT WHATSAPP_APP_SECRET WHATSAPP_VERIFY_TOKEN \
         KEY_C_TO_A KEY_B_TO_A KEY_C_TO_B KEY_A_TO_B KEY_B_TO_C KEY_A_TO_C KEY_OPS VOICE_TOKEN_SECRET          LEARNING_SESSION_SALT; do
  grep -q "^export $v=" "$_secrets" || printf 'export %s="%s"\n' "$v" "$(openssl rand -hex 24)" >> "$_secrets"
done
. "$_secrets"
# Module A's self-learning layer is on for local development (knowledge/docs/self-learning); its
# code default stays off, so production and CI are unchanged. Set it to false before sourcing
# this file to try the assistant without it.
export KNOWLEDGE_SELF_LEARNING_ENABLED="${KNOWLEDGE_SELF_LEARNING_ENABLED:-true}"
# Visitor tokens: Module C signs with its private key (RS256); Module B only holds the public key.
_keys="$_root/.local/keys"
if [ ! -f "$_keys/visitor-jwt.pem" ]; then
  mkdir -p "$_keys" && chmod 700 "$_keys"
  openssl genpkey -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$_keys/visitor-jwt.pem" 2>/dev/null
  openssl pkey -in "$_keys/visitor-jwt.pem" -pubout -out "$_keys/visitor-jwt.pub.pem"
  chmod 600 "$_keys/visitor-jwt.pem"
fi
export JWT_PRIVATE_KEY_FILE="$_keys/visitor-jwt.pem"
export JWT_PUBLIC_KEY_FILE="$_keys/visitor-jwt.pub.pem"
# Development mode: allows the shared-key fallbacks and the dev flags below. Production refuses them.
export APP_ENV=development
# The project's own PostgreSQL data folder (port 5440): inside this folder's git-ignored .local/
# when it has one (scripts/setup_new_mac.sh creates it), so a folder always runs its own data; the
# older shared location only for a folder that has none.
if [ -z "${ADMITAI_PGDATA:-}" ]; then
  if [ -f "$_root/.local/pgdata/PG_VERSION" ]; then export ADMITAI_PGDATA="$_root/.local/pgdata"
  elif [ -d "$HOME/Projects/.admitai-pgdata" ]; then export ADMITAI_PGDATA="$HOME/Projects/.admitai-pgdata"
  else export ADMITAI_PGDATA="$_root/.local/pgdata"; fi
fi
# Every local test script comes from 127.0.0.1, so the per-IP limits (PRD 12.3) are raised here;
# the defaults (60 questions, 30 new chats per IP per minute) apply everywhere else.
export RATE_LIMIT_PER_IP_MINUTE=100000 SESSIONS_PER_IP_MINUTE=100000
# On a laptop (8 GB, Ollama swapping embedding models) a knowledge search took 2-10 s; the 5 s
# default is for the lab GPU. Past it the answer is built without the search results.
export KNOWLEDGE_TIMEOUT_S=12
# Module A calls Module B headlessly (eval runs) and reads its turns (learning signals).
export KB_ASSISTANT_API_URL="http://127.0.0.1:8150"
# ...and tells Module C about knowledge changes, for the college's webhooks (PRD 12.2).
export KB_EXPERIENCE_API_URL="http://127.0.0.1:8200"
# Cross-encoder reranker for Module A's search (PRD 9.4), if downloaded (see knowledge/README.md).
[ -f "$_root/.local/models/reranker/bge-reranker-base/onnx/model_int8.onnx" ] &&
  export KB_RERANKER="$_root/.local/models/reranker/bge-reranker-base"
# Voice models (faster-whisper "base" and a Piper voice), downloaded into the git-ignored .local/.
export VOICE_MODEL_DIR="$(cd "$(dirname "${BASH_SOURCE[0]:-$0}")" && pwd)/.local/models"
# Local development only: fixed two-factor sign-in code (accepted only because DEV_ECHO_CODES=1).
export DEV_MFA_CODE="123456"
