import copy

import pytest
from pydantic import ValidationError

from fakes import GOOD
from schemas import Invoice


def make(**changes):
    data = copy.deepcopy(GOOD)
    data.update(changes)
    return data


def test_valid_invoice_is_accepted_and_currency_normalised():
    inv = Invoice.model_validate(GOOD)
    assert inv.currency == "USD" and str(inv.total) == "22.68"


def test_total_must_match_subtotal_discount_and_tax():
    with pytest.raises(ValidationError, match="total"):
        Invoice.model_validate(make(total=99.0))


def test_discount_is_part_of_the_total():
    inv = Invoice.model_validate(make(discount=1.0, total=21.68))
    assert str(inv.discount) == "1.0"


def test_subtotal_must_equal_sum_of_lines():
    with pytest.raises(ValidationError, match="subtotal"):
        Invoice.model_validate(make(subtotal=30.0, total=31.68))


def test_line_amount_must_equal_quantity_times_price():
    bad = make(line_items=[{"description": "Widget", "quantity": 2, "unit_price": 10.5, "amount": 25.0}],
               subtotal=25.0, total=26.68)
    with pytest.raises(ValidationError, match="quantity"):
        Invoice.model_validate(bad)


def test_due_date_cannot_precede_invoice_date():
    with pytest.raises(ValidationError, match="due_date"):
        Invoice.model_validate(make(due_date="2026-01-01"))


@pytest.mark.parametrize("currency", ["US", "DOLLAR", "12$", ""])
def test_currency_must_be_iso_like(currency):
    with pytest.raises(ValidationError):
        Invoice.model_validate(make(currency=currency))


def test_invoice_needs_at_least_one_line():
    with pytest.raises(ValidationError):
        Invoice.model_validate(make(line_items=[], subtotal=0, total=1.68))
