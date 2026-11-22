"""The knowledge base that builds itself from a college website (site_kb.py). The fake site
below reproduces the layouts seen on a real one: menus and footers on every page, a fee with and
without "Rs", a fee table with one column per quota, eligibility split into sub-headings, an FAQ
page, and a sitemap that still lists a page which no longer exists."""

from decimal import Decimal

import httpx
import psycopg
from conftest import TEST_ADMIN_URL, needs_db

from knowledge_api import curriculum, ingest, search, site_kb

MENU = "<nav><p>Why Us?</p><p>Campuses</p><p>Placements</p><p>Contact</p></nav>"
FOOT = "<footer><p>University House, New BEL Road</p><p>Privacy Policy</p></footer>"


def page(body: str, title: str = "Page") -> str:
    return f"<html><head><title>{title}</title></head><body>{MENU}{body}{FOOT}</body></html>"


ECON = page(
    "<h1>B.Sc. (Hons) in Applied Economics</h1>"
    "<p>Faculty</p><p>School of Social Sciences</p><p>Study</p><p>4 Years</p>"
    "<h3>Eligibility Criteria</h3><p>Passed 12th Std with a minimum aggregate of 50% marks.</p>"
    "<h3>Fee Structure</h3><h3>Fee Structure 2026–27</h3><p>Total Fees Per Year</p><p>Rs.2,00,000</p>"
    "<h3>Intake</h3><p>60 Seats</p>",
    "Economics",
)
AERO = page(
    "<h1>B. Tech. in Aerospace Engineering</h1><p>Study</p><p>4 Years</p>"
    "<h3>Eligibility Criteria</h3><h2>Indian Nationals</h2><p>Passed 10+2 with Physics and Mathematics.</p>"
    "<h2>Lateral Entry</h2><p>A diploma with 45% marks.</p>"
    "<h3>Fee Structure</h3><p>Fee Structure 2025–26</p><p>Total Fee Per Year</p><p>4,50,000</p>"
    "<h3>Intake</h3><p>60 Seats</p>",
    "Aero",
)
MDS = page(
    "<h1>Master of Dental Surgery in Periodontics</h1>"
    "<h3>Annual Programme Fee</h3><p>Fee Structure 2026–27</p><p>Course</p>"
    "<p>Government Quota</p><p>Private Quota</p><p>NRI/Other Quota</p>"
    "<p>MDS Periodontics</p><p>Click Here</p><p>10,75,500 Per Annum</p><p>20,00,000 Per Annum</p>"
    "<h3>Eligibility Criteria</h3><p>Passed BDS.</p>",
    "MDS",
)
FAQ = page(
    "<h1>Admission FAQs</h1>"
    "<h2>How much is the application fee?</h2><p>The online application costs Rs. 2000 per program.</p>"
    "<h2>Is RUASAT online?</h2><p>The entrance test is an online proctored exam.</p>",
    "FAQs",
)
SITE = "https://college.example"
SITEMAP = (
    "<urlset>"
    + "".join(
        f"<url><loc>{SITE}{p}</loc></url>"
        for p in (
            "/course/econ",
            "/course/aero",
            "/course/mds",
            "/admissions/faqs",
            "/course/gone",
            "/news/x",
        )
    )
    + "</urlset>"
)


def fake_site(req: httpx.Request) -> httpx.Response:
    pages = {
        "/robots.txt": (
            f"User-agent: *\nDisallow: /private/\nSitemap: {SITE}/sitemap.xml",
            "text/plain",
        ),
        "/sitemap.xml": (SITEMAP, "application/xml"),
        "/course/econ": (ECON, "text/html"),
        "/course/aero": (AERO, "text/html"),
        "/course/mds": (MDS, "text/html"),
        "/admissions/faqs": (FAQ, "text/html"),
    }
    if req.url.path not in pages:
        return httpx.Response(
            404, text="not found", headers={"content-type": "text/html"}
        )
    text, kind = pages[req.url.path]
    return httpx.Response(200, text=text, headers={"content-type": kind})


def _pages(*htmls):
    out = []
    for i, h in enumerate(htmls):
        blocks, title, _ = ingest.parse_html(h)
        out.append(site_kb.Page(f"{SITE}/course/p{i}", title, blocks))
    site_kb.strip_furniture(out, share=0.5)
    return out


def test_menus_and_footers_are_learned_and_dropped():
    pages = _pages(ECON, AERO, MDS, FAQ)
    text = ingest.full_text(pages[0].content)
    assert "Applied Economics" in text and "Rs.2,00,000" in text
    assert "Placements" not in text and "Privacy Policy" not in text


def test_a_programme_page_becomes_a_catalogue_entry():
    econ = _pages(ECON, AERO, MDS, FAQ)[0]
    prog, fees = site_kb.programme(econ, "2026-27")
    assert prog["name"] == "B.Sc. (Hons) in Applied Economics"
    assert (prog["level"], prog["duration_months"], prog["intake"]) == ("UG", 48, 60)
    assert prog["department"] == "School of Social Sciences"
    assert "50% marks" in prog["eligibility"]
    assert [(f["category"], f["amount"], f["period"]) for f in fees] == [
        ("general", Decimal("200000"), "per year")
    ]


def test_eligibility_sub_sections_and_a_fee_without_rs_are_read():
    aero = _pages(ECON, AERO, MDS, FAQ)[1]
    prog, fees = site_kb.programme(aero, "2026-27")
    assert (
        "Indian Nationals:" in prog["eligibility"]
        and "45% marks" in prog["eligibility"]
    )
    assert fees[0]["amount"] == Decimal("450000")
    assert prog["academic_year"] == "2025-26"  # the page still shows last year's table
    assert "BTech Aerospace Engineering" in prog["aliases"]


def test_a_fee_table_gives_one_fee_per_quota():
    mds = _pages(ECON, AERO, MDS, FAQ)[2]
    _, fees = site_kb.programme(mds, "2026-27")
    assert [(f["category"], f["amount"]) for f in fees] == [
        ("private", Decimal("1075500")),
        ("nri/other", Decimal("2000000")),
    ]


def test_faq_headings_become_question_and_answer():
    faq = _pages(ECON, AERO, MDS, FAQ)[3]
    assert site_kb.programme(faq, "2026-27") is None
    assert site_kb.faqs(faq)[0] == (
        "How much is the application fee?",
        "The online application costs Rs. 2000 per program.",
    )


def test_the_acronym_the_page_spells_out_becomes_a_name_visitors_can_use():
    # seen live: "MDS Periodontics" did not match the catalogue, so the model read the raw fee
    # table and put the private-quota fee under the government quota
    assert "MDS Periodontics" in site_kb.aliases(
        "Master of Dental Surgery in Periodontics",
        "Master of Dental Surgery(MDS) in Periodontics - RUAS",
    )
    assert "MBA" in site_kb.aliases("Master's of Business Administration (MBA)")


def test_a_year_is_never_read_as_a_fee():
    blocks = [
        ingest.Block("Fee Structure 2026–27"),
        ingest.Block("Fees to be announced"),
    ]
    assert site_kb.fee_rows(blocks) == []


@needs_db
def test_the_site_builds_the_knowledge_base_by_itself(settings, embedder, tenant):
    client = httpx.Client(transport=httpx.MockTransport(fake_site))
    include = [f"{SITE}/course/*", f"{SITE}/admissions/*"]
    report = site_kb.build(
        settings,
        embedder,
        tenant,
        f"{SITE}/",
        include,
        client=client,
        sleep=lambda s: None,
        delay=0,
    )
    assert (
        report["pages"] == 4 and report["failed"] == 1
    )  # /course/gone is a stale entry
    assert report["documents"] == {"new": 4}
    assert report["programmes_found"] == 3
    assert report["catalogue"].startswith("replaced 0 programmes with 3 for 2026-27")
    assert report["faqs"] == {"new": 2, "updated": 0, "unchanged": 0}
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        approved = conn.execute(
            "select count(*) from kb.documents where tenant_id = %s and status = 'approved'",
            (tenant,),
        ).fetchone()[0]
        aero_fee = conn.execute(
            "select f.period from kb.program_fees f join kb.programs p on p.id = f.program_id"
            " where p.tenant_id = %s and p.code = 'AERO'",
            (tenant,),
        ).fetchone()[0]
    assert approved == 4  # searchable at once
    assert aero_fee == "per year (2025-26 fee structure)"

    # Run again: nothing changed on the site, so nothing is re-indexed or duplicated.
    again = site_kb.build(
        settings,
        embedder,
        tenant,
        f"{SITE}/",
        include,
        client=client,
        sleep=lambda s: None,
        delay=0,
    )
    assert again["documents"] == {"unchanged": 4}
    assert again["faqs"] == {"new": 0, "updated": 0, "unchanged": 2}


# ------------------------------------------------------------------ curricula and linked files


def _semester(heading: str, rows: list[tuple[str, str, int]]) -> str:
    body = "".join(
        f"<tr><td>{i}</td><td>{code}</td><td>{title}</td><td>{cr}</td></tr>"
        for i, (code, title, cr) in enumerate(rows, 1)
    )
    total = sum(cr for _, _, cr in rows)
    return (
        f"<h2>{heading}</h2><table><thead><tr><th>Sl. No.</th><th>Code</th>"
        "<th>Course Title</th><th>Total Credits</th></tr></thead>"
        f'<tbody>{body}<tr><td colspan="3">Total</td><td>{total}</td></tr></tbody></table>'
    )


PHY = [
    ("MT101", "Engineering Mathematics 1", 4),
    ("PY102", "Engineering Physics", 4),
    ("ME103", "Engineering Drawing", 2),
]
CHE = [
    ("MT101", "Engineering Mathematics 1", 4),
    ("CY104", "Engineering Chemistry", 4),
    ("CS105", "Programming in C", 3),
]
MATHS_CS = page(
    "<h1>B. Tech. in Mathematics and Computing</h1>"
    "<h3>Curriculum Details</h3>"
    + _semester("Semester 1 (Physics Cycle)", PHY)
    + _semester("Semester 1 (Chemistry Cycle)", CHE)
    + _semester(
        "Semester 2 (Physics Cycle)",
        [("MT201", "Engineering Mathematics 2", 4), *PHY[1:]],
    )
    + _semester(
        "Semester 2 (Chemistry Cycle)",
        [("MT201", "Engineering Mathematics 2", 4), *CHE[1:]],
    )
    + _semester(
        "Semester 3",
        [
            ("MC301", "Linear Algebra", 3),
            ("CS302", "Data Structures", 4),
            ("xxxx", "Open Elective-1", 3),
        ],
    )
    + '<p><a href="/uploads/pdf/Regulations 2026.docx">Regulations</a>'
    " <a href='https://elsewhere.example/brochure.pdf'>Brochure</a>"
    " <a href='/uploads/logo.png'>Logo</a></p>"
    "<h3>Eligibility Criteria</h3><p>Passed 12th Std with Mathematics, 45% marks.</p>",
    "Maths and Computing",
)


def test_a_table_row_keeps_its_column_headings():
    blocks, _, _ = ingest.parse_html(MATHS_CS)
    text = ingest.full_text(ingest.table_rows(blocks))
    assert "Code: MC301; Course Title: Linear Algebra; Total Credits: 3" in text
    assert (
        "Total; Total Credits: 10" in text
    )  # the "Total" cell spans the first columns
    assert "Sl. No." not in text
    assert "## Semester 3" in text  # the heading stays above its table


def test_the_curriculum_summary_counts_one_cycle_per_semester_and_the_maths_share():
    blocks, _, _ = ingest.parse_html(MATHS_CS)
    found = curriculum.periods(blocks)
    assert [(p.heading, p.credits) for p in curriculum.plan(found)] == [
        ("Semester 1 (Physics Cycle)", 10),
        ("Semester 2 (Chemistry Cycle)", 11),
        ("Semester 3", 10),
    ]
    text = ingest.full_text(
        curriculum.summary(
            "B. Tech. in Mathematics and Computing", f"{SITE}/course/mc", blocks, 18
        )
    )
    # maths: Engineering Mathematics 1 (4) + 2 (4) + Linear Algebra (3) = 11 of 31 credits
    assert "31 credits in total over 3 semesters" in text
    assert "carry 11 of the 31 credits, which is 35.5% of the programme" in text
    assert "not counting electives" in text
    assert "Electives (subject chosen by the student): 3 of 31 credits (9.7%)" in text
    assert "Not counted: Semester 1 (Chemistry Cycle) (11 credits)" in text
    assert f"curriculum table on {SITE}/course/mc" in text


def test_a_curriculum_that_lists_only_some_semesters_says_so():
    # seen live: a 4-year B.Pharm page lists semesters I-III only; their 78 credits are not the
    # programme's total
    blocks, _, _ = ingest.parse_html(MATHS_CS)
    text = ingest.full_text(
        curriculum.summary(
            "B. Tech. in Mathematics and Computing", f"{SITE}/course/mc", blocks, 48
        )
    )
    assert "lists only 3 of the 8 semesters" in text
    assert "35.5% of the listed semesters" in text
    assert "in total over" not in text


def test_a_page_without_a_credits_table_has_no_curriculum_summary():
    blocks, _, _ = ingest.parse_html(ECON)
    assert (
        curriculum.summary(
            "B.Sc. (Hons) in Applied Economics", f"{SITE}/course/econ", blocks
        )
        is None
    )


def test_course_titles_give_the_subject_area():
    assert (
        curriculum.area("Partial Differential Equations")
        == "Mathematics and statistics"
    )
    assert curriculum.area("Inferential Statistics") == "Mathematics and statistics"
    assert (
        curriculum.area("Measure Theory and Integration")
        == "Mathematics and statistics"
    )
    assert curriculum.area("Pharmaceutical Analysis-I Theory") == "Other subjects"
    assert curriculum.area("Machine Learning-1") == "Computing"
    assert (
        curriculum.area("Project Work-2 or Internship")
        == "Projects, internships and seminars"
    )
    assert curriculum.area("Professional Core Elective-1 or Online Course").startswith(
        "Electives"
    )


def test_files_the_pages_link_to_on_the_same_site_are_found():
    blocks, title, links = ingest.parse_html(MATHS_CS)
    pages = [
        site_kb.Page(
            f"{SITE}/course/mc",
            title,
            blocks,
            links=[f"{SITE}{h}" if h.startswith("/") else h for h in links],
        )
    ]
    robots = site_kb.RobotFileParser()
    robots.parse([])
    assert (
        site_kb.linked_documents(pages, "college.example", robots, [], 10)
        == [
            f"{SITE}/uploads/pdf/Regulations 2026.docx"  # not the other site's PDF, not the image
        ]
    )


def _docx(text: str) -> bytes:
    import io

    import docx

    d = docx.Document()
    d.add_heading("Academic Regulations", 1)
    d.add_paragraph(text)
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


REGULATIONS = _docx(
    "A student must earn 75% attendance in every course to sit the examination."
)


def curriculum_site(req: httpx.Request) -> httpx.Response:
    if req.url.path == "/robots.txt":
        return httpx.Response(200, text=f"Sitemap: {SITE}/sitemap.xml")
    if req.url.path == "/sitemap.xml":
        return httpx.Response(
            200, text=f"<urlset><url><loc>{SITE}/course/mc</loc></url></urlset>"
        )
    if req.url.path == "/course/mc":
        return httpx.Response(200, text=MATHS_CS, headers={"content-type": "text/html"})
    if req.url.path == "/uploads/pdf/Regulations 2026.docx":
        return httpx.Response(
            200,
            content=REGULATIONS,
            headers={"content-type": "application/octet-stream"},
        )
    return httpx.Response(404, text="not found", headers={"content-type": "text/html"})


@needs_db
def test_curriculum_summaries_and_linked_files_are_searchable(
    settings, embedder, tenant
):
    client = httpx.Client(transport=httpx.MockTransport(curriculum_site))
    kw = dict(client=client, sleep=lambda s: None, delay=0)
    report = site_kb.build(
        settings, embedder, tenant, f"{SITE}/", [f"{SITE}/course/*"], **kw
    )
    assert report["documents"] == {"new": 1}
    assert report["curriculum_summaries"] == {"new": 1}
    assert report["linked_files"] == {"new": 1}
    with psycopg.connect(TEST_ADMIN_URL) as conn:
        rows = dict(
            conn.execute(
                "select url, status from kb.documents where tenant_id = %s", (tenant,)
            ).fetchall()
        )
        summary = conn.execute(
            "select content from kb.documents where tenant_id = %s and url like %s",
            (tenant, "%#curriculum-summary"),
        ).fetchone()[0]
    assert set(rows.values()) == {"approved"}
    assert f"{SITE}/uploads/pdf/Regulations 2026.docx" in rows
    assert "35.5% of the programme" in summary
    hits = search.search(
        settings.database_url,
        embedder,
        tenant,
        "What percentage of maths is in Mathematics and Computing?",
        top_k=4,
    )["items"]
    assert any("35.5%" in h["content"] for h in hits)

    # Nothing changed: no new versions, and the file is not downloaded again this week.
    again = site_kb.build(
        settings, embedder, tenant, f"{SITE}/", [f"{SITE}/course/*"], **kw
    )
    assert again["documents"] == {"unchanged": 1}
    assert again["curriculum_summaries"] == {"unchanged": 1}
    assert again["linked_files"] == {"checked recently": 1}


def test_semesters_listed_without_credits_make_the_summary_partial():
    later = (
        "<h2>Semester 4</h2><table><thead><tr><th>Course Name</th><th>Code</th></tr></thead>"
        "<tbody><tr><td>Biostatistics</td><td>BP401</td></tr></tbody></table>"
    )
    blocks, _, _ = ingest.parse_html(
        MATHS_CS.replace("<h3>Eligibility", later + "<h3>Eligibility")
    )
    text = ingest.full_text(
        curriculum.summary(
            "B. Tech. in Mathematics and Computing", f"{SITE}/course/mc", blocks
        )
    )
    assert "Semester 4 list their courses without credits" in text
    assert "35.5% of the semesters with credits" in text


def test_a_programme_is_also_named_by_its_subject_and_acronym():
    # seen live: "how many seats in CSE" matched no programme, so the catalogue's intake was unused
    ug = site_kb.subject_aliases("B.Tech. in Computer Science and Engineering", "UG")
    assert {
        "CSE",
        "B.Tech CSE",
        "BTech CSE",
        "Computer Science",
        "B.Tech Computer Science",
    } <= set(ug)
    pg = site_kb.subject_aliases("M.Tech. in Computer Science and Engineering", "PG")
    assert (
        "M.Tech CSE" in pg and "CSE" not in pg
    )  # a bare acronym means the undergraduate one
    assert "MC" not in site_kb.subject_aliases(
        "B.Tech. in Mathematics and Computing", "UG"
    )
    assert site_kb.subject_aliases("MBA", "PG") == []


def test_honours_in_a_programme_name_can_be_dropped_or_said_as_a_word():
    # seen live: "BBA (Honours)" had no aliases, so a spoken "BBA honors" matched no programme
    assert {"BBA", "BBA Hons", "BBA Honours", "BBA Honors"} <= set(
        site_kb.aliases("BBA (Honours)")
    )
    assert "BCom Accounting and Finance" in site_kb.aliases(
        "B.Com. (Hons.) in Accounting and Finance"
    )


def test_courses_only_a_listing_request_names_are_found():
    """RUAS, 19 Nov 2026: the A-Z course list is filled by POST programme/showProgramsDataAjax;
    the sitemap's course addresses for AI & ML, AI & Data Science and VLSI were out of date."""
    from urllib.robotparser import RobotFileParser

    import httpx

    from knowledge_api.site_kb import listing_urls

    pages = {
        (
            "1",
            "0",
        ): '<a href="https://u.example/course/btech-aiml">AI</a><a href="/course/btech-aids">DS</a>',
        (
            "1",
            "1",
        ): '<a href="https://u.example/course/btech-vlsi">VLSI</a><a href="https://other.example/course/x">x</a>',
        ("1", "2"): "Data not available",
        (
            "2",
            "0",
        ): '<a href="https://u.example/course/mtech-aiml">M</a><a href="https://u.example/landing/x">l</a>',
        ("2", "1"): "Data not available",
    }
    calls = []

    def handler(req: httpx.Request) -> httpx.Response:
        form = dict(x.split("=") for x in req.content.decode().split("&"))
        calls.append(form)
        return httpx.Response(
            200, text=pages[(form["programid"], form["page"])] + "##12"
        )

    robots = RobotFileParser()
    robots.parse(["User-agent: *", "Disallow: /landing/"])
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        found = listing_urls(
            client,
            [
                {
                    "url": "https://u.example/programme/list",
                    "form": {"limit": 12},
                    "vary": {"programid": [1, 2]},
                    "page_field": "page",
                }
            ],
            "u.example",
            ["https://u.example/course/*"],
            [],
            robots,
            sleep=lambda s: None,
        )
    assert found == [
        "https://u.example/course/btech-aiml",
        "https://u.example/course/btech-aids",
        "https://u.example/course/btech-vlsi",
        "https://u.example/course/mtech-aiml",
    ]
    assert all(c["limit"] == "12" for c in calls)


def test_programmes_are_known_by_the_names_visitors_use():
    """Seen 19 Nov 2026: "B.Tech AI and Data Science" matched the M.Tech in Data Science, "AIML"
    matched nothing ("B. Tech." gave the acronym "TAIML") and "VLSI chip design" matched nothing."""
    from knowledge_api.site_kb import subject_aliases

    aiml = subject_aliases(
        "B. Tech. in Artificial Intelligence and Machine Learning", "UG"
    )
    assert {"AIML", "AI & ML", "AI and ML", "B.Tech AIML"} <= set(
        aiml
    ) and "TAIML" not in aiml
    aids = subject_aliases("B.Tech in Artificial Intelligence and Data Science", "UG")
    assert {
        "AIDS",
        "AI & DS",
        "AI and Data Science",
        "B.Tech AI and Data Science",
    } <= set(aids)
    vlsi = subject_aliases(
        "B.Tech in Electronics Engineering (VLSI Design and Technology)", "UG"
    )
    assert {"VLSI", "Chip Design", "B.Tech VLSI", "B.Tech Chip Design"} <= set(vlsi)
    # a postgraduate programme gets only degree-tagged forms, so "AIML" alone means the B.Tech
    mtech = subject_aliases(
        "M.Tech. in Artificial Intelligence & Machine Learning", "PG"
    )
    assert "M.Tech AIML" in mtech and "AIML" not in mtech
    assert "CSE" in subject_aliases("B.Tech. in Computer Science and Engineering", "UG")
