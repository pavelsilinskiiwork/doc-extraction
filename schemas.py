"""What we want out of a document: an invoice, as validated data.

The LLM only proposes values. Pydantic decides whether they are acceptable, including the
arithmetic: a model that misreads one digit usually breaks `total == subtotal - discount + tax`
or `sum(line amounts) == subtotal`, and that failure is what triggers a corrected retry.
"""

from datetime import date
from decimal import Decimal

from pydantic import BaseModel, Field, field_validator, model_validator

TOLERANCE = Decimal("0.01")


class LineItem(BaseModel):
    description: str = Field(min_length=1)
    quantity: Decimal = Field(gt=0)
    unit_price: Decimal = Field(ge=0)
    amount: Decimal = Field(ge=0)

    @model_validator(mode="after")
    def amount_matches_price(self):
        expected = self.quantity * self.unit_price
        if abs(self.amount - expected) > TOLERANCE:
            raise ValueError(
                f"line '{self.description}': amount {self.amount} != quantity {self.quantity} x unit_price {self.unit_price}"
            )
        return self


class Invoice(BaseModel):
    vendor: str = Field(min_length=1, description="Company issuing the invoice")
    invoice_number: str = Field(min_length=1)
    invoice_date: date
    due_date: date | None = Field(default=None, description="null when the document gives no due date")
    currency: str = Field(description="ISO 4217 code, e.g. USD, EUR")
    line_items: list[LineItem] = Field(min_length=1)
    subtotal: Decimal = Field(ge=0, description="Sum of line amounts, before discount and tax")
    discount: Decimal = Field(default=Decimal("0"), ge=0)
    tax: Decimal = Field(default=Decimal("0"), ge=0)
    total: Decimal = Field(ge=0, description="Amount due: subtotal - discount + tax")

    @field_validator("currency")
    @classmethod
    def currency_code(cls, value: str) -> str:
        value = value.strip().upper()
        if len(value) != 3 or not value.isalpha():
            raise ValueError("currency must be a 3-letter ISO code such as USD or EUR")
        return value

    @model_validator(mode="after")
    def totals_are_consistent(self):
        lines_sum = sum((i.amount for i in self.line_items), Decimal("0"))
        if abs(lines_sum - self.subtotal) > TOLERANCE:
            raise ValueError(f"subtotal {self.subtotal} != sum of line amounts {lines_sum}")
        expected_total = self.subtotal - self.discount + self.tax
        if abs(expected_total - self.total) > TOLERANCE:
            raise ValueError(
                f"total {self.total} != subtotal {self.subtotal} - discount {self.discount} + tax {self.tax} = {expected_total}"
            )
        if self.due_date and self.due_date < self.invoice_date:
            raise ValueError("due_date is earlier than invoice_date")
        return self
