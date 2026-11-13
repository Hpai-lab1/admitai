"""Extractive stand-in for the model gateway (LiteLLM / Ollama), for running Module B end to end
where no model is available (CI, a laptop without Ollama). It is not a language model.

It speaks the OpenAI chat-completions API that LiteLLM and Ollama speak, streaming included, and
answers an answer prompt by copying the one source sentence that shares the most words with the
visitor's question, with that source's number: "Hostel fee (general category): 90000 INR per year
[1]." Nothing shares a topic word -> NO_ANSWER. Router prompts get ADMISSION or FACULTY by keyword;
anything else (e.g. the grounding review's quote test) gets NONE.

So it exercises everything around the model (routing, retrieval, sentence checks, streaming,
handoffs) with plausible, source-faithful text. It says nothing about answer quality.

    uvicorn mocks.llm_gateway:app --host 127.0.0.1 --port 8301      (from assistant/)
    LLM_BASE_URL=http://127.0.0.1:8301/v1

POST /_script {"match": "<text in the question>", "reply": "<exact reply>"} makes the stand-in
give a fixed reply (e.g. a deliberately wrong fee, to see the sentence checks block it live);
DELETE /_script clears the list.
"""

from __future__ import annotations

import json
import re
import time
import uuid

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse

app = FastAPI(title="Extractive stand-in for the model gateway (not a model)")
SCRIPT: list[tuple[str, str]] = []
CALLS: list[dict] = []
_GENERIC = set(
    "the and for are with this that from you your can will have has was were our its not but all any "
    "what when where how which who does did is do there about tell please want know give get some "
    "much many also college university program programme course courses student students admission "
    "admissions".split()
)
_FACULTY = re.compile(
    r"syllabus|curriculum|subject|lab|elective|career|placement|project", re.I
)


def _words(text: str) -> set[str]:
    return {
        (w[:-1] if len(w) > 3 and w.endswith("s") else w)
        for w in re.findall(r"[a-z]{3,}", text.lower())
        if w not in _GENERIC
    }


def _sources(prompt: str) -> list[tuple[int, str]]:
    block = prompt.split("Sources:\n", 1)[-1].split("\n\nVisitor's question:", 1)[0]
    out = []
    for m in re.finditer(
        r"^\[(\d+)\] \([^\n]*\)\n(.*?)(?=\n\n\[\d+\] \(|\Z)", block, re.S | re.M
    ):
        out.append((int(m.group(1)), m.group(2).strip()))
    return out


def answer(prompt: str) -> str:
    question = prompt.split("Visitor's question:", 1)[1].split("\n\n", 1)[0].strip()
    for match, reply in SCRIPT:
        if match.lower() in question.lower():
            return reply
    q = _words(question)
    best = None
    for n, text in _sources(prompt):
        for sentence in re.split(r"(?<=[.!?])\s+(?=[A-Z0-9])", text):
            sentence = sentence.strip()
            if len(sentence.split()) < 3:
                continue
            overlap = len(q & _words(sentence))
            key = (overlap, bool(re.search(r"\d", sentence)), -n)
            if overlap and (best is None or key > best[0]):
                best = (key, n, sentence)
    if best is None:
        return "NO_ANSWER"
    _, n, sentence = best
    return f"{sentence.rstrip('.')} [{n}]."


def reply_for(messages: list[dict]) -> str:
    prompt = messages[-1]["content"] if messages else ""
    if prompt.startswith("Classify the admission enquiry"):
        return (
            "FACULTY"
            if _FACULTY.search(prompt.split("Enquiry:", 1)[-1])
            else "ADMISSION"
        )
    if "Visitor's question:" in prompt:
        return answer(prompt)
    return "NONE"


@app.post("/v1/chat/completions")
async def completions(request: Request):
    body = await request.json()
    reply = reply_for(body.get("messages", []))
    CALLS.append({"model": body.get("model"), "reply": reply, "at": time.time()})
    cid = f"chatcmpl-{uuid.uuid4().hex[:12]}"
    if not body.get("stream"):
        return JSONResponse(
            {
                "id": cid,
                "object": "chat.completion",
                "model": body.get("model"),
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": reply},
                        "finish_reason": "stop",
                    }
                ],
            }
        )

    def events():
        for i in range(0, len(reply), 8):
            chunk = {
                "id": cid,
                "object": "chat.completion.chunk",
                "choices": [{"index": 0, "delta": {"content": reply[i : i + 8]}}],
            }
            yield f"data: {json.dumps(chunk)}\n\n"
        yield "data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


@app.post("/_script")
async def add_script(request: Request):
    body = await request.json()
    SCRIPT.insert(0, (body["match"], body["reply"]))
    return {"scripted": len(SCRIPT)}


@app.delete("/_script")
def clear_script():
    SCRIPT.clear()
    return {"scripted": 0}


@app.get("/_calls")
def calls():
    return {"calls": CALLS[-50:]}


@app.get("/health")
def health():
    return {"status": "ok", "module": "llm-gateway-stand-in"}
