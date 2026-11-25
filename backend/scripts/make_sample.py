"""Generate a realistic 2-page sample contract PDF for full_flow.sh.

Uses fpdf2 (already a backend dependency); writes sample_contract.pdf next to
this script (flat in scripts/), or to the path given as argv[1].
"""
import sys
from pathlib import Path

from fpdf import FPDF

PAGES = [
    [
        "MASTER SERVICES AGREEMENT",
        "",
        "This Agreement is entered into on 1 March 2026 between Acme Analytics Pvt Ltd",
        '("the Company") and DataServe Consulting LLP ("the Service Provider").',
        "",
        "1. GOVERNING LAW",
        "This Agreement shall be governed by the laws of the State of Maharashtra, India,",
        "and the courts of Mumbai shall have exclusive jurisdiction.",
        "",
        "2. TERMINATION",
        "2.1 The Company may terminate this Agreement for convenience by giving sixty (60)",
        "days' prior written notice to the Service Provider.",
        "",
        "3. INTELLECTUAL PROPERTY",
        "All deliverables, source code and documentation produced under this Agreement",
        "shall be the sole and exclusive property of the Company, and the Service Provider",
        "hereby assigns all rights, title and interest in such deliverables to the Company.",
    ],
    [
        "4. CONFIDENTIALITY",
        "Each party shall keep the other party's confidential information secret for five",
        "(5) years after termination of this Agreement.",
        "",
        "5. LIABILITY",
        "5.1 The total aggregate liability of the Company under this Agreement shall not",
        "exceed the fees paid by the Company in the three (3) months preceding the claim.",
        "5.2 The Service Provider's liability for indemnity claims shall be uncapped and",
        "each party shall indemnify the other against third-party IP infringement claims.",
        "",
        "6. NON-SOLICITATION",
        "During the term and for twelve months afterwards, neither party shall solicit",
        "the employees of the other party.",
        "",
        "7. ENTIRE AGREEMENT",
        "This document constitutes the entire agreement between the parties.",
    ],
]


def main() -> None:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "sample_contract.pdf"
    pdf = FPDF(format="A4")
    pdf.set_auto_page_break(auto=True, margin=15)
    for page_lines in PAGES:
        pdf.add_page()
        pdf.set_font("helvetica", size=11)
        for line in page_lines:
            pdf.cell(0, 6, text=line, new_x="LMARGIN", new_y="NEXT")
    pdf.output(str(out))
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
