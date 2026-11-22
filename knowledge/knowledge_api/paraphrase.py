"""Paraphrase-tolerant search (PRD 9.4): visitors seldom use the words the college's pages use.

"How many seats in CSE?" has to find "Intake: 360" on the B.Tech. in Computer Science and
Engineering page; "what % is maths" has to find "Mathematics and statistics: 28.0%". The vector
search covers much of this, but the keyword half of the hybrid search only matches the words
asked, and the embedding of a short question full of abbreviations is weak.

So, before searching, a question is expanded with the canonical words of every admissions concept
it names. Each concept is a pattern of the ways people say it and the words the pages use. This
is admissions vocabulary, not anything about one college: it works for every Tenant Pack. No
model call (the latency budget, PRD 7.5); a question in an Indic script is left as it is (its own
multilingual index handles it).
"""

from __future__ import annotations

import re

# (how visitors say it, the words the pages use)
CONCEPTS: list[tuple[str, str]] = [
    # subjects and programme abbreviations
    (r"\bmaths?\b", "mathematics"),
    (r"\bstats\b", "statistics"),
    (r"\bcse\b|\bcs\b|\bcomputer science\b|\bcomps?\b", "computer science engineering"),
    (r"\bece\b", "electronics communication engineering"),
    (r"\beee\b", "electrical electronics engineering"),
    (r"\bmech\b", "mechanical engineering"),
    (r"\bai\s*(&|and|/)?\s*ml\b|\baiml\b", "artificial intelligence machine learning"),
    (r"\bai\b", "artificial intelligence"),
    (r"\bml\b", "machine learning"),
    (r"\bds\b|\bdata sci\b", "data science"),
    (r"\bb\.?\s?tech\b|\bbe\b|\bengineering degree\b", "B.Tech bachelor technology"),
    (r"\bm\.?\s?tech\b", "M.Tech master technology"),
    (r"\bb\.?\s?sc\b", "B.Sc bachelor science"),
    (r"\bm\.?\s?sc\b", "M.Sc master science"),
    (r"\bph\.?\s?d\b|\bdoctorate\b", "PhD doctor philosophy doctoral"),
    (r"\bug\b|\bundergrad", "undergraduate"),
    (r"\bpg\b|\bpostgrad|\bmasters?\b", "postgraduate"),
    # money
    (
        r"\bfees?\b|\bcosts?\b|\bcharges?\b|\btuition\b|\bprice\b|\bhow much\b|\bexpensive\b|\bpay\b",
        "fee fees structure",
    ),
    (
        r"\bscholarships?\b|\bfinancial aid\b|\bdiscount|\bwaiver|\bconcession|\bfree ?ship",
        "scholarship",
    ),
    (r"\bloans?\b|\bemi\b|\binstal+ments?\b", "loan assistance"),
    (r"\brefund", "refund"),
    # admission
    (
        r"\bseats?\b|\bintake\b|\bcapacity\b|\bhow many (students|people)\b|\bbatch size\b",
        "intake seats",
    ),
    (
        r"\beligib|\bcriteria\b|\bqualif|\brequire|\bminimum marks\b|\bmarks needed\b"
        r"|\bpercentage (needed|required)\b|\bcan i (join|apply|get)\b|\bam i\b",
        "eligibility criteria",
    ),
    (
        r"\bapply\b|\bapplication\b|\bregist|\bhow (to|do i|can i) (join|get in|enrol|enroll)\b"
        r"|\badmission process\b|\bprocedure\b",
        "application admission process",
    ),
    (
        r"\bentrance\b|\bexam\b|\btest\b|\bruasat\b|\bcomedk\b|\bkcet\b|\bjee\b",
        "entrance test exam",
    ),
    (r"\bcut-? ?off\b|\brank\b", "cutoff rank"),
    (
        r"\blast date\b|\bdeadline\b|\bclosing date\b|\bwhen (can|should|do) i apply\b",
        "last date",
    ),
    (r"\bdocuments?\b|\bcertificates?\b|\bpapers\b", "documents required"),
    (r"\blateral\b|\bdiploma holders?\b|\bdirect second year\b", "lateral entry"),
    # programme content
    (r"\bhow long\b|\bduration\b|\bhow many years\b|\blength of\b", "duration years"),
    (
        r"\bsyllabus\b|\bsubjects?\b|\bcurricul|\bmodules?\b|\bcredits?\b|\bpapers? taught\b"
        r"|\bwhat (do|will) (we|i|you) (study|learn)\b",
        "curriculum courses credits",
    ),
    (
        r"%|\bpercent|\bshare\b|\bportion\b|\bproportion\b|\bhow much of\b",
        "percentage share credits",
    ),
    (r"\bsemesters?\b|\bsem\b", "semester"),
    # campus life
    (
        r"\bhostels?\b|\baccommodation\b|\bstay\b|\brooms?\b|\bdorm",
        "hostel accommodation",
    ),
    (
        r"\bplacements?\b|\bjobs?\b|\bpackages?\b|\bsalary\b|\blpa\b|\brecruiters?\b|\bcompanies\b",
        "placement recruiters",
    ),
    (
        r"\bcareers?\b|\bscope\b|\bfuture\b|\bafter (the )?(course|degree)\b",
        "career path",
    ),
    (r"\btransport\b|\bbus(es)?\b", "transport"),
    (r"\blibrary\b", "library"),
    (r"\bsports?\b|\bgym\b", "sports facilities"),
    (r"\bcampus(es)?\b|\blocation\b|\bwhere is\b|\baddress\b", "campus location"),
    (r"\branking\b|\bnirf\b|\bnaac\b|\baccredit", "accreditation ranking"),
]
_COMPILED = [(re.compile(p, re.IGNORECASE), words) for p, words in CONCEPTS]
_LOCAL_SCRIPT = re.compile(r"[ऀ-෿]")


def expand(query: str) -> str:
    """The question followed by the page words of every concept it names (each once, and only
    words the question does not already have)."""
    if _LOCAL_SCRIPT.search(query):
        return query
    have = set(re.findall(r"[a-z0-9.]+", query.lower()))
    extra: list[str] = []
    for pattern, words in _COMPILED:
        if pattern.search(query):
            for w in words.split():
                if w.lower() not in have:
                    have.add(w.lower())
                    extra.append(w)
    return f"{query} {' '.join(extra)}" if extra else query
