-- The page a programme was read from (knowledge_api/site_kb.py), so the assistant and the college's
-- own pages can link to the source of every catalogue fact. Null for a catalogue imported from CSV.
alter table kb.programs add column if not exists source_url text;
