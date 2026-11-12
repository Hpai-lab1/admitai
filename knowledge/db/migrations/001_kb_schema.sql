-- Module A schemas: kb (what the assistant knows) and learn (how it learns). PRD 11.1-11.2.
-- Tenants are owned by Module C; here tenant_id is an opaque uuid passed by callers.
-- No pgvector on the local server: embeddings are real[] (768-d, nomic-embed-text) and the
-- vector search runs in the API. Swapping to vector(768) + HNSW is a migration, not a code change
-- for callers.

create schema if not exists kb;
create schema if not exists learn;

create table kb.programs (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        uuid not null,
    code             text not null,
    name             text not null,
    aliases          text[] not null default '{}',
    department       text,
    level            text not null check (level in ('UG', 'PG', 'PhD', 'Diploma')),
    duration_months  int,
    intake           int,
    eligibility      text,
    contact          text,
    academic_year    text not null,
    status           text not null default 'approved' check (status in ('draft', 'approved', 'expired')),
    created_at       timestamptz not null default now(),
    updated_at       timestamptz not null default now(),
    unique (tenant_id, code, academic_year)
);

create table kb.program_fees (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null,
    program_id  uuid not null references kb.programs(id) on delete cascade,
    category    text not null default 'general',
    component   text not null default 'tuition',
    amount      numeric(12, 2) not null,
    currency    char(3) not null default 'INR',
    period      text not null default 'per year',
    valid_from  date,
    valid_to    date,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create table kb.program_dates (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      uuid not null,
    program_id     uuid references kb.programs(id) on delete cascade,
    label          text not null,
    on_date        date not null,
    academic_year  text not null,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);

create table kb.sources (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null,
    kind        text not null check (kind in ('upload', 'crawl')),
    name        text not null,
    config      jsonb not null default '{}',
    last_sync   timestamptz,
    last_error  text,
    created_at  timestamptz not null default now(),
    updated_at  timestamptz not null default now()
);

create table kb.documents (
    id             uuid primary key default gen_random_uuid(),
    tenant_id      uuid not null,
    source_id      uuid references kb.sources(id),
    title          text not null,
    mime           text not null,
    url            text,
    academic_year  text,
    department     text,
    owner_role     text not null default 'admission_officer',
    owner          text,
    status         text not null default 'processing'
                   check (status in ('processing', 'draft', 'needs_review', 'approved', 'expired', 'rejected')),
    status_reason  text,
    valid_from     date,
    valid_to       date,
    version        int not null default 1,
    previous_id    uuid references kb.documents(id),
    checksum       text not null,
    content        text,
    diff_summary   text,
    chunk_count    int not null default 0,
    approved_by    text,
    approved_at    timestamptz,
    created_at     timestamptz not null default now(),
    updated_at     timestamptz not null default now()
);
create index documents_tenant_checksum on kb.documents (tenant_id, checksum);

create table kb.chunks (
    id            uuid primary key default gen_random_uuid(),
    tenant_id     uuid not null,
    document_id   uuid not null references kb.documents(id) on delete cascade,
    ord           int not null,
    content       text not null,
    heading_path  text not null default '',
    page          int,
    embedding     real[] not null,
    tsv           tsvector generated always as (to_tsvector('english', heading_path || ' ' || content)) stored,
    created_at    timestamptz not null default now()
);
create index chunks_tsv on kb.chunks using gin (tsv);
create index chunks_tenant on kb.chunks (tenant_id, document_id);

-- array_to_string is only "stable" in the catalogue, but is immutable for text[]; generated
-- columns need an immutable expression.
create function kb.qa_text(question text, variants text[], answer text) returns text
    language sql immutable as $$ select question || ' ' || array_to_string(variants, ' ') || ' ' || answer $$;

create table kb.curated_qa (
    id                    uuid primary key default gen_random_uuid(),
    tenant_id             uuid not null,
    question              text not null,
    variants              text[] not null default '{}',
    answer                text not null,
    category              text,
    owner_role            text not null default 'admission_officer',
    department            text,
    academic_year         text not null,
    valid_to              date not null,
    status                text not null default 'draft'
                          check (status in ('draft', 'approved', 'suspended', 'expired')),
    status_reason         text,
    version               int not null default 1,
    last_used_at          timestamptz,
    embedding             real[] not null,
    tsv                   tsvector generated always as (
                              to_tsvector('english', kb.qa_text(question, variants, answer))) stored,
    source_correction_id  text,
    external_ref          text,
    created_at            timestamptz not null default now(),
    updated_at            timestamptz not null default now()
);
create index curated_qa_tsv on kb.curated_qa using gin (tsv);
create unique index curated_qa_external_ref on kb.curated_qa (tenant_id, external_ref) where external_ref is not null;

create table kb.curated_qa_history (
    id          bigserial primary key,
    tenant_id   uuid not null,
    qa_id       uuid not null references kb.curated_qa(id) on delete cascade,
    version     int not null,
    snapshot    jsonb not null,
    action      text not null,
    actor       text,
    at          timestamptz not null default now()
);

-- Events from Module C (feedback.created, correction.submitted). At-least-once: de-duplicated by id.
create table learn.events (
    id           bigserial primary key,
    tenant_id    uuid not null,
    event_id     text not null,
    type         text not null,
    payload      jsonb not null,
    received_at  timestamptz not null default now(),
    processed_at timestamptz,
    unique (tenant_id, event_id)
);
