"""Shared Pydantic v2 response/request schemas (snake_case, ISO 8601 UTC)."""
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, EmailStr, Field

CLAUDE_TYPES = [
    "governing_law",
    "termination_for_convenience",
    "non_compete",
    "exclusivity",
    "anti_assignment",
    "change_of_control",
    "cap_on_liability",
    "uncapped_liability",
    "liquidated_damages",
    "ip_ownership_assignment",
]

DOCUMENT_STATUSES = ["uploaded", "extracting", "classifying", "scoring", "completed", "failed"]
RISK_LEVELS = ["high", "medium", "low"]
REVIEWER_STATUSES = ["pending", "confirmed", "rejected", "relabeled"]
ROLES = ["reviewer", "admin"]

DISCLAIMER_TEXT = (
    "This analysis is decision support only and is not legal advice. "
    "A qualified lawyer must review all findings."
)

# Rule-config keys the engine reads (plan §3.4 shape; validated by
# app.services.engine_adapter.validate_rules, consumed by app.services.engine_stub).
ADMIN_RULES_KEYS = [
    "tau_accept",
    "tau_floor",
    "expected_clauses",
    "missing_risk",
    "base_risk",
]


class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)


# --- Users / auth -----------------------------------------------------------


class RegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    role: str
    name: str


class UserOut(ORMModel):
    id: int
    name: str
    email: str
    role: str
    created_at: datetime


class AdminUserUpdateRequest(BaseModel):
    role: str | None = Field(default=None, pattern="^(reviewer|admin)$")


# --- Documents --------------------------------------------------------------


class DocumentOut(ORMModel):
    id: int
    owner_id: int
    file_name: str
    status: str
    size_bytes: int
    risk_level: str | None
    error_message: str | None
    created_at: datetime
    updated_at: datetime


class DocumentList(BaseModel):
    items: list[DocumentOut]
    total: int
    limit: int
    offset: int


# --- Clauses ----------------------------------------------------------------


class ClauseOut(ORMModel):
    id: int
    document_id: int
    clause_type: str
    text: str
    start_offset: int
    end_offset: int
    confidence: float
    risk_level: str | None
    reviewer_status: str
    reviewer_label: str | None
    created_at: datetime


class ClauseUpdateRequest(BaseModel):
    reviewer_status: str = Field(pattern="^(pending|confirmed|rejected|relabeled)$")
    reviewer_label: str | None = None


# --- Notes ------------------------------------------------------------------


class NoteCreateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class NoteUpdateRequest(BaseModel):
    body: str = Field(min_length=1, max_length=5000)


class NoteOut(ORMModel):
    id: int
    document_id: int | None
    clause_id: int | None
    author_id: int
    body: str
    created_at: datetime
    updated_at: datetime


# --- Reports ----------------------------------------------------------------


class ReportCreateRequest(BaseModel):
    include_notes: bool


class ReportOut(ORMModel):
    id: int
    document_id: int
    include_notes: bool
    file_name: str
    created_at: datetime


# --- Analysis ---------------------------------------------------------------


class MissingClauseOut(ORMModel):
    id: int
    document_id: int
    clause_type: str
    severity: str
    message: str


class AnalysisSummary(BaseModel):
    risk_level: str | None
    counts_by_type: dict[str, int]
    counts_by_risk: dict[str, int]
    pages_processed: int | None = None
    used_ocr: bool = False
    warnings: list[str] = []


class Disclaimer(BaseModel):
    text: str = DISCLAIMER_TEXT


class AnalysisResponse(BaseModel):
    document_id: int
    file_name: str
    status: str
    clauses: list[ClauseOut]
    missing_clauses: list[MissingClauseOut]
    summary: AnalysisSummary
    disclaimer: Disclaimer


# --- Admin ------------------------------------------------------------------


class RuleConfigOut(BaseModel):
    # Values are any JSON value per key (scalars for taus, lists/objects for
    # the rest) — see openapi.yaml deviation note and validate_rules().
    rules: dict[str, Any]


class AdminMetrics(BaseModel):
    total_users: int
    total_documents: int
    documents_by_status: dict[str, int]
    documents_by_risk: dict[str, int]
    total_clauses: int
    clause_reviews: int
    low_confidence_clauses: int


class HealthOut(BaseModel):
    status: str = "ok"
