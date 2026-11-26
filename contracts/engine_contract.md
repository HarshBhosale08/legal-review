# Interface A: the `legal_engine` contract

Version: contracts-v1. Owner: Module 1. Used by: Module 2 (the backend calls these functions).
Change only through a pull request approved by all three owners (see `CONTEXT.md`).

The engine is a Python package named `legal_engine` (folder `engine/`). It receives text and
returns data. It never reads files, databases or the network (except loading its own model files).

Machine-readable shapes: `contracts/engine_schemas.json`.
Example data: `contracts/fixtures/` (examples only, not real model output).

## 1. Public functions

```python
from legal_engine import load_rules, classify_text, score_document

rules  = load_rules(path=None)                         # -> dict (Rules)
result = classify_text(text, model_dir=None, progress=None, thresholds=None)   # -> dict (ClassifyResult)
rating = score_document(result["clauses"], rules)      # -> dict (ScoreResult)
```

| Function | Arguments | Returns |
|---|---|---|
| `load_rules(path=None)` | `path`: optional path to a rules JSON file. `None` loads the packaged `rules.default.json`. | A Rules dictionary (Section 4). Raises `InvalidRulesError` if the file does not match the schema. |
| `classify_text(text, model_dir=None, progress=None, thresholds=None)` | `text`: the full extracted text of one contract (`str`). `model_dir`: folder with the trained model; if `None`, use the environment variable `LEGAL_ENGINE_MODEL_DIR`; if that is also empty, use the stub (`model_version` is then `stub-0.1`). `progress`: optional callback `progress(fraction: float) -> None`, called with values from 0.0 to 1.0. `thresholds`: optional dictionary `{"tau_accept": float, "tau_floor": float}`; if `None`, use `configs/thresholds.json` inside the package. The backend passes the two values from the current rules. | A ClassifyResult dictionary (Section 2). |
| `score_document(clauses, rules)` | `clauses`: the `clauses` list returned by `classify_text`. `rules`: a Rules dictionary. | A ScoreResult dictionary (Section 3). |

Command-line tool: `python -m legal_engine analyze contract.txt [--model-dir DIR] [--rules FILE]`
prints one JSON object with the keys `classification` and `score`.

## 2. ClassifyResult

```json
{
  "model_version": "legal-bert-cuad-0.1",
  "clauses": [
    {"clause_type": "cap_on_liability", "start": 813, "end": 1027,
     "text": "4. Limitation of Liability. In no event shall ...",
     "confidence": 0.87, "needs_review": false}
  ]
}
```

Rules that every implementation must follow (Module 2 checks them and rejects the result if one fails):

1. `start` and `end` are character indexes (Python string indexes, not bytes) into exactly the string
   passed to `classify_text`. `text[start:end]` must equal the clause `text`.
2. `clause_type` is one of the 10 ids in `CONTEXT.md`.
3. `confidence` is a number from 0.0 to 1.0, and is the model probability for that type.
4. `needs_review` is `true` when `confidence < tau_accept`, and `false` otherwise.
   Predictions with `confidence < tau_floor` are never returned. `tau_accept` and `tau_floor` come from the
   `thresholds` argument, or from `configs/thresholds.json` when it is `None` (defaults 0.5 and 0.25 until Module 1 tunes them in Stage 4).
5. `clauses` is sorted by `start`, then by `clause_type`. Clauses of different types may overlap.
   Clauses of the same type must not overlap or touch (merge them, keep the highest confidence).
6. Empty or whitespace-only `text` returns `{"model_version": ..., "clauses": []}`. A non-string raises `TypeError`.

## 3. ScoreResult

```json
{
  "overall_risk": "high",
  "clause_ratings": [
    {"index": 1, "risk_level": "low", "explanation": "This clause sets an upper limit ..."}
  ],
  "missing_clauses": [
    {"clause_type": "governing_law", "risk_level": "medium", "explanation": "No governing law clause was found ..."}
  ]
}
```

Scoring logic (this is the specification; Module 1 tests must cover it):

1. **Clause rating.** For each clause (by position `index` in the list passed in):
   `risk_level = rules.base_risk[clause_type]`. If `needs_review` is `true` and the level is `low`, raise it to `medium`.
2. **Missing clauses.** For each `t` in `rules.expected_clauses`: if no clause of type `t` has
   `needs_review == false`, add `{clause_type: t, risk_level: rules.missing_risk[t]}`. Keep the order of `expected_clauses`.
3. **Overall risk.** `high` if any clause rating or missing clause is `high`; otherwise `medium` if any is `medium`; otherwise `low`.
   With no clauses and no missing clauses the result is `low`.
4. **Explanations.** Every clause rating and every missing clause has a non-empty plain-language explanation
   from templates. The explanation says what the clause is in simple words, why it may matter, avoids legal-advice
   wording and ends with `Ask a lawyer if you are unsure.` A clause with `needs_review == true` also says
   that the AI is not sure and the reader should check it.
5. `clause_ratings` has exactly one entry for every input clause, in the same order (`index` = position).

## 4. Rules file

`load_rules` returns a dictionary with exactly these keys (see `engine_schemas.json`, `Rules`).
The default file is `contracts/fixtures/rules.default.json`; Module 1 copies it into the package.

| Key | Meaning |
|---|---|
| `tau_accept` | Confidence at or above this value is accepted (`needs_review = false`). |
| `tau_floor` | Confidence from this value up to `tau_accept` is returned with `needs_review = true`. Below it, dropped. Must be smaller than `tau_accept`. |
| `expected_clauses` | Clause types expected in every contract. If none is accepted, a missing-clause entry is created. |
| `missing_risk` | Risk level for each expected clause type if it is missing. Must have an entry for every type in `expected_clauses`. |
| `base_risk` | Risk level for each of the 10 clause types when it is found. Must have all 10 keys. |

The risk values are team defaults to be reviewed with the project guide. They are not legal advice.
The administrator can change the rules through the backend (`PUT /admin/rules`).

The thresholds in the rules file are the single source of truth at run time: the backend reads the rules
(the administrator can edit them) and passes `tau_accept` and `tau_floor` to `classify_text` through its
`thresholds` argument. `configs/thresholds.json` inside the package only holds the defaults found by Module 1.

## 5. Errors

| Exception | When |
|---|---|
| `EngineError` | Base class for all errors raised by the package. |
| `ModelNotFoundError(EngineError)` | A `model_dir` was given (or set in the environment) but cannot be loaded. |
| `InvalidRulesError(EngineError)` | The rules file or dictionary does not match the schema, or breaks a rule in Section 4. |
| `TypeError` | `text` is not a string. |

The backend turns any `EngineError` into document status `failed` with a short, user-friendly message.

## 6. Environment variables

| Variable | Meaning |
|---|---|
| `LEGAL_ENGINE_MODEL_DIR` | Default model folder. Empty means use the stub. |
| `LEGAL_ENGINE_BACKEND` | `torch` (default) or `onnx` (8-bit model, Stage 7). |

## 7. Versions

| Engine version | Meaning |
|---|---|
| `0.1.x` | Stub engine (keyword rules). Tag at the end of week 1 (Module 1, Stage 1). |
| `0.2.x` | Real model and real inference. Risk engine may still be basic. |
| `1.0.0` | Final: real model, risk engine, explanations, reports and README done. |

The shapes in this file stay the same for all versions. Changing them needs a contract change (Section 7.5 of the development plan).
