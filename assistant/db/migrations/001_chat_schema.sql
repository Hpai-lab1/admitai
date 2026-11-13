-- Module B schema `chat`: conversations, messages with the versions used, citations and traces
-- (PRD 11.1-11.2). Tenants are owned by Module C; tenant_id is an opaque uuid here.
create schema if not exists chat;

-- The agent part of each Tenant Pack (persona, languages, sensitive topics, escalation, model policy).
create table chat.tenant_settings (
    tenant_id   uuid primary key,
    config      jsonb not null,
    version     int not null default 1,
    updated_at  timestamptz not null default now()
);

create table chat.conversations (
    tenant_id        uuid not null,
    id               text not null,
    session_id       uuid,
    channel          text not null default 'web_chat' check (channel in ('web_chat', 'whatsapp', 'web_voice', 'phone')),
    language         text not null default 'en',
    wa_contact_hash  text,
    state            jsonb not null default '{}',
    summary          text,
    status           text not null default 'open' check (status in ('open', 'handed_off', 'closed')),
    started_at       timestamptz not null default now(),
    last_message_at  timestamptz not null default now(),
    primary key (tenant_id, id)
);

create table chat.messages (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        uuid not null,
    conversation_id  text not null,
    sender           text not null check (sender in ('user', 'assistant', 'staff', 'system')),
    content          text not null,
    language         text,
    agent            text,
    agent_version    text,
    prompt_version   text,
    model            text,
    confidence       real,
    trace_id         uuid,
    latency_ms       int,      -- to first released sentence
    total_ms         int,
    route            jsonb,
    checks           jsonb,
    outcome          text check (outcome in ('answered', 'partial', 'no_answer', 'blocked', 'handoff', 'refused', 'error')),
    created_at       timestamptz not null default now(),
    foreign key (tenant_id, conversation_id) references chat.conversations (tenant_id, id)
);
create index messages_conversation on chat.messages (tenant_id, conversation_id, created_at);

create table chat.message_citations (
    id            bigserial primary key,
    tenant_id     uuid not null,
    message_id    uuid not null references chat.messages(id) on delete cascade,
    n             int not null,
    source_type   text not null,
    ref_id        text,
    title         text not null,
    academic_year text,
    heading_path  text,
    url           text
);

-- Langfuse stand-in for the local prototype: one row per turn with its spans and timings.
create table chat.traces (
    trace_id    uuid primary key,
    tenant_id   uuid not null,
    message_id  uuid,
    spans       jsonb not null,
    created_at  timestamptz not null default now()
);

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'admitai_chat') then
        create role admitai_chat login nobypassrls nosuperuser nocreatedb nocreaterole;
    end if;
end $$;

create or replace function chat.current_tenant() returns uuid language sql stable as $$
    select nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

grant usage on schema chat to admitai_chat;
grant execute on function chat.current_tenant() to admitai_chat;

do $$
declare
    t text;
begin
    foreach t in array array['chat.tenant_settings', 'chat.conversations', 'chat.messages',
                             'chat.message_citations', 'chat.traces'] loop
        execute format('alter table %s enable row level security', t);
        execute format('alter table %s force row level security', t);
        execute format(
            'create policy tenant_isolation on %s using (tenant_id = chat.current_tenant())'
            ' with check (tenant_id = chat.current_tenant())', t);
        execute format('grant select, insert, update, delete on %s to admitai_chat', t);
    end loop;
end $$;
grant usage on all sequences in schema chat to admitai_chat;
