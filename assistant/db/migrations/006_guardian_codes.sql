-- Guardian verification over WhatsApp (PRD F5.3). Module C creates the verification and its
-- code; the guardian then writes to the college's WhatsApp number (guardian-initiated, so inside
-- Meta's 24-hour window) and gets the code there, but only from the number the student gave.
-- Until then the code waits here, sealed with a key derived from that number, which Module B
-- never stores: the table alone does not reveal any code. Rows go once delivered or expired.
create table chat.guardian_codes (
    id            bigserial primary key,
    tenant_id     uuid not null,
    contact_hash  text not null,
    sealed        text not null,
    language      text not null default 'en',
    expires_at    timestamptz not null,
    created_at    timestamptz not null default now()
);
create index guardian_codes_contact on chat.guardian_codes (tenant_id, contact_hash);

alter table chat.guardian_codes enable row level security;
alter table chat.guardian_codes force row level security;
create policy tenant_isolation on chat.guardian_codes
    using (tenant_id = chat.current_tenant()) with check (tenant_id = chat.current_tenant());
grant select, insert, update, delete on chat.guardian_codes to admitai_chat;
grant usage on all sequences in schema chat to admitai_chat;
