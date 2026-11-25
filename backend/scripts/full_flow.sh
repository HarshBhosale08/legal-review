#!/usr/bin/env bash
# Full flow smoke test (plan §5.3): register -> login -> upload -> poll ->
# analysis -> patch clause -> note -> report -> download.
#
# Requires: bash, curl, jq, and a running backend (uvicorn app.main:app).
# Usage:  BASE_URL=http://127.0.0.1:8000 ./scripts/full_flow.sh
set -euo pipefail

BASE_URL="${BASE_URL:-http://127.0.0.1:8000}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SAMPLE="${SAMPLE:-$SCRIPT_DIR/sample_contract.pdf}"
EMAIL="${EMAIL:-fullflow-$(date +%s)@example.com}"
PASSWORD="full-flow-pass-123"

step() { printf '\n=== %s ===\n' "$1"; }

# --- 0. sample PDF -----------------------------------------------------------
if [ ! -f "$SAMPLE" ]; then
  step "Generating sample contract PDF"
  "$PYTHON" "${PYTHON:-python3}" "$SCRIPT_DIR/make_sample.py" "$SAMPLE"
fi

step "Health check"
curl -fsS "$BASE_URL/health"
echo

# --- 1. register -------------------------------------------------------------
step "Register $EMAIL"
REGISTER=$(curl -fsS -X POST "$BASE_URL/auth/register" \
  -H 'Content-Type: application/json' \
  -d "{\"name\": \"Full Flow\", \"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}")
USER_ID=$(echo "$REGISTER" | jq -r '.id')
echo "registered user id=$USER_ID"

# --- 2. login ----------------------------------------------------------------
step "Login"
TOKEN=$(curl -fsS -X POST "$BASE_URL/auth/login" \
  -H 'Content-Type: application/json' \
  -d "{\"email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}" | jq -r '.access_token')
AUTH="Authorization: Bearer $TOKEN"
echo "token acquired"

# --- 3. upload ---------------------------------------------------------------
step "Upload $SAMPLE"
UPLOAD=$(curl -fsS -X POST "$BASE_URL/documents" -H "$AUTH" \
  -F "file=@$SAMPLE;type=application/pdf")
DOC_ID=$(echo "$UPLOAD" | jq -r '.id')
echo "document id=$DOC_ID status=$(echo "$UPLOAD" | jq -r '.status')"

# --- 4. poll until completed/failed ------------------------------------------
step "Polling status"
for _ in $(seq 1 60); do
  STATUS=$(curl -fsS "$BASE_URL/documents/$DOC_ID" -H "$AUTH" | jq -r '.status')
  echo "  status=$STATUS"
  case "$STATUS" in
    completed|failed) break ;;
  esac
  sleep 1
done
[ "$STATUS" = "completed" ] || { echo "document did not complete (status=$STATUS)"; exit 1; }

# --- 5. analysis -------------------------------------------------------------
step "Analysis"
ANALYSIS=$(curl -fsS "$BASE_URL/documents/$DOC_ID/analysis" -H "$AUTH")
echo "$ANALYSIS" | jq '{status, risk: .summary.risk_level, clauses: (.clauses | length), missing: (.missing_clauses | length)}'
CLAUSE_ID=$(echo "$ANALYSIS" | jq -r '.clauses[0].id')
[ "$CLAUSE_ID" != "null" ] || { echo "no clauses found"; exit 1; }

# --- 6. patch clause ----------------------------------------------------------
step "Patch clause $CLAUSE_ID (confirm)"
curl -fsS -X PATCH "$BASE_URL/clauses/$CLAUSE_ID" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"reviewer_status": "confirmed"}' | jq '{id, reviewer_status}'

# --- 7. note ------------------------------------------------------------------
step "Add note to clause $CLAUSE_ID"
curl -fsS -X POST "$BASE_URL/clauses/$CLAUSE_ID/notes" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"body": "Checked against the master agreement; confirmed as-is."}' | jq '{id, body}'

# --- 8. report ----------------------------------------------------------------
step "Create report (include_notes=true)"
REPORT_ID=$(curl -fsS -X POST "$BASE_URL/documents/$DOC_ID/report" -H "$AUTH" \
  -H 'Content-Type: application/json' \
  -d '{"include_notes": true}' | jq -r '.id')
echo "report id=$REPORT_ID"

# --- 9. download ---------------------------------------------------------------
step "Download report"
curl -fsS "$BASE_URL/reports/$REPORT_ID/download" -H "$AUTH" -o /tmp/full-flow-report.pdf
ls -la /tmp/full-flow-report.pdf
head -c 8 /tmp/full-flow-report.pdf; echo " <- PDF magic"

printf '\nFULL FLOW OK\n'
