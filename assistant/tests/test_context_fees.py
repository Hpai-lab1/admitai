"""A fee shared by every programme is stated once (answers need not pick one as an example)."""

from assistant_api.context import fee_summary


def _p(code, tuition, hostel=90000):
    return {
        "code": code,
        "name": code,
        "level": "UG",
        "duration_months": 48,
        "fees": [
            {
                "component": "tuition",
                "category": "general",
                "amount": tuition,
                "currency": "INR",
                "period": "per year",
            },
            {
                "component": "hostel",
                "category": "general",
                "amount": hostel,
                "currency": "INR",
                "period": "per year",
            },
        ],
    }


def test_shared_fee_is_stated_and_different_fee_is_not():
    text = fee_summary([_p("A", 100), _p("B", 200)])
    assert "hostel fee (general) is ₹90,000 per year for every program" in text.lower()
    assert "tuition fee (general) is" not in text.lower()


def test_a_fee_missing_from_one_programme_is_not_called_shared():
    other = _p("B", 200)
    other["fees"] = other["fees"][:1]
    assert "every program" not in fee_summary([_p("A", 100), other])


def test_one_programme_has_no_shared_line():
    assert "every program" not in fee_summary([_p("A", 100)])
