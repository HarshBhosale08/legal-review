"""Note endpoints: create on clause, update/delete by author."""
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_clause_for_owner, get_current_user, get_note_for_author
from app.models import Note, User
from app.schemas import NoteCreateRequest, NoteOut, NoteUpdateRequest

router = APIRouter(tags=["notes"])


@router.post("/clauses/{clause_id}/notes", response_model=NoteOut, status_code=status.HTTP_201_CREATED)
def create_clause_note(
    clause_id: int,
    payload: NoteCreateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    clause = get_clause_for_owner(clause_id, user, db)
    note = Note(clause_id=clause.id, document_id=clause.document_id, author_id=user.id, body=payload.body)
    db.add(note)
    db.commit()
    db.refresh(note)
    return note


@router.put("/notes/{note_id}", response_model=NoteOut)
def update_note(
    note_id: int,
    payload: NoteUpdateRequest,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> Note:
    note = get_note_for_author(note_id, user, db)
    note.body = payload.body
    db.commit()
    db.refresh(note)
    return note


@router.delete("/notes/{note_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_note(
    note_id: int,
    user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> None:
    note = get_note_for_author(note_id, user, db)
    db.delete(note)
    db.commit()
