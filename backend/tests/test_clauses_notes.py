"""Clause review + notes tests."""
import io

from tests.test_documents import DOCX_BYTES, _upload


def _first_clause_id(client, headers):
    upload = _upload(client, headers)
    document_id = upload.json()["id"]
    analysis = client.get(f"/documents/{document_id}/analysis", headers=headers).json()
    return document_id, analysis["clauses"][0]["id"]


def test_patch_clause_confirm_reject(client, auth_headers):
    _, clause_id = _first_clause_id(client, auth_headers)

    confirmed = client.patch(
        f"/clauses/{clause_id}",
        json={"reviewer_status": "confirmed"},
        headers=auth_headers,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["reviewer_status"] == "confirmed"

    rejected = client.patch(
        f"/clauses/{clause_id}",
        json={"reviewer_status": "rejected"},
        headers=auth_headers,
    )
    assert rejected.status_code == 200
    assert rejected.json()["reviewer_status"] == "rejected"
    assert rejected.json()["reviewer_label"] is None


def test_patch_clause_relabel(client, auth_headers):
    _, clause_id = _first_clause_id(client, auth_headers)
    relabeled = client.patch(
        f"/clauses/{clause_id}",
        json={"reviewer_status": "relabeled", "reviewer_label": "exclusivity"},
        headers=auth_headers,
    )
    assert relabeled.status_code == 200, relabeled.text
    assert relabeled.json()["reviewer_status"] == "relabeled"
    assert relabeled.json()["reviewer_label"] == "exclusivity"

    missing_label = client.patch(
        f"/clauses/{clause_id}",
        json={"reviewer_status": "relabeled"},
        headers=auth_headers,
    )
    assert missing_label.status_code == 422

    bad_label = client.patch(
        f"/clauses/{clause_id}",
        json={"reviewer_status": "relabeled", "reviewer_label": "not_a_type"},
        headers=auth_headers,
    )
    assert bad_label.status_code == 422


def test_clause_notes_crud(client, auth_headers, user):
    _, clause_id = _first_clause_id(client, auth_headers)

    created = client.post(
        f"/clauses/{clause_id}/notes",
        json={"body": "Check this against company policy."},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    note = created.json()
    assert note["clause_id"] == clause_id
    assert note["author_id"] == user.id

    updated = client.put(
        f"/notes/{note['id']}", json={"body": "Updated note."}, headers=auth_headers
    )
    assert updated.status_code == 200
    assert updated.json()["body"] == "Updated note."

    # Another user cannot edit or delete someone else's note
    client.post(
        "/auth/register",
        json={"name": "Other", "email": "note-other@example.com", "password": "password123"},
    )
    other_token = client.post(
        "/auth/login", json={"email": "note-other@example.com", "password": "password123"}
    ).json()["access_token"]
    forbidden = client.put(
        f"/notes/{note['id']}",
        json={"body": "hijack"},
        headers={"Authorization": f"Bearer {other_token}"},
    )
    assert forbidden.status_code == 403

    deleted = client.delete(f"/notes/{note['id']}", headers=auth_headers)
    assert deleted.status_code == 204
    assert (
        client.put(f"/notes/{note['id']}", json={"body": "x"}, headers=auth_headers).status_code
        == 404
    )
