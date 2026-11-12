-- Self-improvement loop (PRD 8, 11.2): signals -> clusters -> proposals -> approval -> eval gate ->
-- deploy (versioned, reversible). Eval sets, cases and runs are the gate's evidence.

create table learn.signals (
    id              bigserial primary key,
    tenant_id       uuid not null,
    kind            text not null check (kind in ('thumb_down', 'low_rating', 'correction', 'no_answer',
                                                  'blocked', 'partial', 'thumb_up')),
    source_ref      text not null,            -- event id or B message id; one signal per source
    conversation_id text,
    message_id      text,
    trace_id        text,
    question        text,
    answer          text,
    corrected       text,                     -- staff correction text
    reason          text,
    comment         text,
    department      text,
    sources         jsonb not null default '[]',
    outcome         text,                     -- B's outcome for the answer
    root_cause      text,
    verdict         text check (verdict in ('unreviewed', 'wrong', 'correct_but_unwelcome', 'other')),
    proposal_id     uuid,
    occurred_at     timestamptz not null default now(),
    created_at      timestamptz not null default now(),
    unique (tenant_id, kind, source_ref)
);

create table learn.proposals (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid not null,
    kind          text not null check (kind in ('curated_answer', 'document_action', 'sensitive_topic',
                                                'prompt', 'synonym', 'curated_conflict')),
    root_cause    text not null check (root_cause in ('correct_but_unwelcome', 'knowledge_gap', 'outdated',
                                                      'retrieval_miss', 'wrong_routing', 'hallucination',
                                                      'tone', 'policy', 'catalogue_conflict')),
    rung          int not null check (rung between 1 and 5),
    title         text not null,
    proposal      text not null,
    diff          jsonb not null default '{}',
    examples      jsonb not null default '[]',
    department    text,
    severity      int not null,
    affected      int not null default 1,
    impact_raw    real not null default 0,
    impact_score  real not null default 0,
    centroid      real[],
    status        text not null default 'pending' check (status in ('pending', 'evaluating', 'deployed',
                                                                    'rejected', 'failed_gate', 'rolled_back',
                                                                    'approved_manual')),
    due_at        timestamptz not null,
    escalated     boolean not null default false,
    decided_by    text,
    decided_at    timestamptz,
    note          text,
    eval_run_id   uuid,
    deployed_ref  jsonb,
    created_at    timestamptz not null default now(),
    updated_at    timestamptz not null default now()
);
create index proposals_status on learn.proposals (tenant_id, status);

create table learn.eval_cases (
    id              uuid primary key default gen_random_uuid(),
    tenant_id       uuid not null,
    ext_id          text not null,
    question        text not null,
    language        text not null default 'en',
    kind            text not null,
    expected_refs   text[] not null default '{}',
    forbidden_refs  text[] not null default '{}',
    expected_facts  text[] not null default '{}',
    expected_role   text,
    critical        boolean not null default false,
    source          text not null default 'pack' check (source in ('pack', 'proposal', 'staff')),
    proposal_id     uuid,
    active          boolean not null default true,
    created_at      timestamptz not null default now(),
    unique (tenant_id, ext_id)
);

create table learn.eval_runs (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid not null,
    trigger       text not null,              -- manual | proposal:<id>
    label         text,                       -- baseline | candidate | adhoc
    target_tenant uuid not null,              -- the tenant (or sandbox copy) that answered
    status        text not null default 'running' check (status in ('running', 'done', 'error')),
    scores        jsonb,
    results       jsonb,
    passed        boolean,
    gate          jsonb,
    error         text,
    started_at    timestamptz not null default now(),
    finished_at   timestamptz
);

do $$
declare
    t text;
begin
    foreach t in array array['learn.signals', 'learn.proposals', 'learn.eval_cases', 'learn.eval_runs'] loop
        execute format('alter table %s enable row level security', t);
        execute format('alter table %s force row level security', t);
        execute format(
            'create policy tenant_isolation on %s using (tenant_id = kb.current_tenant())'
            ' with check (tenant_id = kb.current_tenant())', t);
        execute format('grant select, insert, update, delete on %s to admitai_kb', t);
    end loop;
end $$;
grant usage on all sequences in schema learn to admitai_kb;

-- Which approved items a sandbox copy came from, so a copy never outlives its evaluation.
create table kb.sandboxes (
    sandbox_id  uuid primary key,
    tenant_id   uuid not null,
    created_at  timestamptz not null default now()
);
