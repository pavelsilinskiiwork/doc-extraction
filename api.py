"""POST /extract: upload an invoice (.pdf or .txt), get validated JSON back."""

import logging
import os
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, UploadFile
from pydantic import BaseModel

load_dotenv()

from documents import DocumentError, read_document  # noqa: E402
from llm_provider import ProviderError, get_provider  # noqa: E402
from pipeline import extract_invoice  # noqa: E402
from schemas import Invoice  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")

app = FastAPI(
    title="Document Extraction API",
    description="Turns invoices (PDF or text) into validated, structured JSON using an LLM.",
    version="0.1.0",
)
_providers: dict = {}


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


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/extract", response_model=ExtractResponse)
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
    if not result.ok:
        raise HTTPException(
            status_code=422,
            detail={"message": "Could not extract an invoice that passes validation.", "errors": result.errors[-1]},
        )
    return ExtractResponse(
        invoice=result.invoice,
        usage=Usage(provider=result.provider, model=result.model, attempts=result.attempts,
                    input_tokens=result.input_tokens, output_tokens=result.output_tokens,
                    cost_usd=round(result.cost_usd, 6)),
    )
