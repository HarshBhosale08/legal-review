"""pytest fixtures: separate throwaway SQLite test database, isolated per test.

Test artifacts (the SQLite file and uploaded files) live in a fresh temp
directory per run so the repository never contains test data.
"""
import os
import pathlib
import sys
import tempfile

# Ensure the backend package is importable regardless of where pytest is invoked.
BACKEND_DIR = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_DIR))

# Separate test database BEFORE app modules are imported.
#
# Sandboxed environments may deny file creation inside freshly created
# subdirectories of the session temp dir while allowing it directly in the
# temp root, so all test artifacts are FLAT, uniquely named files there
# (no mkdtemp subdirectory). as_posix(): SQLAlchemy percent-encodes the
# database part of the URL, so Windows backslashes would break the path.
_TEST_ROOT = pathlib.Path(tempfile.gettempdir())
_TEST_TAG = f"legal-review-tests-{os.getpid()}"
TEST_DIR = _TEST_ROOT
TEST_DB_PATH = _TEST_ROOT / f"{_TEST_TAG}.db"
os.environ["DATABASE_URL"] = f"sqlite:///{TEST_DB_PATH.as_posix()}"
os.environ["UPLOAD_DIR"] = str(_TEST_ROOT / f"{_TEST_TAG}-uploads")
os.environ["JWT_SECRET"] = "test-secret"
# Real stub pipeline by default so documents/clauses/reports tests exercise
# extraction + classification + scoring; fake-mode tests override this.
os.environ["FAKE_ANALYSIS"] = "false"
os.environ["CORS_ORIGINS"] = "http://test.local"

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db import Base, get_db  # noqa: E402
from app.main import app  # noqa: E402
from app.models import User  # noqa: E402
from app.security import create_access_token, hash_password  # noqa: E402


@pytest.fixture()
def db_engine():
    engine = create_engine(
        f"sqlite:///{TEST_DB_PATH}", connect_args={"check_same_thread": False}
    )
    # Enforce foreign keys so tests catch constraint bugs (SQLite skips them by default).
    from sqlalchemy import event

    @event.listens_for(engine, "connect")
    def _fk_on(dbapi_connection, _record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()
    if TEST_DB_PATH.exists():
        TEST_DB_PATH.unlink()


@pytest.fixture()
def db_session(db_engine):
    TestingSession = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture()
def client(db_engine):
    TestingSession = sessionmaker(bind=db_engine, autoflush=False, expire_on_commit=False)

    def override_get_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture()
def user(db_session) -> User:
    u = User(
        name="Reviewer One",
        email="reviewer@example.com",
        password_hash=hash_password("password123"),
        role="reviewer",
    )
    db_session.add(u)
    db_session.commit()
    db_session.refresh(u)
    return u


@pytest.fixture()
def admin(db_session) -> User:
    a = User(
        name="Admin One",
        email="admin@example.com",
        password_hash=hash_password("password123"),
        role="admin",
    )
    db_session.add(a)
    db_session.commit()
    db_session.refresh(a)
    return a


@pytest.fixture()
def auth_headers(user) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(user.id, user.role)}"}


@pytest.fixture()
def admin_headers(admin) -> dict[str, str]:
    return {"Authorization": f"Bearer {create_access_token(admin.id, admin.role)}"}


@pytest.fixture()
def contracts_fixtures():
    """Path to contracts/fixtures (repo root)."""
    return BACKEND_DIR.parent / "contracts" / "fixtures"


# --- Stage 4 sample files (generated once, flat in the pytest temp root) -----


@pytest.fixture(scope="session")
def contract_pages():
    return [
        "This Agreement shall be governed by the laws of the State of Maharashtra, India.",
        "The Service Provider shall not engage in any business competitive with the Company.",
        "The liability of the Company shall not exceed the fees paid in the preceding months.",
    ]


@pytest.fixture(scope="session")
def sample_pdf_path(contract_pages):
    """A 3-page text PDF built with PyMuPDF (flat file in the pytest temp root)."""
    pymupdf = pytest.importorskip("pymupdf")
    root = pathlib.Path(tempfile.gettempdir())
    path = root / f"legal-review-sample-{os.getpid()}.pdf"
    if not path.exists():
        doc = pymupdf.open()
        for text in contract_pages:
            page = doc.new_page()
            page.insert_text((72, 72), text, fontname="helv", fontsize=11)
        doc.save(str(path))
        doc.close()
    yield path
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass


@pytest.fixture(scope="session")
def sample_docx_path(contract_pages):
    """A DOCX built with python-docx (flat file in the pytest temp root)."""
    docx = pytest.importorskip("docx")
    root = pathlib.Path(tempfile.gettempdir())
    path = root / f"legal-review-sample-{os.getpid()}.docx"
    if not path.exists():
        document = docx.Document()
        document.add_paragraph(contract_pages[0])
        document.add_paragraph(contract_pages[1])
        table = document.add_table(rows=1, cols=2)
        table.rows[0].cells[0].text = "Clause"
        table.rows[0].cells[1].text = "Liability"
        document.save(str(path))
    yield path
    if path.exists():
        try:
            path.unlink()
        except OSError:
            pass
