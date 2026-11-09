-- Outbound webhooks (PRD 12.2): a college's own systems (CRM, ERP) are told about leads, handoffs
-- and knowledge changes. Each delivery is signed with the endpoint's secret, retried with
-- exponential backoff for 24 hours, and carries a stable event id so receivers can de-duplicate.

create table app.webhook_endpoints (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references app.tenants(id),
    url         text not null,
    secret      text not null,
    events      text[] not null,
    active      boolean not null default true,
    created_by  uuid references app.profiles(id),
    created_at  timestamptz not null default now()
);

create table app.webhook_deliveries (
    id               bigserial primary key,
    tenant_id        uuid not null references app.tenants(id),
    endpoint_id      uuid not null references app.webhook_endpoints(id),
    event_id         uuid not null,
    type             text not null,
    payload          jsonb not null,
    lead_id          uuid,               -- so erasure can cancel what would carry a person's data
    status           text not null default 'pending'
                     check (status in ('pending', 'delivered', 'failed', 'cancelled')),
    attempts         int not null default 0,
    next_attempt_at  timestamptz not null default now(),
    last_status      int,
    last_error       text,
    created_at       timestamptz not null default now(),
    delivered_at     timestamptz,
    unique (endpoint_id, event_id)
);
create index webhook_due on app.webhook_deliveries (next_attempt_at) where status = 'pending';

alter table app.webhook_endpoints enable row level security;
alter table app.webhook_endpoints force row level security;
alter table app.webhook_deliveries enable row level security;
alter table app.webhook_deliveries force row level security;

-- The College Admin manages endpoints; the delivery worker ('system') reads them.
create policy webhook_endpoints_admin on app.webhook_endpoints for all
    using (tenant_id = app.current_tenant() and app.is_staff('college_admin'))
    with check (tenant_id = app.current_tenant() and app.is_staff('college_admin'));
create policy webhook_endpoints_system on app.webhook_endpoints for select
    using (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy webhook_deliveries_admin on app.webhook_deliveries for select
    using (tenant_id = app.current_tenant() and app.is_staff('college_admin'));
create policy webhook_deliveries_system on app.webhook_deliveries for all
    using (tenant_id = app.current_tenant() and app.is_staff('system'))
    with check (tenant_id = app.current_tenant() and app.is_staff('system'));

-- Queue an event for every active endpoint of the current college that subscribed to it. Any
-- role may emit (a visitor creating a lead), but only through this function, which reveals
-- nothing about the endpoints. Returns how many deliveries were queued.
create or replace function app.enqueue_webhook(p_type text, p_event uuid, p_body jsonb, p_lead uuid)
returns int language plpgsql security definer set search_path = app, pg_temp as
$$
declare n int;
begin
    insert into app.webhook_deliveries (tenant_id, endpoint_id, event_id, type, payload, lead_id)
    select e.tenant_id, e.id, p_event, p_type, p_body, p_lead
    from app.webhook_endpoints e
    where e.tenant_id = app.current_tenant() and e.active and p_type = any(e.events)
    on conflict (endpoint_id, event_id) do nothing;
    get diagnostics n = row_count;
    return n;
end
$$;

-- Erasure: deliveries still waiting that would carry this lead's data are cancelled and blanked.
create or replace function app.cancel_lead_webhooks(p_lead uuid) returns int
language plpgsql security definer set search_path = app, pg_temp as
$$
declare n int;
begin
    update app.webhook_deliveries set status = 'cancelled', payload = '{}'::jsonb
    where tenant_id = app.current_tenant() and lead_id = p_lead and status = 'pending';
    get diagnostics n = row_count;
    return n;
end
$$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant select, insert, update, delete on app.webhook_endpoints, app.webhook_deliveries to admitai_app;
        grant usage, select on all sequences in schema app to admitai_app;
        grant execute on function app.enqueue_webhook(text, uuid, jsonb, uuid),
                                  app.cancel_lead_webhooks(uuid) to admitai_app;
    end if;
end $$;
