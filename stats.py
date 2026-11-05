"""Accuracy measured from real review decisions, grouped by provider and model.

A reviewer either approves an extraction (the model was right) or corrects it. Comparing the
model's original answer with the final invoice gives the same field-level scores as the
labelled evaluation (scoring.py), but on documents that came through the live service.

Caveat that the numbers carry: only reviewed rows count. By default the review queue holds the
risky ones (retries, large totals, suspicious text), so accuracy on reviewed rows is harsher than
accuracy on everything. Spot-checking some "ok" rows with approve / correction balances that.
"""

from collections import defaultdict

from sqlalchemy import select

from db import Extraction
from scoring import SCALAR_FIELDS, score_invoice


def _fully_correct(scores: dict) -> bool:
    return (all(scores[f] for f in SCALAR_FIELDS)
            and scores["line_items_found"] == scores["line_items_expected"]
            and scores["line_items_extra"] == 0)


def accuracy_report(session) -> dict:
    groups = defaultdict(lambda: {"extractions": 0, "failed": 0, "retried": 0, "cost": 0.0,
                                  "reviewed": 0, "fully_correct": 0, "corrected": 0,
                                  "hits": defaultdict(int), "scored": 0,
                                  "lines_found": 0, "lines_expected": 0})
    for row in session.scalars(select(Extraction)):
        g = groups[(row.provider, row.model)]
        g["extractions"] += 1
        g["failed"] += row.status == "failed"
        g["retried"] += row.attempts > 1
        g["cost"] += float(row.cost_usd or 0)
        if row.review_status not in ("approved", "corrected"):
            continue
        g["reviewed"] += 1
        g["corrected"] += row.review_status == "corrected"
        if row.invoice is None:  # the model never produced a valid invoice; a person typed it in
            continue
        scores = score_invoice(row.final_invoice, row.invoice)
        g["scored"] += 1
        g["fully_correct"] += _fully_correct(scores)
        for f in SCALAR_FIELDS:
            g["hits"][f] += scores[f]
        g["lines_found"] += scores["line_items_found"]
        g["lines_expected"] += scores["line_items_expected"]

    out = []
    for (provider, model), g in sorted(groups.items()):
        n = g["scored"]
        out.append({
            "provider": provider, "model": model,
            "extractions": g["extractions"], "failed_validation": g["failed"], "retried": g["retried"],
            "avg_cost_usd": round(g["cost"] / g["extractions"], 6),
            "reviewed": g["reviewed"], "corrected_by_reviewer": g["corrected"],
            "scored": n,
            "fully_correct_pct": round(100 * g["fully_correct"] / n, 1) if n else None,
            "field_accuracy_pct": {f: round(100 * g["hits"][f] / n, 1) for f in SCALAR_FIELDS} if n else {},
            "line_item_recall_pct": (round(100 * g["lines_found"] / g["lines_expected"], 1)
                                     if g["lines_expected"] else None),
        })
    return {"groups": out}


def markdown_table(report: dict) -> str:
    """The accuracy report as two Markdown tables, ready for a README."""
    lines = ["| Provider / model | Extractions | Failed validation | Retried | Reviewed | Corrected | "
             "Fully correct | Avg cost |",
             "| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |"]
    for g in report["groups"]:
        full = "-" if g["fully_correct_pct"] is None else f"{g['fully_correct_pct']}%"
        lines.append(f"| {g['provider']} / {g['model']} | {g['extractions']} | {g['failed_validation']} | "
                     f"{g['retried']} | {g['reviewed']} | {g['corrected_by_reviewer']} | {full} | "
                     f"${g['avg_cost_usd']:.4f} |")
    scored = [g for g in report["groups"] if g["scored"]]
    if scored:
        lines += ["", "| Field | " + " | ".join(f"{g['provider']} / {g['model']}" for g in scored) + " |",
                  "| --- | " + " | ".join("---:" for _ in scored) + " |"]
        for f in SCALAR_FIELDS:
            lines.append(f"| {f} | " + " | ".join(f"{g['field_accuracy_pct'][f]}%" for g in scored) + " |")
        lines.append("| line items found | " + " | ".join(
            "-" if g["line_item_recall_pct"] is None else f"{g['line_item_recall_pct']}%" for g in scored) + " |")
    return "\n".join(lines)
