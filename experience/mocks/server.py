"""Stand-ins for Module A (Knowledge & Learning) and Module B (Assistant) until the week-4
walking skeleton. Fake data only. Run:

    uvicorn mocks.server:app --host 127.0.0.1 --port 8230

Module A is mounted at /a and Module B at /b, matching KNOWLEDGE_API_URL / ASSISTANT_API_URL.
"""

from __future__ import annotations

import asyncio
import json
import re
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse

app = FastAPI(title="AdmitAI mocks for Module C (A at /a, B at /b)")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8210", "http://localhost:8210"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ------------------------------------------------------------------ Module A
PROPOSALS: dict[str, dict] = {}
EVENTS: list[dict] = []


def _seed_proposals() -> None:
    PROPOSALS.clear()
    rows = [
        (
            "curated_answer",
            None,
            0.92,
            "Hostel fee question answered wrongly 14 times",
            "Hostel fee for 2026-27 is listed in the fee notice, section 4.",
        ),
        (
            "document_update",
            None,
            0.81,
            "Fee notice 2025-26 still used for 2026-27 questions",
            "Mark 'Fee notice 2025-26' as outdated.",
        ),
        (
            "curated_answer",
            "Computer Science",
            0.74,
            "Lateral entry eligibility asked often, no answer",
            "Diploma holders with 45% may join the second year (lateral entry).",
        ),
        (
            "synonym",
            None,
            0.40,
            "Visitors say 'CET' meaning the state entrance test",
            "Add synonym CET -> KCET",
        ),
        (
            "curated_answer",
            "Mechanical",
            0.33,
            "Workshop timings question",
            "Workshops run 09:00-13:00.",
        ),
    ]
    for kind, dept, impact, title, proposal in rows:
        pid = str(uuid4())
        PROPOSALS[pid] = {
            "id": pid,
            "kind": kind,
            "department": dept,
            "impact_score": impact,
            "title": title,
            "proposal": proposal,
            "evidence_count": int(impact * 20),
            "status": "pending",
        }


_seed_proposals()


@app.get("/a/improvements")
def improvements(status: str | None = None):
    items = [p for p in PROPOSALS.values() if status is None or p["status"] == status]
    return {"items": sorted(items, key=lambda p: p["impact_score"], reverse=True)}


@app.post("/a/improvements/{pid}/{decision}")
def decide(pid: str, decision: str, body: dict):
    if pid not in PROPOSALS or decision not in ("approve", "reject"):
        raise HTTPException(404, "unknown proposal")
    PROPOSALS[pid]["status"] = "approved" if decision == "approve" else "rejected"
    PROPOSALS[pid]["decided_by"] = body.get("decided_by")
    return PROPOSALS[pid]


@app.post("/a/_reset")
def reset():
    _seed_proposals()
    EVENTS.clear()
    return {"ok": True}


@app.get("/a/kb/documents")
def documents():
    return {
        "items": [
            {
                "id": "doc-1",
                "title": "Prospectus 2026-27",
                "status": "indexed",
                "academic_year": "2026-27",
                "owner": "Admissions",
                "chunks": 212,
            },
            {
                "id": "doc-2",
                "title": "Fee notice 2025-26",
                "status": "outdated",
                "academic_year": "2025-26",
                "owner": "Finance",
                "chunks": 18,
            },
            {
                "id": "doc-3",
                "title": "Hostel rules",
                "status": "indexed",
                "academic_year": "2026-27",
                "owner": "Student Affairs",
                "chunks": 34,
            },
        ]
    }


@app.get("/a/curated-qa")
def curated():
    return {
        "items": [
            {
                "id": "qa-1",
                "question": "Is there a hostel for first-year students?",
                "answer": "Yes. Separate hostels for men and women; apply after admission.",
                "status": "published",
            },
            {
                "id": "qa-2",
                "question": "What is the last date to apply?",
                "answer": "See the admissions calendar for 2026-27.",
                "status": "draft",
            },
        ]
    }


@app.post("/a/events")
def events(body: dict):
    if body.get("event_id") not in {
        e.get("event_id") for e in EVENTS
    }:  # at-least-once: de-duplicate
        EVENTS.append(body)
    return {"ok": True}


@app.get("/a/events")
def list_events():
    return {"items": EVENTS}


# ------------------------------------------------------------------ Module B
ANSWERS = [  # fake facts for the demo college
    (
        r"\bug\b|undergrad|b\.?tech",
        "We offer B.Tech in Computer Science, Mechanical and Civil Engineering "
        "(4 years). Admission is through the entrance test rank and 12th marks.",
        "Prospectus 2026-27",
        0.84,
    ),
    (
        r"\bpg\b|post ?grad|m\.?tech|mba",
        "PG programmes: M.Tech (2 years) and MBA (2 years). "
        "You need a relevant bachelor's degree with at least 50%.",
        "Prospectus 2026-27",
        0.8,
    ),
    (
        r"lateral",
        "Diploma holders with at least 45% can join B.Tech directly in the second year "
        "(lateral entry).",
        "Prospectus 2026-27",
        0.77,
    ),
    (
        r"process|how (do|to) apply|apply",
        "Apply online, upload your marks cards, pay the application fee, "
        "then attend counselling. You'll get an e-mail at each step.",
        "Admission process",
        0.82,
    ),
    (
        r"scholarship",
        "Merit scholarships cover up to 50% of tuition for top rank holders; "
        "need-based support is also available.",
        "Scholarship policy 2026-27",
        0.75,
    ),
    (
        r"loan",
        "The college has tie-ups with partner banks for education loans; the admissions office "
        "gives you the documents the bank needs.",
        "Loan assistance",
        0.72,
    ),
    (
        r"hostel",
        "Yes, there are separate hostels for men and women. Rooms are allotted after admission.",
        "Hostel rules",
        0.86,
    ),
    (
        r"fee|cost",
        "The tuition fee for B.Tech in 2026-27 is listed in the fee notice; a sample figure is "
        "Rs 2,50,000 a year.",
        "Fee notice 2026-27",
        0.78,
    ),
    (
        r"last date|deadline",
        "Applications for 2026-27 close on 30 June 2026.",
        "Prospectus 2026-27",
        0.8,
    ),
]


ANSWERS_HI = [  # Hindi versions of a few fake facts (F1.6 demo)
    (
        r"hostel|छात्रावास|हॉस्टल",
        "हाँ, लड़कों और लड़कियों के लिए अलग-अलग छात्रावास हैं। प्रवेश के बाद कमरे दिए जाते हैं।",
        "Hostel rules",
        0.86,
    ),
    (
        r"fee|फीस|शुल्क",
        "2026-27 के लिए B.Tech की ट्यूशन फीस फीस सूचना में दी गई है; उदाहरण राशि Rs 2,50,000 प्रति वर्ष है।",
        "Fee notice 2026-27",
        0.78,
    ),
    (
        r"scholarship|छात्रवृत्ति",
        "शीर्ष रैंक वालों को ट्यूशन का 50% तक मेरिट छात्रवृत्ति मिलती है।",
        "Scholarship policy 2026-27",
        0.75,
    ),
    (
        r"\bug\b|b\.?tech|स्नातक",
        "हम कंप्यूटर साइंस, मैकेनिकल और सिविल में B.Tech (4 वर्ष) कराते हैं।",
        "Prospectus 2026-27",
        0.84,
    ),
    (
        r"lateral|लेटरल",
        "45% अंकों वाले डिप्लोमा धारक सीधे दूसरे वर्ष में प्रवेश ले सकते हैं।",
        "Prospectus 2026-27",
        0.77,
    ),
]
HINDI_FALLBACK = "मुझे इसका पक्का उत्तर नहीं पता। क्या मैं प्रवेश टीम से आपसे संपर्क करवाऊँ?"


@app.post("/b/chat/{conversation_id}/messages")
async def chat(conversation_id: str, request: Request):
    body = await request.json()
    text = str(body.get("text", ""))
    answer, source, confidence = None, None, 0.2
    hindi = (
        body.get("language") == "hi" or re.search(r"[\u0900-\u097F]", text) is not None
    )
    for pattern, a, s, c in ANSWERS_HI if hindi else ANSWERS:
        if re.search(pattern, text, re.I):
            answer, source, confidence = a, s, c
            break
    handoff = answer is None
    if handoff:
        answer = (
            HINDI_FALLBACK
            if hindi
            else (
                "I'm not sure about that one. I can ask someone from the admissions team to contact you. "
                "Would you like that?"
            )
        )
    message_id = str(uuid4())

    async def stream():
        for word in answer.split(" "):
            yield f"event: token\ndata: {json.dumps({'text': word + ' '})}\n\n"
            await asyncio.sleep(0.02)
        done = {
            "message_id": message_id,
            "conversation_id": conversation_id,
            "trace_id": str(uuid4()),
            "confidence": confidence,
            "offer_handoff": handoff,
            "sources": [{"title": source, "academic_year": "2026-27"}]
            if source
            else [],
        }
        yield f"event: done\ndata: {json.dumps(done)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.post("/b/voice/token")
def voice_token():
    return {
        "token": "mock-voice-token",
        "expires_in": 60,
        "note": "mock: voice streaming is Module B's",
    }
