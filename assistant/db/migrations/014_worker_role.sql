-- Background jobs (the retention sweep's list of colleges) ran as the database superuser, which bypasses every rule: a bug in
-- them could read or change anything on the server (review, 18 Nov 2026). They now connect as
-- admitai_chat_worker: it may log in and read across colleges (BYPASSRLS, as these jobs must), but it is not a
-- superuser, owns nothing, cannot create objects and has rights only in the chat schema.
-- The superuser stays for migrations. Set its password on a real server:
--   alter role admitai_chat_worker password '...';   and CHAT_WORKER_DATABASE_URL in the service's .env.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'admitai_chat_worker') then
        create role admitai_chat_worker login bypassrls nosuperuser nocreatedb nocreaterole noinherit;
    end if;
end $$;
grant usage on schema chat to admitai_chat_worker;
grant select on all tables in schema chat to admitai_chat_worker;
grant execute on all functions in schema chat to admitai_chat_worker;
alter default privileges in schema chat grant select on tables to admitai_chat_worker;
alter default privileges in schema chat grant execute on functions to admitai_chat_worker;
