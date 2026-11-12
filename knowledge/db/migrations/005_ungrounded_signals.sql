-- Answers that Module B's asynchronous grounding review (PRD F1.4b) found unsupported.
alter table learn.signals drop constraint signals_kind_check;
alter table learn.signals add constraint signals_kind_check check (kind in (
    'thumb_down', 'low_rating', 'correction', 'no_answer', 'blocked', 'partial', 'thumb_up', 'ungrounded'));
