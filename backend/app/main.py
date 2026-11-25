"""FastAPI application factory: CORS, request IDs, structured logging, routers."""
import logging
import uuid
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from app.config import get_settings
from app.routers import admin, auth, clauses, documents, me, notes, reports
from app.schemas import HealthOut

# Structured-ish logging: one line per record with level/target; uvicorn access
# logs stay off in production (nginx fronting). File contents and passwords are
# NEVER logged anywhere in this codebase — only ids, statuses and counts.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)
logger = logging.getLogger("legal-review-backend")

# Correlation id header echoed/accepted from clients.
REQUEST_ID_HEADER = "X-Request-ID"


@asynccontextmanager
async def lifespan(_app: FastAPI):
    settings = get_settings()
    Path(settings.upload_dir).mkdir(parents=True, exist_ok=True)
    if settings.engine_mode == "stub":
        logger.info("ENGINE_MODE=stub: using the deterministic stub engine (Module 1 pending)")
    if settings.tesseract_cmd:
        import pytesseract  # noqa: F401 - imported to fail fast when TESSERACT_CMD is set

        pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
    yield


async def request_id_middleware(request: Request, call_next):
    """Attach an X-Request-ID to every request/response and log one line per
    request with method, path, status and duration — never any body content."""
    import time

    request_id = request.headers.get(REQUEST_ID_HEADER) or uuid.uuid4().hex[:16]
    request.state.request_id = request_id
    started = time.perf_counter()
    try:
        response = await call_next(request)
    except Exception:
        logger.exception(
            "request_id=%s unhandled error %s %s",
            request_id,
            request.method,
            request.url.path,
        )
        raise
    duration_ms = (time.perf_counter() - started) * 1000
    response.headers[REQUEST_ID_HEADER] = request_id
    logger.info(
        "request_id=%s %s %s -> %s %.1fms",
        request_id,
        request.method,
        request.url.path,
        response.status_code,
        duration_ms,
    )
    return response


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="Legal Document Review & Risk Flagging Platform API",
        version="0.1.0",
        description="Decision support for contract review - NOT legal advice.",
        lifespan=lifespan,
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    from starlette.middleware.base import BaseHTTPMiddleware

    app.add_middleware(BaseHTTPMiddleware, dispatch=request_id_middleware)

    app.include_router(auth.router)
    app.include_router(me.router)
    app.include_router(documents.router)
    app.include_router(clauses.router)
    app.include_router(notes.router)
    app.include_router(reports.router)
    app.include_router(admin.router)

    @app.get("/health", response_model=HealthOut, tags=["auth"])
    def health() -> HealthOut:
        return HealthOut(status="ok")

    return app


app = create_app()
