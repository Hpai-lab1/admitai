"""Knowledge Ingestion (PRD F4.1, F4.7): parse -> clean -> semantic chunks with heading paths.

Every parser returns a list of Blocks (a heading or a paragraph, with its page if known). The
chunker packs paragraphs under their heading path up to ~400 tokens with 15% overlap. Nothing
here touches the database, so it is easy to test.
"""

from __future__ import annotations

import csv
import io
import re
import unicodedata
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import PurePath

SUPPORTED = {
    ".pdf": "application/pdf",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".csv": "text/csv",
    ".html": "text/html",
    ".htm": "text/html",
    ".txt": "text/plain",
    ".md": "text/markdown",
}


class IngestError(ValueError):
    """The file cannot be ingested; the message is safe to show to staff."""


class NeedsReview(Exception):
    """Ingested text is too poor to serve (e.g. a scanned PDF without OCR)."""


@dataclass
class Block:
    text: str
    level: int = 0  # 0 = paragraph; 1-3 = heading level
    page: int | None = None
    ocr: bool = False  # text recognised from a scanned page (F4.1)
    # An HTML table cell: (table, row, column, column span, header cell). Lets table_rows() keep a
    # row together with its column headings ("Total Credits: 4") instead of loose numbers.
    cell: tuple[int, int, int, int, bool] | None = None


@dataclass
class Chunk:
    content: str
    heading_path: str
    page: int | None
    ord: int


def mime_for(filename: str) -> str:
    ext = PurePath(filename).suffix.lower()
    if ext not in SUPPORTED:
        raise IngestError(
            f"Unsupported file type '{ext or filename}'. Use PDF, DOCX, PPTX, XLSX, CSV, HTML, TXT or MD."
        )
    return SUPPORTED[ext]


def clean(text: str) -> str:
    text = unicodedata.normalize("NFC", text).replace(" ", " ").replace("­", "")
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # words hyphenated across lines
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


# ------------------------------------------------------------------ parsers


def _plain(text: str, markdown: bool) -> list[Block]:
    blocks: list[Block] = []
    for para in re.split(r"\n\s*\n", text):
        para = para.strip()
        if not para:
            continue
        m = re.match(r"^(#{1,3})\s+(.+)$", para.split("\n", 1)[0]) if markdown else None
        if m:
            blocks.append(Block(m.group(2).strip(), level=len(m.group(1))))
            rest = para.split("\n", 1)[1].strip() if "\n" in para else ""
            if rest:
                blocks.append(Block(clean(rest.replace("\n", " "))))
        else:
            blocks.append(Block(clean(para.replace("\n", " "))))
    return blocks


def _looks_like_heading(line: str) -> bool:
    line = line.strip()
    return (
        2 < len(line) <= 70
        and not line.endswith((".", ",", ";", ":"))
        and (
            line.isupper()
            or re.match(r"^(\d+(\.\d+)*\.?\s+)?[A-Z][^.]*$", line) is not None
        )
        and len(line.split()) <= 9
    )


def _pdf(data: bytes) -> list[Block]:
    from pypdf import PdfReader

    try:
        reader = PdfReader(io.BytesIO(data))
        pages = [(i + 1, p.extract_text() or "") for i, p in enumerate(reader.pages)]
    except Exception as exc:  # pypdf raises many types for broken files
        raise IngestError(
            f"The PDF could not be read ({exc.__class__.__name__})."
        ) from None
    ocr = False
    if _too_little(pages):
        from . import ocr as ocr_mod

        try:
            pages = ocr_mod.pdf_pages(data)
        except ocr_mod.OCRUnavailable:
            raise NeedsReview(
                "Scanned PDF: almost no text found and OCR is not available on this server."
            ) from None
        if _too_little(pages):
            raise NeedsReview(
                "Scanned PDF: OCR could not read enough text. Upload a clearer scan or a text PDF."
            )
        ocr = True
    blocks: list[Block] = []
    for page_no, text in pages:
        para: list[str] = []
        for line in text.splitlines():
            if not line.strip():
                if para:
                    blocks.append(Block(clean(" ".join(para)), page=page_no))
                    para = []
            elif _looks_like_heading(line) and not para:
                blocks.append(Block(line.strip(), level=2, page=page_no))
            else:
                para.append(line.strip())
                if (
                    line.rstrip().endswith((".", ":", "?"))
                    and sum(len(p) for p in para) > 300
                ):
                    blocks.append(Block(clean(" ".join(para)), page=page_no))
                    para = []
        if para:
            blocks.append(Block(clean(" ".join(para)), page=page_no))
    if ocr:
        for b in blocks:
            b.ocr = True
    return blocks


def _too_little(pages: list[tuple[int, str]]) -> bool:
    return sum(len(t.strip()) for _, t in pages) < 40 * max(1, len(pages)) // 4


def _docx(data: bytes) -> list[Block]:
    import docx

    try:
        document = docx.Document(io.BytesIO(data))
    except Exception as exc:
        raise IngestError(
            f"The DOCX could not be read ({exc.__class__.__name__})."
        ) from None
    blocks: list[Block] = []
    for p in document.paragraphs:
        text = p.text.strip()
        if not text:
            continue
        style = (p.style.name or "").lower() if p.style is not None else ""
        m = re.match(r"heading (\d)", style)
        blocks.append(
            Block(text, level=min(int(m.group(1)), 3)) if m else Block(clean(text))
        )
    for table in document.tables:
        for row in table.rows:
            cells = [c.text.strip() for c in row.cells]
            if any(cells):
                blocks.append(Block(" | ".join(cells)))
    return blocks


def _pptx(data: bytes) -> list[Block]:
    from pptx import Presentation

    try:
        deck = Presentation(io.BytesIO(data))
    except Exception as exc:
        raise IngestError(
            f"The PPTX could not be read ({exc.__class__.__name__})."
        ) from None
    blocks: list[Block] = []
    for i, slide in enumerate(deck.slides, start=1):
        title = (
            slide.shapes.title.text.strip()
            if slide.shapes.title is not None
            else f"Slide {i}"
        )
        blocks.append(Block(title, level=2, page=i))
        for shape in slide.shapes:
            if shape.has_text_frame and shape != slide.shapes.title:
                text = clean(shape.text_frame.text)
                if text:
                    blocks.append(Block(text, page=i))
    return blocks


def _rows(rows: list[list[str]], sheet: str | None = None) -> list[Block]:
    """Tables become one sentence-like line per row ("Column: value; ..."), so numbers keep their labels."""
    rows = [r for r in rows if any(c.strip() for c in r)]
    if not rows:
        return []
    header, body = rows[0], rows[1:]
    blocks = [Block(sheet, level=2)] if sheet else []
    for r in body:
        pairs = [f"{h.strip()}: {v.strip()}" for h, v in zip(header, r) if v.strip()]
        if pairs:
            blocks.append(Block("; ".join(pairs) + "."))
    return blocks


def _xlsx(data: bytes) -> list[Block]:
    from openpyxl import load_workbook

    try:
        wb = load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    except Exception as exc:
        raise IngestError(
            f"The XLSX could not be read ({exc.__class__.__name__})."
        ) from None
    blocks: list[Block] = []
    for ws in wb.worksheets:
        rows = [
            ["" if c is None else str(c) for c in row]
            for row in ws.iter_rows(values_only=True)
        ]
        blocks.extend(_rows(rows, ws.title))
    return blocks


class _HTMLText(HTMLParser):
    SKIP = {"script", "style", "nav", "footer", "header", "noscript", "svg", "form"}
    BLOCK = {"p", "li", "div", "section", "article", "tr", "br", "td", "th", "dd", "dt"}

    def __init__(self) -> None:
        super().__init__()
        self.blocks: list[Block] = []
        self.buf: list[str] = []
        self.skip = 0
        self.heading: int = 0
        self.title = ""
        self._in_title = False
        self.links: list[str] = []
        self.tables: list[dict] = []  # open tables, innermost last
        self.n_tables = 0
        self.cell: tuple[int, int, int, int, bool] | None = None

    def _flush(self) -> None:
        text = clean(" ".join(self.buf))
        if text:
            self.blocks.append(Block(text, level=self.heading, cell=self.cell))
        self.buf = []

    def _table_tag(self, tag: str, attrs) -> None:
        if tag == "table":
            self.tables.append(
                {"id": self.n_tables, "row": -1, "col": 0, "head": False}
            )
            self.n_tables += 1
            return
        if not self.tables:
            return
        t = self.tables[-1]
        if tag == "thead":
            t["head"] = True
        elif tag in ("tbody", "tfoot"):
            t["head"] = False
        elif tag == "tr":
            t["row"], t["col"] = t["row"] + 1, 0
        elif tag in ("td", "th"):
            try:
                span = max(1, int(dict(attrs).get("colspan") or 1))
            except ValueError:
                span = 1
            row = max(0, t["row"])
            self.cell = (t["id"], row, t["col"], span, tag == "th" or t["head"])
            t["col"] += span

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        elif tag == "title":
            self._in_title = True
        elif tag in ("h1", "h2", "h3", "h4"):
            self._flush()
            self.heading = min(int(tag[1]), 3)
        elif tag in self.BLOCK or tag in ("table", "thead", "tbody", "tfoot"):
            self._flush()
        if not self.skip and tag in (
            "table",
            "thead",
            "tbody",
            "tfoot",
            "tr",
            "td",
            "th",
        ):
            self._table_tag(tag, attrs)
        if tag == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag):
        if tag in self.SKIP:
            self.skip = max(0, self.skip - 1)
        elif tag == "title":
            self._in_title = False
        elif tag in ("h1", "h2", "h3", "h4"):
            self._flush()
            self.heading = 0
        elif tag in self.BLOCK or tag == "table":
            self._flush()
        if not self.skip and tag in ("td", "th", "table"):
            self.cell = None
            if tag == "table" and self.tables:
                self.tables.pop()

    def handle_data(self, data):
        if self._in_title:
            self.title += data
        elif not self.skip and data.strip():
            self.buf.append(data.strip())


@dataclass
class Table:
    """One HTML table: its header cells by column and its body rows as (column, span, text)."""

    header: dict[int, str]
    rows: list[list[tuple[int, int, str]]]


_SERIAL = re.compile(
    r"^(s\.?\s*l?\.?\s*no\.?|sr\.?\s*no\.?|serial( no\.?)?|no\.|#)$", re.I
)


def tables(blocks: list[Block]) -> list[tuple[int, Table]]:
    """(index of the table's first block, table) for every table among the blocks. The first row
    is the header when it sits in <thead> or is made of <th> cells."""
    out: list[tuple[int, Table]] = []
    i = 0
    while i < len(blocks):
        if blocks[i].cell is None:
            i += 1
            continue
        tid, start = blocks[i].cell[0], i
        cells: dict[tuple[int, int], list] = {}
        while i < len(blocks) and blocks[i].cell and blocks[i].cell[0] == tid:
            _, row, col, span, head = blocks[i].cell
            c = cells.setdefault((row, col), [span, head, []])
            c[2].append(blocks[i].text)
            i += 1
        by_row: dict[int, list] = {}
        for (row, col), (span, head, texts) in sorted(cells.items()):
            by_row.setdefault(row, []).append((col, span, head, " ".join(texts)))
        rows = [by_row[r] for r in sorted(by_row)]
        header: dict[int, str] = {}
        if rows and all(c[2] for c in rows[0]):
            header = {col: text for col, _, _, text in rows[0]}
            rows = rows[1:]
        out.append(
            (start, Table(header, [[(c[0], c[1], c[3]) for c in r] for r in rows]))
        )
    return out


def row_text(table: Table, row: list[tuple[int, int, str]]) -> str:
    """'Code: MTB101A; Course Title: Engineering Mathematics 1; Total Credits: 4'. A cell spanning
    several columns ("Total") has no single heading, so it is written as it is; serial numbers
    are left out."""
    parts = []
    for col, span, text in row:
        head = table.header.get(col, "")
        if not text or (span == 1 and head and _SERIAL.match(head.strip())):
            continue
        parts.append(f"{head}: {text}" if head and span == 1 else text)
    return "; ".join(parts)


def table_rows(blocks: list[Block]) -> list[Block]:
    """The blocks with every table rewritten as one block per row, each value next to its column
    heading, so a chunk keeps "Total Credits: 4" together instead of a run of loose numbers."""
    found = dict(tables(blocks))
    out: list[Block] = []
    i = 0
    while i < len(blocks):
        if i not in found:
            if blocks[i].cell is None:
                out.append(blocks[i])
            i += 1
            continue
        tid = blocks[i].cell[0]
        t = found[i]
        page = blocks[i].page
        for row in t.rows:
            text = row_text(t, row)
            if text:
                out.append(Block(text, page=page))
        if t.header and not t.rows:  # a heading row on its own is still text
            out.append(Block(" | ".join(t.header.values()), page=page))
        while i < len(blocks) and blocks[i].cell and blocks[i].cell[0] == tid:
            i += 1
    return out


def parse_html(text: str) -> tuple[list[Block], str, list[str]]:
    p = _HTMLText()
    p.feed(text)
    p._flush()
    return p.blocks, clean(p.title), p.links


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16"):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            continue
    raise IngestError("The text file is not UTF-8 encoded.")


def parse(filename: str, data: bytes) -> list[Block]:
    mime = mime_for(filename)
    if not data:
        raise IngestError("The file is empty.")
    if mime == "application/pdf":
        blocks = _pdf(data)
    elif mime.endswith("wordprocessingml.document"):
        blocks = _docx(data)
    elif mime.endswith("presentationml.presentation"):
        blocks = _pptx(data)
    elif mime.endswith("spreadsheetml.sheet"):
        blocks = _xlsx(data)
    elif mime == "text/csv":
        blocks = _rows(list(csv.reader(io.StringIO(_decode(data)))))
    elif mime == "text/html":
        blocks = table_rows(parse_html(_decode(data))[0])
    else:
        blocks = _plain(_decode(data), markdown=mime == "text/markdown")
    if not any(b.level == 0 and b.text for b in blocks):
        raise IngestError("No readable text was found in the file.")
    return blocks


# ------------------------------------------------------------------ chunking


def _tokens(text: str) -> int:
    """Rough token count (no tokenizer download): ~1.3 tokens per word for English."""
    return int(len(text.split()) * 1.3) + 1


def _split_long(text: str, limit: int) -> list[str]:
    """Split one over-long paragraph at sentence boundaries."""
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    parts, cur = [], ""
    for s in sentences:
        if cur and _tokens(cur + " " + s) > limit:
            parts.append(cur)
            cur = s
        else:
            cur = f"{cur} {s}".strip()
    if cur:
        parts.append(cur)
    return parts


def chunk(
    blocks: list[Block], max_tokens: int = 400, overlap: float = 0.15
) -> list[Chunk]:
    """Pack paragraphs under the same heading path into chunks of up to max_tokens; a new heading
    always starts a new chunk. Consecutive chunks in one section share ~overlap of their text."""
    chunks: list[Chunk] = []
    path: list[str] = []
    section: list[tuple[str, int | None]] = []

    def emit() -> None:
        if not section:
            return
        heading = " > ".join(path)
        cur: list[str] = []
        page = section[0][1]
        for text, pg in section:
            pieces = (
                _split_long(text, max_tokens) if _tokens(text) > max_tokens else [text]
            )
            for piece in pieces:
                if cur and _tokens(" ".join(cur + [piece])) > max_tokens:
                    chunks.append(Chunk(" ".join(cur), heading, page, len(chunks)))
                    # carry the tail of the previous chunk over as overlap
                    words = " ".join(cur).split()
                    keep = max(1, int(len(words) * overlap))
                    cur = [" ".join(words[-keep:])]
                    page = pg
                cur.append(piece)
        if cur:
            chunks.append(Chunk(" ".join(cur), heading, page, len(chunks)))
        section.clear()

    for b in blocks:
        if b.level:
            emit()
            path[:] = path[: b.level - 1] + [b.text]
        else:
            section.append((b.text, b.page))
    emit()
    return chunks


def full_text(blocks: list[Block]) -> str:
    return "\n".join(("#" * b.level + " " if b.level else "") + b.text for b in blocks)
