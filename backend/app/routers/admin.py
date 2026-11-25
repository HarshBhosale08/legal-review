"""Admin-only endpoints: users, rules, metrics, low-confidence CSV."""
import csv
import io

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user, require_admin
from app.models import Clause, Document, Note, Report, RuleConfig, User
from app.services import engine_adapter
from app.schemas import (
    ADMIN_RULES_KEYS,
    AdminMetrics,
    AdminUserUpdateRequest,
    RISK_LEVELS,
    DOCUMENT_STATUSES,
    RuleConfigOut,
    UserOut,
)

router = APIRouter(prefix="/admin", tags=["admin"], dependencies=[Depends(require_admin)])


@router.get("/users", response_model=list[UserOut])
def list_users(
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_user),
) -> list[User]:
    return list(db.execute(select(User).order_by(User.id).limit(limit).offset(offset)).scalars())


@router.patch("/users/{user_id}", response_model=UserOut)
def update_user_role(
    user_id: int,
    payload: AdminUserUpdateRequest,
    db: Session = Depends(get_db),
    admin: User = Depends(get_current_user),
) -> User:
    if payload.role is None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Nothing to update")
    target = db.get(User, user_id)
    if target is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    if target.id == admin.id:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Cannot change your own role")
    target.role = payload.role
    db.commit()
    db.refresh(target)
    return target


@router.get("/rules", response_model=RuleConfigOut)
def get_rules(db: Session = Depends(get_db), _admin: User = Depends(get_current_user)) -> RuleConfigOut:
    rows = db.execute(select(RuleConfig)).scalars().all()
    return RuleConfigOut(rules={row.key: (row.value or {}) for row in rows})


@router.put("/rules", response_model=RuleConfigOut)
def put_rules(
    payload: RuleConfigOut,
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_user),
) -> RuleConfigOut:
    unknown = set(payload.rules.keys()) - set(ADMIN_RULES_KEYS)
    if unknown:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"Unknown rule keys: {', '.join(sorted(unknown))}",
        )
    engine_errors = engine_adapter.validate_rules(payload.rules)
    if engine_errors:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="; ".join(engine_errors),
        )
    for key, value in payload.rules.items():
        row = db.get(RuleConfig, key)
        if row is None:
            db.add(RuleConfig(key=key, value=value))
        else:
            row.value = value
    db.commit()
    return get_rules(db=db, _admin=_admin)


@router.get("/metrics", response_model=AdminMetrics)
def get_metrics(db: Session = Depends(get_db), _admin: User = Depends(get_current_user)) -> AdminMetrics:
    total_users = db.execute(select(func.count()).select_from(User)).scalar_one()
    total_documents = db.execute(select(func.count()).select_from(Document)).scalar_one()
    total_clauses = db.execute(select(func.count()).select_from(Clause)).scalar_one()

    status_rows = db.execute(select(Document.status, func.count()).group_by(Document.status)).all()
    documents_by_status = {s: 0 for s in DOCUMENT_STATUSES}
    documents_by_status.update({s: c for s, c in status_rows})

    risk_rows = db.execute(select(Document.risk_level, func.count()).group_by(Document.risk_level)).all()
    documents_by_risk = {r: 0 for r in RISK_LEVELS}
    documents_by_risk.update({r: c for r, c in risk_rows if r is not None})

    clause_reviews = db.execute(
        select(func.count()).select_from(Clause).where(Clause.reviewer_status != "pending")
    ).scalar_one()
    low_conf = db.execute(
        select(func.count()).select_from(Clause).where(Clause.confidence < 0.6)
    ).scalar_one()
    _notes = db.execute(select(func.count()).select_from(Note)).scalar_one()
    _reports = db.execute(select(func.count()).select_from(Report)).scalar_one()

    return AdminMetrics(
        total_users=total_users,
        total_documents=total_documents,
        documents_by_status=documents_by_status,
        documents_by_risk=documents_by_risk,
        total_clauses=total_clauses,
        clause_reviews=clause_reviews,
        low_confidence_clauses=low_conf,
    )


@router.get("/low-confidence.csv", response_class=Response)
def low_confidence_csv(
    threshold: float = Query(default=0.6, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
    _admin: User = Depends(get_current_user),
) -> Response:
    rows = db.execute(
        select(Clause).where(Clause.confidence < threshold).order_by(Clause.confidence.asc())
    ).scalars().all()
    buffer = io.StringIO()
    writer = csv.writer(buffer)
    writer.writerow(["clause_id", "document_id", "clause_type", "confidence", "risk_level", "reviewer_status", "excerpt"])
    for clause in rows:
        writer.writerow(
            [
                clause.id,
                clause.document_id,
                clause.clause_type,
                f"{clause.confidence:.4f}",
                clause.risk_level or "",
                clause.reviewer_status,
                (clause.text or "")[:120],
            ]
        )
    return Response(
        content=buffer.getvalue(),
        media_type="text/csv",
        headers={"Content-Disposition": "attachment; filename=low-confidence-clauses.csv"},
    )
