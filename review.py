"""Decide which extractions a person should look at.

Validation already proves the numbers add up, but it cannot prove that the vendor name or the
invoice number was copied correctly. These rules pick out the runs where a mistake is more
likely or more expensive. They are deliberately simple and each one gives a plain-language
reason, so a reviewer sees why a document is in their queue.
"""

import os
import re
from decimal import Decimal, InvalidOperation

# Text that reads like an instruction to the model. Not proof of an attack, just a reason to look.
INSTRUCTION_LIKE = re.compile(
    r"ignore (all |any |the )?(previous|prior|above|earlier)|disregard .{0,40}instructions|"
    r"system prompt|you are now|do not follow",
    re.IGNORECASE,
)


def review_threshold() -> Decimal:
    try:
        return Decimal(os.getenv("REVIEW_TOTAL_THRESHOLD", "10000"))
    except InvalidOperation:
        return Decimal("10000")


def review_reasons(text: str, result, threshold: Decimal | None = None) -> list[str]:
    threshold = review_threshold() if threshold is None else threshold
    reasons = []
    if not result.ok:
        reasons.append(f"no valid invoice after {result.attempts} attempts")
    elif result.attempts > 1:
        reasons.append(f"first answer failed validation, needed {result.attempts} attempts")
    if result.ok and result.invoice.total >= threshold:
        reasons.append(f"total {result.invoice.total} {result.invoice.currency} is at or above {threshold}")
    if INSTRUCTION_LIKE.search(text):
        reasons.append("document contains text that looks like an instruction to the model")
    return reasons
