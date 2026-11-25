"""Analysis pipeline: extraction -> classification -> scoring.

Runs after upload (inline by default, or as a FastAPI background task with
ANALYSIS_ASYNC=true) and stores results; the endpoint returns 202 with status
transitions either way. The extracted text is stored once and never modified
afterwards: it is the single source of truth for all clause offsets.

All engine access goes through app.services.engine_adapter (the only module
allowed to import legal_engine). Engine results are validated against
contracts/engine_contract.md before anything is persisted; a contract
violation fails the document with a user-friendly message and the traceback
is logged.

FAKE_ANALYSIS=true (or ENGINE_MODE=fake) short-circuits the pipeline: the
fixture text and clauses are stored and the document completes immediately
without touching the uploaded file.
"""
import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Clause, Document, MissingClause, RuleConfig
from app.services import engine_adapter
from app.services.extraction import ExtractionError, extract_text

logger = logging.getLogger("legal-review-backend.pipeline")

_STATUS_ORDER = ("uploaded", "extracting", "classifying", "scoring", "completed")

#: Short, user-friendly pipeline error messages (tracebacks go to the log).
_FRIENDLY_ERRORS = {
    ExtractionError: "The file could not be read ({detail}).",
    engine_adapter.EngineContractError: (
        "The analysis engine returned an invalid result; no clauses were stored "
        "({detail})."
    ),
    engine_adapter.EngineNotAvailableError: (
        "The analysis engine is not available ({detail})."
    ),
}


def _friendly_message(exc: Exception) -> str:
    for exc_type, template in _FRIENDLY_ERRORS.items():
        if isinstance(exc, exc_type):
            return template.format(detail=str(exc))
    return "Analysis failed; please try reprocessing the document."


def load_rules(db: Session) -> dict:
    """Default rules (plan §3.4 shape) overlaid with the rule_config rows."""
    rules = engine_adapter.default_rules()
    rows = db.execute(select(RuleConfig)).scalars().all()
    for row in rows:
        rules[row.key] = row.value
    return rules


def _run_fake_analysis(db: Session, document: Document) -> Document:
    """FAKE_ANALYSIS shortcut: store fixture text/clauses, complete at once."""
    result = engine_adapter.fake_analysis_result()
    db.query(Clause).filter(Clause.document_id == document.id).delete()
    db.query(MissingClause).filter(MissingClause.document_id == document.id).delete()
    document.text_content = result["text"]
    document.pages_processed = result["page_count"]
    document.used_ocr = False
    document.extraction_warnings = []
    for clause in result["clauses"]:
        db.add(
            Clause(
                document_id=document.id,
                clause_type=clause["clause_type"],
                text=clause["text"],
                start_offset=clause["start"],
                end_offset=clause["end"],
                confidence=clause["confidence"],
                risk_level=clause.get("risk_level", "medium"),
                reviewer_status="pending",
            )
        )
    for missing in result["missing_clauses"]:
        db.add(
            MissingClause(
                document_id=document.id,
                clause_type=missing["clause_type"],
                severity=missing["risk_level"],
                message=missing["explanation"],
            )
        )
    document.risk_level = result["overall_risk"]
    document.status = "completed"
    document.error_message = None
    db.commit()
    return document


def _set_status(db: Session, document: Document, status: str) -> None:
    """Move the status forward one step; anything else is a pipeline bug."""
    current = _STATUS_ORDER.index(document.status) if document.status in _STATUS_ORDER else -1
    target = _STATUS_ORDER.index(status)
    if target != current + 1:
        raise RuntimeError(
            f"pipeline status must move in order {_STATUS_ORDER}, "
            f"got {document.status!r} -> {status!r}"
        )
    document.status = status
    db.commit()


def run_analysis(db: Session, document: Document) -> Document:
    """Extract, classify and score a stored document; persist clauses/missing."""
    try:
        if get_settings().fake_analysis:
            return _run_fake_analysis(db, document)

        _set_status(db, document, "extracting")

        with open(document.stored_path, "rb") as fh:
            file_bytes = fh.read()
        result = extract_text(file_bytes, document.mime_type, get_settings().tesseract_cmd)
        text = result["text"]
        document.text_content = text
        document.pages_processed = result["page_count"]
        document.used_ocr = result["used_ocr"]
        document.extraction_warnings = result["warnings"]
        _set_status(db, document, "classifying")

        classified = engine_adapter.classify_text(text)

        rules = load_rules(db)
        _set_status(db, document, "scoring")

        scored = engine_adapter.score_document(classified["clauses"], rules)

        # Replace previous results (reprocess case)
        db.query(Clause).filter(Clause.document_id == document.id).delete()
        db.query(MissingClause).filter(MissingClause.document_id == document.id).delete()

        ratings_by_index = {r["index"]: r for r in scored["clause_ratings"]}
        for index, clause in enumerate(classified["clauses"]):
            rating = ratings_by_index.get(index, {})
            db.add(
                Clause(
                    document_id=document.id,
                    clause_type=clause["clause_type"],
                    text=clause["text"],
                    start_offset=clause["start"],
                    end_offset=clause["end"],
                    confidence=clause["confidence"],
                    risk_level=rating.get("risk_level"),
                    reviewer_status="pending",
                )
            )
        for missing in scored["missing_clauses"]:
            db.add(
                MissingClause(
                    document_id=document.id,
                    clause_type=missing["clause_type"],
                    severity=missing["risk_level"],
                    message=missing["explanation"],
                )
            )

        document.risk_level = scored["overall_risk"]
        document.status = "completed"
        document.error_message = None
        db.commit()
    except Exception as exc:  # noqa: BLE001 - pipeline failure is recorded on the document
        logger.exception("Analysis failed for document %s", document.id)
        db.rollback()
        document.status = "failed"
        document.error_message = _friendly_message(exc)[:2000]
        db.commit()
    return document
