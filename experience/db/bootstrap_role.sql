-- Run once per database server by whoever administers it (on the lab servers: the guide).
-- Creates the login role the Module C API uses. It owns nothing and cannot bypass RLS.
-- Set its password from your .env (never commit it):  ALTER ROLE admitai_app PASSWORD '...';
do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'admitai_app') then
        create role admitai_app login nobypassrls nosuperuser nocreatedb nocreaterole;
    end if;
end $$;
