-- Erasure on request and transcript retention (PRD 11.4): a conversation's personal content is
-- blanked and the time recorded; its turn rows stay for anonymous analytics.
alter table chat.conversations add column erased_at timestamptz;
create index conversations_session on chat.conversations (tenant_id, session_id);
create index conversations_last_message on chat.conversations (tenant_id, last_message_at)
    where erased_at is null;
