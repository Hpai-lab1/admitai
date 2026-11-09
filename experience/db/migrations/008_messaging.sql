-- E-mail delivery (PRD F5.3 guardian codes, F3.4 callback acknowledgements, F3.6 staff replies,
-- F7.5 staff notifications) and overdue callbacks (PRD F3 error handling).
--
-- app.message_outbox becomes a real queue: the worker (`python -m experience_api.messaging run`)
-- sends pending rows by SMTP and blanks the body once sent. Staff messages name an audience and
-- are addressed when sent, so a deactivated account is never mailed. Guardian codes are never
-- queued: they are sent at once and stored nowhere.

alter table app.message_outbox
    alter column destination drop not null,
    add column audience        text,          -- 'admission', 'faculty:<department>', 'college_admin'
    add column subject         text,
    add column body            text,          -- blanked once sent: a queue, not an archive
    add column lead_id         uuid,          -- so erasure can cancel what would reach a person
    add column handoff_id      uuid references app.handoffs(id) on delete set null,
    add column status          text not null default 'pending'
                               check (status in ('pending', 'sent', 'failed', 'cancelled')),
    add column attempts        int not null default 0,
    add column next_attempt_at timestamptz not null default now(),
    add column last_error      text,
    -- only a message still waiting needs an address; sent and cancelled ones forget it
    add constraint message_outbox_addressed
        check (status <> 'pending' or destination is not null or audience is not null);

-- Rows written before this migration were stand-ins with nothing to send.
update app.message_outbox set status = case when sent_at is null then 'cancelled' else 'sent' end;
create index message_outbox_due on app.message_outbox (next_attempt_at) where status = 'pending';

-- Overdue callbacks are reminded, escalated and explained to the applicant once.
alter table app.handoffs add column escalated_at timestamptz;
alter table app.handoff_events drop constraint handoff_events_kind_check;
alter table app.handoff_events add constraint handoff_events_kind_check
    check (kind in ('created', 'claimed', 'replied', 'resolved', 'reopened', 'escalated', 'emailed'));

-- The delivery worker ('system') reads staff addresses and updates the queue; nothing else.
create policy message_outbox_system on app.message_outbox for update
    using (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy profiles_system on app.profiles for select
    using (tenant_id = app.current_tenant() and app.is_staff('system'));
create policy profile_departments_system on app.profile_departments for select
    using (tenant_id = app.current_tenant() and app.is_staff('system'));

-- Erasure or withdrawal drops queued messages to that person, whoever triggers it (a visitor
-- cannot otherwise touch the queue).
create or replace function app.cancel_lead_messages(p_lead uuid) returns int
language plpgsql security definer set search_path = app, pg_temp as
$$
declare n int;
begin
    update app.message_outbox set status = 'cancelled', destination = null, subject = null, body = null
    where tenant_id = app.current_tenant() and lead_id = p_lead and status = 'pending';
    get diagnostics n = row_count;
    return n;
end
$$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant execute on function app.cancel_lead_messages(uuid) to admitai_app;
    end if;
end $$;
