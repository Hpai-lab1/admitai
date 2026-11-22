-- WhatsApp text channel (PRD F1.11-F1.12). Module B never stores the WhatsApp number: contacts are
-- a salted hash (11.2); the number reaches Module C only after the person agrees to be contacted.

-- Which tenant a business number belongs to. Looked up before any tenant is known, so reads go
-- through a security-definer function that returns only the tenant id.
create table chat.channels (
    tenant_id    uuid not null,
    channel      text not null check (channel in ('whatsapp')),
    external_id  text not null,           -- WhatsApp phone_number_id
    config       jsonb not null default '{}',
    primary key (channel, external_id)
);

create or replace function chat.tenant_for_channel(p_channel text, p_external_id text) returns uuid
    language sql stable security definer set search_path = chat as $$
    select tenant_id from chat.channels where channel = p_channel and external_id = p_external_id
$$;

-- Inbound and outbound WhatsApp message ids, for de-duplicating Meta's retries and for mapping
-- a reaction on one of our messages back to the answer it rated.
create table chat.wa_messages (
    tenant_id       uuid not null,
    wa_message_id   text not null,
    direction       text not null check (direction in ('in', 'out')),
    conversation_id text not null,
    message_id      uuid,                 -- our assistant message (outbound answers)
    created_at      timestamptz not null default now(),
    primary key (tenant_id, wa_message_id)
);

-- Local stand-in for the Cloud API: what would have been sent (no phone number, only the hash).
create table chat.wa_outbox (
    id            bigserial primary key,
    tenant_id     uuid not null,
    contact_hash  text not null,
    payload       jsonb not null,
    created_at    timestamptz not null default now()
);

do $$
declare
    t text;
begin
    foreach t in array array['chat.channels', 'chat.wa_messages', 'chat.wa_outbox'] loop
        execute format('alter table %s enable row level security', t);
        execute format('alter table %s force row level security', t);
        execute format(
            'create policy tenant_isolation on %s using (tenant_id = chat.current_tenant())'
            ' with check (tenant_id = chat.current_tenant())', t);
        execute format('grant select, insert, update, delete on %s to admitai_chat', t);
    end loop;
end $$;
grant usage on all sequences in schema chat to admitai_chat;
grant execute on function chat.tenant_for_channel(text, text) to admitai_chat;
