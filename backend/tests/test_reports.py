"""Report generation and download tests."""
import io

from tests.test_documents import DOCX_BYTES, _upload


def test_report_lifecycle(client, auth_headers):
    upload = _upload(client, auth_headers, name="report-me.docx", content=DOCX_BYTES)
    document_id = upload.json()["id"]

    created = client.post(
        f"/documents/{document_id}/report",
        json={"include_notes": True},
        headers=auth_headers,
    )
    assert created.status_code == 201, created.text
    report = created.json()
    assert report["document_id"] == document_id
    assert report["include_notes"] is True

    download = client.get(f"/reports/{report['id']}/download", headers=auth_headers)
    assert download.status_code == 200, download.text
    assert download.headers["content-type"] == "application/pdf"
    assert download.content.startswith(b"%PDF")


def test_report_requires_completed_analysis(client, auth_headers, db_session):
    from app.models import Document

    document = Document(
        owner_id=None,
        file_name="never.db",
        stored_path="/nonexistent/never.db",
        status="failed",
        size_bytes=1,
    )
    # insert with a real owner
    from tests.test_documents import _upload  # noqa: F401

    upload = _upload(client, auth_headers)
    document_id = upload.json()["id"]
    db_session.query(Document).filter(Document.id == document_id).update({"status": "scoring"})
    db_session.commit()

    response = client.post(
        f"/documents/{document_id}/report", json={"include_notes": False}, headers=auth_headers
    )
    assert response.status_code == 409
