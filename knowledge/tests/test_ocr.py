"""OCR for scanned PDFs (PRD F4.1). The scan is made here from synthetic text; Tesseract must be
installed for the recognition tests (they are skipped otherwise, e.g. in GitHub CI)."""

import io
import os
from pathlib import Path

import pytest

from conftest import needs_db
from knowledge_api import ingest, ocr

LINES = [
    "Hostel Rules 2027-28",
    "The hostel fee is 90000 rupees per year.",
    "Rooms are allotted to first year students",
    "before classes begin in August.",
]
needs_tesseract = pytest.mark.skipif(
    not ocr.available(), reason="Tesseract not installed"
)


def font():
    from PIL import ImageFont

    for path in (
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ):
        if Path(path).exists():
            return ImageFont.truetype(path, 36)
    pytest.skip("no TrueType font to draw the test scan")


def scanned_pdf(lines=LINES, pages=1) -> bytes:
    """A PDF whose pages are only pictures of text, like a photocopy run through a scanner."""
    from PIL import Image, ImageDraw

    images = []
    for _ in range(pages):
        img = Image.new("RGB", (1240, 1754), "white")
        draw = ImageDraw.Draw(img)
        for i, line in enumerate(lines):
            draw.text((100, 150 + i * 70), line, fill="black", font=font())
        images.append(img)
    buf = io.BytesIO()
    images[0].save(
        buf, format="PDF", save_all=True, append_images=images[1:], resolution=150
    )
    return buf.getvalue()


@needs_tesseract
def test_scanned_pdf_is_read_by_ocr():
    blocks = ingest.parse("hostel-scan.pdf", scanned_pdf(pages=2))
    text = ingest.full_text(blocks)
    assert all(b.ocr for b in blocks)
    assert "90000" in text and "hostel fee" in text.lower()
    assert {b.page for b in blocks} == {1, 2}


def test_without_tesseract_a_scan_waits_for_review(monkeypatch):
    monkeypatch.setenv("KB_TESSERACT", "/nonexistent/tesseract")
    with pytest.raises(ingest.NeedsReview, match="OCR is not available"):
        ingest.parse("scan.pdf", scanned_pdf(lines=["x"]))


@needs_tesseract
def test_a_blank_scan_is_not_served():
    with pytest.raises(ingest.NeedsReview, match="could not read enough text"):
        ingest.parse("blank.pdf", scanned_pdf(lines=[]))


@needs_db
@needs_tesseract
def test_uploaded_scan_is_searchable_after_approval_with_a_warning(client, tenant):
    h = {"X-Tenant-Id": tenant}
    r = client.post(
        "/kb/documents",
        headers=h,
        files={"file": ("hostel-scan.pdf", scanned_pdf())},
        data={"title": "Hostel rules (scan)"},
    )
    assert r.status_code == 202, r.text  # parsed in the background
    d = client.get(f"/kb/documents/{r.json()['id']}", headers=h).json()
    assert d["status"] == "draft" and "OCR" in d["status_reason"]
    assert d["chunks"]
    client.patch(
        f"/kb/documents/{d['id']}",
        headers=h,
        json={"action": "approve", "actor": "officer"},
    )
    hits = client.post(
        "/kb/search", headers=h, json={"query": "What is the hostel fee?"}
    ).json()["items"]
    assert hits and "90000" in hits[0]["content"]


def test_ocr_settings_are_read_from_the_environment(monkeypatch):
    monkeypatch.setenv("KB_TESSERACT", "definitely-not-a-command")
    assert ocr.binary() is None and not ocr.available()
    monkeypatch.delenv("KB_TESSERACT")
    assert ocr.available() == bool(ocr.binary())
    assert os.environ.get("KB_OCR_LANGS", "eng")
