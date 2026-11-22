from assistant_api.display import duration, indian_grouping, money, period
from assistant_api.checks import numbers_in
from decimal import Decimal


def test_amounts_are_written_the_way_the_college_website_writes_them():
    assert money("600000") == "₹6,00,000"
    assert money(2000000) == "₹20,00,000"
    assert money("642700", "INR") == "₹6,42,700"
    assert money("96000") == "₹96,000"
    assert money("1250.50") == "₹1,250.50"
    assert money("3500", "USD") == "USD 3,500"
    assert indian_grouping("999") == "999"


def test_a_formatted_amount_is_still_the_exact_catalogue_number():
    for raw in ("600000", "1575000", "72350", "1250.50"):
        assert numbers_in(money(raw)) == {Decimal(raw)}


def test_no_period_is_invented_when_the_website_does_not_state_one():
    assert (
        period("not stated") == ""
        and period(None) == ""
        and period("per year") == "per year"
    )


def test_duration_reads_in_years_and_keeps_the_exact_months():
    assert duration(48) == "4 years (48 months)"
    assert duration(12) == "1 year (12 months)"
    assert duration(54) == "54 months"
    assert duration(None) == ""
