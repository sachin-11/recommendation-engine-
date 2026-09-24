#!/usr/bin/env bash
# End-to-end test against a running RecoEngine (real OpenAI and Pinecone).
#
#   ./scripts/e2e_test.sh                       # against http://localhost:8000
#   RECO_API=https://api.example.com ./scripts/e2e_test.sh
#
# Registers a throwaway tenant, uploads 10 HR jobs, waits for embedding, runs by-text and
# by-profile recommendations, sends feedback, checks analytics, then deletes the tenant
# (and its vectors). Prints PASS/FAIL per step; exits 1 if any step failed.
# Needs: bash, curl, and python3 (or python) for JSON parsing.
set -uo pipefail

RECO_API="${RECO_API:-http://localhost:8000}"
API="$RECO_API/api/v1"
BATCH_TIMEOUT="${BATCH_TIMEOUT:-180}"
PY=""
# The first interpreter that actually runs (Windows has a python3 stub that only opens the Store).
for candidate in python3 python; do
  if "$candidate" -c "import json" >/dev/null 2>&1; then PY="$candidate"; break; fi
done
[ -n "$PY" ] || { echo "python3 is required"; exit 2; }

EMAIL="e2e-$(date +%s)-$RANDOM@e2e.example"
PASSWORD="e2e-$(date +%s)-password"
API_KEY=""
PASSED=0
FAILED=0
BODY_FILE="$(mktemp)"
trap 'cleanup; rm -f "$BODY_FILE"' EXIT

green() { printf '\033[32m%s\033[0m' "$*"; }
red() { printf '\033[31m%s\033[0m' "$*"; }
pass() { PASSED=$((PASSED + 1)); printf '%s  %s\n' "$(green PASS)" "$*"; }
fail() { FAILED=$((FAILED + 1)); printf '%s  %s\n' "$(red FAIL)" "$*"; }

# request METHOD PATH [JSON_BODY] -> sets STATUS, response body in $BODY_FILE
request() {
  local method="$1" path="$2" data="${3:-}"
  local args=(-s -o "$BODY_FILE" -w '%{http_code}' -X "$method" "$API$path" -H "Content-Type: application/json")
  [ -n "$API_KEY" ] && args+=(-H "X-API-Key: $API_KEY")
  [ -n "$data" ] && args+=(--data "$data")
  STATUS="$(curl "${args[@]}" || echo 000)"
}

# json EXPR -> evaluates a Python expression against the last response body `d`
json() {
  local file="$BODY_FILE"
  # Git Bash on Windows: a native Python needs a Windows path for the temp file.
  command -v cygpath >/dev/null 2>&1 && file="$(cygpath -w "$file")"
  "$PY" -c "import json,sys; d=json.load(open(sys.argv[1], encoding='utf-8')); print($1)" "$file" 2>/dev/null | tr -d '\r'
}

cleanup() {
  [ -z "$API_KEY" ] && return
  request POST /me/delete "{\"confirm_email\": \"$EMAIL\", \"password\": \"$PASSWORD\"}"
  if [ "$STATUS" = "204" ]; then echo "cleanup: test tenant and its vectors deleted"; else echo "cleanup: delete returned $STATUS"; fi
  API_KEY=""
}

echo "RecoEngine E2E against $RECO_API"
echo

# ---------------------------------------------------------------- 1. register
request POST /auth/register "{
  \"name\": \"E2E Hiring\", \"email\": \"$EMAIL\", \"password\": \"$PASSWORD\", \"domain_type\": \"HR\",
  \"domain_config\": {
    \"primary_embedding_field\": \"description\",
    \"searchable_fields\": [\"title\", \"description\", \"skills\"],
    \"filter_fields\": [\"location\", \"experience_years\", \"job_type\"],
    \"item_label\": \"job\"
  }
}"
if [ "$STATUS" = "201" ]; then
  REGISTER_KEY="$(json "d['api_key']")"
  pass "1. Register tenant ($EMAIL)"
else
  fail "1. Register tenant: HTTP $STATUS $(head -c 300 "$BODY_FILE")"
  echo; echo "Cannot continue without a tenant."; exit 1
fi

# ---------------------------------------------------------------- 2. API key
API_KEY="$REGISTER_KEY"
request POST /me/api-keys '{"name": "e2e-backend"}'
NEW_KEY="$(json "d.get('api_key', '')")"
if [ "$STATUS" = "201" ] && [[ "$NEW_KEY" == reco_* ]]; then
  API_KEY="$NEW_KEY"
  request GET /me
  if [ "$STATUS" = "200" ]; then pass "2. Generate API key and authenticate with it"; else fail "2. New key rejected: HTTP $STATUS"; fi
else
  fail "2. Generate API key: HTTP $STATUS"
fi

# ---------------------------------------------------------------- 3. upload
request POST /items/upload '{"async": true, "items": [
  {"external_id": "job-1", "title": "Senior Python Developer", "description": "Build FastAPI microservices and own PostgreSQL schemas.", "skills": ["Python", "FastAPI", "PostgreSQL"], "location": "Remote", "experience_years": 5, "job_type": "full_time"},
  {"external_id": "job-2", "title": "Backend Engineer (Go)", "description": "Build payment APIs in Go with gRPC.", "skills": ["Go", "gRPC"], "location": "Bangalore", "experience_years": 4, "job_type": "full_time"},
  {"external_id": "job-3", "title": "Data Scientist", "description": "Train ranking models on user behaviour data.", "skills": ["Python", "PyTorch", "SQL"], "location": "Remote", "experience_years": 3, "job_type": "full_time"},
  {"external_id": "job-4", "title": "Frontend Engineer", "description": "Build React and TypeScript interfaces.", "skills": ["React", "TypeScript"], "location": "Pune", "experience_years": 2, "job_type": "contract"},
  {"external_id": "job-5", "title": "DevOps Engineer", "description": "Run Kubernetes on AWS with Terraform.", "skills": ["Kubernetes", "AWS", "Terraform"], "location": "Delhi", "experience_years": 4, "job_type": "full_time"},
  {"external_id": "job-6", "title": "Python Backend Developer", "description": "Build Django REST services for a marketplace.", "skills": ["Python", "Django", "Redis"], "location": "Delhi", "experience_years": 3, "job_type": "full_time"},
  {"external_id": "job-7", "title": "Machine Learning Engineer", "description": "Ship embedding and retrieval models to production.", "skills": ["Python", "PyTorch", "MLOps"], "location": "Remote", "experience_years": 4, "job_type": "full_time"},
  {"external_id": "job-8", "title": "HR Business Partner", "description": "Own employee relations and hiring plans.", "skills": ["Recruiting"], "location": "Delhi", "experience_years": 6, "job_type": "full_time"},
  {"external_id": "job-9", "title": "Junior Python Developer", "description": "Write and test Python scripts and Flask APIs.", "skills": ["Python", "Flask"], "location": "Pune", "experience_years": 0, "job_type": "internship"},
  {"external_id": "job-10", "title": "Site Reliability Engineer", "description": "Keep Python and Go services reliable; observability and on-call.", "skills": ["Prometheus", "Linux"], "location": "Bangalore", "experience_years": 5, "job_type": "full_time"}
]}'
BATCH_ID="$(json "d.get('batch_id', '')")"
if [ "$STATUS" = "202" ] && [ -n "$BATCH_ID" ]; then
  pass "3. Upload 10 HR items (batch $BATCH_ID)"
else
  fail "3. Upload items: HTTP $STATUS $(head -c 300 "$BODY_FILE")"
fi

# ---------------------------------------------------------------- 4. batch status
BATCH_STATUS="none"
if [ -n "$BATCH_ID" ]; then
  deadline=$((SECONDS + BATCH_TIMEOUT))
  while [ $SECONDS -lt $deadline ]; do
    request GET "/items/batch/$BATCH_ID"
    BATCH_STATUS="$(json "d['status']")"
    [ "$BATCH_STATUS" = "DONE" ] || [ "$BATCH_STATUS" = "PARTIAL_FAIL" ] && break
    sleep 2
  done
fi
if [ "$BATCH_STATUS" = "DONE" ]; then
  pass "4. Batch DONE: $(json "f\"{d['processed_items']}/{d['total_items']} embedded\"") in $((SECONDS))s"
else
  fail "4. Batch status: $BATCH_STATUS ($(json "f\"{d.get('processed_items')} done, {d.get('failed_items')} failed\""))"
fi

# ---------------------------------------------------------------- 5. by-text
request POST /recommend/by-text '{"query": "senior python backend engineer with fastapi", "top_k": 3, "filters": {"location": "Remote"}}'
TOP_TEXT="$(json "d['results'][0]['external_id']")"
QUERY_ID="$(json "d['query_id']")"
if [ "$STATUS" = "200" ] && [ "$TOP_TEXT" = "job-1" ]; then
  pass "5. by-text: top result $TOP_TEXT ($(json "d['results'][0]['score_label']"), $(json "d['latency_ms']") ms)"
else
  fail "5. by-text: HTTP $STATUS, top result '${TOP_TEXT:-none}' (expected job-1)"
fi

# ---------------------------------------------------------------- 6. by-profile
request POST /recommend/by-profile '{"profile": {"skills": "Python, Django, Redis", "experience": "3 years building REST APIs"}, "top_k": 3, "filters": {"experience_years": {"lte": 4}}}'
PROFILE_IDS="$(json "','.join(r['external_id'] for r in d['results'])")"
PROFILE_OK="$(json "all(r['metadata']['experience_years'] <= 4 for r in d['results']) and len(d['results']) > 0")"
if [ "$STATUS" = "200" ] && [ "$PROFILE_OK" = "True" ]; then
  pass "6. by-profile: $PROFILE_IDS (all within experience_years <= 4)"
else
  fail "6. by-profile: HTTP $STATUS, results '$PROFILE_IDS'"
fi

# ---------------------------------------------------------------- 7. feedback
if [ -n "$QUERY_ID" ]; then
  request POST /recommend/feedback "{\"query_id\": \"$QUERY_ID\", \"external_item_id\": \"job-1\", \"feedback_type\": \"CLICK\"}"
fi
if [ "${STATUS:-}" = "201" ] && [ -n "$QUERY_ID" ]; then pass "7. Submit feedback (CLICK on job-1)"; else fail "7. Submit feedback: HTTP ${STATUS:-none}"; fi

# ---------------------------------------------------------------- 8. analytics
request GET /analytics/overview
ANALYTICS_OK="$(json "d['total_items'] == 10 and d['total_recommendations_today'] >= 2 and d['embedding_status_breakdown']['DONE'] == 10")"
if [ "$STATUS" = "200" ] && [ "$ANALYTICS_OK" = "True" ]; then
  pass "8. Analytics overview: $(json "f\"{d['total_items']} items, {d['total_recommendations_today']} recommendations today, avg {d['avg_latency_ms']} ms\"")"
else
  fail "8. Analytics overview: HTTP $STATUS $(head -c 300 "$BODY_FILE")"
fi

# ---------------------------------------------------------------- summary
echo
cleanup
echo
if [ "$FAILED" -eq 0 ]; then
  echo "$(green "ALL $PASSED STEPS PASSED")"
  exit 0
fi
echo "$(red "$FAILED FAILED"), $PASSED passed"
exit 1
