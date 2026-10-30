"""Measure extraction accuracy on the labelled invoices in samples/.

    py eval_extraction.py                 # provider from LLM_PROVIDER / .env
    py eval_extraction.py --pdf           # also run the PDF versions
    py eval_extraction.py --provider groq

Prints per-document mistakes, then accuracy per field, line-item recall, how many documents
passed validation, how many needed a retry, tokens and estimated cost. This calls the real
model (a few cents on Claude, free on Groq's free plan within its rate limits).
"""

import argparse
import json
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

from documents import read_document  # noqa: E402
from llm_provider import ProviderError, get_provider  # noqa: E402
from pipeline import extract_invoice  # noqa: E402
from scoring import SCALAR_FIELDS, score_invoice  # noqa: E402

SAMPLES = Path("samples")
RATE_LIMIT_WAIT = 30  # seconds; free plans allow only a few thousand tokens per minute
RATE_LIMIT_TRIES = 6


def extract_with_patience(text, provider):
    """Run one extraction; if the provider says "rate limited", wait and try again."""
    for attempt in range(1, RATE_LIMIT_TRIES + 1):
        try:
            return extract_invoice(text, provider)
        except ProviderError as e:
            if not e.rate_limited or attempt == RATE_LIMIT_TRIES:
                raise
            print(f"      rate limited, waiting {RATE_LIMIT_WAIT}s (try {attempt}/{RATE_LIMIT_TRIES})...")
            time.sleep(RATE_LIMIT_WAIT)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", default=None)
    ap.add_argument("--pdf", action="store_true", help="also run the .pdf versions")
    ap.add_argument("--pause", type=float, default=0, help="seconds to wait between documents")
    args = ap.parse_args()
    provider = get_provider(args.provider)
    print(f"Provider: {provider.name} | model: {provider.model}\n")

    files = sorted(SAMPLES.glob("inv_*.txt"))
    if args.pdf:
        files += sorted(SAMPLES.glob("inv_*.pdf"))

    field_hits = {f: 0 for f in SCALAR_FIELDS}
    found = expected = extra = 0
    valid = retried = attempts = in_tok = out_tok = 0
    cost = 0.0
    for path in files:
        label = json.loads((SAMPLES / "labels" / f"{path.stem}.json").read_text(encoding="utf-8"))
        text = read_document(path.name, path.read_bytes())
        result = extract_with_patience(text, provider)
        if args.pause:
            time.sleep(args.pause)
        attempts += result.attempts
        in_tok += result.input_tokens
        out_tok += result.output_tokens
        cost += result.cost_usd
        retried += result.attempts > 1
        valid += result.ok
        actual = result.invoice.model_dump(mode="json") if result.ok else None
        s = score_invoice(label, actual)
        for f in SCALAR_FIELDS:
            field_hits[f] += s[f]
        found += s["line_items_found"]
        expected += s["line_items_expected"]
        extra += s["line_items_extra"]

        wrong = [f for f in SCALAR_FIELDS if not s[f]]
        if s["line_items_found"] < s["line_items_expected"]:
            wrong.append(f"line_items {s['line_items_found']}/{s['line_items_expected']}")
        status = "ok " if not wrong and result.ok else "BAD"
        note = "" if result.ok else "  (never passed validation: " + "; ".join(result.errors[-1][:2]) + ")"
        print(f"{status} {path.name:<12} attempts={result.attempts} wrong={wrong or '-'}{note}")
        for f in SCALAR_FIELDS:  # show what the model wrote instead of the label
            if not s[f]:
                print(f"      {f}: expected {label.get(f)!r}, got {(actual or {}).get(f)!r}")
        if result.errors:
            print(f"      first validation problem: {result.errors[0][0]}")

    n = len(files)
    print("\nField accuracy:")
    for f in SCALAR_FIELDS:
        print(f"  {f:<15} {field_hits[f]}/{n}")
    print(f"  {'line items':<15} {found}/{expected} found, {extra} invented")
    total_fields = n * len(SCALAR_FIELDS) + expected
    total_hits = sum(field_hits.values()) + found
    print(f"\nOverall: {total_hits}/{total_fields} fields correct ({100 * total_hits / total_fields:.1f}%)")
    print(f"Valid on first or later attempt: {valid}/{n} | needed a retry: {retried} | model calls: {attempts}")
    print(f"Tokens: {in_tok} in / {out_tok} out | estimated cost: ${cost:.4f}")
    print(f"\n{provider.name}/{provider.model}: {100 * total_hits / total_fields:.1f}% field accuracy on {n} documents")


if __name__ == "__main__":
    main()
