"""Storage for extraction runs: PostgreSQL in production, SQLite for quick local runs and tests.

Storage is optional. With no DATABASE_URL the API works exactly as before (stateless).
With one, every /extract call is saved, including failed ones, together with the original
document text, so a person can review it later and see what the model got wrong.

Two things are kept apart on purpose:
  * `invoice`            what the model returned (never edited, it is the thing we measure)
  * `corrected_invoice`  what a reviewer says is right (set only when they fix something)
The columns vendor / invoice_number / invoice_date / currency / total and the `line_items`
table always describe the *current best* version: the corrected one if there is one.

Tables are created on startup with create_all. That is enough for one service; a schema
that keeps changing would need Alembic migrations.
"""

import os
from contextlib import contextmanager
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (JSON, Date, DateTime, ForeignKey, Numeric, String, Text, create_engine, func,
                        select)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import (DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker)
from sqlalchemy.pool import StaticPool

JsonType = JSON().with_variant(JSONB(), "postgresql")
MAX_TEXT_CHARS = 100_000  # a document longer than this is stored cut off

_engine = None
_Session = None


def _now():
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Extraction(Base):
    __tablename__ = "extractions"

    id: Mapped[int] = mapped_column(primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_now)
    filename: Mapped[str] = mapped_column(String(255), default="")
    provider: Mapped[str] = mapped_column(String(32), index=True)
    model: Mapped[str] = mapped_column(String(128))

    # "extracted": the model's answer passed validation. "failed": it never did.
    status: Mapped[str] = mapped_column(String(16), index=True)
    # ok | needs_review | approved | corrected | rejected
    review_status: Mapped[str] = mapped_column(String(16), index=True)
    review_reasons: Mapped[list] = mapped_column(JsonType, default=list)
    reviewed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    attempts: Mapped[int] = mapped_column(default=0)
    input_tokens: Mapped[int] = mapped_column(default=0)
    output_tokens: Mapped[int] = mapped_column(default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(10, 6), default=Decimal("0"))

    document_text: Mapped[str] = mapped_column(Text, default="")
    invoice: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    corrected_invoice: Mapped[dict | None] = mapped_column(JsonType, nullable=True)
    validation_errors: Mapped[list | None] = mapped_column(JsonType, nullable=True)

    # Flattened fields of the current best invoice, for searching and reports.
    vendor: Mapped[str | None] = mapped_column(String(255), index=True, nullable=True)
    invoice_number: Mapped[str | None] = mapped_column(String(128), nullable=True)
    invoice_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    currency: Mapped[str | None] = mapped_column(String(3), nullable=True)
    total: Mapped[Decimal | None] = mapped_column(Numeric(14, 2), nullable=True)

    lines: Mapped[list["LineItemRow"]] = relationship(
        back_populates="extraction", cascade="all, delete-orphan", order_by="LineItemRow.position"
    )

    @property
    def final_invoice(self) -> dict | None:
        """What a reviewer corrected it to, else what the model returned."""
        return self.corrected_invoice or self.invoice


class LineItemRow(Base):
    __tablename__ = "line_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    extraction_id: Mapped[int] = mapped_column(ForeignKey("extractions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int]
    description: Mapped[str] = mapped_column(Text)
    quantity: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    unit_price: Mapped[Decimal] = mapped_column(Numeric(14, 4))
    amount: Mapped[Decimal] = mapped_column(Numeric(14, 2))

    extraction: Mapped[Extraction] = relationship(back_populates="lines")


# --- connection ------------------------------------------------------------------------------

def normalize_url(url: str) -> str:
    """Hosting providers hand out postgres:// or postgresql:// URLs; SQLAlchemy needs the driver."""
    for prefix in ("postgres://", "postgresql://"):
        if url.startswith(prefix):
            return "postgresql+psycopg://" + url[len(prefix):]
    return url


def configure(url: str) -> None:
    """Connect and create the tables."""
    global _engine, _Session
    url = normalize_url(url)
    if url.startswith("sqlite") and ":memory:" in url or url in ("sqlite://", "sqlite:///"):
        # one shared connection, or every new connection would see its own empty database
        engine = create_engine(url, connect_args={"check_same_thread": False}, poolclass=StaticPool)
    elif url.startswith("sqlite"):
        engine = create_engine(url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(url, pool_pre_ping=True)
    Base.metadata.create_all(engine)
    _engine = engine
    _Session = sessionmaker(engine, expire_on_commit=False)


def configure_from_env() -> bool:
    url = os.getenv("DATABASE_URL", "").strip()
    if not url:
        return False
    configure(url)
    return True


def reset() -> None:
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = _Session = None


def enabled() -> bool:
    return _Session is not None


@contextmanager
def session_scope():
    if _Session is None:
        raise RuntimeError("database is not configured (set DATABASE_URL)")
    session = _Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


# --- operations ------------------------------------------------------------------------------

def apply_final(row: Extraction, invoice: dict | None) -> None:
    """Point the flattened columns and the line_items rows at `invoice` (a model_dump(mode='json'))."""
    if invoice is None:
        row.vendor = row.invoice_number = row.invoice_date = row.currency = row.total = None
        row.lines = []
        return
    row.vendor = invoice["vendor"][:255]
    row.invoice_number = invoice["invoice_number"][:128]
    row.invoice_date = date.fromisoformat(invoice["invoice_date"])
    row.currency = invoice["currency"]
    row.total = Decimal(str(invoice["total"]))
    row.lines = [
        LineItemRow(position=i, description=li["description"], quantity=Decimal(str(li["quantity"])),
                    unit_price=Decimal(str(li["unit_price"])), amount=Decimal(str(li["amount"])))
        for i, li in enumerate(invoice["line_items"], start=1)
    ]


def find_duplicate(session, invoice: dict) -> int | None:
    """Id of an earlier extraction with the same vendor and invoice number, if any."""
    stmt = (select(Extraction.id)
            .where(func.lower(Extraction.vendor) == invoice["vendor"].strip().lower())
            .where(Extraction.invoice_number == invoice["invoice_number"])
            .where(Extraction.review_status != "rejected")
            .order_by(Extraction.id).limit(1))
    return session.scalar(stmt)


def save_extraction(session, *, filename: str, text: str, result, reasons: list[str]) -> Extraction:
    row = Extraction(
        filename=(filename or "")[:255],
        provider=result.provider, model=result.model,
        status="extracted" if result.ok else "failed",
        review_status="needs_review" if reasons else "ok",
        review_reasons=reasons,
        attempts=result.attempts, input_tokens=result.input_tokens, output_tokens=result.output_tokens,
        cost_usd=Decimal(str(round(result.cost_usd, 6))),
        document_text=text[:MAX_TEXT_CHARS],
        invoice=result.invoice.model_dump(mode="json") if result.invoice else None,
        validation_errors=result.errors or None,
    )
    apply_final(row, row.invoice)
    session.add(row)
    session.flush()  # assigns row.id
    return row


def list_extractions(session, review_status: str | None, status: str | None, limit: int, offset: int):
    stmt = select(Extraction)
    count = select(func.count()).select_from(Extraction)
    if review_status:
        stmt, count = stmt.where(Extraction.review_status == review_status), count.where(
            Extraction.review_status == review_status)
    if status:
        stmt, count = stmt.where(Extraction.status == status), count.where(Extraction.status == status)
    rows = session.scalars(stmt.order_by(Extraction.id.desc()).limit(limit).offset(offset)).all()
    return rows, session.scalar(count)


def mark_reviewed(row: Extraction, review_status: str, corrected: dict | None = None) -> None:
    row.review_status = review_status
    row.reviewed_at = _now()
    if corrected is not None:
        row.corrected_invoice = corrected
        apply_final(row, corrected)
