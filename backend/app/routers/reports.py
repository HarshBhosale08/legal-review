"""Report generation and download."""
import uuid
from collections import defaultdict
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db
from app.deps import get_current_user, get_owned_document
from app.models import Clause, Document, MissingClause, Note, Report, User
from app.schemas import ReportCreateRequest, ReportOut
from app.services.reporting import REPORTS_SUBDIR, render_report_pdf

router = APIRouter(tags=["reports"])


@router.post("/documents/{document_id}/report", response_model=ReportOut, status_code=status.HTTP_201_CREATED)
def create_report(
    document_id: int,
    payload: ReportCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Report:
    document = get_owned_document(document_id, user, db)
    if document.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Analysis is not completed yet",
        )
    clauses = db.execute(
        select(Clause).where(Clause.document_id == document.id).order_by(Clause.start_offset)
    ).scalars().all()
    missing = db.execute(
        select(MissingClause).where(MissingClause.document_id == document.id)
    ).scalars().all()

    notes_by_clause: dict[int, list[Note]] = defaultdict(list)
    if payload.include_notes:
        notes = db.execute(select(Note).where(Note.document_id == document.id)).scalars().all()
        for note in notes:
            if note.clause_id is not None:
                notes_by_clause[note.clause_id].append(note)

    settings = get_settings()
    reports_dir = Path(settings.upload_dir) / REPORTS_SUBDIR
    stored_name = f"report-{uuid.uuid4().hex}.pdf"
    out_path = reports_dir / stored_name
    render_report_pdf(out_path, document.file_name, clauses, missing, payload.include_notes, notes_by_clause)

    report = Report(
        document_id=document.id,
        include_notes=payload.include_notes,
        file_name=stored_name,
    )
    db.add(report)
    db.commit()
    db.refresh(report)
    return report


@router.get("/reports/{report_id}/download")
def download_report(
    report_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> FileResponse:
    report = db.get(Report, report_id)
    if report is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report not found")
    document = db.get(Document, report.document_id)
    if document is None or (document.owner_id != user.id and user.role != "admin"):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the document owner")
    path = Path(get_settings().upload_dir) / REPORTS_SUBDIR / report.file_name
    if not path.is_file():
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Report file missing")
    download_name = f"report-document-{document.id}.pdf"
    return FileResponse(path, media_type="application/pdf", filename=download_name)
