-- Erasure on request and retention (PRD 11.4; DPDP "erasure on request"). Module C coordinates:
-- it erases its own rows for a visitor session, then asks Module B (transcripts) and Module A
-- (learning signals) through the outbox, so a module that is down catches up on the next run.
-- The work runs as the 'system' role, which may only blank personal fields, never read staff data.

alter table app.outbox_events drop constraint outbox_events_type_check;
alter table app.outbox_events add constraint outbox_events_type_check
    check (type in ('feedback.created', 'correction.submitted', 'erasure.requested'));

alter table app.visitor_sessions add column erased_at timestamptz;

create policy sessions_system on app.visitor_sessions for all
    using (tenant_id = app.current_tenant() and app.is_staff('system'))
    with check (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy consents_system on app.consents for all
    using (tenant_id = app.current_tenant() and app.is_staff('system'))
    with check (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy guardian_verifications_system on app.guardian_verifications for all
    using (tenant_id = app.current_tenant() and app.is_staff('system'))
    with check (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy leads_system on app.leads for all
    using (tenant_id = app.current_tenant() and app.is_staff('system'))
    with check (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy handoffs_system on app.handoffs for all
    using (tenant_id = app.current_tenant() and app.is_staff('system'))
    with check (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy feedback_system on app.feedback for all
    using (tenant_id = app.current_tenant() and app.is_staff('system'))
    with check (tenant_id = app.current_tenant() and app.is_staff('system'));

-- Audit logs stay append-only, except that rows past the 3-year retention may be removed (by the
-- retention job, which runs with the migration role; the API role still cannot delete).
create or replace function app.audit_is_append_only() returns trigger language plpgsql as
$$
begin
    if tg_op = 'DELETE' and old.at < now() - interval '3 years' then
        return old;
    end if;
    raise exception 'audit_logs is append-only';
end
$$;
