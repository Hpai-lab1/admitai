#!/usr/bin/env bash
# Nightly jobs for every tenant (PRD 8.2: Feedback Analyst nightly in Level 1; F1.4b grounding
# review off-peak). The grounding review runs first so its findings reach the analyst.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
# shellcheck source=/dev/null
source local.env.sh
# Privacy first (PRD 11.4): deliver queued erasures, then apply retention in every module.
(cd experience && python -m experience_api.privacy outbox && python -m experience_api.privacy retention)
(cd assistant && python -m assistant_api.privacy retention)
# Knowledge that builds itself (knowledge_api/site_kb.py): every college whose Tenant Pack names a
# website is re-read; changed pages become new versions, and the catalogue and FAQs follow them.
for pack in packs/*/pack.yaml; do
  slug=$(python -c "import sys, yaml; p = yaml.safe_load(open(sys.argv[1])) or {}; print(p['college']['slug'] if p.get('website') else '')" "$pack")
  [ -n "$slug" ] || continue
  tid=$(psql "$ADMIN_DATABASE_URL" -Atc "select id from app.tenants where slug = '$slug' and status = 'live'")
  [ -n "$tid" ] || continue
  echo "== website knowledge for $slug"
  (cd knowledge && python -m knowledge_api.site_kb --tenant-id "$tid" --pack "../$pack") ||
    echo "website knowledge for $slug failed; the knowledge base keeps its last version"
done
for tid in $(psql "$ADMIN_DATABASE_URL" -Atc "select id from app.tenants where status = 'live' order by slug"); do
  echo "== tenant $tid"
  (cd assistant && python -m assistant_api.review run --tenant-id "$tid" --limit 200)
  (cd knowledge && python -m knowledge_api.learning run --tenant-id "$tid")
done
