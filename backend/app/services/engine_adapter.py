"""Engine adapter: the ONLY module allowed to import the Module 1 engine.

Selects the backend with ENGINE_MODE (app.config Settings.engine_mode):

- ``stub``: deterministic keyword engine in ``app/services/engine_stub.py``
  (default until Module 1 tags v0.2).
- ``real``: the packaged ``legal_engine`` package, called exactly as
  ``contracts/engine_contract.md`` defines (``load_rules``, ``classify_text``,
  ``score_document``).
- ``fake``: fixture-driven demo output (also selected by ``FAKE_ANALYSIS=true``
  at the pipeline level).

Every backend result is validated against the engine contract before the
pipeline persists it: required keys present, clause ids from the contract,
``0 <= confidence <= 1`` and ``text[start:end] == clause text``; scoring
results must carry ``overall_risk`` / ``clause_ratings`` / ``missing_clauses``
with valid risk levels and clause indexes. Violations raise
``EngineContractError`` so the document ends up ``failed`` with a clear,
user-friendly message instead of corrupt offsets being stored.

Rules follow the plan §3.4 shape: ``tau_accept``, ``tau_floor``,
``expected_clauses``, ``missing_risk``, ``base_risk`` (see ``DEFAULT_RULES``).
The pipeline starts from ``default_rules()`` and overlays the ``rule_config``
table rows (admin-editable, shape-validated by ``validate_rules``).
"""
import json
from functools import lru_cache
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.schemas import ADMIN_RULES_KEYS, CLAUDE_TYPES, RISK_LEVELS
from app.services import engine_stub

_REPO_ROOT = Path(__file__).resolve().parents[3]
_DEFAULT_FIXTURE_PATH = _REPO_ROOT / "contracts" / "fixtures" / "sample_analysis_response.json"

#: Packaged default rules (plan §3.4) used when the rule_config table is empty.
DEFAULT_RULES: dict[str, Any] = {
    "tau_accept": 0.5,
    "tau_floor": 0.25,
    "expected_clauses": [
        "governing_law",
        "cap_on_liability",
        "termination_for_convenience",
    ],
    "missing_risk": {
        "governing_law": "medium",
        "cap_on_liability": "high",
        "termination_for_convenience": "medium",
    },
    "base_risk": {
        "governing_law": "low",
        "termination_for_convenience": "medium",
        "non_compete": "medium",
        "exclusivity": "medium",
        "anti_assignment": "low",
        "change_of_control": "medium",
        "cap_on_liability": "low",
        "uncapped_liability": "high",
        "liquidated_damages": "medium",
        "ip_ownership_assignment": "medium",
    },
}


class EngineContractError(RuntimeError):
    """The engine returned output that violates contracts/engine_contract.md."""


class EngineNotAvailableError(RuntimeError):
    """The selected engine backend is not installed or not configured."""


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# --- Contract validation -----------------------------------------------------


def validate_classification(text: str, raw: Any) -> dict:
    """Validate a classify_text result against the engine contract.

    Raises EngineContractError with a precise message when required keys are
    missing, the clause id is unknown, confidence is out of [0, 1], or
    text[start:end] differs from the clause text.
    """
    if not isinstance(raw, dict):
        raise EngineContractError("classify_text must return an object")
    for key in ("model_version", "clauses"):
        if key not in raw:
            raise EngineContractError(f"classify_text result is missing required key {key!r}")
    if not isinstance(raw["model_version"], str) or not raw["model_version"]:
        raise EngineContractError("classify_text model_version must be a non-empty string")
    clauses = raw["clauses"]
    if not isinstance(clauses, list):
        raise EngineContractError("classify_text clauses must be a list")
    for i, clause in enumerate(clauses):
        where = f"clauses[{i}]"
        if not isinstance(clause, dict):
            raise EngineContractError(f"{where} must be an object")
        for key in ("clause_type", "start", "end", "text", "confidence"):
            if key not in clause:
                raise EngineContractError(f"{where} is missing required key {key!r}")
        if clause["clause_type"] not in CLAUDE_TYPES:
            raise EngineContractError(f"{where} has unknown clause_type {clause['clause_type']!r}")
        start, end = clause["start"], clause["end"]
        if not _is_int(start) or not _is_int(end):
            raise EngineContractError(f"{where} start/end must be integers")
        if not 0 <= start < end <= len(text):
            raise EngineContractError(
                f"{where} offsets [{start}, {end}) are outside the text (length {len(text)})"
            )
        if text[start:end] != clause["text"]:
            raise EngineContractError(
                f"{where} text[start:end] does not equal the clause text; offsets must point "
                "into the exact string passed to classify_text"
            )
        confidence = clause["confidence"]
        if not _is_number(confidence) or not 0 <= float(confidence) <= 1:
            raise EngineContractError(f"{where} confidence must be a number between 0 and 1")
    return raw


def validate_scoring(raw: Any, clause_count: int) -> dict:
    """Validate a score_document result against the engine contract."""
    if not isinstance(raw, dict):
        raise EngineContractError("score_document must return an object")
    for key in ("overall_risk", "clause_ratings", "missing_clauses"):
        if key not in raw:
            raise EngineContractError(f"score_document result is missing required key {key!r}")
    if raw["overall_risk"] not in RISK_LEVELS:
        raise EngineContractError(
            f"score_document overall_risk must be one of {RISK_LEVELS}, got {raw['overall_risk']!r}"
        )
    ratings = raw["clause_ratings"]
    if not isinstance(ratings, list):
        raise EngineContractError("score_document clause_ratings must be a list")
    for i, rating in enumerate(ratings):
        where = f"clause_ratings[{i}]"
        if not isinstance(rating, dict):
            raise EngineContractError(f"{where} must be an object")
        for key in ("index", "risk_level", "explanation"):
            if key not in rating:
                raise EngineContractError(f"{where} is missing required key {key!r}")
        if not _is_int(rating["index"]) or not 0 <= rating["index"] < clause_count:
            raise EngineContractError(
                f"{where} index {rating['index']!r} is not a valid clause index (0..{clause_count - 1})"
            )
        if rating["risk_level"] not in RISK_LEVELS:
            raise EngineContractError(
                f"{where} risk_level must be one of {RISK_LEVELS}, got {rating['risk_level']!r}"
            )
        if not isinstance(rating["explanation"], str):
            raise EngineContractError(f"{where} explanation must be a string")
    missing = raw["missing_clauses"]
    if not isinstance(missing, list):
        raise EngineContractError("score_document missing_clauses must be a list")
    for i, entry in enumerate(missing):
        where = f"missing_clauses[{i}]"
        if not isinstance(entry, dict):
            raise EngineContractError(f"{where} must be an object")
        for key in ("clause_type", "risk_level", "explanation"):
            if key not in entry:
                raise EngineContractError(f"{where} is missing required key {key!r}")
        if entry["clause_type"] not in CLAUDE_TYPES:
            raise EngineContractError(f"{where} has unknown clause_type {entry['clause_type']!r}")
        if entry["risk_level"] not in RISK_LEVELS:
            raise EngineContractError(
                f"{where} risk_level must be one of {RISK_LEVELS}, got {entry['risk_level']!r}"
            )
        if not isinstance(entry["explanation"], str):
            raise EngineContractError(f"{where} explanation must be a string")
    return raw


# --- Backend selection -------------------------------------------------------


def _mode() -> str:
    mode = (get_settings().engine_mode or "stub").lower()
    if mode not in ("stub", "real", "fake"):
        raise EngineNotAvailableError(f"ENGINE_MODE={mode!r} is not one of stub|real|fake")
    return mode


def _real_engine():
    try:
        import legal_engine  # noqa: PLC0415 - imported lazily; only exists once Module 1 ships
    except ImportError as exc:
        raise EngineNotAvailableError(
            "ENGINE_MODE=real but the legal_engine package is not installed; "
            "install Module 1's engine or set ENGINE_MODE=stub."
        ) from exc
    return legal_engine


# --- Engine-contract call shapes ---------------------------------------------


def classify_text(text: str) -> dict:
    """Return {"model_version", "clauses": [...]} per the engine contract.

    Each clause carries {"clause_type", "start", "end", "text", "confidence"};
    offsets point into the exact `text` passed in.
    """
    mode = _mode()
    if mode == "fake":
        fake = fake_analysis_result()
        return validate_classification(
            fake["text"],
            {"model_version": fake["model_version"], "clauses": fake["clauses"]},
        )
    if mode == "real":
        engine = _real_engine()
        model_dir = get_settings().legal_engine_model_dir or None
        raw = engine.classify_text(text, model_dir=model_dir)
    else:
        raw = engine_stub.classify_text(text)
    return validate_classification(text, raw)


def score_document(clauses: list[dict], rules: dict) -> dict:
    """Return {"overall_risk", "clause_ratings", "missing_clauses"} per the contract."""
    mode = _mode()
    if mode == "fake":
        fake = fake_analysis_result()
        return validate_scoring(
            {
                "overall_risk": fake["overall_risk"],
                "clause_ratings": [
                    {
                        "index": index,
                        "risk_level": clause.get("risk_level", "medium"),
                        "explanation": "Fixture demo rating (FAKE_ANALYSIS).",
                    }
                    for index, clause in enumerate(fake["clauses"])
                ],
                "missing_clauses": fake["missing_clauses"],
            },
            clause_count=len(fake["clauses"]),
        )
    if mode == "real":
        raw = _real_engine().score_document(clauses, rules)
    else:
        raw = engine_stub.score_document(clauses, rules)
    return validate_scoring(raw, clause_count=len(clauses))


def default_rules() -> dict:
    """Packaged default rules (plan §3.4 shape); real mode prefers the engine's
    own packaged rules for the known keys, falling back to DEFAULT_RULES."""
    rules = json.loads(json.dumps(DEFAULT_RULES))  # deep copy
    if _mode() == "real":
        try:
            engine_rules = _real_engine().load_rules()
        except EngineNotAvailableError:
            engine_rules = {}
        if isinstance(engine_rules, dict):
            for key in ADMIN_RULES_KEYS:
                if key in engine_rules:
                    rules[key] = engine_rules[key]
    return rules


# --- FAKE_ANALYSIS / ENGINE_MODE=fake ----------------------------------------


def _fixture_path_str() -> str:
    override = get_settings().fake_fixture_path
    return override or str(_DEFAULT_FIXTURE_PATH)


@lru_cache(maxsize=1)
def _fixture_json(path_str: str) -> dict:
    path = Path(path_str)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise EngineNotAvailableError(
            f"FAKE_ANALYSIS fixture not found at {path}; set FAKE_FIXTURE_PATH or "
            "deploy the repository's contracts/fixtures folder."
        ) from exc
    except (OSError, ValueError) as exc:
        raise EngineNotAvailableError(
            f"FAKE_ANALYSIS fixture at {path} could not be parsed: {exc}"
        ) from exc
    if not isinstance(raw, dict) or not isinstance(raw.get("clauses"), list):
        raise EngineNotAvailableError(f"FAKE_ANALYSIS fixture at {path} has no 'clauses' list.")
    return raw


def fake_analysis_result() -> dict:
    """Full FAKE_ANALYSIS result: the fixture text with clauses whose offsets
    are valid inside that text.

    The fixture's original offsets point into text the repository does not
    ship, so they are recomputed by locating each clause text in the joined
    fixture clause texts. Missing clauses are mapped from the fixture shape
    (severity/message) to the engine-contract shape (risk_level/explanation).
    """
    fixture = _fixture_json(_fixture_path_str())
    clause_texts = [str(entry["text"]) for entry in fixture["clauses"]]
    text = "\n\n".join(clause_texts)
    clauses: list[dict] = []
    cursor = 0
    for raw_clause in fixture["clauses"]:
        clause_text = str(raw_clause["text"])
        start = text.find(clause_text, cursor)
        if start < 0:
            raise EngineContractError(
                "FAKE_ANALYSIS fixture clause text could not be located in the fixture text"
            )
        end = start + len(clause_text)
        cursor = end
        clauses.append(
            {
                "clause_type": raw_clause["clause_type"],
                "start": start,
                "end": end,
                "text": clause_text,
                "confidence": float(raw_clause.get("confidence", 0.9)),
                "needs_review": False,
                "risk_level": raw_clause.get("risk_level", "medium"),
            }
        )
    missing = [
        {
            "clause_type": raw_missing["clause_type"],
            "risk_level": raw_missing.get("severity", "medium"),
            "explanation": raw_missing.get(
                "message", f"No {raw_missing['clause_type']} clause was found in the document."
            ),
        }
        for raw_missing in fixture.get("missing_clauses", [])
    ]
    result = {
        "model_version": f"fake-fixture-{fixture.get('document_id', 'demo')}",
        "text": text,
        "page_count": fixture.get("summary", {}).get("pages_processed"),
        "clauses": clauses,
        "missing_clauses": missing,
        "overall_risk": fixture.get("summary", {}).get("risk_level", "medium"),
    }
    validate_classification(
        result["text"], {"model_version": result["model_version"], "clauses": clauses}
    )
    return result


# --- Rule validation (used by PUT /admin/rules) -------------------------------


def validate_rules(rules: Any) -> list[str]:
    """Validate admin-supplied rules against the plan §3.4 rules schema.

    Returns a list of human-readable error strings (empty when valid).
    """
    if not isinstance(rules, dict):
        return ["rules must be an object keyed by rule name"]
    errors: list[str] = []
    for key in rules:
        if key not in ADMIN_RULES_KEYS:
            errors.append(f"Unknown rule key: {key}")
    taus: dict[str, float] = {}
    for name in ("tau_accept", "tau_floor"):
        value = rules.get(name)
        if value is None:
            continue
        if not _is_number(value) or not 0 <= float(value) <= 1:
            errors.append(f"{name} must be a number between 0 and 1")
        else:
            taus[name] = float(value)
    if {"tau_accept", "tau_floor"} <= taus.keys() and taus["tau_floor"] > taus["tau_accept"]:
        errors.append("tau_floor must not be greater than tau_accept")
    expected = rules.get("expected_clauses")
    if expected is not None:
        if not isinstance(expected, list):
            errors.append("expected_clauses must be a list of clause ids")
        else:
            for clause_id in expected:
                if clause_id not in CLAUDE_TYPES:
                    errors.append(f"Unknown clause id in expected_clauses: {clause_id}")
    for name in ("missing_risk", "base_risk"):
        mapping = rules.get(name)
        if mapping is None:
            continue
        if not isinstance(mapping, dict):
            errors.append(f"{name} must be an object mapping clause ids to risk levels")
            continue
        for clause_id, level in mapping.items():
            if clause_id not in CLAUDE_TYPES:
                errors.append(f"Unknown clause id in {name}: {clause_id}")
            if level not in RISK_LEVELS:
                errors.append(f"{name}[{clause_id}] must be one of {RISK_LEVELS}")
    return errors
