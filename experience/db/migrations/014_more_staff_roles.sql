-- Staff roles beyond PRD 5.2, for the way colleges staff admissions (README, "Staff roles"):
--   admission_counsellor  front desk / telecalling: works the admission queue and leads, suggests
--                         corrections but approves nothing          -> policies of admission_officer
--   finance_officer       fees, refunds, scholarships for its departments      -> policies of faculty
--   hostel_warden         hostel and accommodation for its departments          -> policies of faculty
--   management            principal, dean: aggregated analytics only    -> policies of marketing_manager
-- Each new role reaches exactly the rows of the PRD role it extends (same tenant and department
-- limits), so every existing policy keeps working; the API narrows what each role may do.

alter table app.profiles drop constraint if exists profiles_role_check;
alter table app.profiles add constraint profiles_role_check check (role in (
    'super_admin', 'college_admin', 'admission_officer', 'faculty', 'marketing_manager',
    'admission_counsellor', 'finance_officer', 'hostel_warden', 'management'));

create or replace function app.policy_role(r text) returns text language sql immutable as
$$ select case r
       when 'admission_counsellor' then 'admission_officer'
       when 'finance_officer' then 'faculty'
       when 'hostel_warden' then 'faculty'
       when 'management' then 'marketing_manager'
       else r end $$;

-- Every policy asks app.is_staff(...); an added role passes where its PRD role does.
create or replace function app.is_staff(variadic roles text[]) returns boolean language sql stable as
$$ select app.my_role() = any(roles) or app.policy_role(app.my_role()) = any(roles) $$;

-- The names of colleagues (who claimed a task), for every staff role.
create or replace function app.staff_names()
returns table (id uuid, full_name text)
language sql stable security definer set search_path = app, pg_temp as
$$ select id, full_name from app.profiles
   where tenant_id = app.current_tenant()
     and app.is_staff('super_admin', 'college_admin', 'admission_officer', 'faculty', 'marketing_manager') $$;

do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant execute on function app.policy_role(text) to admitai_app;
        grant execute on function app.staff_names() to admitai_app;
    end if;
end $$;
