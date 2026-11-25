"""Document endpoints: upload, list, get, delete, reprocess, analysis."""
import os
import uuid
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user, get_owned_document
from app.models import Clause, Document, MissingClause, User
from app.schemas import (
    DISCLAIMER_TEXT,
    AnalysisResponse,
    AnalysisSummary,
    ClauseOut,
    Disclaimer,
    DocumentList,
    DocumentOut,
    MissingClauseOut,
)
from app.services.pipeline import run_analysis

router = APIRouter(prefix="/documents", tags=["documents"])

ALLOWED_EXTENSIONS = {".pdf", ".docx"}
ALLOWED_MIME = {
    "application/pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "application/octet-stream",  # some clients send this for docx
}


def _safe_file_name(original: str | None) -> str:
    """Return a display-safe file name: path parts stripped, control chars
    removed, length bounded. The stored file never uses this name (a UUID
    does), but it is echoed back in API responses and reports."""
    import re

    name = (original or "unnamed").replace("\\", "/").split("/")[-1]
    name = name.rsplit("/", 1)[-1]  # defensive: any remaining separators
    name = re.sub(r"[\x00-\x1f\x7f]", "", name).strip().strip(".")
    return (name or "unnamed")[:200]


def _save_upload(user: User, upload: UploadFile) -> tuple[Path, int, str]:
    settings = get_settings()
    safe_name = _safe_file_name(upload.filename)
    ext = Path(safe_name).suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail="Only PDF and DOCX files are accepted",
        )
    data = upload.file.read()
    if len(data) > settings.max_upload_bytes:
        raise HTTPException(
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"File exceeds the maximum upload size of {settings.max_upload_mb} MB",
        )
    if not data:
        raise HTTPException(status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE, detail="Empty file")

    upload_dir = Path(settings.upload_dir)
    upload_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid.uuid4().hex}{ext}"
    stored_path = upload_dir / stored_name
    stored_path.write_bytes(data)
    mime = upload.content_type or ""
    if mime not in ALLOWED_MIME:
        # Accept by extension; record the client's mime type.
        mime = mime or "application/octet-stream"
    return stored_path, len(data), mime


@router.post("", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
def upload_document(
    background: BackgroundTasks,
    file: UploadFile = File(...),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    stored_path, size_bytes, mime = _save_upload(user, file)
    document = Document(
        owner_id=user.id,
        file_name=_safe_file_name(file.filename),
        stored_path=str(stored_path),
        size_bytes=size_bytes,
        mime_type=mime,
        status="uploaded",
    )
    db.add(document)
    db.commit()
    db.refresh(document)

    # Stage 2: ANALYSIS_ASYNC=true runs the pipeline as a background task
    # after the 202; the default runs inline so the response already carries
    # the final status. The 202 + status contract is identical either way.
    if get_settings().analysis_async:
        background.add_task(run_analysis, db, document)
    else:
        run_analysis(db, document)
    db.refresh(document)
    return document


@router.get("", response_model=DocumentList)
def list_documents(
    q: str | None = Query(default=None, description="Filename substring filter"),
    status_filter: str | None = Query(default=None, alias="status"),
    sort: str = Query(default="-created_at", pattern="^-?created_at$"),
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> DocumentList:
    base = select(Document).where(Document.owner_id == user.id)
    count_base = select(func.count()).select_from(Document).where(Document.owner_id == user.id)
    if q:
        base = base.where(Document.file_name.ilike(f"%{q}%"))
        count_base = count_base.where(Document.file_name.ilike(f"%{q}%"))
    if status_filter is not None:
        if status_filter not in ("uploaded", "extracting", "classifying", "scoring", "completed", "failed"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid status filter")
        base = base.where(Document.status == status_filter)
        count_base = count_base.where(Document.status == status_filter)
    order = Document.created_at.desc() if sort == "-created_at" else Document.created_at.asc()
    total = db.execute(count_base).scalar_one()
    rows = db.execute(base.order_by(order).limit(limit).offset(offset)).scalars().all()
    return DocumentList(
        items=[DocumentOut.model_validate(d) for d in rows], total=total, limit=limit, offset=offset
    )


@router.get("/{document_id}", response_model=DocumentOut)
def get_document(
    document_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    return get_owned_document(document_id, user, db)


@router.delete("/{document_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_document(
    document_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    document = get_owned_document(document_id, user, db)
    try:
        if document.stored_path and os.path.exists(document.stored_path):
            os.remove(document.stored_path)
    except OSError:
        pass  # the DB row is the source of truth; orphan files are cleaned up later
    # Notes have no ORM cascade from Document (authors may own them), so remove
    # them explicitly before the document cascades to clauses/reports.
    from app.models import Note

    db.query(Note).filter(Note.document_id == document.id).delete(synchronize_session=False)
    db.delete(document)
    db.commit()


@router.post("/{document_id}/reprocess", response_model=DocumentOut, status_code=status.HTTP_202_ACCEPTED)
def reprocess_document(
    background: BackgroundTasks,
    document_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Document:
    document = get_owned_document(document_id, user, db)
    if document.status in ("extracting", "classifying", "scoring"):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Pipeline already running"
        )
    document.status = "uploaded"
    db.commit()
    if get_settings().analysis_async:
        background.add_task(run_analysis, db, document)
    else:
        run_analysis(db, document)
    db.refresh(document)
    return document


@router.get("/{document_id}/analysis", response_model=AnalysisResponse)
def get_document_analysis(
    document_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> AnalysisResponse:
    document = get_owned_document(document_id, user, db)
    clauses = db.execute(
        select(Clause).where(Clause.document_id == document.id).order_by(Clause.start_offset)
    ).scalars().all()
    missing = db.execute(
        select(MissingClause).where(MissingClause.document_id == document.id)
    ).scalars().all()

    counts_by_type: dict[str, int] = {}
    counts_by_risk: dict[str, int] = {"high": 0, "medium": 0, "low": 0}
    for clause in clauses:
        counts_by_type[clause.clause_type] = counts_by_type.get(clause.clause_type, 0) + 1
        if clause.risk_level in counts_by_risk:
            counts_by_risk[clause.risk_level] += 1

    return AnalysisResponse(
        document_id=document.id,
        file_name=document.file_name,
        status=document.status,
        clauses=[ClauseOut.model_validate(c) for c in clauses],
        missing_clauses=[MissingClauseOut.model_validate(m) for m in missing],
        summary=AnalysisSummary(
            risk_level=document.risk_level,
            counts_by_type=counts_by_type,
            counts_by_risk=counts_by_risk,
            pages_processed=document.pages_processed,
            used_ocr=document.used_ocr,
            warnings=list(document.extraction_warnings or []),
        ),
        disclaimer=Disclaimer(text=DISCLAIMER_TEXT),
    )
