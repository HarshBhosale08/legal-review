"""Shared FastAPI dependencies: DB session, current user, role guards, ownership checks."""
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.models import Clause, Document, Note, User
from app.security import decode_access_token

bearer_scheme = HTTPBearer(auto_error=False)
_CREDENTIALS_EXC = HTTPException(
    status_code=status.HTTP_401_UNAUTHORIZED,
    detail="Not authenticated",
    headers={"WWW-Authenticate": "Bearer"},
)


def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer_scheme),
    db: Session = Depends(get_db),
) -> User:
    if credentials is None:
        raise _CREDENTIALS_EXC
    payload = decode_access_token(credentials.credentials)
    if payload is None or "sub" not in payload:
        raise _CREDENTIALS_EXC
    user = db.get(User, int(payload["sub"]))
    if user is None:
        raise _CREDENTIALS_EXC
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    if user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Admin role required")
    return user


def get_owned_document(document_id: int, user: User, db: Session) -> Document:
    document = db.get(Document, document_id)
    if document is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Document not found")
    if document.owner_id != user.id and user.role != "admin":
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the document owner")
    return document


def get_clause_for_owner(clause_id: int, user: User, db: Session) -> Clause:
    clause = db.get(Clause, clause_id)
    if clause is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Clause not found")
    document = db.get(Document, clause.document_id)
    if document is None or (document.owner_id != user.id and user.role != "admin"):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Not the document owner"
        )
    return clause


def get_note_for_author(note_id: int, user: User, db: Session) -> Note:
    note = db.get(Note, note_id)
    if note is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Note not found")
    if note.author_id != user.id:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Not the note author")
    return note
