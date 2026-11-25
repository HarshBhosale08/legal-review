"""Document lifecycle tests: upload, list/get/delete, analysis, reprocess."""
import io

DOCX_BYTES = (
    b"This Agreement shall be governed by the laws of the State of Maharashtra, India. "
    b"The Service Provider shall not engage in any business competitive with the Company. "
    b"The liability of the Company shall not exceed the fees paid in the three months "
    b"preceding the claim. The deliverables shall remain the sole property of the Company."
)
PDF_MAGIC = b"%PDF-1.4\n"


def _upload(client, headers, name="contract.docx", content=DOCX_BYTES,
            mime="application/vnd.openxmlformats-officedocument.wordprocessingml.document"):
    return client.post(
        "/documents",
        files={"file": (name, io.BytesIO(content), mime)},
        headers=headers,
    )


def test_upload_runs_stub_analysis(client, auth_headers, user):
    response = _upload(client, auth_headers)
    assert response.status_code == 202, response.text
    document = response.json()
    assert document["status"] in ("completed", "failed")
    assert document["file_name"] == "contract.docx"
    assert document["owner_id"] == user.id

    analysis = client.get(f"/documents/{document['id']}/analysis", headers=auth_headers)
    assert analysis.status_code == 200, analysis.text
    data = analysis.json()
    assert data["document_id"] == document["id"]
    assert data["disclaimer"]["text"]
    assert "not legal advice" in data["disclaimer"]["text"]
    assert isinstance(data["clauses"], list)
    assert isinstance(data["missing_clauses"], list)
    assert set(data["summary"]["counts_by_risk"]) == {"high", "medium", "low"}
    if document["status"] == "completed":
        assert any(c["clause_type"] == "governing_law" for c in data["clauses"])


def test_upload_rejects_bad_extension(client, auth_headers):
    response = client.post(
        "/documents",
        files={"file": ("notes.txt", io.BytesIO(b"hello"), "text/plain")},
        headers=auth_headers,
    )
    assert response.status_code == 415
    assert "detail" in response.json()


def test_upload_sanitises_filename(client, auth_headers, db_session):
    """Path traversal, control chars and dot tricks are neutralised in the
    stored display name (the on-disk name is a UUID either way)."""
    from app.routers.documents import _safe_file_name

    assert _safe_file_name("../../etc/passwd.pdf") == "passwd.pdf"
    assert _safe_file_name("..\\..\\windows\\evil.docx") == "evil.docx"
    assert _safe_file_name(".hidden.pdf") == "hidden.pdf"
    assert _safe_file_name("bad\x00\x1fname.pdf") == "badname.pdf"
    assert _safe_file_name(None) == "unnamed"
    assert _safe_file_name("") == "unnamed"
    assert _safe_file_name("...") == "unnamed"
    assert len(_safe_file_name("x" * 500 + ".pdf")) == 200

    upload = client.post(
        "/documents",
        files={"file": ("../../weird name\x1f.pdf", io.BytesIO(DOCX_BYTES),
                        "application/vnd.openxmlformats-officedocument.wordprocessingml.document")},
        headers=auth_headers,
    )
    assert upload.status_code == 202, upload.text
    body = upload.json()
    assert "/" not in body["file_name"] and "\\" not in body["file_name"]
    assert "\x1f" not in body["file_name"]
    assert body["file_name"].endswith(".pdf")


def test_list_documents_filters(client, auth_headers, sample_pdf_path):
    _upload(client, auth_headers, name="alpha.docx")
    _upload(client, auth_headers, name="beta.pdf", content=sample_pdf_path.read_bytes(),
            mime="application/pdf")

    all_docs = client.get("/documents", headers=auth_headers).json()
    assert all_docs["total"] >= 2

    filtered = client.get("/documents", headers=auth_headers, params={"q": "alpha"}).json()
    assert filtered["total"] == 1
    assert filtered["items"][0]["file_name"] == "alpha.docx"

    by_status = client.get(
        "/documents", headers=auth_headers, params={"status": "completed"}
    ).json()
    assert by_status["total"] == all_docs["total"]

    oldest_first = client.get("/documents", headers=auth_headers, params={"sort": "created_at"}).json()
    assert oldest_first["items"][0]["file_name"] == "alpha.docx"


def test_get_update_delete_document(client, auth_headers, user, db_session):
    upload = _upload(client, auth_headers)
    document_id = upload.json()["id"]

    got = client.get(f"/documents/{document_id}", headers=auth_headers)
    assert got.status_code == 200
    assert got.json()["id"] == document_id

    missing = client.get("/documents/999999", headers=auth_headers)
    assert missing.status_code == 404

    # Second user cannot see or delete the document
    other = client.post(
        "/auth/register",
        json={"name": "Other", "email": "other@example.com", "password": "password123"},
    ).json()
    other_login = client.post(
        "/auth/login", json={"email": other["email"], "password": "password123"}
    ).json()
    other_headers = {"Authorization": f"Bearer {other_login['access_token']}"}
    forbidden = client.get(f"/documents/{document_id}", headers=other_headers)
    assert forbidden.status_code == 403

    reprocess = client.post(f"/documents/{document_id}/reprocess", headers=auth_headers)
    assert reprocess.status_code == 202

    # Notes attached to the document's clauses must not block deletion
    analysis = client.get(f"/documents/{document_id}/analysis", headers=auth_headers).json()
    if analysis["clauses"]:
        client.post(
            f"/clauses/{analysis['clauses'][0]['id']}/notes",
            json={"body": "blocker check"},
            headers=auth_headers,
        )

    deleted = client.delete(f"/documents/{document_id}", headers=auth_headers)
    assert deleted.status_code == 204, deleted.text
    assert client.get(f"/documents/{document_id}", headers=auth_headers).status_code == 404
