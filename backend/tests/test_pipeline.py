"""Stage 5 pipeline tests: real stub pipeline end-to-end, fake mode,
adapter contract validation, and strict status ordering.

The conftest default is FAKE_ANALYSIS=false so the real stub pipeline runs
inline; fake-mode tests patch app.services.pipeline.get_settings.
"""
import io
import types

import pytest

from app.services import engine_adapter
from tests.test_documents import DOCX_BYTES, PDF_MAGIC, _upload


# --- (a) real stub pipeline: completed end-to-end with valid offsets ----------


def test_upload_completes_and_offsets_are_valid(client, auth_headers, db_session):
    upload = _upload(client, auth_headers)
    assert upload.status_code == 202, upload.text
    document = upload.json()
    assert document["status"] == "completed", document.get("error_message")

    # Reprocess is idempotent: completes again with the same results.
    reprocess = client.post(f"/documents/{document['id']}/reprocess", headers=auth_headers)
    assert reprocess.status_code == 202, reprocess.text
    after = client.get(f"/documents/{document['id']}", headers=auth_headers).json()
    assert after["status"] == "completed", after.get("error_message")

    analysis = client.get(f"/documents/{document['id']}/analysis", headers=auth_headers)
    assert analysis.status_code == 200, analysis.text
    clauses = analysis.json()["clauses"]
    assert clauses, "stub engine should find clauses in DOCX_BYTES"
    assert any(c["clause_type"] == "governing_law" for c in clauses)

    # Offsets must slice the stored extracted text exactly (text_content is
    # not in the API response; read it through the DB).
    from sqlalchemy import select

    from app.models import Document as DocModel

    stored = db_session.execute(
        select(DocModel).where(DocModel.id == document["id"])
    ).scalar_one()
    assert stored.text_content is not None
    text = stored.text_content
    for clause in clauses:
        start, end = clause["start_offset"], clause["end_offset"]
        assert 0 <= start < end <= len(text), (clause["clause_type"], start, end, len(text))
        assert text[start:end] == clause["text"], clause["clause_type"]


# --- (b) broken file: friendly failure, no clauses ---------------------------


def test_broken_file_fails_with_friendly_message_and_no_clauses(
    client, auth_headers, db_session
):
    upload = _upload(
        client,
        auth_headers,
        name="broken.pdf",
        content=PDF_MAGIC + b"junk",
        mime="application/pdf",
    )
    assert upload.status_code == 202, upload.text
    document = upload.json()
    assert document["status"] == "failed", document
    assert document["error_message"]
    # Friendly, human-readable — not a raw traceback.
    assert "could not be read" in document["error_message"]
    assert "Traceback" not in document["error_message"]

    from sqlalchemy import select

    from app.models import Clause as ClauseModel

    stored_clauses = db_session.execute(
        select(ClauseModel).where(ClauseModel.document_id == document["id"])
    ).scalars().all()
    assert stored_clauses == []


# --- (c) fake mode: fixture-based analysis completes without extraction ------


def test_fake_analysis_mode_completes(client, auth_headers, db_session, monkeypatch):
    monkeypatch.setattr(
        "app.services.pipeline.get_settings",
        lambda: types.SimpleNamespace(fake_analysis=True),
    )
    upload = _upload(client, auth_headers)
    assert upload.status_code == 202, upload.text
    document = upload.json()
    assert document["status"] == "completed", document.get("error_message")

    from sqlalchemy import select

    from app.models import Clause as ClauseModel, Document as DocModel

    stored = db_session.execute(
        select(DocModel).where(DocModel.id == document["id"])
    ).scalar_one()
    assert stored.text_content  # fixture text stored in place of extraction

    clauses = db_session.execute(
        select(ClauseModel).where(ClauseModel.document_id == document["id"])
    ).scalars().all()
    assert clauses
    text = stored.text_content
    for clause in clauses:
        assert 0 <= clause.start_offset < clause.end_offset <= len(text)
        assert text[clause.start_offset:clause.end_offset] == clause.text


# --- (d) adapter contract validation unit tests ------------------------------


def _classification(text: str, **overrides) -> dict:
    clause = {
        "clause_type": "governing_law",
        "start": 0,
        "end": len(text),
        "text": text,
        "confidence": 0.9,
    }
    clause.update(overrides)
    return {"model_version": "stub-keyword-0.1", "clauses": [clause]}


def test_validate_classification_accepts_valid_result():
    text = "This Agreement shall be governed by the laws of India."
    result = engine_adapter.validate_classification(text, _classification(text))
    assert result["model_version"] == "stub-keyword-0.1"


def test_validate_classification_missing_required_key():
    text = "Some clause text."
    raw = _classification(text)
    del raw["clauses"][0]["confidence"]
    with pytest.raises(engine_adapter.EngineContractError, match="confidence"):
        engine_adapter.validate_classification(text, raw)
    with pytest.raises(engine_adapter.EngineContractError, match="model_version"):
        engine_adapter.validate_classification(text, {"clauses": []})


def test_validate_classification_bad_confidence():
    text = "Some clause text."
    with pytest.raises(engine_adapter.EngineContractError, match="confidence"):
        engine_adapter.validate_classification(text, _classification(text, confidence=1.5))
    with pytest.raises(engine_adapter.EngineContractError, match="confidence"):
        engine_adapter.validate_classification(text, _classification(text, confidence=-0.1))


def test_validate_classification_offsets_must_slice_text():
    text = "First part. Second clause about liability."
    bad_text = "Different text entirely."
    with pytest.raises(engine_adapter.EngineContractError, match="does not equal"):
        engine_adapter.validate_classification(text, _classification(bad_text))
    with pytest.raises(engine_adapter.EngineContractError, match="outside the text"):
        engine_adapter.validate_classification(text, _classification(text, end=len(text) + 5))


def test_validate_scoring_rejects_bad_shapes():
    ok = {
        "overall_risk": "medium",
        "clause_ratings": [{"index": 0, "risk_level": "low", "explanation": "fine"}],
        "missing_clauses": [],
    }
    assert engine_adapter.validate_scoring(ok, clause_count=1) == ok
    for missing_key in ("overall_risk", "clause_ratings", "missing_clauses"):
        broken = dict(ok)
        del broken[missing_key]
        with pytest.raises(engine_adapter.EngineContractError, match=missing_key):
            engine_adapter.validate_scoring(broken, clause_count=1)
    with pytest.raises(engine_adapter.EngineContractError, match="index"):
        engine_adapter.validate_scoring(
            {**ok, "clause_ratings": [{"index": 7, "risk_level": "low", "explanation": "x"}]},
            clause_count=1,
        )
    with pytest.raises(engine_adapter.EngineContractError, match="overall_risk"):
        engine_adapter.validate_scoring({**ok, "overall_risk": "extreme"}, clause_count=1)


def test_validate_rules_cases():
    assert engine_adapter.validate_rules({}) == []
    assert engine_adapter.validate_rules(
        {"tau_accept": 0.7, "tau_floor": 0.4, "expected_clauses": ["governing_law"],
         "missing_risk": {"governing_law": "high"}, "base_risk": {"non_compete": "medium"}}
    ) == []

    errors = engine_adapter.validate_rules({"nonsense": 1})
    assert any("Unknown rule key" in e for e in errors)
    errors = engine_adapter.validate_rules({"tau_accept": 0.4, "tau_floor": 0.9})
    assert any("tau_floor" in e for e in errors)
    errors = engine_adapter.validate_rules({"tau_accept": "high"})
    assert any("tau_accept" in e for e in errors)
    errors = engine_adapter.validate_rules({"expected_clauses": ["not_a_clause"]})
    assert any("expected_clauses" in e for e in errors)
    errors = engine_adapter.validate_rules({"missing_risk": {"governing_law": "extreme"}})
    assert any("missing_risk" in e for e in errors)
    errors = engine_adapter.validate_rules({"base_risk": {"nope": "high"}})
    assert any("base_risk" in e for e in errors)


# --- (e) status order: mid-pipeline failure, then reprocess recovers ---------


def test_mid_pipeline_failure_marks_failed_then_reprocess_recovers(
    client, auth_headers, db_session, monkeypatch
):
    def _boom(text):
        raise RuntimeError("engine exploded mid-classification")

    monkeypatch.setattr("app.services.engine_adapter.classify_text", _boom)

    upload = _upload(client, auth_headers)
    assert upload.status_code == 202, upload.text
    document = upload.json()
    assert document["status"] == "failed", document
    assert "reprocessing" in document["error_message"].lower()

    from sqlalchemy import select

    from app.models import Clause as ClauseModel

    stored_clauses = db_session.execute(
        select(ClauseModel).where(ClauseModel.document_id == document["id"])
    ).scalars().all()
    assert stored_clauses == []

    # Restore the real stub engine; reprocess must complete the document.
    monkeypatch.undo()
    reprocess = client.post(f"/documents/{document['id']}/reprocess", headers=auth_headers)
    assert reprocess.status_code == 202, reprocess.text
    after = client.get(f"/documents/{document['id']}", headers=auth_headers).json()
    assert after["status"] == "completed", after.get("error_message")
    assert after["error_message"] is None
