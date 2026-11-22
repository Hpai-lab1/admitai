# What Module B needs from Module A

For Module A's owner. Module B calls Module A only through `assistant_api/knowledge.py`, on the
agreed contract (`contracts/knowledge.openapi.yaml`). Until Module A is ready, B runs against
`mocks/knowledge.py`, which serves `tests/fixtures/demo_college.json`. **The mock's replies are
the reference shapes**: if Module A answers like the mock, B plugs in by URL with no code change.

## Connection

| | |
|---|---|
| Base URL | `KNOWLEDGE_API_URL` (locally `http://127.0.0.1:8100`) |
| Headers on every call | `X-Tenant-Id: <college uuid>` and `X-Service-Key: <KEY_B_TO_A>` |
| Timeout | 5 s per call (`KNOWLEDGE_TIMEOUT_S`), 2 s to connect; retried once only if the connection failed |
| `404` | "not found": no facts for that programme, or no programme list (see 3) |
| Any other error, a timeout or a reply that is not JSON | B tells the visitor it has no reliable source and offers a person. It never guesses. |

Module A must check the key and limit every read to the tenant in `X-Tenant-Id`.

## 1. `POST /kb/search` (agreed)

B sends:

```json
{"query": "What is the hostel fee?", "top_k": 6, "role": "admission",
 "department": "Computer Science", "academic_year": "2027-28"}
```

`role` is `admission` or `faculty`. `department` and `academic_year` are sent only when B knows
them. A long question is cut to 2,000 characters.

B expects (best first):

```json
{
  "items": [
    {
      "id": "03cb0fba-…",
      "kind": "curated_qa",
      "content": "Q: Is there a hostel for first-year students?\nA: Yes. …",
      "score": 0.0312,
      "vector_score": 0.83,
      "citation": {
        "source_type": "curated_qa", "title": "Is there a hostel for first-year students?",
        "heading_path": "Hostel", "document_id": null, "qa_id": "03cb0fba-…", "page": null,
        "academic_year": "2027-28", "department": null, "url": null
      }
    }
  ],
  "embedder": "primary"
}
```

Points that matter:

- **`vector_score` is the cosine similarity (0 to 1) between the question and the item**, not the
  fused ranking score. B uses the top 4 items and drops any below 0.50 (0.40 when `embedder` is
  `multilingual`). If only `score` is sent, B treats it as the similarity. A reciprocal-rank-fusion
  score of about 0.03 would then drop every result, and B would answer "I don't know" every time.
- `score` is any ranking score, higher is better. B sorts by it.
- `content` is the text B is allowed to quote. Every number and date in an answer must appear in a
  cited `content` (or in the catalogue facts below), or B removes the sentence. So keep fees and
  dates as they are written in the chunks.
- `kind` is `chunk` or `curated_qa`. `citation.title` is what the visitor sees on the citation chip.
- `embedder` is `multilingual` when the multilingual model answered (Hindi, Kannada). Otherwise it
  is `primary`.

## 2. `GET /catalogue/programs/{program_id}/facts` (agreed)

B calls this with the programme's **`code`** from the list (for example `BTECH-CSE`). Please
accept the code as well as the id. Return `404` for a programme you do not know.

```json
{
  "id": "abbd4ec5-…", "code": "BTECH-CSE", "name": "B.Tech Computer Science and Engineering",
  "aliases": ["CSE", "Computer Science", "B.Tech CS"],
  "department": "Computer Science", "level": "UG", "academic_year": "2027-28",
  "duration_months": 48, "intake": 120,
  "eligibility": "10+2 with Physics, Chemistry and Mathematics, at least 50% …",
  "contact": "admissions@demo-college.example",
  "fees": [{"category": "general", "component": "tuition", "amount": 250000,
            "currency": "INR", "period": "per year"}],
  "dates": [{"label": "Last date to apply", "date": "2027-06-30"}],
  "citation": {"source_type": "catalogue", "title": "Program catalogue 2027-28: …",
               "academic_year": "2027-28"}
}
```

- `amount` is a number, exact, never a string such as "2.5 lakh". B quotes it exactly.
- `component` is one of: tuition, hostel, application, mess, transport, exam, registration,
  development, library, laboratory, deposit, uniform. `category` is one of: general, management,
  nri, government, sc, st, obc, ews. B checks that a number in an answer is the fee of the
  component and category the sentence names. A word outside these lists still works, but that
  check is weaker for it.
- `dates[].date` is ISO `YYYY-MM-DD`. `label` is free text ("Applications open", "Last date to
  apply", "Classes begin").

## 3. `GET /catalogue/programs?facts=true` (needs agreement)

B needs the programme list to recognise "CSE" or "Mechanical" in a question. It matches on name,
code and aliases, and keeps the list for 60 seconds. This endpoint is **not in the frozen contract
yet**. It is proposed in `docs/proposed-contracts/knowledge.openapi.json`, and the guide decides.

```json
{"items": [ /* programmes as in 2; with facts=true each includes fees and dates */ ],
 "general_dates": [{"label": "Applications open", "date": "2027-01-15", "academic_year": "2027-28"}]}
```

If it returns `404`, B keeps working from search alone, but it cannot quote exact catalogue fees,
and the trace says why.

## What Module A may call on Module B

Use `X-Service-Key: <KEY_A_TO_B>` and `X-Tenant-Id`. Anything else is refused with `403`.

| Call | What for |
|---|---|
| `POST /chat/{conversation_id}/messages` | Headless evaluation runs (SSE reply, same as the widget) |
| `GET /signals` | Implicit feedback signals for the learning loop |
| `GET /messages/{id}`, `GET /reviews/…`, `POST /reviews/run`, `POST /reviews/messages` | Answers and grounding reviews |
| `GET` / `PUT /tenant-settings`, `DELETE /tenant-data` | Agent settings from the Tenant Pack; deleting a sandbox after an evaluation |
| `POST /voice/eval-result` `{chat_accuracy, voice_accuracy, cases, run_id}` | Voice vs chat on the test set (F2.10). More than 3 points worse moves voice to the answer model. |

## Testing Module A against Module B

1. Use the same `KEY_B_TO_A` value in both modules' environments, and start A on 8100.
2. Start B with `KNOWLEDGE_API_URL=http://127.0.0.1:8100`.
3. From the repository root, with A, B and C running: `python assistant/scripts/e2e_live.py`.
   It runs 20 checks against the real modules, including exact fees from A's catalogue.
4. To compare shapes without B running, start the mock from `assistant/` with
   `MOCK_KB_TENANTS` set to `*` and then `python -m uvicorn mocks.knowledge:app --port 8151`.
   Send the same request to the mock and to your service, and compare the JSON.
