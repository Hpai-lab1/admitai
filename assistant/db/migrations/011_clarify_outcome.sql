-- "Did you mean ...?" (suggest.py): the assistant asked the visitor to confirm a programme name it
-- may have misheard. Not a knowledge gap, so it is not 'no_answer' (Module A's learning signals
-- read no_answer, blocked and partial turns).
alter table chat.messages drop constraint if exists messages_outcome_check;
alter table chat.messages add constraint messages_outcome_check
    check (outcome in ('answered', 'partial', 'no_answer', 'blocked', 'handoff', 'refused', 'error', 'clarify'));
