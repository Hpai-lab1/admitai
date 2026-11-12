-- Row-Level Security (PRD 11.3): every kb/learn table is visible only for the tenant set with
-- `set local app.tenant_id = ...` in the current transaction. The API connects as admitai_kb,
-- which cannot bypass RLS.

do $$
begin
    if not exists (select 1 from pg_roles where rolname = 'admitai_kb') then
        create role admitai_kb login nobypassrls nosuperuser nocreatedb nocreaterole;
    end if;
end $$;

create or replace function kb.current_tenant() returns uuid language sql stable as $$
    select nullif(current_setting('app.tenant_id', true), '')::uuid
$$;

grant usage on schema kb, learn to admitai_kb;
grant execute on function kb.current_tenant() to admitai_kb;

do $$
declare
    t text;
begin
    foreach t in array array[
        'kb.programs', 'kb.program_fees', 'kb.program_dates', 'kb.sources', 'kb.documents',
        'kb.chunks', 'kb.curated_qa', 'kb.curated_qa_history', 'learn.events'
    ] loop
        execute format('alter table %s enable row level security', t);
        execute format('alter table %s force row level security', t);
        execute format(
            'create policy tenant_isolation on %s using (tenant_id = kb.current_tenant())'
            ' with check (tenant_id = kb.current_tenant())', t);
        execute format('grant select, insert, update, delete on %s to admitai_kb', t);
    end loop;
end $$;

grant usage on all sequences in schema kb, learn to admitai_kb;
