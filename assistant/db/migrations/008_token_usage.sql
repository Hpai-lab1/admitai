-- PRD F9.4: model usage per college. Tokens the model gateway reported for each answer (prompt =
-- what was sent, completion = what it wrote), summed over every model call of the turn (router,
-- answer, retries). Null when the gateway did not report them.
alter table chat.messages add column if not exists prompt_tokens integer;
alter table chat.messages add column if not exists completion_tokens integer;
