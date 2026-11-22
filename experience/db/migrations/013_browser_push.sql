-- PRD F7.5: browser push for staff. A push says only that something is waiting; what it is stays
-- in the console behind sign-in, so no applicant detail passes through the browser push services.
alter table app.notifications add column if not exists pushed_at timestamptz;
update app.notifications set pushed_at = created_at where pushed_at is null;  -- never push the past

create table if not exists app.push_subscriptions (
    id          bigserial primary key,
    tenant_id   uuid not null references app.tenants(id),
    profile_id  uuid not null references app.profiles(id) on delete cascade,
    endpoint    text not null unique check (length(endpoint) between 20 and 1000),
    created_at  timestamptz not null default now(),
    last_ok_at  timestamptz
);
alter table app.push_subscriptions enable row level security;
alter table app.push_subscriptions force row level security;
drop policy if exists own_subscriptions on app.push_subscriptions;
create policy own_subscriptions on app.push_subscriptions
    using (tenant_id = app.current_tenant() and profile_id = app.current_profile())
    with check (tenant_id = app.current_tenant() and profile_id = app.current_profile());

-- A browser that signs in as someone else must stop getting the first person's pushes. RLS hides
-- other people's rows, so this one function removes an endpoint whoever it belonged to.
create or replace function app.forget_push_endpoint(url text) returns void
language sql volatile security definer set search_path = app, pg_temp as
$$ delete from app.push_subscriptions where endpoint = url $$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant select, insert, update, delete on app.push_subscriptions to admitai_app;
        grant execute on function app.forget_push_endpoint(text) to admitai_app;
        grant usage, select on sequence app.push_subscriptions_id_seq to admitai_app;
    end if;
end $$;
