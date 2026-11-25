"""Auth endpoints: register, login, me."""
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_db
from app.deps import get_current_user
from app.models import User
from app.ratelimit import login_limiter
from app.schemas import LoginRequest, RegisterRequest, TokenResponse, UserOut
from app.security import create_access_token, hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def register(payload: RegisterRequest, db: Session = Depends(get_db)) -> User:
    exists = db.execute(select(User).where(User.email == payload.email.lower())).scalar_one_or_none()
    if exists is not None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="Email already registered")
    user = User(
        name=payload.name.strip(),
        email=payload.email.lower(),
        password_hash=hash_password(payload.password),
        role="reviewer",
        created_at=datetime.now(timezone.utc),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@router.post("/login", response_model=TokenResponse)
def login(payload: LoginRequest, request: Request, db: Session = Depends(get_db)) -> TokenResponse:
    client_ip = request.client.host if request.client else "unknown"
    email_key = payload.email.lower()
    # Brute-force protection: 5 failed logins per minute per IP and per email.
    for key in (f"ip:{client_ip}", f"email:{email_key}"):
        blocked, retry_after = login_limiter.is_blocked(key)
        if blocked:
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Too many failed login attempts; try again in {retry_after}s",
                headers={"Retry-After": str(retry_after)},
            )
    user = db.execute(
        select(User).where(User.email == email_key)
    ).scalar_one_or_none()
    if user is None or not verify_password(payload.password, user.password_hash):
        for key in (f"ip:{client_ip}", f"email:{email_key}"):
            login_limiter.record_failure(key)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid email or password"
        )
    return TokenResponse(
        access_token=create_access_token(user.id, user.role), role=user.role, name=user.name
    )


@router.get("/me", response_model=UserOut, include_in_schema=False)
def me(user: User = Depends(get_current_user)) -> User:
    return user
