"""Compare an extracted invoice with its label, field by field.

Strings ignore case and extra whitespace, numbers must match to the cent, dates exactly.
Line items are scored separately: a labelled line counts as found when some extracted line
has the same description, quantity, unit price and amount. Extra invented lines lower the
precision of the line-item score but do not hide a missing one.
"""

from decimal import Decimal

SCALAR_FIELDS = ["vendor", "invoice_number", "invoice_date", "due_date", "currency",
                 "subtotal", "discount", "tax", "total"]
MONEY = {"subtotal", "discount", "tax", "total"}


def _norm(value) -> str:
    return " ".join(str(value).lower().split())


def _same(field: str, expected, actual) -> bool:
    if expected is None or actual is None:
        return expected is None and actual is None
    if field in MONEY:
        return abs(Decimal(str(expected)) - Decimal(str(actual))) < Decimal("0.005")
    return _norm(expected) == _norm(actual)


def _same_line(exp: dict, act: dict) -> bool:
    return (
        _norm(exp["description"]) == _norm(act["description"])
        and all(abs(Decimal(str(exp[k])) - Decimal(str(act[k]))) < Decimal("0.005")
                for k in ("quantity", "unit_price", "amount"))
    )


def score_invoice(expected: dict, actual: dict | None) -> dict:
    """{field: bool, ..., 'line_items_found': n, 'line_items_expected': n, 'line_items_extra': n}"""
    actual = actual or {}
    scores = {f: _same(f, expected.get(f), actual.get(f)) for f in SCALAR_FIELDS}
    exp_lines, act_lines = expected["line_items"], list(actual.get("line_items") or [])
    found = 0
    for line in exp_lines:
        match = next((a for a in act_lines if _same_line(line, a)), None)
        if match is not None:
            act_lines.remove(match)
            found += 1
    scores["line_items_found"] = found
    scores["line_items_expected"] = len(exp_lines)
    scores["line_items_extra"] = len(act_lines)
    return scores
