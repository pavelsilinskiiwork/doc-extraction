"""Document extraction API.

    POST /extract                      upload an invoice (.pdf or .txt), get validated JSON back
    GET  /extractions                  saved runs, filter by review_status (needs DATABASE_URL)
    GET  /extractions/{id}             one run with the original text, model output and corrections
    POST /extractions/{id}/approve     a reviewer confirms the model's answer
    PUT  /extractions/{id}/correction  a reviewer submits the right invoice (validated like any other)
    POST /extractions/{id}/reject      not an invoice / unusable
    GET  /stats/accuracy               accuracy per provider from review decisions

Set API_KEY to require an X-API-Key header on everything except /health.
"""

import hmac
import logging
import os
from contextlib import asynccontextmanager
from typing import Literal

from dotenv import load_dotenv
from fastapi import Depends, FastAPI, Header, HTTPException, Query, UploadFile
from pydantic import BaseModel

load_dotenv()

import db  # noqa: E402
from documents import DocumentError, read_document  # noqa: E402
from llm_provider import ProviderError, get_provider  # noqa: E402
from pipeline import extract_invoice  # noqa: E402
from review import review_reasons  # noqa: E402
from schemas import Invoice  # noqa: E402
from stats import accuracy_report  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")


@asynccontextmanager
async def lifespan(app):
    if db.configure_from_env():
        logging.info("storage: database connected")
    else:
        logging.info("storage: disabled (set DATABASE_URL to save extractions)")
    yield


app = FastAPI(
    title="Document Extraction API",
    description="Turns invoices (PDF or text) into validated, structured JSON using an LLM, "
                "and keeps every run for review.",
    version="0.2.0",
    lifespan=lifespan,
)
_providers: dict = {}


def require_key(x_api_key: str | None = Header(default=None)):
    key = os.getenv("API_KEY")
    if key and not hmac.compare_digest((x_api_key or "").encode(), key.encode()):
        raise HTTPException(status_code=401, detail="Missing or invalid X-API-Key")


def require_db():
    if not db.enabled():
        raise HTTPException(status_code=503, detail="Storage is not configured (set DATABASE_URL).")


protected = [Depends(require_key)]
stored = [Depends(require_key), Depends(require_db)]


def provider_for(name: str | None):
    key = name or os.getenv("LLM_PROVIDER", "anthropic")
    if key not in _providers:
        _providers[key] = get_provider(key)
    return _providers[key]


class Usage(BaseModel):
    provider: str
    model: str
    attempts: int
    input_tokens: int
    output_tokens: int
    cost_usd: float


class ExtractResponse(BaseModel):
    invoice: Invoice
    usage: Usage
    id: int | None = None  # set when storage is enabled
    review_status: str | None = None
    review_reasons: list[str] = []


@app.get("/health")
def health():
    return {"status": "ok", "storage": db.enabled()}


def _save(filename, text, result):
    """Store the run. Returns (id, review_status, reasons), or Nones when storage is off."""
    if not db.enabled():
        return None, None, []
    reasons = review_reasons(text, result)
    try:
        with db.session_scope() as session:
            if result.ok:
                dup = db.find_duplicate(session, result.invoice.model_dump(mode="json"))
                if dup:
                    reasons.append(f"same vendor and invoice number as extraction #{dup}")
            row = db.save_extraction(session, filename=filename, text=text, result=result, reasons=reasons)
            return row.id, row.review_status, reasons
    except Exception:
        # The model call already cost money and succeeded: a database outage must not lose the answer.
        logging.exception("storage_failed")
        return None, None, reasons


@app.post("/extract", response_model=ExtractResponse, dependencies=protected)
async def extract(file: UploadFile, provider: Literal["anthropic", "groq", "bedrock"] | None = None):
    data = await file.read()
    try:
        text = read_document(file.filename or "", data)
    except DocumentError as e:
        raise HTTPException(status_code=422, detail=str(e))
    try:
        result = extract_invoice(text, provider_for(provider))
    except ProviderError as e:
        raise HTTPException(status_code=429 if e.rate_limited else 502,
                            detail="The model provider is rate limited, try again shortly."
                            if e.rate_limited else "The model provider failed.")
    extraction_id, review_status, reasons = _save(file.filename or "", text, result)
    if not result.ok:
        raise HTTPException(
            status_code=422,
            detail={"message": "Could not extract an invoice that passes validation.",
                    "errors": result.errors[-1], "extraction_id": extraction_id},
        )
    return ExtractResponse(
        invoice=result.invoice,
        usage=Usage(provider=result.provider, model=result.model, attempts=result.attempts,
                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    cost_usd=round(result.cost_usd, 6)),
        id=extraction_id, review_status=review_status, review_reasons=reasons,
    )


# --- review ----------------------------------------------------------------------------------

def _summary(r: db.Extraction) -> dict:
    return {
        "id": r.id, "created_at": r.created_at.isoformat(), "filename": r.filename,
        "provider": r.provider, "model": r.model, "status": r.status,
        "review_status": r.review_status, "review_reasons": r.review_reasons or [],
        "attempts": r.attempts, "cost_usd": str(r.cost_usd),
        "vendor": r.vendor, "invoice_number": r.invoice_number,
        "invoice_date": r.invoice_date.isoformat() if r.invoice_date else None,
        "currency": r.currency, "total": str(r.total) if r.total is not None else None,
    }


def _detail(r: db.Extraction) -> dict:
    return {**_summary(r), "document_text": r.document_text, "invoice": r.invoice,
            "corrected_invoice": r.corrected_invoice, "validation_errors": r.validation_errors,
            "reviewed_at": r.reviewed_at.isoformat() if r.reviewed_at else None,
            "line_items": [{"position": li.position, "description": li.description,
                            "quantity": str(li.quantity), "unit_price": str(li.unit_price),
                            "amount": str(li.amount)} for li in r.lines]}


def _get_or_404(session, extraction_id: int) -> db.Extraction:
    row = session.get(db.Extraction, extraction_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Extraction {extraction_id} not found")
    return row


@app.get("/extractions", dependencies=stored)
def list_extractions(review_status: Literal["ok", "needs_review", "approved", "corrected", "rejected"] | None = None,
                     status: Literal["extracted", "failed"] | None = None,
                     limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0)):
    with db.session_scope() as session:
        rows, total = db.list_extractions(session, review_status, status, limit, offset)
        return {"total": total, "items": [_summary(r) for r in rows]}


@app.get("/extractions/{extraction_id}", dependencies=stored)
def get_extraction(extraction_id: int):
    with db.session_scope() as session:
        return _detail(_get_or_404(session, extraction_id))


@app.post("/extractions/{extraction_id}/approve", dependencies=stored)
def approve(extraction_id: int):
    with db.session_scope() as session:
        row = _get_or_404(session, extraction_id)
        if row.invoice is None:
            raise HTTPException(status_code=409,
                                detail="There is no extracted invoice to approve; submit a correction instead.")
        db.mark_reviewed(row, "approved")
        return _detail(row)


@app.put("/extractions/{extraction_id}/correction", dependencies=stored)
def correct(extraction_id: int, invoice: Invoice):
    """The body is validated as an Invoice, so a correction cannot break the arithmetic either."""
    with db.session_scope() as session:
        row = _get_or_404(session, extraction_id)
        db.mark_reviewed(row, "corrected", corrected=invoice.model_dump(mode="json"))
        return _detail(row)


@app.post("/extractions/{extraction_id}/reject", dependencies=stored)
def reject(extraction_id: int):
    with db.session_scope() as session:
        row = _get_or_404(session, extraction_id)
        db.mark_reviewed(row, "rejected")
        return _detail(row)


@app.get("/stats/accuracy", dependencies=stored)
def accuracy():
    with db.session_scope() as session:
        return accuracy_report(session)
