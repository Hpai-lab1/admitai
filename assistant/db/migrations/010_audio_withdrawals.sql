-- PRD F2.6: withdrawing audio consent must stop recording at once, not when the visitor's voice
-- token (10 minutes) expires. A withdrawal (or an erasure request) is recorded per session; a
-- recording is refused while one exists, and a new consent given later removes it.
create table chat.audio_withdrawals (
    tenant_id     uuid not null,
    session_id    text not null,
    withdrawn_at  timestamptz not null default now(),
    primary key (tenant_id, session_id)
);
alter table chat.audio_withdrawals enable row level security;
alter table chat.audio_withdrawals force row level security;
create policy tenant_isolation on chat.audio_withdrawals using (tenant_id = chat.current_tenant())
    with check (tenant_id = chat.current_tenant());
grant select, insert, update, delete on chat.audio_withdrawals to admitai_chat;
