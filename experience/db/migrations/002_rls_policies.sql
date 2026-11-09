-- Row-Level Security for every table in `app` (PRD 5.3 permission matrix, 11.3 RLS pattern).
-- Tables are FORCED so that the owner is also subject to the policies; the API's database role
-- (`admitai_app`) is neither owner nor BYPASSRLS.

do $$
declare t text;
begin
    foreach t in array array['tenants', 'profiles', 'profile_departments', 'refresh_tokens',
        'visitor_sessions', 'audit_logs', 'handoffs', 'handoff_events', 'consents',
        'guardian_verifications', 'leads', 'lead_events', 'feedback', 'corrections',
        'outbox_events', 'notifications', 'message_outbox']
    loop
        execute format('alter table app.%I enable row level security', t);
        execute format('alter table app.%I force row level security', t);
    end loop;
end $$;

-- tenants: a college sees only itself; only its admin edits its configuration
create policy tenants_read on app.tenants for select using (id = app.current_tenant());
create policy tenants_admin on app.tenants for update
    using (id = app.current_tenant() and app.is_staff('college_admin'));

-- profiles: admins manage staff; everyone sees their own row; 'login' is the sign-in step
create policy profiles_read on app.profiles for select using (
    tenant_id = app.current_tenant()
    and (app.is_staff('college_admin', 'login') or id = app.current_profile()));
create policy profiles_insert on app.profiles for insert
    with check (tenant_id = app.current_tenant() and app.is_staff('college_admin'));
create policy profiles_update on app.profiles for update using (
    tenant_id = app.current_tenant()
    and (app.is_staff('college_admin') or id = app.current_profile()));

create policy profile_departments_read on app.profile_departments for select using (
    tenant_id = app.current_tenant()
    and (app.is_staff('college_admin', 'login') or profile_id = app.current_profile()));
create policy profile_departments_write on app.profile_departments for all
    using (tenant_id = app.current_tenant() and app.is_staff('college_admin'))
    with check (tenant_id = app.current_tenant() and app.is_staff('college_admin'));

create policy refresh_tokens_own on app.refresh_tokens for all
    using (tenant_id = app.current_tenant()
           and (profile_id = app.current_profile() or app.is_staff('college_admin')))
    with check (tenant_id = app.current_tenant()
           and (profile_id = app.current_profile() or app.is_staff('college_admin')));

-- visitor sessions
create policy sessions_insert on app.visitor_sessions for insert
    with check (tenant_id = app.current_tenant() and app.is_staff('visitor', 'service'));
create policy sessions_read on app.visitor_sessions for select using (
    tenant_id = app.current_tenant()
    and (id = app.current_session() or app.is_staff('service', 'college_admin', 'admission_officer', 'faculty')));

-- audit: anyone in the tenant can append; only the college admin reads (PRD 5.3)
create policy audit_insert on app.audit_logs for insert with check (tenant_id = app.current_tenant());
create policy audit_read on app.audit_logs for select
    using (tenant_id = app.current_tenant() and app.is_staff('college_admin'));

-- handoffs: admission queue for officers, faculty queue limited to the faculty member's departments
create policy handoffs_read on app.handoffs for select using (
    tenant_id = app.current_tenant() and (
        app.is_staff('college_admin', 'service')
        or (app.is_staff('admission_officer') and queue = 'admission')
        or (app.is_staff('faculty') and queue = 'faculty' and department = any(app.current_departments()))
        or (app.is_staff('visitor') and session_id = app.current_session())));
create policy handoffs_insert on app.handoffs for insert with check (
    tenant_id = app.current_tenant()
    and app.is_staff('service', 'visitor', 'college_admin', 'admission_officer', 'faculty'));
create policy handoffs_update on app.handoffs for update using (
    tenant_id = app.current_tenant() and (
        app.is_staff('college_admin')
        or (app.is_staff('admission_officer') and queue = 'admission')
        or (app.is_staff('faculty') and queue = 'faculty' and department = any(app.current_departments()))));

create policy handoff_events_all on app.handoff_events for all
    using (tenant_id = app.current_tenant()
           and exists (select 1 from app.handoffs h where h.id = handoff_id))
    with check (tenant_id = app.current_tenant()
           and exists (select 1 from app.handoffs h where h.id = handoff_id));

-- consents: the visitor's own session, the assistant service, and admission staff
create policy consents_read on app.consents for select using (
    tenant_id = app.current_tenant() and (
        session_id = app.current_session()
        or app.is_staff('service', 'college_admin', 'admission_officer')));
create policy consents_insert on app.consents for insert
    with check (tenant_id = app.current_tenant() and app.is_staff('visitor', 'service'));
create policy consents_withdraw on app.consents for update using (
    tenant_id = app.current_tenant()
    and (session_id = app.current_session() or app.is_staff('service', 'college_admin')));

create policy guardian_verifications_own on app.guardian_verifications for all
    using (tenant_id = app.current_tenant()
           and (session_id = app.current_session() or app.is_staff('service')))
    with check (tenant_id = app.current_tenant()
           and (session_id = app.current_session() or app.is_staff('service')));

-- leads: admission staff and marketing (read); faculty only for leads linked to their handoffs
create policy leads_read on app.leads for select using (
    tenant_id = app.current_tenant() and (
        app.is_staff('college_admin', 'admission_officer', 'marketing_manager', 'service')
        or session_id = app.current_session()
        or (app.is_staff('faculty') and exists (select 1 from app.handoffs h where h.lead_id = leads.id))));
create policy leads_insert on app.leads for insert
    with check (tenant_id = app.current_tenant() and app.is_staff('visitor', 'service'));
create policy leads_update on app.leads for update using (
    tenant_id = app.current_tenant() and (
        app.is_staff('college_admin', 'admission_officer') or session_id = app.current_session()));

create policy lead_events_all on app.lead_events for all
    using (tenant_id = app.current_tenant() and exists (select 1 from app.leads l where l.id = lead_id))
    with check (tenant_id = app.current_tenant() and exists (select 1 from app.leads l where l.id = lead_id));

-- feedback: anyone may rate; staff read
create policy feedback_insert on app.feedback for insert with check (
    tenant_id = app.current_tenant()
    and app.is_staff('visitor', 'service', 'college_admin', 'admission_officer', 'faculty'));
create policy feedback_read on app.feedback for select using (
    tenant_id = app.current_tenant()
    and app.is_staff('college_admin', 'admission_officer', 'faculty', 'marketing_manager', 'visitor', 'service'));

-- corrections: officers, faculty and admins (PRD 5.3: Feedback & corrections C/R)
create policy corrections_all on app.corrections for all
    using (tenant_id = app.current_tenant() and app.is_staff('college_admin', 'admission_officer', 'faculty'))
    with check (tenant_id = app.current_tenant() and app.is_staff('college_admin', 'admission_officer', 'faculty'));

-- outbox to Module A and message outbox: written by anyone in the tenant, read by admin/system
create policy outbox_insert on app.outbox_events for insert with check (tenant_id = app.current_tenant());
create policy outbox_read on app.outbox_events for select
    using (tenant_id = app.current_tenant() and app.is_staff('college_admin', 'system'));
create policy outbox_update on app.outbox_events for update
    using (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy message_outbox_insert on app.message_outbox for insert with check (tenant_id = app.current_tenant());
create policy message_outbox_read on app.message_outbox for select
    using (tenant_id = app.current_tenant() and app.is_staff('college_admin', 'system'));

-- notifications: by queue
create policy notifications_insert on app.notifications for insert with check (tenant_id = app.current_tenant());
create policy notifications_read on app.notifications for select using (
    tenant_id = app.current_tenant() and (
        app.is_staff('college_admin')
        or (audience = 'admission' and app.is_staff('admission_officer'))
        or (audience like 'faculty:%' and app.is_staff('faculty')
            and split_part(audience, ':', 2) = any(app.current_departments()))));
create policy notifications_update on app.notifications for update using (
    tenant_id = app.current_tenant() and (
        app.is_staff('college_admin')
        or (audience = 'admission' and app.is_staff('admission_officer'))
        or (audience like 'faculty:%' and app.is_staff('faculty')
            and split_part(audience, ':', 2) = any(app.current_departments()))));

-- ------------------------------------------------------------------ grants for the API's role
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant usage on schema app to admitai_app;
        grant select, insert, update, delete on all tables in schema app to admitai_app;
        grant usage, select on all sequences in schema app to admitai_app;
        grant execute on all functions in schema app to admitai_app;
        revoke update, delete on app.audit_logs from admitai_app;
        revoke delete on app.consents, app.feedback, app.corrections from admitai_app;
    end if;
end $$;
