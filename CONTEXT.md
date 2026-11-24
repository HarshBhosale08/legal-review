# Project context
Product: a web app that reviews a contract. The user uploads a PDF/DOCX; the system extracts
text (OCR for scans), an AI model finds clauses of 10 types with a confidence 0..1, a rule
engine rates risk (high/medium/low) and finds missing expected clauses, and a web viewer
shows colour-coded highlights. The reviewer confirms/rejects/relabels clauses, adds notes and
downloads a PDF report. It is decision support, NOT legal advice (show this everywhere).

Three modules (one owner each):
1. engine/   Python package legal_engine: model training + inference + risk engine.
2. backend/  FastAPI + PostgreSQL: auth, files, extraction/OCR, runs the engine, reports.
3. frontend/ React + TypeScript + Tailwind: all screens.
Only two interfaces between modules:
A. legal_engine functions (see contracts/engine_contract.md): load_rules, classify_text,
   score_document. Offsets are character positions in the exact text passed in.
B. REST API in contracts/openapi.yaml. JSON snake_case, ISO dates, Bearer JWT, errors
   {"detail": "..."}.
Clause ids: governing_law, termination_for_convenience, non_compete, exclusivity,
anti_assignment, change_of_control, cap_on_liability, uncapped_liability,
liquidated_damages, ip_ownership_assignment.
Statuses: uploaded, extracting, classifying, scoring, completed, failed.
Conventions: Python 3.13 with type hints and pytest; TypeScript strict; small commits;
never commit data, model weights, .env or uploaded contracts; never change files in
contracts/ without a pull request approved by all three owners.
Example data for every module: contracts/fixtures/.
