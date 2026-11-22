-- PRD F9.2: the dashboard counts one tenant's messages over a date range.
create index if not exists messages_tenant_created on chat.messages (tenant_id, created_at);
