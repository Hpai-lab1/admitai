-- 1. Conversation events (PRD F8.3 implicit signals, and the 3.5 metric definitions: qualifying,
--    resolved, abandoned). Written by the widget or Module B; no personal data.
create table app.conversation_events (
    id               bigserial primary key,
    tenant_id        uuid not null references app.tenants(id),
    session_id       uuid references app.visitor_sessions(id),
    conversation_id  text not null,
    kind             text not null check (kind in ('user_message', 'answer', 'rephrase', 'talk_to_human',
                                                   'voice_hangup', 'barge_in')),
    message_id       text,
    channel          text not null default 'web_chat' check (channel in ('web_chat', 'whatsapp', 'web_voice')),
    created_at       timestamptz not null default now()
);
create index on app.conversation_events (tenant_id, conversation_id, created_at);

alter table app.conversation_events enable row level security;
alter table app.conversation_events force row level security;
create policy conversation_events_insert on app.conversation_events for insert with check (
    tenant_id = app.current_tenant() and app.is_staff('visitor', 'service')
    and (session_id = app.current_session() or app.is_staff('service')));
create policy conversation_events_read on app.conversation_events for select using (
    tenant_id = app.current_tenant()
    and app.is_staff('college_admin', 'admission_officer', 'faculty', 'marketing_manager', 'service'));

-- 2. Department of the answer that was rated (from Module B's routing), so faculty review only
--    their departments' low-rated answers (F7.4).
alter table app.feedback add column department text;

-- 3. Break-glass (PRD 5.4): a Super-Admin sees a college's personal data only after stating a
--    reason; access lasts 1 hour and the College Admin is notified.
create table app.break_glass (
    id          uuid primary key default gen_random_uuid(),
    tenant_id   uuid not null references app.tenants(id),
    profile_id  uuid not null references app.profiles(id),
    reason      text not null check (length(reason) >= 10),
    started_at  timestamptz not null default now(),
    expires_at  timestamptz not null default now() + interval '1 hour',
    ended_at    timestamptz
);
alter table app.break_glass enable row level security;
alter table app.break_glass force row level security;
create policy break_glass_own on app.break_glass for all
    using (tenant_id = app.current_tenant()
           and ((app.is_staff('super_admin') and profile_id = app.current_profile()) or app.is_staff('college_admin')))
    with check (tenant_id = app.current_tenant() and app.is_staff('super_admin') and profile_id = app.current_profile());

create or replace function app.break_glass_active() returns boolean language sql stable as
$$ select app.my_role() = 'super_admin' and exists (
       select 1 from app.break_glass b
       where b.tenant_id = app.current_tenant() and b.profile_id = app.current_profile()
         and b.ended_at is null and b.expires_at > now()) $$;

-- extra read policies (policies are OR-ed): personal data only during an active break-glass window
create policy leads_break_glass on app.leads for select
    using (tenant_id = app.current_tenant() and app.break_glass_active());
create policy handoffs_break_glass on app.handoffs for select
    using (tenant_id = app.current_tenant() and app.break_glass_active());
create policy consents_break_glass on app.consents for select
    using (tenant_id = app.current_tenant() and app.break_glass_active());
create policy feedback_super_admin on app.feedback for select
    using (tenant_id = app.current_tenant() and app.is_staff('super_admin'));
create policy notifications_super_admin on app.notifications for select
    using (tenant_id = app.current_tenant() and app.break_glass_active());

-- staff names visible to the super-admin too
create or replace function app.staff_names()
returns table (id uuid, full_name text)
language sql stable security definer set search_path = app, pg_temp as
$$ select id, full_name from app.profiles
   where tenant_id = app.current_tenant()
     and app.my_role() in ('super_admin', 'college_admin', 'admission_officer', 'faculty', 'marketing_manager') $$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant select, insert, update on app.conversation_events, app.break_glass to admitai_app;
        grant usage, select on all sequences in schema app to admitai_app;
        grant execute on function app.break_glass_active() to admitai_app;
        revoke delete on app.break_glass from admitai_app;
    end if;
end $$;
