-- PRD 12.3: rate limits shared by every API process. Kept in memory, each process counted on its
-- own, so with N processes a guesser got N times the attempts. Keys are HMACs (ratelimit.py) of
-- what is limited, for example the network address, so no address or e-mail is stored readable.
create table if not exists app.rate_hits (
    key  text        not null,
    at   timestamptz not null default clock_timestamp()
);
create index if not exists rate_hits_key_at on app.rate_hits (key, at);

-- Row-Level Security with no policies: the API role can neither read nor write rows directly. Only
-- the functions below touch the table; they run as its owner, who is not subject to (unforced) RLS.
alter table app.rate_hits enable row level security;
revoke all on app.rate_hits from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        revoke all on app.rate_hits from admitai_app;
    end if;
end $$;

-- Seconds until `k` may try again (0: now). With `take`, an allowed try is counted at once, under a
-- per-key lock, so two processes cannot both take the last place.
create or replace function app.rate_wait(k text, window_s int, lim int, take boolean)
returns int language plpgsql volatile security definer set search_path = app, pg_temp as
$$
declare
    n int;
    oldest timestamptz;
begin
    perform pg_advisory_xact_lock(hashtextextended(k, 0));
    delete from app.rate_hits
     where key = k and at < clock_timestamp() - make_interval(secs => window_s);
    select count(*), min(at) into n, oldest from app.rate_hits where key = k;
    if n >= lim then
        return greatest(1, ceil(extract(epoch from
            oldest + make_interval(secs => window_s) - clock_timestamp()))::int);
    end if;
    if take then
        insert into app.rate_hits (key) values (k);
    end if;
    return 0;
end $$;

-- Count one failure for `k`; now and then, drop rows older than a day for keys that went quiet.
create or replace function app.rate_fail(k text)
returns void language plpgsql volatile security definer set search_path = app, pg_temp as
$$
begin
    insert into app.rate_hits (key) values (k);
    if random() < 0.01 then
        delete from app.rate_hits where at < clock_timestamp() - interval '1 day';
    end if;
end $$;

create or replace function app.rate_clear(k text)
returns void language sql volatile security definer set search_path = app, pg_temp as
$$ delete from app.rate_hits where key = k $$;

revoke all on function app.rate_wait(text, int, int, boolean), app.rate_fail(text),
    app.rate_clear(text) from public;
do $$
begin
    if exists (select 1 from pg_roles where rolname = 'admitai_app') then
        grant execute on function app.rate_wait(text, int, int, boolean), app.rate_fail(text),
            app.rate_clear(text) to admitai_app;
    end if;
end $$;
