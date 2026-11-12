-- PRD 8.3 peak-season mode and 8.4 abuse resistance.
--
-- Peak season (a per-college switch): only wrong-fact proposals reach the experts; lower-severity
-- ones are held, not escalated, and come back when the switch is turned off.
-- Bursts: many negative ratings of the same question within an hour are rate-limited. Signals over
-- the limit are kept for the record but marked 'suspected_abuse' and never become proposals; the
-- weekly report shows them as an anomaly.

create table learn.settings (
    tenant_id    uuid primary key,
    peak_season  boolean not null default false,
    updated_by   text,
    updated_at   timestamptz not null default now()
);
alter table learn.settings enable row level security;
alter table learn.settings force row level security;
create policy tenant_isolation on learn.settings
    using (tenant_id = kb.current_tenant()) with check (tenant_id = kb.current_tenant());
grant select, insert, update, delete on learn.settings to admitai_kb;

alter table learn.signals drop constraint signals_verdict_check;
alter table learn.signals add constraint signals_verdict_check check (verdict in (
    'unreviewed', 'wrong', 'correct_but_unwelcome', 'other', 'suspected_abuse'));
