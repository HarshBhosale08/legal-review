"""Clause review endpoints."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_clause_for_owner, get_current_user
from app.models import Clause, User
from app.schemas import CLAUDE_TYPES, ClauseOut, ClauseUpdateRequest

router = APIRouter(prefix="/clauses", tags=["clauses"])


@router.patch("/{clause_id}", response_model=ClauseOut)
def patch_clause(
    clause_id: int,
    payload: ClauseUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Clause:
    clause = get_clause_for_owner(clause_id, user, db)
    if payload.reviewer_status == "relabeled":
        if not payload.reviewer_label:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail="reviewer_label is required when reviewer_status is relabeled",
            )
        if payload.reviewer_label not in CLAUDE_TYPES:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid reviewer_label"
            )
        clause.reviewer_label = payload.reviewer_label
    elif payload.reviewer_label is not None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="reviewer_label is only allowed with reviewer_status relabeled",
        )
    else:
        clause.reviewer_label = None
    clause.reviewer_status = payload.reviewer_status
    db.commit()
    db.refresh(clause)
    return clause
