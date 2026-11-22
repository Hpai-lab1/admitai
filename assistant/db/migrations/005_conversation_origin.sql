-- Who started a conversation. Module A's evaluation runs and operator scripts call Module B
-- directly; their turns must not count as visitors in analytics or become learning signals.
alter table chat.conversations add column origin text not null default 'visitor'
    check (origin in ('visitor', 'evaluation', 'operator'));
-- Earlier evaluation runs, recognisable by Module A's conversation ids (eval-<run>-<case>).
update chat.conversations set origin = 'evaluation' where id like 'eval-%';
