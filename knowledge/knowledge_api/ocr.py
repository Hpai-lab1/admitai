"""OCR for scanned PDFs (PRD F4.1, 9.4: Tesseract OCR).

A scanned PDF has pictures of pages instead of text. Each page's images are passed to the
`tesseract` command (installed in Module A's container image; nothing is downloaded at run time).
Without Tesseract, scanned PDFs are kept for review as before.

Settings: KB_TESSERACT (path, default `tesseract` on PATH), KB_OCR_LANGS (default `eng`; e.g.
`eng+hin` when the Hindi language data is installed), KB_OCR_MAX_PAGES (default 60).
"""

from __future__ import annotations

import io
import os
import shutil
import subprocess


class OCRUnavailable(RuntimeError):
    pass


def binary() -> str | None:
    return shutil.which(os.environ.get("KB_TESSERACT", "tesseract"))


def available() -> bool:
    return binary() is not None


def _image_text(png: bytes, langs: str, exe: str) -> str:
    try:
        out = subprocess.run(
            [exe, "stdin", "stdout", "-l", langs, "--psm", "3"],
            input=png,
            capture_output=True,
            timeout=120,
            check=True,
        )
    except (subprocess.SubprocessError, OSError):
        return ""
    return out.stdout.decode("utf-8", "replace")


def pdf_pages(data: bytes) -> list[tuple[int, str]]:
    """[(page number, recognised text)] for a scanned PDF."""
    exe = binary()
    if exe is None:
        raise OCRUnavailable("Tesseract is not installed")
    from pypdf import PdfReader

    langs = os.environ.get("KB_OCR_LANGS", "eng")
    max_pages = int(os.environ.get("KB_OCR_MAX_PAGES", "60"))
    reader = PdfReader(io.BytesIO(data))
    pages: list[tuple[int, str]] = []
    for i, page in enumerate(reader.pages[:max_pages]):
        texts = []
        try:
            images = list(page.images)
        except Exception:  # noqa: BLE001 - an unreadable image is skipped, not fatal
            images = []
        for img in images:
            try:
                buf = io.BytesIO()
                img.image.convert("L").save(buf, format="PNG")
            except Exception:  # noqa: BLE001
                continue
            texts.append(_image_text(buf.getvalue(), langs, exe))
        pages.append((i + 1, "\n\n".join(t for t in texts if t.strip())))
    return pages
