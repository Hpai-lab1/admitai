-- Background jobs (outbox, webhooks, web push, reminders, retention, college onboarding) ran as the database superuser, which bypasses every rule: a bug in
-- them could read or change anything on the server (review, 18 Nov 2026). They now connect as
-- admitai_app_worker: it may log in and read across colleges (BYPASSRLS, as these jobs must), but it is not a
-- superuser, owns nothing, cannot create objects and has rights only in the app schema.
-- The superuser stays for migrations. Set its password on a real server:
--   alter role admitai_app_worker password '...';   and WORKER_DATABASE_URL in the service's .env.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'admitai_app_worker') then
        create role admitai_app_worker login bypassrls nosuperuser nocreatedb nocreaterole noinherit;
    end if;
end $$;
grant usage on schema app to admitai_app_worker;
grant select, insert, update, delete on all tables in schema app to admitai_app_worker;
grant execute on all functions in schema app to admitai_app_worker;
alter default privileges in schema app grant select, insert, update, delete on tables to admitai_app_worker;
alter default privileges in schema app grant execute on functions to admitai_app_worker;
grant usage, select on all sequences in schema app to admitai_app_worker;
alter default privileges in schema app grant usage, select on sequences to admitai_app_worker;
