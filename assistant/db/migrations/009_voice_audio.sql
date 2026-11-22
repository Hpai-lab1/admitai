-- PRD F2.6: "Transcript + audio (if consented) stored and linked to the conversation; audio
-- retention configurable (default 30 days)". Off unless the college turns it on (pack.yaml
-- voice.store_audio) AND the visitor consents (Module C purpose audio_recording). One row per
-- spoken question; the answer's audio is generated text-to-speech and is not kept.
create table chat.voice_audio (
    id               uuid primary key default gen_random_uuid(),
    tenant_id        uuid not null,
    conversation_id  text not null,
    session_id       text,
    channel          text not null check (channel in ('web_voice', 'livekit')),
    mime             text not null default 'audio/wav',
    audio            bytea not null check (octet_length(audio) <= 6 * 1024 * 1024),
    transcript       text,
    created_at       timestamptz not null default now(),
    expires_at       timestamptz not null
);
create index voice_audio_conversation on chat.voice_audio (tenant_id, conversation_id);
create index voice_audio_expiry on chat.voice_audio (expires_at);
alter table chat.voice_audio enable row level security;
alter table chat.voice_audio force row level security;
create policy tenant_isolation on chat.voice_audio using (tenant_id = chat.current_tenant())
    with check (tenant_id = chat.current_tenant());
grant select, insert, update, delete on chat.voice_audio to admitai_chat;
