-- PRD 13, handoff banner: "Connecting you to Admissions…", then the staff member's name once claimed.
-- A visitor cannot read app.profiles, so this returns only what the banner shows: the status, the
-- queue and the first name of whoever claimed the visitor's own latest task in this conversation.
create or replace function app.my_handoff_status(conversation text)
returns table (status text, queue text, department text, staff_first_name text)
language sql stable security definer set search_path = app, pg_temp as
$$ select h.status, h.queue, h.department,
          case when h.status = 'claimed' then nullif(split_part(
              regexp_replace(trim(p.full_name), '^((dr|prof|mr|mrs|ms|shri|smt|sri)\.?\s+)+', '', 'i'),
              ' ', 1), '') end
   from app.handoffs h
   left join app.profiles p on p.id = h.assigned_to and p.tenant_id = h.tenant_id
   where h.tenant_id = app.current_tenant()
     and h.session_id = app.current_session()
     and h.conversation_id = conversation
   order by h.created_at desc
   limit 1 $$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant execute on function app.my_handoff_status(text) to admitai_app;
    end if;
end $$;
