"""Evaluation Agent: rubric grading by judge models (PRD 9.5, 20).

The deterministic grader (gate.grade) checks citations, facts, refusals and routing; it decides the
gate. This adds what the PRD asks for on top: each answer of a finished run is graded against a
rubric by DeepSeek-R1-Distill-Qwen-14B, cross-checked by DeepSeek-R1-Distill-Llama-8B, and a random
10% (plus every case the judges disagree on) goes to a person for a spot check.

The distills run only in the scheduled window (deploy/README.md): outside it a run is refused
unless forced in development. Models are reached through the shared OpenAI-compatible gateway;
nothing is loaded here.
"""

from __future__ import annotations

import json
import random
import re
from datetime import datetime, time
from typing import Protocol
from zoneinfo import ZoneInfo

import httpx

from .config import Settings
from .db import Jsonb, tx

VERDICTS = ("correct", "partly", "incorrect")
RUBRIC = """You grade answers of a college admissions assistant. Reply with JSON only:
{"verdict": "correct" | "partly" | "incorrect", "reason": "<one sentence>"}

Rules:
- correct: the answer gives the information the question asks for, with every expected fact
  exactly (numbers, dates, names), and claims nothing false. A refusal is correct only when
  the case says the assistant must decline or hand off.
- partly: right but incomplete, or right with an unneeded or unclear extra claim.
- incorrect: a wrong or missing expected fact, an invented fact, answering a question it should
  have declined, or declining one it should have answered.
Ignore style and length."""


class JudgeError(RuntimeError):
    pass


class JudgeModel(Protocol):
    name: str

    def grade(self, prompt: str) -> dict: ...


def parse_verdict(text: str) -> dict:
    """The verdict from a judge's reply. Reasoning models think aloud first (<think>...</think>)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S).strip()
    for m in re.finditer(r"\{[^{}]*\}", text, flags=re.S):
        try:
            data = json.loads(m.group(0))
        except json.JSONDecodeError:
            continue
        v = str(data.get("verdict", "")).strip().lower()
        if v in VERDICTS:
            return {"verdict": v, "reason": str(data.get("reason", ""))[:300]}
    m = re.search(r"\b(correct|partly|incorrect)\b", text.lower())
    if m:
        return {"verdict": m.group(1), "reason": "verdict found outside JSON"}
    raise JudgeError("The judge's reply had no verdict")


class GatewayJudge:
    """A judge model behind the OpenAI-compatible gateway (LiteLLM on the lab, Ollama locally)."""

    def __init__(
        self, settings: Settings, model: str, client: httpx.Client | None = None
    ):
        self.name = model
        self.url = settings.judge_url.rstrip("/") + "/chat/completions"
        self.headers = (
            {"Authorization": f"Bearer {settings.judge_api_key}"}
            if settings.judge_api_key
            else {}
        )
        self.client = client or httpx.Client(timeout=300)

    def grade(self, prompt: str) -> dict:
        try:
            r = self.client.post(
                self.url,
                headers=self.headers,
                json={
                    "model": self.name,
                    "temperature": 0,
                    "max_tokens": 2048,  # reasoning distills think before the verdict
                    "messages": [
                        {"role": "system", "content": RUBRIC},
                        {"role": "user", "content": prompt},
                    ],
                },
            )
            r.raise_for_status()
            content = r.json()["choices"][0]["message"]["content"]
        except (httpx.HTTPError, KeyError, IndexError, ValueError) as exc:
            raise JudgeError(f"Judge {self.name}: {exc.__class__.__name__}") from exc
        return parse_verdict(content)


def in_window(window: str, tz: str, now: datetime | None = None) -> bool:
    """`window` like "22:00-06:00" (may wrap past midnight), in time zone `tz`."""
    if not window.strip():
        return True
    start_s, end_s = (x.strip() for x in window.split("-", 1))
    start, end = time.fromisoformat(start_s), time.fromisoformat(end_s)
    try:
        zone = ZoneInfo(tz)
    except Exception:  # noqa: BLE001 - no time-zone database (Windows without tzdata)
        zone = None
    t = (
        (now or datetime.now(zone)).astimezone(zone).time()
        if zone
        else (now or datetime.now()).time()
    )
    return start <= t < end if start < end else (t >= start or t < end)


def prompt_for(case: dict, result: dict) -> str:
    kind = case.get("kind", "direct")
    expect = {
        "out_of_scope": "The assistant must decline: the answer is not in the college's knowledge.",
        "sensitive": "The assistant must hand this over to a person, not answer it.",
    }.get(kind, "The assistant must answer it.")
    facts = ", ".join(case.get("expected_facts") or []) or "(none listed)"
    return (
        f"Question: {case['question']}\n"
        f"Case: {expect}\n"
        f"Expected facts: {facts}\n"
        f"Assistant outcome: {result.get('outcome')}\n"
        f"Cited sources: {', '.join(result.get('cited') or []) or '(none)'}\n"
        f"Answer: {result.get('answer') or '(no answer)'}"
    )


def judge_scores(results: list[dict]) -> dict:
    graded = [r["judge"] for r in results if r.get("judge")]
    final = [j["final"] for j in graded if j.get("final")]
    both = [j for j in graded if j.get("primary") and j.get("check")]
    return {
        "judge_accuracy": round(sum(v == "correct" for v in final) / len(final), 4)
        if final
        else None,
        "judge_partly": sum(v == "partly" for v in final),
        "judge_agreement": round(
            sum(j["primary"] == j["check"] for j in both) / len(both), 4
        )
        if both
        else None,
        "judge_disputed": sum(j.get("disputed", False) for j in graded),
        "human_pending": sum(
            j.get("spot_check") and not j.get("human") for j in graded
        ),
        "judged": len(graded),
    }


def judge_run(
    settings: Settings,
    tenant: str,
    run_id: str,
    primary: JudgeModel | None = None,
    check: JudgeModel | None = None,
) -> dict:
    """Grade every case of a finished run with both judges and store the verdicts on the run."""
    primary = primary or GatewayJudge(settings, settings.judge_model)
    check = check or GatewayJudge(settings, settings.judge_check_model)
    with tx(settings.database_url, tenant) as cur:
        cur.execute("select * from learn.eval_runs where id::text = %s", (run_id,))
        run = cur.fetchone()
        cur.execute("select * from learn.eval_cases")
        cases = {c["ext_id"]: c for c in cur.fetchall()}
    if run is None or run["status"] != "done":
        raise JudgeError("Only a finished run can be graded")
    results = run["results"] or []
    rng = random.Random(f"spot:{run_id}")
    errors = 0
    for r in results:
        case = cases.get(r["id"])
        if case is None:
            continue
        prompt = prompt_for(case, r)
        j: dict = {}
        for role, model in (("primary", primary), ("check", check)):
            try:
                v = model.grade(prompt)
                j[role], j[f"{role}_reason"] = v["verdict"], v["reason"]
            except JudgeError as exc:
                j[f"{role}_error"] = str(exc)
                errors += 1
        if j.get("primary") and j.get("check"):
            j["disputed"] = j["primary"] != j["check"]
            j["final"] = None if j["disputed"] else j["primary"]
        else:
            j["final"] = j.get("primary") or j.get("check")
            j["disputed"] = False
        # A person checks every disputed case and a random 10% of the rest (PRD 20).
        j["spot_check"] = j["disputed"] or rng.random() < settings.human_spot_share
        j["human"] = None
        r["judge"] = j
    scores = {**(run["scores"] or {}), **judge_scores(results)}
    scores["judge_models"] = [primary.name, check.name]
    scores["judge_errors"] = errors
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "update learn.eval_runs set results = %s, scores = %s where id = %s",
            (Jsonb(results), Jsonb(scores), run["id"]),
        )
    return {
        "run_id": run_id,
        **{k: v for k, v in scores.items() if k.startswith(("judge", "human"))},
    }


def human_grade(
    settings: Settings,
    tenant: str,
    run_id: str,
    case_id: str,
    verdict: str,
    grader: str,
) -> dict:
    """A person's verdict on a spot-checked case; it overrides the judges' final verdict."""
    if verdict not in VERDICTS:
        raise JudgeError(f"verdict must be one of {', '.join(VERDICTS)}")
    with tx(settings.database_url, tenant) as cur:
        cur.execute(
            "select * from learn.eval_runs where id::text = %s for update", (run_id,)
        )
        run = cur.fetchone()
        if run is None:
            raise JudgeError("Run not found")
        results = run["results"] or []
        r = next((x for x in results if x["id"] == case_id), None)
        if r is None or not r.get("judge"):
            raise JudgeError("That case has not been graded by the judges")
        r["judge"]["human"] = {"verdict": verdict, "grader": grader}
        r["judge"]["final"] = verdict
        scores = {**(run["scores"] or {}), **judge_scores(results)}
        cur.execute(
            "update learn.eval_runs set results = %s, scores = %s where id = %s",
            (Jsonb(results), Jsonb(scores), run["id"]),
        )
    return {"case_id": case_id, **r["judge"], **judge_scores(results)}
