import copy
import json
from pathlib import Path

from scoring import score_invoice

LABEL = json.loads(Path("samples/labels/inv_01.json").read_text(encoding="utf-8"))


def test_identical_invoice_scores_everything_right():
    s = score_invoice(LABEL, copy.deepcopy(LABEL))
    assert all(v for k, v in s.items() if not k.startswith("line_items"))
    assert s["line_items_found"] == s["line_items_expected"] and s["line_items_extra"] == 0


def test_case_whitespace_and_cents_are_handled():
    got = copy.deepcopy(LABEL)
    got["vendor"] = "  " + LABEL["vendor"].upper() + " "
    got["total"] = str(float(LABEL["total"]) + 0.004)
    s = score_invoice(LABEL, got)
    assert s["vendor"] and s["total"]


def test_wrong_and_missing_values_are_detected():
    got = copy.deepcopy(LABEL)
    got["invoice_date"] = "2026-04-03"  # day/month swapped
    got["due_date"] = None
    got["line_items"] = got["line_items"][:2] + [{"description": "Invented", "quantity": 1, "unit_price": 5, "amount": 5}]
    s = score_invoice(LABEL, got)
    assert not s["invoice_date"] and not s["due_date"]
    assert s["line_items_found"] == 2 and s["line_items_expected"] == 3 and s["line_items_extra"] == 1


def test_failed_extraction_scores_zero():
    s = score_invoice(LABEL, None)
    assert not any(v for k, v in s.items() if k in ("vendor", "total")) and s["line_items_found"] == 0


def test_every_label_satisfies_the_schema():
    from schemas import Invoice

    for path in Path("samples/labels").glob("*.json"):
        Invoice.model_validate(json.loads(path.read_text(encoding="utf-8")))
