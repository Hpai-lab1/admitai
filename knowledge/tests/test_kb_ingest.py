"""Parsing and chunking (F4.1, F4.7): no database, no model."""

import io

import pytest

from knowledge_api import ingest
from knowledge_api.catalogue import CatalogueError, parse_catalogue
from knowledge_api.documents import diff_summary


def test_markdown_headings_become_heading_paths():
    text = "# Admissions\n\nIntro text.\n\n## Fees\n\nTuition is 250000 per year.\n\n## Hostel\n\nRooms for all."
    chunks = ingest.chunk(ingest.parse("guide.md", text.encode()))
    paths = [c.heading_path for c in chunks]
    assert paths == ["Admissions", "Admissions > Fees", "Admissions > Hostel"]
    assert "250000" in chunks[1].content


def test_long_sections_split_with_overlap():
    sentences = " ".join(
        f"Sentence number {i} talks about admissions." for i in range(400)
    )
    chunks = ingest.chunk(
        [ingest.Block("Big", level=1), ingest.Block(sentences)],
        max_tokens=400,
        overlap=0.15,
    )
    assert len(chunks) > 3
    for c in chunks:
        assert len(c.content.split()) * 1.3 <= 400 * 1.2
    # the start of each chunk repeats the end of the previous one
    first_tail = chunks[0].content.split()[-5:]
    assert " ".join(first_tail) in chunks[1].content


def test_html_drops_navigation_and_keeps_headings():
    html = b"""<html><head><title>Hostel</title><script>var x=1</script></head><body>
    <nav>Home | About</nav><h1>Hostel</h1><p>Separate hostels for men and women.</p>
    <h2>Fees</h2><ul><li>Room: 90000 per year</li></ul><footer>Copyright</footer></body></html>"""
    blocks, title, _ = ingest.parse_html(html.decode())
    texts = [b.text for b in blocks]
    assert title == "Hostel"
    assert "Home | About" not in texts and "Copyright" not in texts
    assert "Room: 90000 per year" in texts


def test_csv_rows_keep_column_labels():
    blocks = ingest.parse("fees.csv", b"Program,Fee\nB.Tech CSE,250000\n")
    assert blocks[0].text == "Program: B.Tech CSE; Fee: 250000."


def test_docx_headings_and_tables():
    import docx

    d = docx.Document()
    d.add_heading("Scholarships", level=1)
    d.add_paragraph("Merit scholarships cover up to 50% of tuition.")
    t = d.add_table(rows=1, cols=2)
    t.rows[0].cells[0].text, t.rows[0].cells[1].text = "Rank 1-100", "50%"
    buf = io.BytesIO()
    d.save(buf)
    chunks = ingest.chunk(ingest.parse("s.docx", buf.getvalue()))
    assert chunks[0].heading_path == "Scholarships"
    assert "Rank 1-100 | 50%" in chunks[0].content


@pytest.mark.parametrize(
    "name,data,message",
    [
        ("virus.exe", b"MZ", "Unsupported file type"),
        ("empty.txt", b"", "empty"),
        ("broken.pdf", b"%PDF-1.4 not really", "could not be read"),
        ("broken.docx", b"PK not a zip", "could not be read"),
    ],
)
def test_bad_files_are_rejected_with_a_reason(name, data, message):
    with pytest.raises(ingest.IngestError, match=message):
        ingest.parse(name, data)


def test_catalogue_reports_every_problem_at_once():
    programs = (
        "code,name,department,level,duration_months,intake,eligibility,academic_year\n"
        "CSE,B.Tech CSE,Computer Science,UG,48,120,12th PCM,2027-28\n"
        "CSE,Dup,Computer Science,Masters,x,,,\n"
    )
    fees = "program_code,category,component,amount\nCSE,general,tuition,2,50,000\nXYZ,general,tuition,abc\n"
    with pytest.raises(CatalogueError) as err:
        parse_catalogue(programs, fees)
    text = " ".join(err.value.problems)
    for expected in (
        "duplicate code",
        "level must be",
        "not a whole number",
        "academic_year is required",
        "unknown program_code 'XYZ'",
        "'abc' is not a number",
    ):
        assert expected in text


def test_diff_summary_counts_changes():
    s = diff_summary("Fee: 200000\nHostel: yes", "Fee: 250000\nHostel: yes")
    assert s.startswith("1 line(s) added, 1 removed")
    assert "+Fee: 250000" in s
