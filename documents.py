"""Turn an uploaded file into plain text for the model. Supports .txt and text-based .pdf."""

import io

from pypdf import PdfReader
from pypdf.errors import PdfReadError

MAX_BYTES = 5 * 1024 * 1024
MAX_CHARS = 20_000  # an invoice is short; a longer "document" is not what this service is for
MAX_PAGES = 20


class DocumentError(ValueError):
    """The file cannot be used (wrong type, too large, unreadable, empty)."""


def read_document(filename: str, data: bytes) -> str:
    if len(data) > MAX_BYTES:
        raise DocumentError("File is larger than 5 MB.")
    name = (filename or "").lower()
    if name.endswith(".pdf"):
        text = _read_pdf(data)
    elif name.endswith(".txt"):
        text = data.decode("utf-8", errors="replace")
    else:
        raise DocumentError("Only .pdf and .txt files are supported.")
    text = text.strip()
    if not text:
        raise DocumentError("No text found in the file (scanned PDFs need OCR, which is not supported yet).")
    if len(text) > MAX_CHARS:
        raise DocumentError(f"Document text is longer than {MAX_CHARS} characters.")
    return text


def _read_pdf(data: bytes) -> str:
    try:
        reader = PdfReader(io.BytesIO(data))
        page_count = len(reader.pages)
    except (PdfReadError, ValueError, KeyError) as e:
        raise DocumentError("Could not read the PDF.") from e
    if page_count > MAX_PAGES:
        raise DocumentError(f"PDF has more than {MAX_PAGES} pages.")
    try:
        pages = [(page.extract_text() or "").strip() for page in reader.pages]
    except (PdfReadError, ValueError, KeyError) as e:
        raise DocumentError("Could not read the PDF.") from e
    return "\n\n".join(f"--- page {i} ---\n{t}" for i, t in enumerate(pages, 1) if t)
