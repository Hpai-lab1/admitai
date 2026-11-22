"""How catalogue values are written for visitors: the way the college's own website writes them.

Module A returns exact values ("600000", 48 months); these helpers only change their spelling, never
the value. The sentence checks read Indian grouping ("6,00,000") and "₹" as the same number, so a
formatted amount still matches the catalogue exactly.
"""

from __future__ import annotations

from decimal import Decimal, InvalidOperation

# Periods that mean "the source does not say": nothing is printed rather than a guess.
_NO_PERIOD = {"", "not stated", "as stated", "unknown"}


def indian_grouping(digits: str) -> str:
    """'600000' -> '6,00,000'; '2000000' -> '20,00,000' (lakh/crore grouping)."""
    if len(digits) <= 3:
        return digits
    head, tail = digits[:-3], digits[-3:]
    groups = []
    while len(head) > 2:
        groups.insert(0, head[-2:])
        head = head[:-2]
    if head:
        groups.insert(0, head)
    return ",".join(groups) + "," + tail


def money(amount: object, currency: str | None = "INR") -> str:
    """Exact amount in the website's style: INR as '₹6,00,000', others as 'USD 3,500'.
    Paise are kept when present ('₹1,250.50'); anything that is not a number is returned as is."""
    cur = (currency or "INR").upper()
    try:
        value = Decimal(str(amount).replace(",", ""))
    except (InvalidOperation, ValueError):
        return f"{amount} {cur}".strip()
    if value == value.to_integral_value():
        whole, frac = str(value.quantize(Decimal(1))), ""
    else:
        whole, frac = str(value).split(".")
        frac = "." + frac
    sign = "-" if whole.startswith("-") else ""
    whole = whole.lstrip("-")
    if cur == "INR":
        return f"{sign}₹{indian_grouping(whole)}{frac}"
    return f"{cur} {sign}{int(whole):,}{frac}"


def period(value: str | None) -> str:
    """The fee period as the website states it, or '' when it does not state one."""
    v = (value or "").strip()
    return "" if v.lower() in _NO_PERIOD else v


def duration(months: int | None) -> str:
    """'4 years (48 months)': years as the website writes them, with the exact catalogue value."""
    if not months:
        return ""
    m = int(months)
    if m % 12 == 0:
        y = m // 12
        return f"{y} year{'s' if y != 1 else ''} ({m} months)"
    return f"{m} months"
