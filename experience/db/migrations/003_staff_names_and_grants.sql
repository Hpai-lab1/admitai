-- 1. The migrations bookkeeping table is for the migration runner only, not the API.
-- 2. Officers and faculty may not read other staff profiles (PRD 5.3), but a queue needs to show
--    who claimed a task. This returns only id + name for staff of the current college.

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        revoke all on app.schema_migrations from admitai_app;
    end if;
end $$;

create or replace function app.staff_names()
returns table (id uuid, full_name text)
language sql stable security definer set search_path = app, pg_temp as
$$ select id, full_name from app.profiles
   where tenant_id = app.current_tenant()
     and app.my_role() in ('college_admin', 'admission_officer', 'faculty', 'marketing_manager') $$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant execute on function app.staff_names() to admitai_app;
    end if;
end $$;

-- 3. Marketing managers need visitor-session counts for the funnel (PRD 5.3: analytics R).
--    Sessions hold no personal data (channel, UTM, referrer only).
drop policy sessions_read on app.visitor_sessions;
create policy sessions_read on app.visitor_sessions for select using (
    tenant_id = app.current_tenant()
    and (id = app.current_session()
         or app.is_staff('service', 'college_admin', 'admission_officer', 'faculty', 'marketing_manager')));
