-- Background jobs (evaluation gate, learning, self-learning worker, recovery sweeps) ran as the database superuser, which bypasses every rule: a bug in
-- them could read or change anything on the server (review, 18 Nov 2026). They now connect as
-- admitai_kb_worker: it may log in and read across colleges (BYPASSRLS, as these jobs must), but it is not a
-- superuser, owns nothing, cannot create objects and has rights only in the kb schema and the learn schema.
-- The superuser stays for migrations. Set its password on a real server:
--   alter role admitai_kb_worker password '...';   and KB_WORKER_DATABASE_URL in the service's .env.
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'admitai_kb_worker') then
        create role admitai_kb_worker login bypassrls nosuperuser nocreatedb nocreaterole noinherit;
    end if;
end $$;
grant usage on schema kb to admitai_kb_worker;
grant select, insert, update, delete on all tables in schema kb to admitai_kb_worker;
grant execute on all functions in schema kb to admitai_kb_worker;
alter default privileges in schema kb grant select, insert, update, delete on tables to admitai_kb_worker;
alter default privileges in schema kb grant execute on functions to admitai_kb_worker;
grant usage, select on all sequences in schema kb to admitai_kb_worker;
alter default privileges in schema kb grant usage, select on sequences to admitai_kb_worker;
grant usage on schema learn to admitai_kb_worker;
grant select, insert, update, delete on all tables in schema learn to admitai_kb_worker;
grant execute on all functions in schema learn to admitai_kb_worker;
alter default privileges in schema learn grant select, insert, update, delete on tables to admitai_kb_worker;
alter default privileges in schema learn grant execute on functions to admitai_kb_worker;
grant usage, select on all sequences in schema learn to admitai_kb_worker;
alter default privileges in schema learn grant usage, select on sequences to admitai_kb_worker;
