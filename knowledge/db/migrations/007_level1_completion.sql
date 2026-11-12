-- Completing Module A for Level 1 (November 2026).
--
-- F8.3  implicit signals from Module C: rephrased question, "talk to a human", abandonment,
--       voice hang-up and barge-in.
-- 8.3   approval SLA: a named delegate first, then the College Admin; a monthly second review
--       of a random 10% of approved proposals.
-- F10.3 launch test cases generated from the program catalogue (marked, so they never count
--       towards the 150 real questions).
-- 20    rubric grading by the judge models, human spot checks, and model comparisons.

alter table learn.signals drop constraint signals_kind_check;
alter table learn.signals add constraint signals_kind_check check (kind in (
    'thumb_down', 'low_rating', 'correction', 'no_answer', 'blocked', 'partial', 'thumb_up', 'ungrounded',
    'rephrase', 'talk_to_human', 'abandoned', 'voice_hangup', 'barge_in'));
alter table learn.signals add column channel text;

-- Named delegates per department ({"default": "...", "<department>": "..."}).
alter table learn.settings add column delegates jsonb not null default '{}';

-- 0 = with the owning approver, 1 = with the named delegate, 2 = with the College Admin.
alter table learn.proposals add column escalation_level int not null default 0;
alter table learn.proposals add column assigned_to text;
-- Monthly approval-quality check: null = not sampled; pending | agreed | disagreed.
alter table learn.proposals add column second_review text
    check (second_review in ('pending', 'agreed', 'disagreed'));
alter table learn.proposals add column second_review_month text;
alter table learn.proposals add column second_reviewer text;
alter table learn.proposals add column second_review_note text;
alter table learn.proposals add column second_reviewed_at timestamptz;
-- Escalated proposals already in the database were with the College Admin.
update learn.proposals set escalation_level = 2 where escalated;

alter table learn.eval_cases drop constraint eval_cases_source_check;
alter table learn.eval_cases add constraint eval_cases_source_check
    check (source in ('pack', 'proposal', 'staff', 'catalogue'));

-- One row per model comparison (PRD 20: same test set, several answer models).
create table learn.comparisons (
    id           uuid primary key default gen_random_uuid(),
    tenant_id    uuid not null,
    models       text[] not null,
    status       text not null default 'running' check (status in ('running', 'done', 'error')),
    report       jsonb,
    error        text,
    requested_by text,
    started_at   timestamptz not null default now(),
    finished_at  timestamptz
);
alter table learn.comparisons enable row level security;
alter table learn.comparisons force row level security;
create policy tenant_isolation on learn.comparisons
    using (tenant_id = kb.current_tenant()) with check (tenant_id = kb.current_tenant());
grant select, insert, update, delete on learn.comparisons to admitai_kb;
