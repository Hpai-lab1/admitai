-- Second embedding for questions written in Indic scripts (PRD F1.6 Hindi in L1; 9.4 lists bge-m3
-- as the multilingual embedder). nomic-embed-text is English-only: on the RUAS set it found the
-- right FAQ for 1/10 Hindi questions. Null until filled (python -m knowledge_api.pack reembed).
alter table kb.chunks add column embedding_ml real[];
alter table kb.curated_qa add column embedding_ml real[];
