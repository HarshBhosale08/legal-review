# contracts/

The agreements between the three modules. Change these files only through a pull request that all three
owners approve (see `CONTEXT.md`), and add a line to `CHANGELOG.md`.

| File | What it is | Main users |
|---|---|---|
| `engine_contract.md` | Interface A in words: the three `legal_engine` functions, rules for offsets, thresholds, scoring logic, errors | Module 1 writes it, Module 2 calls it |
| `engine_schemas.json` | Interface A as JSON Schema (ClassifyResult, ScoreResult, Rules) | Module 1 tests, Module 2 adapter |
| `openapi.yaml` | Interface B: every REST endpoint, request and response | Module 2 builds it, Module 3 builds against it |
| `fixtures/sample_contract.txt` | A made-up contract (not real, not legal advice) for tests and demos | everyone |
| `fixtures/sample_engine_output.json` | What `classify_text` returns for the sample contract (example, not real model output) | Module 1, Module 2 |
| `fixtures/sample_score_output.json` | What `score_document` returns for that output with the default rules | Module 1, Module 2 |
| `fixtures/sample_analysis_response.json` | What `GET /documents/{id}/analysis` returns for the same contract | Module 2, Module 3 (mock server) |
| `fixtures/rules.default.json` | The default rules (team defaults, to be reviewed with the guide) | Module 1, Module 2 |
| `validate_contracts.py` | Checks that all the files above agree with each other | everyone, before every pull request |

Run the check from the repository root:

    pip install pyyaml
    python contracts/validate_contracts.py

All checks must pass. The fixtures describe one story: the sample contract has six clauses, one of them
low confidence (needs review), and no governing law clause, so the overall risk is high.
