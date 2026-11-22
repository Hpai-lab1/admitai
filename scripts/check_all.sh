#!/usr/bin/env bash
# Everything, in order: unit suites, lint and typecheck for all three modules and the web app; then
# (with the services running) the walking skeleton, channels, live answer quality and, unless
# --quick, the full improvement cycle. Exit status is non-zero if anything failed.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source local.env.sh
status=0
step() { printf '\n=== %s\n' "$1"; }
run() { "$@" || { echo "FAILED: $*"; status=1; }; }
# Run a step showing only its last lines, without losing its exit status (a pipe would).
quiet() {
  local n=$1; shift
  local out; out=$(mktemp)
  if "$@" > "$out" 2>&1; then tail -n "$n" "$out"; else tail -n 20 "$out"; echo "FAILED: $*"; status=1; fi
  rm -f "$out"
}

for m in knowledge assistant experience; do
  step "unit tests + lint: $m"
  pushd "$m" > /dev/null
  run ruff check . -q
  run ruff format --check . -q
  quiet 1 python -m pytest -q
  popd > /dev/null
done
step "web: typecheck, lint, tests"
pushd experience/web > /dev/null
run npx tsc --noEmit
run npx eslint src
quiet 3 npx vitest run
popd > /dev/null

step "contracts: every agreed endpoint implemented"; quiet 1 python scripts/check_contracts.py

if ! curl -sf http://127.0.0.1:8150/health > /dev/null; then
  echo "Services are not running (scripts/run_local.sh); skipping live checks."; exit $status
fi
step "walking skeleton (C -> B -> A)"; quiet 1 python scripts/e2e_local.py
step "channels: WhatsApp, voice, grounding review"; quiet 1 python scripts/e2e_channels.py
step "webhooks and error format (PRD 12.2, 12.3)"; quiet 1 python scripts/e2e_webhooks.py
if ! psql "$ADMIN_DATABASE_URL" -Atc "select 1 from app.tenants where slug = 'riverside'" | grep -q 1; then
  step "onboard a new college from its Tenant Pack (G4)"; quiet 2 python scripts/onboard_college.py packs/riverside
fi
step "PRD 20 suites: routing, multi-intent, minors, red team, channels, load"; quiet 1 python scripts/suite_prd20.py
step "live answer quality (both colleges)"; run python scripts/eval_live.py
if [ "${1:-}" != "--quick" ]; then
  step "full improvement cycle"; quiet 1 python scripts/e2e_learning.py --rollback
fi
echo
[ $status -eq 0 ] && echo "ALL CHECKS PASSED" || echo "SOME CHECKS FAILED"
exit $status
