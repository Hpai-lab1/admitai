"""Tenant Pack import, validation, readiness and launch verdict (PRD F10.1-F10.3, F10.6)."""

import io
import zipfile
from pathlib import Path

import psycopg

from conftest import TEST_URL, needs_db
from knowledge_api import tenant_pack

pytestmark = needs_db
DEMO = Path(__file__).resolve().parents[2] / "packs" / "demo-college"


def zipped(
    root: Path = DEMO,
    extra: dict | None = None,
    drop: set | None = None,
    prefix: str = "demo-college/",
) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for f in root.rglob("*"):
            rel = f.relative_to(root).as_posix()
            if f.is_file() and rel not in (drop or set()):
                zf.write(f, prefix + rel)
        for name, content in (extra or {}).items():
            zf.writestr(name, content)
    return buf.getvalue()


def h(tenant):
    return {"X-Tenant-Id": tenant}


def post_pack(client, tenant, data):
    return client.post(
        "/tenant-pack",
        headers=h(tenant),
        files={"file": ("pack.zip", data, "application/zip")},
    )


def test_pack_imports_and_reports_readiness(lclient, tenant):
    r = post_pack(lclient, tenant, zipped())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["college"]["slug"] == "demo-college"
    assert body["loaded"]["documents"] == 5 and body["loaded"]["eval_cases"] == 25
    items = {i["key"]: i for i in body["readiness"]}
    assert (
        items["fees"]["ok"]
        and items["dates"]["ok"]
        and items["eligibility"]["ok"]
        and items["owners"]["ok"]
    )
    # 25 questions: below the PRD's 150 real questions unless the pack sets its own minimum
    assert not items["test_set"]["ok"] and "needs 150" in items["test_set"]["detail"]
    ok = lclient.get(
        "/tenant-pack/readiness?min_test_questions=20", headers=h(tenant)
    ).json()["items"]
    assert all(i["ok"] for i in ok if i["blocking"])
    with psycopg.connect(TEST_URL) as conn:
        conn.execute("select set_config('app.tenant_id', %s, false)", (tenant,))
        assert (
            conn.execute(
                "select count(*) from kb.chunks where embedding_ml is not null"
            ).fetchone()[0]
            == 0
        )


def test_a_pack_with_problems_loads_nothing_and_lists_them_all(lclient, tenant):
    bad = zipped(
        extra={
            "demo-college/catalogue/fees.csv": "program_code,category,component,amount\nNOPE,general,tuition,abc\n",
            "demo-college/curated_qa.csv": "question,answer,academic_year,valid_to\nQ?,,2027-28,soon\n",
            "demo-college/documents/virus.exe": "MZ",
        },
        drop={"catalogue/fees.csv", "curated_qa.csv"},
    )
    r = post_pack(lclient, tenant, bad)
    assert r.status_code == 422
    problems = " | ".join(r.json()["problems"])
    for expected in (
        "unknown program_code 'NOPE'",
        "'abc' is not a number",
        "question and answer are required",
        "valid_to 'soon' is not a date",
    ):
        assert expected in problems
    assert (
        lclient.get("/kb/documents", headers=h(tenant)).json()["items"] == []
    )  # nothing half-imported


def test_unsafe_or_foreign_zip_content_is_refused(lclient, tenant):
    for name in ("../evil.txt", "demo-college/../../evil.txt"):
        r = post_pack(lclient, tenant, zipped(extra={name: "x"}))
        assert r.status_code == 422 and "Unsafe path" in str(r.json())
    r = post_pack(lclient, tenant, zipped(extra={"demo-college/notes/secret.txt": "x"}))
    assert r.status_code == 422 and "Unexpected file" in str(r.json())
    r = post_pack(lclient, tenant, b"not a zip")
    assert r.status_code == 422 and "not a zip" in str(r.json())
    r = post_pack(lclient, tenant, zipped(drop={"pack.yaml"}))
    assert r.status_code == 422 and "pack.yaml is missing" in str(r.json())


def test_readiness_names_what_is_missing(lclient, tenant):
    fees = (DEMO / "catalogue" / "fees.csv").read_text().splitlines()
    without_mba = "\n".join(line for line in fees if not line.startswith("MBA,")) + "\n"
    r = post_pack(
        lclient,
        tenant,
        zipped(
            extra={"demo-college/catalogue/fees.csv": without_mba},
            drop={"catalogue/fees.csv"},
        ),
    )
    items = {i["key"]: i for i in r.json()["readiness"]}
    assert (
        not items["fees"]["ok"]
        and items["fees"]["detail"] == "Master of Business Administration"
    )


def test_launch_verdict():
    good = {"accuracy": 0.9, "refusal": 1.0}
    assert tenant_pack.launch_verdict(
        good, [{"id": "a", "critical": True, "passed": True}]
    )["passed"]
    v = tenant_pack.launch_verdict(
        {"accuracy": 0.7, "refusal": 0.5},
        [{"id": "c1", "critical": True, "passed": False}],
    )
    assert not v["passed"] and len(v["reasons"]) == 3


def test_launch_run_reports_its_verdict(lclient, assistant, tenant):
    post_pack(lclient, tenant, zipped())
    r = lclient.post(
        "/evals/runs", headers=h(tenant), json={"label": "launch", "limit": 5}
    )
    assert r.status_code == 202
    got = lclient.get(f"/tenant-pack/launch/{r.json()['id']}", headers=h(tenant)).json()
    cases = lclient.get("/evals/cases", headers=h(tenant)).json()["items"]
    critical = sum(
        c["critical"] for c in cases
    )  # pack cases + catalogue tuition-fee cases
    assert critical == 14
    assert (
        got["status"] == "done"
        and "passed" in got
        and got["scores"]["cases"] == critical
    )  # the limit never drops critical cases


def test_duplicate_entries_in_the_zip_are_refused(lclient, tenant):
    import warnings

    with warnings.catch_warnings():
        warnings.simplefilter(
            "ignore"
        )  # zipfile itself warns about the duplicate we are creating
        dup = zipped(
            extra={
                "demo-college/curated_qa.csv": "question,answer,academic_year,valid_to\n"
            }
        )
    r = post_pack(lclient, tenant, dup)
    assert r.status_code == 422 and "appears twice" in str(r.json())
