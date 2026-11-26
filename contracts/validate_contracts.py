#!/usr/bin/env python3
"""Checks that the shared contract files agree with each other.

Run from the repository root:   python contracts/validate_contracts.py
Needs only PyYAML:              pip install pyyaml
Exit code 0 = all checks passed, 1 = at least one check failed.
Run it before every pull request that touches contracts/, and in CI later.
"""
import json
import sys
from pathlib import Path

try:
    import yaml
except ImportError:
    sys.exit("PyYAML is missing. Run: pip install pyyaml")

BASE = Path(__file__).resolve().parent
FIX = BASE / "fixtures"
RESULTS = []


def check(name, ok, detail=""):
    RESULTS.append((name, ok, detail))
    print(("PASS  " if ok else "FAIL  ") + name + (("  -> " + detail) if (detail and not ok) else ""))


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- tiny JSON Schema checker
def resolve(ref, root):
    node = root
    for part in ref.lstrip("#/").split("/"):
        node = node[part]
    return node


def validate(value, schema, root, path="$"):
    """Returns a list of error strings. Supports the subset of JSON Schema used in contracts/."""
    errors = []
    if "$ref" in schema:
        return validate(value, resolve(schema["$ref"], root), root, path)
    if "anyOf" in schema:
        if not any(not validate(value, s, root, path) for s in schema["anyOf"]):
            errors.append(f"{path}: does not match any allowed shape")
        return errors
    t = schema.get("type")
    if t is not None:
        types = t if isinstance(t, list) else [t]

        def is_type(v, name):
            if name == "null":
                return v is None
            if name == "boolean":
                return isinstance(v, bool)
            if name == "integer":
                return isinstance(v, int) and not isinstance(v, bool)
            if name == "number":
                return isinstance(v, (int, float)) and not isinstance(v, bool)
            if name == "string":
                return isinstance(v, str)
            if name == "array":
                return isinstance(v, list)
            if name == "object":
                return isinstance(v, dict)
            return True
        if not any(is_type(value, n) for n in types):
            return [f"{path}: expected {types}, got {type(value).__name__}"]
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} < minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} > maximum {schema['maximum']}")
    if isinstance(value, str) and "minLength" in schema and len(value) < schema["minLength"]:
        errors.append(f"{path}: shorter than minLength {schema['minLength']}")
    if isinstance(value, dict):
        for req in schema.get("required", []):
            if req not in value:
                errors.append(f"{path}: missing required field '{req}'")
        props = schema.get("properties", {})
        addl = schema.get("additionalProperties", True)
        pn = schema.get("propertyNames")
        for k, v in value.items():
            if pn is not None:
                errors += validate(k, pn, root, f"{path}.<key {k}>")
            if k in props:
                errors += validate(v, props[k], root, f"{path}.{k}")
            elif addl is False:
                errors.append(f"{path}: unexpected field '{k}'")
            elif isinstance(addl, dict):
                errors += validate(v, addl, root, f"{path}.{k}")
    if isinstance(value, list) and "items" in schema:
        for i, v in enumerate(value):
            errors += validate(v, schema["items"], root, f"{path}[{i}]")
    return errors


def walk_refs(node, found):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "$ref" and isinstance(v, str):
                found.append(v)
            else:
                walk_refs(v, found)
    elif isinstance(node, list):
        for v in node:
            walk_refs(v, found)


# ---------------------------------------------------------------- load files
api = yaml.safe_load((BASE / "openapi.yaml").read_text(encoding="utf-8"))
eng = load_json(BASE / "engine_schemas.json")
contract_text = (FIX / "sample_contract.txt").read_text(encoding="utf-8")
engine_out = load_json(FIX / "sample_engine_output.json")
score_out = load_json(FIX / "sample_score_output.json")
analysis = load_json(FIX / "sample_analysis_response.json")
rules = load_json(FIX / "rules.default.json")

CLAUSE_TYPES = eng["$defs"]["ClauseType"]["enum"]
ORDER = {"low": 0, "medium": 1, "high": 2}
SUFFIX = "Ask a lawyer if you are unsure."

# ---------------------------------------------------------------- 1. OpenAPI file
check("openapi: version is 3.1.x", str(api.get("openapi", "")).startswith("3.1"))
refs = []
walk_refs(api, refs)
bad = []
for r in refs:
    try:
        resolve(r, api)
    except (KeyError, TypeError):
        bad.append(r)
check("openapi: every $ref resolves", not bad, ", ".join(bad))

expected = {
    ("get", "/health"), ("post", "/auth/register"), ("post", "/auth/login"), ("get", "/me"),
    ("post", "/documents"), ("get", "/documents"), ("get", "/documents/{id}"),
    ("delete", "/documents/{id}"), ("post", "/documents/{id}/reprocess"),
    ("get", "/documents/{id}/analysis"), ("patch", "/clauses/{id}"),
    ("post", "/clauses/{id}/notes"), ("put", "/notes/{id}"), ("delete", "/notes/{id}"),
    ("post", "/documents/{id}/report"), ("get", "/reports/{id}/download"),
    ("get", "/admin/users"), ("patch", "/admin/users/{id}"), ("get", "/admin/rules"),
    ("put", "/admin/rules"), ("get", "/admin/metrics"), ("get", "/admin/low-confidence.csv"),
}
actual = set()
op_ids = []
no_resp = []
for path, item in api["paths"].items():
    for method, op in item.items():
        if method in ("get", "post", "put", "patch", "delete"):
            actual.add((method, path))
            op_ids.append(op.get("operationId"))
            if not op.get("responses"):
                no_resp.append(f"{method} {path}")
check("openapi: all agreed endpoints exist and none are extra",
      actual == expected,
      f"missing={sorted(expected - actual)} extra={sorted(actual - expected)}")
check("openapi: operationIds are unique and present", len(op_ids) == len(set(op_ids)) and None not in op_ids)
check("openapi: every operation has responses", not no_resp, ", ".join(no_resp))
public = {("get", "/health"), ("post", "/auth/register"), ("post", "/auth/login")}
wrong_sec = []
for method, path in actual:
    op = api["paths"][path][method]
    is_public = op.get("security") == []
    if (method, path) in public and not is_public:
        wrong_sec.append(f"{method} {path} should be public")
    if (method, path) not in public and is_public:
        wrong_sec.append(f"{method} {path} should need a token")
check("openapi: only health, register and login are public", not wrong_sec, "; ".join(wrong_sec))

# examples inside the file must match their own schemas
ex_errors = []
for path, item in api["paths"].items():
    for method, op in item.items():
        if method not in ("get", "post", "put", "patch", "delete"):
            continue
        bodies = []
        if "requestBody" in op:
            bodies += list(op["requestBody"]["content"].values())
        for resp in op["responses"].values():
            if "$ref" in resp:
                resp = resolve(resp["$ref"], api)
            bodies += list(resp.get("content", {}).values())
        for b in bodies:
            if "example" in b and "schema" in b:
                for e in validate(b["example"], b["schema"], api):
                    ex_errors.append(f"{method} {path}: {e}")
check("openapi: examples match their schemas", not ex_errors, "; ".join(ex_errors[:3]))

# ---------------------------------------------------------------- 2. Schemas agree between the two files
def norm(node):
    if isinstance(node, dict):
        return {k: (v.split("/")[-1] if k == "$ref" else norm(v)) for k, v in node.items()}
    if isinstance(node, list):
        return [norm(v) for v in node]
    return node

check("schemas: Rules is identical in openapi.yaml and engine_schemas.json",
      norm(api["components"]["schemas"]["Rules"]) == norm(eng["$defs"]["Rules"]))
check("schemas: clause type list is identical in both files",
      api["components"]["schemas"]["ClauseType"]["enum"] == CLAUSE_TYPES)
check("schemas: risk level list is identical in both files",
      api["components"]["schemas"]["RiskLevel"]["enum"] == eng["$defs"]["RiskLevel"]["enum"])
ctx = BASE.parent / "CONTEXT.md"
if ctx.exists():
    txt = ctx.read_text(encoding="utf-8")
    missing_ids = [c for c in CLAUSE_TYPES if c not in txt]
    check("CONTEXT.md lists all clause ids", not missing_ids, ", ".join(missing_ids))

# ---------------------------------------------------------------- 3. Fixtures match the schemas
def schema_check(name, value, schema, root):
    errs = validate(value, schema, root)
    check(name, not errs, "; ".join(errs[:3]))

schema_check("fixture: sample_engine_output.json matches ClassifyResult", engine_out, {"$ref": "#/$defs/ClassifyResult"}, eng)
schema_check("fixture: sample_score_output.json matches ScoreResult", score_out, {"$ref": "#/$defs/ScoreResult"}, eng)
schema_check("fixture: rules.default.json matches Rules", rules, {"$ref": "#/$defs/Rules"}, eng)
schema_check("fixture: sample_analysis_response.json matches AnalysisResponse", analysis,
             {"$ref": "#/components/schemas/AnalysisResponse"}, api)

# ---------------------------------------------------------------- 4. Rules for the engine (engine_contract.md)
check("rules: tau_floor is smaller than tau_accept", rules["tau_floor"] < rules["tau_accept"])
check("rules: base_risk has all 10 clause types", set(rules["base_risk"]) == set(CLAUSE_TYPES))
check("rules: missing_risk keys equal expected_clauses",
      set(rules["missing_risk"]) == set(rules["expected_clauses"]))

clauses = engine_out["clauses"]
ok_off = all(contract_text[c["start"]:c["end"]] == c["text"] for c in clauses)
check("engine output: text[start:end] equals clause text (offsets are characters)", ok_off)
keys = [(c["start"], c["clause_type"]) for c in clauses]
check("engine output: sorted by start, then clause_type", keys == sorted(keys))
overlap = False
for i, a in enumerate(clauses):
    for b in clauses[i + 1:]:
        if a["clause_type"] == b["clause_type"] and a["start"] <= b["end"] and b["start"] <= a["end"]:
            overlap = True
check("engine output: clauses of the same type do not overlap or touch", not overlap)
check("engine output: needs_review equals confidence < tau_accept",
      all(c["needs_review"] == (c["confidence"] < rules["tau_accept"]) for c in clauses))
check("engine output: no clause below tau_floor", all(c["confidence"] >= rules["tau_floor"] for c in clauses))

# ---------------------------------------------------------------- 5. Scoring logic is reproducible
def level(c):
    lv = rules["base_risk"][c["clause_type"]]
    return "medium" if (c["needs_review"] and lv == "low") else lv

want_ratings = [level(c) for c in clauses]
got_ratings = [r["risk_level"] for r in score_out["clause_ratings"]]
check("scoring: clause risk levels follow the rules", want_ratings == got_ratings, f"want {want_ratings}, got {got_ratings}")
check("scoring: one rating per clause, index = position",
      [r["index"] for r in score_out["clause_ratings"]] == list(range(len(clauses))))
present = {c["clause_type"] for c in clauses if not c["needs_review"]}
want_missing = [(t, rules["missing_risk"][t]) for t in rules["expected_clauses"] if t not in present]
got_missing = [(m["clause_type"], m["risk_level"]) for m in score_out["missing_clauses"]]
check("scoring: missing clauses follow the rules", want_missing == got_missing, f"want {want_missing}, got {got_missing}")
levels = want_ratings + [lv for _, lv in want_missing]
want_overall = max(levels, key=lambda v: ORDER[v]) if levels else "low"
check("scoring: overall risk follows the rules", score_out["overall_risk"] == want_overall)
expls = [r["explanation"] for r in score_out["clause_ratings"]] + [m["explanation"] for m in score_out["missing_clauses"]]
check(f"scoring: every explanation ends with '{SUFFIX}'", all(e.endswith(SUFFIX) for e in expls))

# ---------------------------------------------------------------- 6. The API fixture matches the engine fixtures
a_text = analysis["text"]
a_clauses = analysis["clauses"]
check("analysis: clause offsets point into the analysis text",
      all(a_text[c["start"]:c["end"]] == c["text"] for c in a_clauses))
same = len(a_clauses) == len(clauses) and all(
    (a["clause_type"], a["start"], a["end"], a["confidence"], a["needs_review"]) ==
    (e["clause_type"], e["start"], e["end"], e["confidence"], e["needs_review"])
    for a, e in zip(a_clauses, clauses))
check("analysis: clauses equal the engine output", same)
check("analysis: risk and explanation come from the score output",
      all(a["risk_level"] == r["risk_level"] and a["explanation"] == r["explanation"]
          for a, r in zip(a_clauses, score_out["clause_ratings"])))
check("analysis: missing clauses equal the score output", analysis["missing_clauses"] == score_out["missing_clauses"])
check("analysis: document.overall_risk equals the score output", analysis["document"]["overall_risk"] == score_out["overall_risk"])
note_ids = [n["id"] for c in a_clauses for n in c["notes"]]
check("analysis: ids are unique", len({c["id"] for c in a_clauses}) == len(a_clauses) and len(set(note_ids)) == len(note_ids))
check("analysis: relabeled clauses carry a reviewer_label, others do not",
      all((c["reviewer_status"] == "relabeled") == (c["reviewer_label"] is not None) for c in a_clauses))

failed = [r for r in RESULTS if not r[1]]
print(f"\n{len(RESULTS) - len(failed)} of {len(RESULTS)} checks passed.")
sys.exit(1 if failed else 0)
