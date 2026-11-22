-- PRD 8.4 gate, made cheaper: a run records the fingerprint of what it measured (knowledge base,
-- catalogue, test set, Module B's settings and versions, the date). A later gate whose college has
-- the same fingerprint reuses that run as its baseline instead of answering every case again.
alter table learn.eval_runs add column if not exists fingerprint text;
create index if not exists eval_runs_fingerprint on learn.eval_runs (tenant_id, fingerprint)
    where fingerprint is not null;
