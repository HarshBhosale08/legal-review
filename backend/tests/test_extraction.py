"""Stage 4 tests: real PDF/DOCX extraction with page offsets, OCR gating,
and clear errors for corrupt/encrypted files.

Sample files come from the session fixtures in conftest.py (flat files in
the temp root — the sandbox denies writes inside fresh subdirectories there).
"""
import io

import pytest

from app.services.extraction import ExtractionError, extract_text

pytest.importorskip("pymupdf")
pytest.importorskip("docx")


def test_pdf_extraction_returns_plan_shape(sample_pdf_path, contract_pages):
    result = extract_text(sample_pdf_path.read_bytes(), "application/pdf")
    assert set(result) == {"text", "page_count", "pages", "used_ocr", "warnings"}
    assert result["page_count"] == 3
    assert result["used_ocr"] is False
    assert result["warnings"] == []
    # Pages joined with a blank line; recorded offsets must slice exactly.
    assert result["text"] == "\n\n".join(contract_pages)
    for entry, expected in zip(result["pages"], contract_pages):
        assert result["text"][entry["start"]:entry["end"]] == expected
    assert [p["page"] for p in result["pages"]] == [1, 2, 3]


def test_docx_extraction_returns_plan_shape(sample_docx_path, contract_pages):
    result = extract_text(
        sample_docx_path.read_bytes(),
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    )
    assert set(result) == {"text", "page_count", "pages", "used_ocr", "warnings"}
    assert result["page_count"] is None  # DOCX has no page layout
    assert result["pages"] == []
    assert contract_pages[0] in result["text"]
    assert "Liability" in result["text"]  # table cells are read


def test_text_offsets_survive_roundtrip_through_api(
    client, auth_headers, sample_pdf_path, db_session
):
    upload = client.post(
        "/documents",
        files={
            "file": (
                "sample-contract.pdf",
                io.BytesIO(sample_pdf_path.read_bytes()),
                "application/pdf",
            )
        },
        headers=auth_headers,
    )
    assert upload.status_code == 202, upload.text
    document = upload.json()
    assert document["status"] == "completed", document.get("error_message")

    analysis = client.get(f"/documents/{document['id']}/analysis", headers=auth_headers)
    assert analysis.status_code == 200
    summary = analysis.json()["summary"]
    assert summary["pages_processed"] == 3
    assert summary["used_ocr"] is False

    from sqlalchemy import select

    from app.models import Document as DocModel

    stored = db_session.execute(
        select(DocModel).order_by(DocModel.id.desc())
    ).scalars().first()
    assert stored.text_content is not None
    assert "\n\n" in stored.text_content  # pages joined with a blank line


def test_encrypted_pdf_raises_clear_error():
    import pathlib
    import os
    import tempfile

    import pymupdf

    # Flat file in the temp root: pytest's numbered tmp dirs are denied here.
    path = pathlib.Path(tempfile.gettempdir()) / f"legal-review-encrypted-{os.getpid()}.pdf"
    if not path.exists():
        doc = pymupdf.open()
        doc.new_page()
        doc.save(
            str(path),
            encryption=pymupdf.PDF_ENCRYPT_AES_256,
            user_pw="secret",
            owner_pw="secret",
        )
        doc.close()
    with pytest.raises(ExtractionError) as excinfo:
        extract_text(path.read_bytes(), "application/pdf", tesseract_cmd="")
    assert "password" in str(excinfo.value).lower()


def test_corrupt_pdf_raises_clear_error():
    with pytest.raises(ExtractionError):
        extract_text(b"%PDF-1.4\nthis is not a real pdf body", "application/pdf")


def test_corrupt_docx_raises_clear_error():
    with pytest.raises(ExtractionError):
        extract_text(b"PK\x03\x04 definitely not a word document", "application/docx")


def test_empty_file_raises_clear_error():
    with pytest.raises(ExtractionError):
        extract_text(b"", "application/pdf")


def test_ocr_runs_only_when_tesseract_installed():
    """OCR runs only when Tesseract is actually available; skip cleanly
    when it is missing (plan: 'skip the OCR test when tesseract is not
    installed')."""
    import shutil

    tesseract = shutil.which("tesseract")
    if not tesseract:
        pytest.skip("Tesseract is not installed")

    import os
    import pathlib
    import tempfile

    import pymupdf

    # A page with no text layer (image-only) triggers the scanned-page path.
    # Flat file in the temp root: pytest's numbered tmp dirs are denied here.
    path = pathlib.Path(tempfile.gettempdir()) / f"legal-review-scanned-{os.getpid()}.pdf"
    doc = pymupdf.open()
    page = doc.new_page()
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 100))
    pix.clear_with(255)
    page.insert_image(page.rect, pixmap=pix)
    path.write_bytes(doc.tobytes())
    doc.close()

    result = extract_text(path.read_bytes(), "application/pdf", tesseract_cmd=tesseract)
    assert result["used_ocr"] is True
    assert any("scanned" in w.lower() for w in result["warnings"])
