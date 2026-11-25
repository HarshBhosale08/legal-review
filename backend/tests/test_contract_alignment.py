"""Contract-alignment tests: the API responses must match contracts/openapi.yaml conventions."""
import io

import yaml

from tests.test_documents import DOCX_BYTES, _upload

CONTRACT_PATH = __import__("pathlib").Path(__file__).resolve().parents[2] / "contracts" / "openapi.yaml"


def test_contract_file_exists_and_is_valid():
    assert CONTRACT_PATH.exists(), "contracts/openapi.yaml is missing"
    spec = yaml.safe_load(CONTRACT_PATH.read_text(encoding="utf-8"))
    assert spec["openapi"].startswith("3.1")
    for path in [
        "/auth/register",
        "/auth/login",
        "/me",
        "/documents",
        "/documents/{id}",
        "/documents/{id}/reprocess",
        "/documents/{id}/analysis",
        "/clauses/{id}",
        "/clauses/{id}/notes",
        "/notes/{id}",
        "/documents/{id}/report",
        "/reports/{id}/download",
        "/admin/users",
        "/admin/rules",
        "/admin/metrics",
        "/admin/low-confidence.csv",
    ]:
        assert path in spec["paths"], f"{path} missing from contract"


def test_error_shape_is_detail(client):
    response = client.get("/me")
    assert response.status_code == 401
    assert set(response.json().keys()) == {"detail"}


def test_document_and_clause_shapes_match_contract(client, auth_headers):
    upload = _upload(client, auth_headers, content=DOCX_BYTES)
    assert upload.status_code == 202
    document = upload.json()
    for key in ("id", "owner_id", "file_name", "status", "created_at", "updated_at"):
        assert key in document, f"{key} missing from document response"
    assert document["status"] in ("uploaded", "extracting", "classifying", "scoring", "completed", "failed")
    assert isinstance(document["id"], int)

    analysis = client.get(f"/documents/{document['id']}/analysis", headers=auth_headers).json()
    assert analysis["summary"]["risk_level"] in ("high", "medium", "low", None)
    for clause in analysis["clauses"]:
        assert clause["reviewer_status"] in ("pending", "confirmed", "rejected", "relabeled")
        assert 0.0 <= clause["confidence"] <= 1.0
    for missing in analysis["missing_clauses"]:
        assert missing["severity"] in ("high", "medium", "low")


def test_fixture_matches_schema(contracts_fixtures):
    import json

    sample = json.loads((contracts_fixtures / "sample_analysis_response.json").read_text(encoding="utf-8"))
    for key in ("document_id", "status", "clauses", "missing_clauses", "summary", "disclaimer"):
        assert key in sample
    assert sample["clauses"][0]["reviewer_status"] in ("pending", "confirmed", "rejected", "relabeled")
    assert sample["summary"]["risk_level"] in ("high", "medium", "low")
