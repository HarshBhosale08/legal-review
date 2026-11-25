"""Admin endpoints tests."""
import io

from tests.test_documents import DOCX_BYTES, _upload


def test_admin_requires_role(client, auth_headers, admin):
    for method, url in (("get", "/admin/users"), ("get", "/admin/rules"), ("get", "/admin/metrics"), ("get", "/admin/low-confidence.csv")):
        response = getattr(client, method)(url, headers=auth_headers)
        assert response.status_code == 403, (method, url, response.status_code)


def test_admin_users_list_and_role_change(client, auth_headers, admin_headers, admin, user):
    listing = client.get("/admin/users", headers=admin_headers)
    assert listing.status_code == 200
    ids = [u["id"] for u in listing.json()]
    assert admin.id in ids and user.id in ids

    promoted = client.patch(
        f"/admin/users/{user.id}", json={"role": "admin"}, headers=admin_headers
    )
    assert promoted.status_code == 200
    assert promoted.json()["role"] == "admin"

    self_change = client.patch(
        f"/admin/users/{admin.id}", json={"role": "reviewer"}, headers=admin_headers
    )
    assert self_change.status_code == 409

    missing = client.patch("/admin/users/99999", json={"role": "admin"}, headers=admin_headers)
    assert missing.status_code == 404


def test_admin_rules_get_put(client, admin_headers):
    current = client.get("/admin/rules", headers=admin_headers)
    assert current.status_code == 200
    assert "rules" in current.json()

    updated = client.put(
        "/admin/rules",
        json={"rules": {"tau_accept": 0.6}},
        headers=admin_headers,
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["rules"]["tau_accept"] == 0.6

    bad_key = client.put(
        "/admin/rules", json={"rules": {"nonsense_key": {}}}, headers=admin_headers
    )
    assert bad_key.status_code == 422

    # Engine-level validation: tau_floor above tau_accept, bad risk level.
    bad_taus = client.put(
        "/admin/rules",
        json={"rules": {"tau_accept": 0.4, "tau_floor": 0.9}},
        headers=admin_headers,
    )
    assert bad_taus.status_code == 422, bad_taus.text

    bad_risk = client.put(
        "/admin/rules",
        json={"rules": {"missing_risk": {"governing_law": "extreme"}}},
        headers=admin_headers,
    )
    assert bad_risk.status_code == 422, bad_risk.text

    bad_clause = client.put(
        "/admin/rules",
        json={"rules": {"expected_clauses": ["not_a_clause"]}},
        headers=admin_headers,
    )
    assert bad_clause.status_code == 422, bad_clause.text

    # Valid full rules payload round-trips.
    ok = client.put(
        "/admin/rules",
        json={
            "rules": {
                "tau_accept": 0.75,
                "tau_floor": 0.4,
                "expected_clauses": ["governing_law", "cap_on_liability"],
                "missing_risk": {"governing_law": "high"},
                "base_risk": {"non_compete": "medium"},
            }
        },
        headers=admin_headers,
    )
    assert ok.status_code == 200, ok.text
    rules = ok.json()["rules"]
    assert rules["tau_accept"] == 0.75
    assert rules["expected_clauses"] == ["governing_law", "cap_on_liability"]


def test_admin_metrics_and_low_confidence_csv(client, admin_headers, auth_headers):
    _upload(client, auth_headers, name="metrics.docx", content=DOCX_BYTES)

    metrics = client.get("/admin/metrics", headers=admin_headers)
    assert metrics.status_code == 200, metrics.text
    data = metrics.json()
    assert data["total_documents"] >= 1
    assert data["total_users"] >= 1
    for status_name in ("uploaded", "extracting", "classifying", "scoring", "completed", "failed"):
        assert status_name in data["documents_by_status"]
    for risk in ("high", "medium", "low"):
        assert risk in data["documents_by_risk"]

    csv_response = client.get("/admin/low-confidence.csv", headers=admin_headers)
    assert csv_response.status_code == 200
    assert csv_response.text.startswith("clause_id,document_id,clause_type,confidence")

    strict = client.get(
        "/admin/low-confidence.csv", headers=admin_headers, params={"threshold": 0.0}
    )
    assert strict.status_code == 200
    assert strict.text.strip().count("\n") == 0  # header only: nothing is below 0.0
