-- Asynchronous grounding review (PRD F1.4b): every answer is reviewed off the hot path against the
-- exact source text the model saw, which is now kept with each citation.
alter table chat.message_citations add column content text;

create table chat.grounding_reviews (
    message_id   uuid primary key references chat.messages(id) on delete cascade,
    tenant_id    uuid not null,
    verdict      text not null check (verdict in ('supported', 'unsupported', 'skipped')),
    sentences    jsonb not null,           -- [{sentence, cited, quote, supported}]
    model        text,
    reviewed_at  timestamptz not null default now()
);
alter table chat.grounding_reviews enable row level security;
alter table chat.grounding_reviews force row level security;
create policy tenant_isolation on chat.grounding_reviews using (tenant_id = chat.current_tenant())
    with check (tenant_id = chat.current_tenant());
grant select, insert, update, delete on chat.grounding_reviews to admitai_chat;
