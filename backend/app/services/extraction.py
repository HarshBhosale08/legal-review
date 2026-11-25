"""Text extraction for PDF and DOCX uploads (Stage 4).

`extract_text` returns the plan-shaped result:

    {text, page_count, pages: [{page, start, end}], used_ocr, warnings}

- PDF: PyMuPDF when installed (primary), with a stdlib zlib stream parser as
  fallback. Pages are joined with a blank line and every page's start/end
  character offset in the *cleaned* joined text is recorded.
- DOCX: python-docx (paragraphs and tables in document order), with the
  stdlib zip+XML parser as fallback. A DOCX has no page layout, so
  page_count is None and pages is empty.
- Scanned PDF pages (fewer than 30 characters) are OCR'd with pytesseract at
  200 dpi, but only when TESSERACT_CMD is configured; otherwise a warning is
  reported.
- Text is lightly cleaned (control characters removed, whitespace normalised,
  hyphenated line-end words rejoined). The cleaned string is stored once and
  NEVER modified afterwards: it is the single source of truth for all
  offsets.

Encrypted or corrupt files raise ExtractionError with a short, user-friendly
message (the pipeline stores it on the document).
"""
import io
import re
import zipfile
import zlib
from xml.etree import ElementTree

_SCANNED_PAGE_THRESHOLD = 30  # characters; fewer means the page is a scan
OCR_WARNING = "Some pages were scanned; text quality may be lower"
NO_OCR_WARNING = (
    "Some pages had little or no extractable text; OCR is not configured "
    "(set TESSERACT_CMD to enable it)"
)
_DEGRADED_PDF_WARNING = (
    "PDF text extraction ran in degraded (stdlib) mode; install PyMuPDF for "
    "better results"
)

_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


class ExtractionError(ValueError):
    """Raised for encrypted, corrupt or otherwise unreadable files."""


def extract_text(
    file_bytes: bytes, mime_type: str = "", tesseract_cmd: str = ""
) -> dict:
    """Return {text, page_count, pages, used_ocr, warnings} for the uploaded file.

    `pages` entries are {page, start, end} with 1-based page numbers and
    character offsets into the returned (cleaned) text.
    """
    if not file_bytes:
        raise ExtractionError("The uploaded file is empty.")
    if _looks_like_docx(file_bytes):
        return _extract_docx(file_bytes)
    if file_bytes.startswith(b"%PDF-"):
        return _extract_pdf(file_bytes, tesseract_cmd)
    # Plain-text fallback (used by tests and accidental text uploads).
    return {
        "text": _clean_page(file_bytes.decode("utf-8", errors="ignore")),
        "page_count": None,
        "pages": [],
        "used_ocr": False,
        "warnings": [],
    }


def _looks_like_docx(data: bytes) -> bool:
    return data[:2] == b"PK"  # ZIP local file header


def _clean_page(text: str) -> str:
    """Light cleaning applied BEFORE offsets are recorded."""
    text = _CONTROL_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = re.sub(r"(\w)-\n(\w)", r"\1\2", text)  # rejoin hyphenated line-end words
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip("\n")


# --- PDF ---------------------------------------------------------------------


def _extract_pdf(data: bytes, tesseract_cmd: str) -> dict:
    try:
        import pymupdf
    except ImportError:
        return _extract_pdf_fallback(data)
    try:
        doc = pymupdf.open(stream=data, filetype="pdf")
    except Exception as exc:  # noqa: BLE001 - corrupt file becomes a clear error
        raise ExtractionError(
            "The PDF file appears to be corrupt or is not a valid PDF."
        ) from exc
    try:
        if doc.needs_pass:
            raise ExtractionError(
                "The PDF is password-protected; upload an unlocked copy."
            )
        parts: list[str] = []
        pages: list[dict] = []
        used_ocr = False
        scanned_pages = 0
        pos = 0
        for number, page in enumerate(doc, start=1):
            try:
                page_text = page.get_text("text")
            except Exception:  # noqa: BLE001 - a bad page must not fail the file
                page_text = ""
            page_text = _clean_page(page_text)
            if len(page_text) < _SCANNED_PAGE_THRESHOLD:
                scanned_pages += 1
                if tesseract_cmd:
                    ocr_text = _ocr_page(page, tesseract_cmd)
                    if ocr_text.strip():
                        page_text = _clean_page(ocr_text)
                        used_ocr = True
            start = pos
            parts.append(page_text)
            pos = start + len(page_text)
            pages.append({"page": number, "start": start, "end": pos})
            pos += 2  # the blank-line separator between pages
        text = "\n\n".join(parts)
        warnings: list[str] = []
        if scanned_pages:
            warnings.append(OCR_WARNING if used_ocr else NO_OCR_WARNING)
        return {
            "text": text,
            "page_count": doc.page_count,
            "pages": pages,
            "used_ocr": used_ocr,
            "warnings": warnings,
        }
    finally:
        doc.close()


def _ocr_page(page, tesseract_cmd: str) -> str:
    """OCR one PyMuPDF page at 200 dpi (English)."""
    import pytesseract
    from PIL import Image

    if tesseract_cmd:
        pytesseract.pytesseract.tesseract_cmd = tesseract_cmd
    pix = page.get_pixmap(dpi=200, alpha=False)
    image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    try:
        return pytesseract.image_to_string(image, lang="eng")
    except pytesseract.TesseractNotFoundError as exc:
        raise ExtractionError(
            "OCR is configured (TESSERACT_CMD) but Tesseract could not be run."
        ) from exc


def _extract_pdf_fallback(data: bytes) -> dict:
    """Stdlib fallback for simple unencrypted PDFs (no PyMuPDF installed)."""
    if b"/Encrypt" in data:
        raise ExtractionError(
            "The PDF is password-protected; upload an unlocked copy."
        )
    chunks: list[str] = []
    for match in re.finditer(rb"stream\r?\n(.*?)endstream", data, re.S):
        raw = match.group(1).strip(b"\r\n")
        try:
            content = zlib.decompress(raw)
        except Exception:  # noqa: BLE001 - uncompressed or exotic filters
            content = raw
        for tm in re.finditer(rb"\((?:\\.|[^\\()])*\)", content):
            s = tm.group(0)[1:-1]
            s = s.replace(b"\\(", b"(").replace(b"\\)", b")").replace(b"\\\\", b"\\")
            chunks.append(s.decode("latin-1", errors="ignore"))
    text = _clean_page(" ".join(chunks))
    page_count = len(re.findall(rb"/Type\s*/Page[^s]", data)) or None
    pages = [{"page": 1, "start": 0, "end": len(text)}] if text else []
    return {
        "text": text,
        "page_count": page_count,
        "pages": pages,
        "used_ocr": False,
        "warnings": [_DEGRADED_PDF_WARNING] if text else [],
    }


# --- DOCX --------------------------------------------------------------------


def _extract_docx(data: bytes) -> dict:
    try:
        zf = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise ExtractionError(
            "The DOCX file appears to be corrupt or is not a valid Word document."
        ) from exc
    with zf:
        try:
            xml = zf.read("word/document.xml")
        except KeyError as exc:
            raise ExtractionError(
                "The DOCX file appears to be corrupt or is not a valid Word document."
            ) from exc
        try:
            import docx
            from docx.table import Table
            from docx.text.paragraph import Paragraph
        except ImportError:
            return _extract_docx_xml(xml)  # stdlib fallback
        try:
            document = docx.Document(io.BytesIO(data))
        except Exception as exc:  # noqa: BLE001 - corrupt XML becomes a clear error
            raise ExtractionError(
                "The DOCX file appears to be corrupt or is not a valid Word document."
            ) from exc
        paragraphs: list[str] = []
        for child in document.element.body.iterchildren():
            if child.tag == f"{_W}p":
                text = Paragraph(child, document).text
                if text.strip():
                    paragraphs.append(text)
            elif child.tag == f"{_W}tbl":
                for row in Table(child, document).rows:
                    cells = [cell.text.strip() for cell in row.cells]
                    if any(cells):
                        paragraphs.append(" | ".join(cells))
    return {
        "text": _clean_page("\n".join(paragraphs)),
        "page_count": None,
        "pages": [],
        "used_ocr": False,
        "warnings": [],
    }


def _extract_docx_xml(xml: bytes) -> dict:
    """Stdlib DOCX fallback: <w:p>/<w:t> text (tables included via iteration)."""
    try:
        root = ElementTree.fromstring(xml)
    except ElementTree.ParseError as exc:
        raise ExtractionError(
            "The DOCX file appears to be corrupt or is not a valid Word document."
        ) from exc
    paragraphs: list[str] = []
    for p in root.iter(f"{_W}p"):
        runs = "".join(t.text or "" for t in p.iter(f"{_W}t"))
        if runs.strip():
            paragraphs.append(runs)
    return {
        "text": _clean_page("\n".join(paragraphs)),
        "page_count": None,
        "pages": [],
        "used_ocr": False,
        "warnings": [],
    }
