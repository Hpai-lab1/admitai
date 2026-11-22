from assistant_api.scope import FOCUSED_TOP_K, about_other_programmes, focus

PROGS = [
    {
        "code": "BCA",
        "name": "Bachelor of Computer Applications (BCA)",
        "aliases": ["BCA"],
    },
    {
        "code": "MCA",
        "name": "Master of Computer Applications (MCA)",
        "aliases": ["MCA"],
    },
    {"code": "BDES-FD", "name": "B.Des. Fashion Design", "aliases": ["Fashion Design"]},
]


def h(title):
    return {"citation": {"title": title}}


def test_another_programmes_page_is_left_out_and_shared_pages_stay():
    bca, mca = (
        h("Bachelor of Computer Applications (BCA) — programme page"),
        h("Master of Computer Applications (MCA) — programme page"),
    )
    hostel, faq = (
        h("Hostel Facilities"),
        h("How much is the application fee for the university?"),
    )
    assert about_other_programmes(mca, "BCA", PROGS)
    assert not about_other_programmes(bca, "BCA", PROGS)
    assert focus([mca, bca, hostel, faq], "BCA", PROGS) == [bca, hostel, faq]
    # a department page that names the programme stays
    assert focus([h("Department of Fashion Design")], "BDES-FD", PROGS)
    # no programme in focus: nothing is left out
    assert focus([mca, bca], None, PROGS) == [mca, bca]
    assert FOCUSED_TOP_K > 6
