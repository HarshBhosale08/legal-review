"""Minimal PDF report renderer (Stage 1) using fpdf2."""
from pathlib import Path

from fpdf import FPDF

from app.schemas import DISCLAIMER_TEXT

REPORTS_SUBDIR = "reports"


def render_report_pdf(path: Path, file_name: str, clauses: list, missing: list, include_notes: bool, notes_by_clause: dict[int, list]) -> None:
    pdf = FPDF()
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 16)
    pdf.cell(0, 10, "Contract Review Report", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 10)
    pdf.cell(0, 8, f"Document: {file_name}", new_x="LMARGIN", new_y="NEXT")
    pdf.ln(2)

    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Detected clauses", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    if not clauses:
        pdf.multi_cell(0, 6, "No clauses detected.", new_x="LMARGIN", new_y="NEXT")
    for clause in clauses:
        line = (
            f"[{clause.risk_level or '?'}] {clause.clause_type} "
            f"(confidence {clause.confidence:.2f}, review: {clause.reviewer_status})"
        )
        pdf.multi_cell(0, 5, line, new_x="LMARGIN", new_y="NEXT")
        excerpt = (clause.text or "")[:300]
        pdf.multi_cell(0, 4, excerpt, new_x="LMARGIN", new_y="NEXT")
        if include_notes and clause.id in notes_by_clause:
            for note in notes_by_clause[clause.id]:
                pdf.multi_cell(0, 4, f"  Note: {note.body[:200]}", new_x="LMARGIN", new_y="NEXT")
        pdf.ln(1)

    pdf.ln(2)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(0, 8, "Missing clauses", new_x="LMARGIN", new_y="NEXT")
    pdf.set_font("Helvetica", "", 9)
    if not missing:
        pdf.multi_cell(0, 6, "None.", new_x="LMARGIN", new_y="NEXT")
    for mc in missing:
        pdf.multi_cell(0, 5, f"[{mc.severity}] {mc.clause_type}: {mc.message}", new_x="LMARGIN", new_y="NEXT")

    pdf.ln(4)
    pdf.set_font("Helvetica", "I", 8)
    pdf.multi_cell(0, 4, DISCLAIMER_TEXT)

    path.parent.mkdir(parents=True, exist_ok=True)
    pdf.output(str(path))
