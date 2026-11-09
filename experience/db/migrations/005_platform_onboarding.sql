-- Plug-and-play onboarding (PRD F10.1-F10.5, 5.2): the Platform Super-Admin creates a college from
-- its Tenant Pack, seeds its staff roster while it is onboarding, and launches it after the gate.
-- Tenants hold no personal data, so the platform may list them; staff of a live college remain the
-- College Admin's business (PRD 5.3: Users & role assignment, Super-Admin CRUD only via onboarding).

create or replace function app.tenant_onboarding(t uuid) returns boolean
language sql stable security definer set search_path = app, pg_temp as
$$ select exists (select 1 from app.tenants where id = t and status = 'onboarding') $$;

create or replace function app.tenant_status(t uuid) returns text
language sql stable security definer set search_path = app, pg_temp as
$$ select status from app.tenants where id = t $$;

create policy tenants_platform_read on app.tenants for select using (app.is_staff('super_admin'));
create policy tenants_platform_insert on app.tenants for insert
    with check (app.is_staff('super_admin') and status = 'onboarding');
create policy tenants_platform_update on app.tenants for update using (app.is_staff('super_admin'));

create policy profiles_platform_seed on app.profiles for insert
    with check (tenant_id = app.current_tenant() and app.is_staff('super_admin')
                and app.tenant_onboarding(tenant_id) and role <> 'super_admin');
create policy profiles_platform_read on app.profiles for select
    using (tenant_id = app.current_tenant() and app.is_staff('super_admin') and app.tenant_onboarding(tenant_id));
create policy profile_departments_platform_seed on app.profile_departments for insert
    with check (tenant_id = app.current_tenant() and app.is_staff('super_admin') and app.tenant_onboarding(tenant_id));
create policy profile_departments_platform_read on app.profile_departments for select
    using (tenant_id = app.current_tenant() and app.is_staff('super_admin') and app.tenant_onboarding(tenant_id));

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant execute on function app.tenant_onboarding(uuid), app.tenant_status(uuid) to admitai_app;
    end if;
end $$;
