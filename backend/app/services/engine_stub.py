"""Stub engine backend used by app.services.engine_adapter (ENGINE_MODE=stub).

Deterministic keyword matcher that mirrors the call and output shapes of
contracts/engine_contract.md so the pipeline, validation and REST layer are
exercised identically before Module 1's real model exists:

- classify_text(text) -> {"model_version", "clauses": [{clause_type, start,
  end, text, confidence, needs_review}]} with offsets into the exact string
  passed in.
- score_document(clauses, rules) -> {"overall_risk", "clause_ratings":
  [{index, risk_level, explanation}], "missing_clauses": [{clause_type,
  risk_level, explanation}]}.

Rules use the plan §3.4 shape (tau_accept, tau_floor, expected_clauses,
missing_risk, base_risk); see engine_adapter.DEFAULT_RULES for the packaged
defaults. The real engine (Module 1) replaces this module behind the adapter
without any pipeline change.
"""
import re

CLAUSE_TYPES = [
    "governing_law",
    "termination_for_convenience",
    "non_compete",
    "exclusivity",
    "anti_assignment",
    "change_of_control",
    "cap_on_liability",
    "uncapped_liability",
    "liquidated_damages",
    "ip_ownership_assignment",
]

STUB_MODEL_VERSION = "stub-keyword-0.1"

# Keyword triggers used by the stub classifier (the real model replaces this).
_PATTERNS: dict[str, str] = {
    "governing_law": r"governed by|governing law|jurisdiction of",
    "termination_for_convenience": r"terminate.{0,40}for convenience|termination for convenience",
    "non_compete": r"non-?compete|shall not compete|competitive business",
    "exclusivity": r"exclusiv|sole (provider|supplier)|shall not (engage|provide).{0,40}any (other|third)",
    "anti_assignment": r"shall not assign|no assignment|assign.{0,30}without.{0,20}consent",
    "change_of_control": r"change of control|change in control",
    "cap_on_liability": r"liability .{0,60}shall not exceed|limitation of liability|capped at",
    "uncapped_liability": r"without limitation of liability|notwithstanding.{0,60}liability|unlimited liability",
    "liquidated_damages": r"liquidated damages|penalt(y|ies)",
    "ip_ownership_assignment": r"intellectual property|ip (rights|ownership)|shall (be|remain) the (sole )?property",
}

# Curated reviewer-facing text for well-known missing clauses; others get a
# generic message built from the clause id.
DEFAULT_MISSING_MESSAGES: dict[str, str] = {
    "termination_for_convenience": (
        "No clause allows termination for convenience; consider adding a "
        "notice-based termination right."
    ),
    "ip_ownership_assignment": (
        "No clause assigns intellectual property in the deliverables."
    ),
    "cap_on_liability": (
        "No limitation of liability found; exposure is uncapped."
    ),
}

_STUB_CONFIDENCE = 0.75


def classify_text(text: str) -> dict:
    """Return {"model_version", "clauses"} per the engine contract."""
    findings: list[dict] = []
    lowered = text.lower()
    for clause_type, pattern in _PATTERNS.items():
        match = re.search(pattern, lowered)
        if match:
            findings.append(
                {
                    "clause_type": clause_type,
                    "start": match.start(),
                    "end": match.end(),
                    "text": text[match.start(): match.end()],
                    "confidence": _STUB_CONFIDENCE,
                    "needs_review": False,
                }
            )
    return {"model_version": STUB_MODEL_VERSION, "clauses": findings}


def score_document(clauses: list[dict], rules: dict | None = None) -> dict:
    """Return {"overall_risk", "clause_ratings", "missing_clauses"} per the contract."""
    rules = rules or {}
    base_risk: dict = rules.get("base_risk") or {}
    missing_risk: dict = rules.get("missing_risk") or {}
    expected: list = rules.get("expected_clauses") or []
    tau_accept = rules.get("tau_accept", 0.5)
    try:
        tau_accept = float(tau_accept)
    except (TypeError, ValueError):
        tau_accept = 0.5

    clause_ratings: list[dict] = []
    for index, clause in enumerate(clauses):
        clause_type = clause.get("clause_type", "")
        level = base_risk.get(clause_type, "medium")
        if level not in ("high", "medium", "low"):
            level = "medium"
        clause_ratings.append(
            {
                "index": index,
                "risk_level": level,
                "explanation": (
                    f"Stub engine: keyword match for '{clause_type}' rated {level} by "
                    "the base_risk rule table."
                ),
            }
        )

    found_types = {c.get("clause_type") for c in clauses}
    missing_clauses: list[dict] = []
    for clause_type in expected:
        if clause_type in found_types:
            continue
        level = missing_risk.get(clause_type, "medium")
        if level not in ("high", "medium", "low"):
            level = "medium"
        message = DEFAULT_MISSING_MESSAGES.get(
            clause_type, f"No {clause_type.replace('_', ' ')} clause was found in the document."
        )
        missing_clauses.append(
            {"clause_type": clause_type, "risk_level": level, "explanation": message}
        )

    high_count = sum(1 for r in clause_ratings if r["risk_level"] == "high")
    if high_count >= 2:
        overall = "high"
    elif clause_ratings:
        overall = "medium"
    else:
        overall = "low"
    return {
        "overall_risk": overall,
        "clause_ratings": clause_ratings,
        "missing_clauses": missing_clauses,
    }
