-- Self-learning knowledge layer (knowledge/docs/self-learning/README.md).
--
-- Feedback is a signal, never the truth: every lesson needs evidence from the college's own
-- pages, documents or catalogue, and numbers are never written from feedback. All tables are per
-- college with the same RLS as the rest of `learn` (kb.current_tenant(), migration 002).
-- With KNOWLEDGE_SELF_LEARNING_ENABLED=false nothing writes to these tables; the trigger below
-- only sends a notification nobody listens to.

-- One row per feedback.created event the worker has taken on. event_ref is learn.events.id.
create table learn.feedback_events (
    id               bigserial primary key,
    tenant_id        uuid not null,
    event_ref        bigint not null references learn.events(id) on delete cascade,
    conversation_id  text,
    message_id       text,
    trace_id         text,
    rating           text check (rating in ('up', 'down')),
    rating_kind      text,                       -- thumb | rating
    rating_value     int,
    reason           text,
    comment          text,                       -- masked (pii.mask_pii)
    question         text,                       -- masked
    answer           text,                       -- masked
    outcome          text,
    citations        jsonb not null default '[]',
    retrieved_refs   jsonb not null default '[]', -- Module A's own search for the question (B keeps only citations)
    channel          text,
    actor            text,
    session_fp       text,                       -- salted hash; never a raw session id or address
    status           text not null default 'new'
                     check (status in ('new', 'processing', 'done', 'failed')),
    attempts         int not null default 0,
    next_attempt_at  timestamptz not null default now(),
    last_error       text,
    outcome_note     text,                       -- what the worker did with it, in words
    lesson_id        uuid,
    created_at       timestamptz not null default now(),
    processed_at     timestamptz,
    unique (tenant_id, event_ref)
);
create index feedback_events_todo on learn.feedback_events (status, next_attempt_at);
create index feedback_events_fp on learn.feedback_events (tenant_id, session_fp, created_at);

create table learn.feedback_analysis (
    feedback_id  bigint primary key references learn.feedback_events(id) on delete cascade,
    tenant_id    uuid not null,
    class        text not null check (class in ('retrieval_miss', 'missing_answer', 'wrong_fact_claim',
                                                 'answer_style', 'praise', 'junk_abuse')),
    claim        text,
    topic        text,
    programme    text,
    confidence   real not null default 0,
    model        text not null,                  -- model name, or 'rules'
    reasoning    text,
    created_at   timestamptz not null default now()
);

-- subject_kind 'feedback' (subject_id = feedback id) or 'session' (subject_id = session_fp).
create table learn.trust_scores (
    tenant_id     uuid not null,
    subject_kind  text not null check (subject_kind in ('feedback', 'session')),
    subject_id    text not null,
    score         real not null,
    factors       jsonb not null default '{}',
    updated_at    timestamptz not null default now(),
    primary key (tenant_id, subject_kind, subject_id)
);

create table learn.lessons (
    id                 uuid primary key default gen_random_uuid(),
    tenant_id          uuid not null,
    issue_class        text not null,
    type               text check (type in ('alias', 'retrieval_boost', 'curated_from_source',
                                            'catalogue_reextract', 'answer_hint')),
    status             text not null default 'proposed'
                       check (status in ('proposed', 'verifying', 'gated', 'live', 'rejected',
                                         'rolled_back', 'pending_approval')),
    question           text,
    topic              text,
    centroid           real[],
    payload            jsonb not null default '{}',
    created_from       bigint[] not null default '{}',   -- learn.feedback_events ids
    sessions           text[] not null default '{}',     -- distinct trusted session_fp
    proposal_id        uuid,                             -- learn.proposals: inbox, gate, rollback
    knowledge_version  int,
    reason             text,
    gate_started_at    timestamptz,
    attempt            int not null default 0,           -- 1 = fallback lesson after a failed gate
    created_at         timestamptz not null default now(),
    updated_at         timestamptz not null default now(),
    live_at            timestamptz,
    rolled_back_at     timestamptz
);
create index lessons_status on learn.lessons (tenant_id, status);

create table learn.lesson_evidence (
    id            bigserial primary key,
    tenant_id     uuid not null,
    lesson_id     uuid not null references learn.lessons(id) on delete cascade,
    source_url    text,
    document_id   uuid,
    chunk_id      uuid,
    quote         text not null,                 -- exact span copied from the source
    supports      boolean,                       -- true supports, false contradicts, null unclear
    note          text,
    fetched_at    timestamptz not null default now(),
    content_hash  text
);

create table learn.lesson_metrics (
    id           bigserial primary key,
    tenant_id    uuid not null,
    lesson_id    uuid not null references learn.lessons(id) on delete cascade,
    phase        text not null check (phase in ('gate', 'live', 'nightly')),
    metrics      jsonb not null,
    measured_at  timestamptz not null default now()
);

-- What search reads when the flag is on. Rows belong to one lesson; rollback sets active = false.
create table learn.lesson_aliases (
    id         bigserial primary key,
    tenant_id  uuid not null,
    lesson_id  uuid not null,
    phrase     text not null,                    -- how visitors say it (lower case, whole words)
    expansion  text not null,                    -- the words the college's page uses
    active     boolean not null default true,
    created_at timestamptz not null default now()
);

create table learn.retrieval_boosts (
    id              bigserial primary key,
    tenant_id       uuid not null,
    lesson_id       uuid not null,
    centroid        real[] not null,             -- the question cluster, primary embedder
    min_similarity  real not null,
    chunk_id        uuid,
    document_url    text,
    quote           text not null,               -- finds the chunk again after a re-crawl
    active          boolean not null default true,
    created_at      timestamptz not null default now()
);

-- Each lesson going live or being rolled back is a new knowledge version of the college.
create table learn.knowledge_versions (
    tenant_id    uuid not null,
    version      int not null,
    lesson_id    uuid,
    proposal_id  uuid,
    action       text not null check (action in ('apply', 'rollback')),
    note         text,
    at           timestamptz not null default now(),
    primary key (tenant_id, version)
);

do $$
declare
    t text;
begin
    foreach t in array array[
        'learn.feedback_events', 'learn.feedback_analysis', 'learn.trust_scores', 'learn.lessons',
        'learn.lesson_evidence', 'learn.lesson_metrics', 'learn.lesson_aliases',
        'learn.retrieval_boosts', 'learn.knowledge_versions'
    ] loop
        execute format('alter table %s enable row level security', t);
        execute format('alter table %s force row level security', t);
        execute format(
            'create policy tenant_isolation on %s using (tenant_id = kb.current_tenant())'
            ' with check (tenant_id = kb.current_tenant())', t);
        execute format('grant select, insert, update, delete on %s to admitai_kb', t);
    end loop;
end $$;
grant usage on all sequences in schema learn to admitai_kb;

-- Lessons reach the approval inbox, the gate and rollback as proposals of these kinds.
alter table learn.proposals drop constraint proposals_kind_check;
alter table learn.proposals add constraint proposals_kind_check check (kind in (
    'curated_answer', 'document_action', 'sensitive_topic', 'prompt', 'synonym', 'curated_conflict',
    'retrieval_boost', 'search_alias', 'catalogue_reextract'));
alter table learn.proposals drop constraint proposals_root_cause_check;
alter table learn.proposals add constraint proposals_root_cause_check check (root_cause in (
    'correct_but_unwelcome', 'knowledge_gap', 'outdated', 'retrieval_miss', 'wrong_routing',
    'hallucination', 'tone', 'policy', 'catalogue_conflict', 'wrong_fact'));

-- Wake the worker within a second of a new feedback event (it also polls, in case a
-- notification is missed).
create or replace function learn.notify_feedback() returns trigger language plpgsql as $$
begin
    if new.type = 'feedback.created' then
        perform pg_notify('learn_feedback', new.tenant_id::text);
    end if;
    return new;
end $$;
create trigger events_notify_feedback after insert on learn.events
    for each row execute function learn.notify_feedback();
