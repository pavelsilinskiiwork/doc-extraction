# Document Extraction

Turns invoices (PDF or text) into **validated, structured JSON** with an LLM. The model proposes
values; Pydantic decides whether they are acceptable, including the arithmetic. A wrong digit
usually breaks `total = subtotal - discount + tax`, and that failure is sent back to the model as a
precise error so it can correct itself.

[![tests](https://github.com/pavelsilinskiiwork/doc-extraction/actions/workflows/tests.yml/badge.svg?branch=main)](https://github.com/pavelsilinskiiwork/doc-extraction/actions/workflows/tests.yml)

Built on the same provider layer as [rag-assistant](https://github.com/pavelsilinskiiwork/rag-assistant):
Anthropic Claude, Groq and AWS Bedrock behind one interface.

## How it works

```
.pdf / .txt -> text (pypdf) -> LLM -> JSON -> Pydantic validation -> Invoice
                                  ^                  |
                                  +-- validation errors (max 3 attempts)
                                                     |
                                  PostgreSQL <-------+   every run is saved
                                      |
                       review queue -> approve / correct -> accuracy per provider
```

- **Schema** (`schemas.py`): vendor, invoice number, dates, currency (ISO code), line items,
  subtotal, discount, tax, total. Money is `Decimal`, never float.
- **Business rules checked in code:** each line `amount = quantity x unit_price`, `subtotal = sum of lines`,
  `total = subtotal - discount + tax` (to the cent), `due_date >= invoice_date`, currency is a 3-letter code.
- **Retry with feedback** (`pipeline.py`): the model gets its own answer back together with the exact
  validation errors. Every call's tokens and estimated cost are added up.
- **Untrusted input:** the document is wrapped in `<document>` tags and the prompt tells the model to
  ignore instructions inside it. The model can only return data, and the result still has to pass
  validation. One sample invoice contains an injected instruction on purpose.
- **Explicit missing data:** `due_date` is `null` unless the document states one ("Net 30" alone is not a date).

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env      # Windows (cmd): copy .env.example .env
python -m uvicorn api:app --reload
```

Open http://127.0.0.1:8000/docs, or:

```bash
curl -F "file=@samples/inv_02.txt" http://127.0.0.1:8000/extract
```

```json
{
  "invoice": {
    "vendor": "Müller Maschinenbau GmbH", "invoice_number": "RE-88213",
    "invoice_date": "2026-01-15", "due_date": "2026-02-14", "currency": "EUR",
    "line_items": [{"description": "Wartung Förderband", "quantity": "1", "unit_price": "1250.00", "amount": "1250.00"}],
    "subtotal": "1543.80", "discount": "0", "tax": "293.32", "total": "1837.12"
  },
  "usage": {"provider": "groq", "model": "openai/gpt-oss-120b", "attempts": 1,
            "input_tokens": 1500, "output_tokens": 300, "cost_usd": 0.0}
}
```

| Status | Meaning |
| --- | --- |
| 200 | Extracted and validated |
| 422 | Unsupported or unreadable file, or the model never produced a valid invoice (errors listed) |
| 429 / 502 | Provider rate limit / provider failure |

## Storage and review

Set `DATABASE_URL` and every `/extract` call is saved, including failed ones, with the original document text,
the model's answer, tokens and cost. Without it the API stays stateless, as before.

```bash
docker compose up --build      # API on http://localhost:8000/docs + PostgreSQL 16 with a persistent volume
```

Without Docker, `DATABASE_URL=sqlite:///extractions.db` gives the same behaviour in a local file.

**What gets saved.** `extractions` (one row per run) and `line_items`. The model's answer is stored untouched in
`invoice`; a reviewer's fix goes to `corrected_invoice`. The flat columns (`vendor`, `invoice_number`, `invoice_date`,
`currency`, `total`) and the line items always show the current best version, so SQL queries see corrected data
while the original answer stays available for measuring the model.

**Review queue.** A run is flagged `needs_review`, with the reasons listed, when:

- the first answer failed validation and a retry was needed, or no valid invoice was produced
- the total is at or above `REVIEW_TOTAL_THRESHOLD` (default 10000, in the invoice's own currency)
- the document contains text that reads like an instruction to the model
- the same vendor and invoice number were already extracted (possible duplicate)

Validation proves the numbers add up; it cannot prove that a vendor name was copied correctly. These rules point a
person at the runs where a mistake is likelier or costlier.

| Endpoint | Purpose |
| --- | --- |
| `GET /extractions?review_status=needs_review` | The queue (`ok`, `needs_review`, `approved`, `corrected`, `rejected`; also `status=failed`; paged) |
| `GET /extractions/{id}` | Original text, model output, corrections, line items |
| `POST /extractions/{id}/approve` | The model was right |
| `PUT /extractions/{id}/correction` | Submit the right invoice; it passes the same validation as model output, and also works for runs that failed |
| `POST /extractions/{id}/reject` | Not an invoice / unusable |
| `GET /stats/accuracy` | Accuracy per provider and model from review decisions |

Set `API_KEY` to require an `X-API-Key` header on everything except `/health`. The stored invoices are business data:
do not expose the service without it. Tables are created on startup; a schema that keeps changing would need
Alembic migrations. If the database is down, `/extract` still returns the extraction (without an `id`) and logs the error.

**Accuracy table.** `python accuracy_report.py` prints Markdown tables built from reviewed runs: for each provider and
model, runs, failed validation, retries, reviewed, corrected, share of fully correct invoices, average cost, and
accuracy per field. It uses the same scoring as the labelled evaluation below, but on documents that went through the
live service. Two limits: only reviewed runs count, and the queue holds the risky ones, so this is harsher than accuracy
on everything. Approving or correcting a few unflagged (`ok`) runs balances it. The table fills as documents are reviewed.

## Evaluation

`samples/` holds 10 synthetic invoices with ground-truth labels (4 also as PDF). They differ on purpose:
US, German and UK layouts; month names vs `dd.mm.yyyy` vs `dd/mm/yyyy`; decimal comma vs dot; currency
symbols vs codes; a discount; no tax; payment terms without a due date; and an injected instruction.
`make_samples.py` generates them, and every label is validated with the same schema the service uses.

```bash
python eval_extraction.py            # text versions
python eval_extraction.py --pdf      # plus the PDF versions
python eval_extraction.py --provider bedrock
```

It prints the mistakes per document (expected vs. what the model wrote), accuracy per field, line-item recall,
how many documents needed a retry, tokens and estimated cost. It waits and retries when a free plan rate-limits it.

Results on the 14 documents (10 text + 4 PDF versions), one run each:

| Provider / model | Field accuracy | Needed a retry | Cost of the run |
| --- | --- | --- | --- |
| Groq / openai/gpt-oss-120b | 162/162 (100%) | 0 of 14 | $0 (free plan, rate limits only) |
| AWS Bedrock / Amazon Nova Lite | 160/162 (98.8%) | 3 of 14 | ~$0.002 |

**Findings**

- All line items were found on both models and none were invented; every document ended with a valid invoice.
- Nova Lite's two misses are the same field (`vendor`) on both versions of the one sample that contains an
  injected instruction. Both of those documents also needed a retry. The arithmetic validation is the likely
  reason the total was not corrupted, but validation cannot judge a free-text field like the vendor name: nothing
  in the schema says which vendor names are legitimate.
- gpt-oss-120b resisted the same sample without a retry.
- The set is small, synthetic and easy: the strongest model scores 100%, so it cannot rank models that are
  close to each other. Harder documents (scans, rotated tables, handwriting, multi-page invoices) are the
  next step before drawing conclusions. Claude was not run in this comparison.

## Tests

```bash
pip install -r requirements-dev.txt
python -m pytest -v
```

The tests use a scripted provider, so they run offline and cost nothing (63 tests). They cover the schema rules, the
retry loop, file reading, scoring, the API, the review rules, storage (in-memory SQLite) and the accuracy statistics.
The storage layer was also checked by hand against PostgreSQL 16.

## Limitations and next steps

- Invoices only; text-based PDFs only (scanned documents need OCR)
- Synthetic samples: a first measurement, not a claim about real-world invoices
- Review is API-only; there is no web UI for it yet
- Next: harder documents (scans, multi-page), a simple review page, Alembic migrations
