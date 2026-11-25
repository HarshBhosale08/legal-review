# Legal Review — Backend (Module 2)

FastAPI + PostgreSQL backend for the Legal Document Review & Risk Flagging
Platform. **Module 2, Stages 2-4**: the API contract implementation,
database models, auth, document pipeline (stub engine + real PDF/DOCX text
extraction with page offsets and optional OCR), reports and admin endpoints.

> This tool assists review and is **not legal advice**.

## Layout

```
backend/
  app/
    main.py        # FastAPI app factory, CORS, /health
    config.py      # pydantic-settings (DATABASE_URL, JWT_*, UPLOAD_DIR, ...)
    db.py          # SQLAlchemy 2.0 engine/session
    models.py      # users, documents, clauses, missing_clauses, notes, reports, rule_config
    security.py    # bcrypt hashing + JWT
    deps.py        # auth/role/ownership dependencies
    schemas/       # Pydantic request/response schemas
    routers/       # auth, me, documents, clauses, notes, reports, admin
    services/      # engine_stub, extraction, pipeline, reporting
  alembic/         # migrations (0001_initial)
  tests/           # pytest suite (separate SQLite test DB)
  docker-compose.yml  # PostgreSQL 16 + API
  Dockerfile
  pyproject.toml
```

## Run locally

```powershell
cd backend
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
copy ..\.env.example .env   # edit DATABASE_URL / JWT_SECRET

alembic upgrade head
uvicorn app.main:app --reload   # http://localhost:8000/docs
```

## Run with Docker (PostgreSQL 16 + API)

```bash
cp ../.env.example .env
docker compose up --build
# API on http://localhost:8000, docs at /docs
```

## Tests

Tests use a **separate SQLite database** (`.pytest-tmp/test.db`) — no Postgres
or Docker needed:

```powershell
cd backend
pytest -q --tb=short
```

## Engine integration

`ENGINE_MODE=stub` (default in Stage 1) uses a deterministic keyword stub in
`app/services/engine_stub.py` with the same call shapes as
`contracts/engine_contract.md` (`load_rules`, `classify_text`, `score_document`).
When Module 1's `legal_engine` package is ready, swap the stub calls for the
real package; no router or schema changes are needed.

`FAKE_ANALYSIS=true` short-circuits the pipeline for UI demos.

## Notes

- Never commit `.env` or uploaded files (uploads live under `UPLOAD_DIR`, git-ignored).
- API contract: `contracts/openapi.yaml`; changes require a PR approved by all three owners.
- Reports are rendered with `fpdf2`.

## Stage 4 extraction & OCR

`app/services/extraction.py` returns the plan-shaped result
`{text, page_count, pages: [{page, start, end}], used_ocr, warnings}`:

- **PDF**: PyMuPDF (primary). Pages are joined with a blank line and each
  page's start/end character offset in the cleaned text is recorded. A
  stdlib zlib fallback handles simple unencrypted PDFs if PyMuPDF is missing.
- **DOCX**: python-docx (paragraphs and tables in document order); stdlib
  zip+XML fallback. A DOCX has no page layout, so `page_count` is `null`.
- **OCR**: pages with fewer than 30 extracted characters are treated as
  scans. OCR (pytesseract, 200 dpi, English) runs **only when
  `TESSERACT_CMD` is set**; otherwise the analysis completes with the
  warning `Some pages had little or no extractable text; OCR is not
  configured`. With OCR the warning is `Some pages were scanned; text
  quality may be lower` and `summary.used_ocr` is `true` in the analysis
  response.
- The cleaned text is stored once and **never modified afterwards**: it is
  the single source of truth for every clause offset and web highlight.
- Encrypted or corrupt files raise a clear, user-friendly error which the
  pipeline stores in `documents.error_message` (status `failed`).

Install the optional packages with:
`pip install -e ".[ocr]"` plus `pymupdf`:

```powershell
.venv\Scripts\pip install pymupdf python-docx pytesseract pillow
```

### Installing Tesseract OCR

- **Windows**: run the UB-Mannheim installer
  <https://github.com/UB-Mannheim/tesseract/wiki> (default path
  `C:\Program Files\Tesseract-OCR\tesseract.exe`), then set
  `TESSERACT_CMD=C:\Program Files\Tesseract-OCR\tesseract.exe` in `.env`.
- **macOS**: `brew install tesseract`
- **Ubuntu/Debian**: `sudo apt install tesseract-ocr` (binary is on PATH;
  set `TESSERACT_CMD=/usr/bin/tesseract` if it is not).

`ANALYSIS_ASYNC=true` runs the pipeline as a FastAPI background task after
the 202 response (default `false` runs inline, which is what the tests and
immediate-analysis UX expect). Both modes emit the same status transitions
(`uploaded -> extracting -> classifying -> scoring -> completed|failed`).
