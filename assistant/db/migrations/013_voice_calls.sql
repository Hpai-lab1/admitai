-- Live voice calls (Retell web calls, Vapi phone calls) kept in the database as well as in memory,
-- so a restart of Module B or a second worker does not drop a call in progress (seen 18 Nov 2026:
-- the registry was only a dict in one process; 12 Retell calls failed to connect after restarts).
-- Only ids, the college, the conversation and the language: never a phone number or audio.
create table if not exists chat.voice_calls (
    call_id         text primary key,
    provider        text not null check (provider in ('retell', 'vapi')),
    tenant_id       uuid not null,
    conversation_id text not null,
    session_id      uuid,
    language        text,
    direction       text,
    created_at      timestamptz not null default now()
);
create index if not exists voice_calls_tenant_created on chat.voice_calls (tenant_id, created_at);

alter table chat.voice_calls enable row level security;
alter table chat.voice_calls force row level security;
drop policy if exists tenant_isolation on chat.voice_calls;
create policy tenant_isolation on chat.voice_calls
    using (tenant_id = chat.current_tenant()) with check (tenant_id = chat.current_tenant());
grant select, insert, update, delete on chat.voice_calls to admitai_chat;

-- A provider's request names only its call id; the college is learnt from this one row, as
-- chat.tenant_for_channel does for WhatsApp numbers. Calls older than two hours are not returned.
create or replace function chat.voice_call(p_provider text, p_call_id text)
    returns table (tenant_id uuid, conversation_id text, session_id uuid, language text,
                   direction text, created_at timestamptz)
    language sql stable security definer set search_path = chat as $$
    select tenant_id, conversation_id, session_id, language, direction, created_at
      from chat.voice_calls
     where provider = p_provider and call_id = p_call_id
       and created_at > now() - interval '2 hours'
$$;
revoke all on function chat.voice_call(text, text) from public;
grant execute on function chat.voice_call(text, text) to admitai_chat;
