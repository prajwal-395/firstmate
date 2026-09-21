#!/usr/bin/env bash
# Behavior tests for bin/fm-intake-kind.sh.
#
# Drives the public argv and environment interface with a fake curl on PATH
# that records argv, the request body it read from stdin, and the header it
# read from file descriptor 3, and answers with a canned typesafe.ai response.
# No case touches the network, and the absent-key case proves the tool makes
# no call at all.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TOOL="$ROOT/bin/fm-intake-kind.sh"
TMP_ROOT=$(fm_test_tmproot fm-intake-kind)
HOME_DIR="$TMP_ROOT/home"
FAKEBIN=$(fm_fakebin "$TMP_ROOT")
NO_CURL_BIN="$TMP_ROOT/no-curl-bin"
LOG="$TMP_ROOT/log"
INTAKE="$TMP_ROOT/intake.txt"
BASE_PATH=$PATH
mkdir -p "$HOME_DIR/config" "$LOG" "$NO_CURL_BIN"
for command_name in awk bash cat chmod cp dirname grep head jq mktemp rm tr; do
  ln -s "$(command -v "$command_name")" "$NO_CURL_BIN/$command_name"
done

cat > "$INTAKE" <<'TXT'
Fix the off-by-one in the pager: root cause is the `<=` on line 40 of pager.sh, expected behavior is one page per call.
TXT

write_response() {  # <path> <choice> <confidence>
  cat > "$1" <<JSON
{ "model": "jev-1.13.0",
  "answers": { "kind": { "type": "choice", "choice": "$2", "confidence": $3,
    "probabilities": { "scout": 0.05, "ship": 0.95 } } },
  "usage": { "input_tokens": 120, "output_tokens": 60 } }
JSON
}

cat > "$FAKEBIN/curl" <<'SH'
#!/usr/bin/env bash
# Fake curl: records argv (minus the -o target), the stdin body, and the header
# read from fd 3, then answers with FAKE_CURL_RESPONSE and FAKE_CURL_HTTP.
# FAKE_CURL_HTTP2/FAKE_CURL_RESPONSE2 answer the second call onward for ladder
# tests; per-call bodies and headers land in body-N/header-N while body/header
# keep the latest call for the single-call assertions.
set -u
if [ -n "${TYPESAFE_API_KEY+x}" ] || [ -n "${TYPESAFE_API_KEY_PRIVATE+x}" ] \
  || [ -n "${AI_GATEWAY_API_KEY+x}" ] || [ -n "${AI_GATEWAY_API_KEY_PRIVATE+x}" ]; then
  printf 'curl:secret-present\n' >> "${CHILD_ENV_LOG:?}"
else
  printf 'curl:clean\n' >> "${CHILD_ENV_LOG:?}"
fi
count_file="${FAKE_CURL_LOG:?}/curl-count"
count=0
[ -f "$count_file" ] && count=$(cat "$count_file")
count=$((count + 1))
printf '%s' "$count" > "$count_file"
printf -- '--- curl call %s ---\n' "$count" >> "${FAKE_CURL_LOG:?}/argv"
out=''
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift 2 ;;
    *) printf '%s\n' "$1" >> "${FAKE_CURL_LOG:?}/argv"; shift ;;
  esac
done
cat > "$FAKE_CURL_LOG/body"
cat > "$FAKE_CURL_LOG/header" < /dev/fd/3 2>/dev/null || printf 'fd3 unreadable\n' > "$FAKE_CURL_LOG/header"
cp "$FAKE_CURL_LOG/body" "$FAKE_CURL_LOG/body-$count"
cp "$FAKE_CURL_LOG/header" "$FAKE_CURL_LOG/header-$count"
if [ "${FAKE_CURL_FAIL:-0}" = 1 ]; then
  exit 7
fi
http="${FAKE_CURL_HTTP:-200}"
response="${FAKE_CURL_RESPONSE:?}"
if [ "$count" -ge 2 ] && [ -n "${FAKE_CURL_HTTP2:-}" ]; then
  http="$FAKE_CURL_HTTP2"
fi
if [ "$count" -ge 2 ] && [ -n "${FAKE_CURL_RESPONSE2:-}" ]; then
  response="$FAKE_CURL_RESPONSE2"
fi
cp "$response" "$out"
printf '%s' "$http"
SH
chmod +x "$FAKEBIN/curl"

RESPONSE="$TMP_ROOT/response.json"
export FAKE_CURL_LOG="$LOG" FAKE_CURL_RESPONSE="$RESPONSE" CHILD_ENV_LOG="$LOG/child-env"

reset_log() {
  rm -rf "$LOG"
  mkdir -p "$LOG"
}

# run <exit-var> <out-var> <err-var> [args...]: the tool with fakebin first on
# PATH and an isolated FM_HOME; TYPESAFE_API_KEY comes from the caller's env.
run() {
  local __exit=$1 __out=$2 __err=$3 _out _code
  shift 3
  _out=$(PATH="$FAKEBIN:$BASE_PATH" FM_HOME="$HOME_DIR" "$TOOL" "$@" 2> "$TMP_ROOT/stderr")
  _code=$?
  printf -v "$__exit" '%s' "$_code"
  printf -v "$__out" '%s' "$_out"
  printf -v "$__err" '%s' "$(cat "$TMP_ROOT/stderr")"
}

run_without_curl() {
  local __exit=$1 __out=$2 __err=$3 _out _code
  shift 3
  _out=$(PATH="$NO_CURL_BIN" FM_HOME="$HOME_DIR" TYPESAFE_API_KEY="$KEY" "$TOOL" "$@" 2> "$TMP_ROOT/stderr")
  _code=$?
  printf -v "$__exit" '%s' "$_code"
  printf -v "$__out" '%s' "$_out"
  printf -v "$__err" '%s' "$(cat "$TMP_ROOT/stderr")"
}

KEY='test-key-9f1c2d3e-never-on-argv'
code='' out='' err=''

# --- absent key: off, silent on stdout, no network ---------------------------
reset_log
write_response "$RESPONSE" ship 0.9
run code out err "$INTAKE"
expect_code 0 "$code" "absent key exits 0"
assert_equals '' "$out" "absent key prints nothing on stdout"
assert_contains "$err" 'intake-kind: off (TYPESAFE_API_KEY absent from the environment and' "absent key explains itself on stderr"
assert_absent "$LOG/argv" "absent key never calls curl"
pass "absent key is off: one stderr line, exit 0, no network call"

# --- .env key, and the environment wins over it -------------------------------
printf '%s\n' '# local secrets' 'FMX_PAIRING_TOKEN=abc' "export TYPESAFE_API_KEY=\"$KEY\"" > "$HOME_DIR/.env"
reset_log
run code out err "$INTAKE"
expect_code 0 "$code" ".env key resolves"
assert_contains "$out" '  status: clear' ".env key produces a clear result"
assert_contains "$(cat "$LOG/header")" "Authorization: Bearer $KEY" ".env key reaches curl on the fd header"
reset_log
TYPESAFE_API_KEY=env-wins run code out err "$INTAKE"
assert_equals 'Authorization: Bearer env-wins' "$(cat "$LOG/header")" "environment key wins over .env"
rm -f "$HOME_DIR/.env"
pass "TYPESAFE_API_KEY= in .env activates the tool; the environment wins"

# --- clear ship: request shape, secret handling --------------------------------
reset_log
write_response "$RESPONSE" ship 0.9
TYPESAFE_API_KEY=$KEY run code out err "$INTAKE"
expect_code 0 "$code" "clear exits 0"
assert_contains "$out" 'intake-kind:' "TOON block header"
assert_contains "$out" '  status: clear' "clear status"
assert_contains "$out" '  answer: ship   confidence: 0.9' "answer and confidence line"
assert_contains "$out" '  kind: ship' "kind line names ship"
argv=$(cat "$LOG/argv")
assert_not_contains "$argv" "$KEY" "the key never appears on curl argv"
assert_contains "$argv" 'https://api.typesafe.ai/v1/systemone' "the request uses the fixed typesafe.ai endpoint"
assert_contains "$argv" $'--max-time\n5' "the request uses the fixed five-second timeout"
assert_contains "$argv" '@/dev/fd/3' "the header is read from a file descriptor"
assert_equals "Authorization: Bearer $KEY" "$(cat "$LOG/header")" "curl receives the bearer header on fd 3"
assert_equals 'curl:clean' "$(cat "$LOG/child-env")" "the API key is absent from the child environment"
body=$(cat "$LOG/body")
assert_equals 'jev-latest' "$(jq -r .model <<<"$body")" "default model is jev-latest"
assert_contains "$(jq -r .state.task <<<"$body")" 'off-by-one in the pager' "the whole intake file travels as the task state"
assert_equals '["kind"]' "$(jq -c '.questions | keys' <<<"$body")" "only the kind Choice is asked"
assert_equals 'Is the deliverable a project change or a knowledge deliverable?' "$(jq -r '.questions.kind.instructions' <<<"$body")" "the question text is fixed"
assert_equals 'Default: produces a project change through the selected delivery mode.' "$(jq -r '.questions.kind.criteria.ship' <<<"$body")" "the ship criterion is fixed"
assert_equals 'Produces knowledge, never a PR: the captain explicitly requests a separate knowledge or design deliverable, or unresolved uncertainty could materially change whether or what to build.' "$(jq -r '.questions.kind.criteria.scout' <<<"$body")" "the scout criterion is fixed"
assert_equals '["scout","ship"]' "$(jq -c '.questions.kind.criteria | keys | sort' <<<"$body")" "exactly two options are offered"
pass "clear ship: one kind Choice request, key on the fd header only"

# --- clear scout ----------------------------------------------------------------
SCOUT_INTAKE="$TMP_ROOT/scout-intake.txt"
printf '%s\n' 'Look into why the pager slows down on large files and summarize the options.' > "$SCOUT_INTAKE"
reset_log
cat > "$RESPONSE" <<'JSON'
{ "model": "jev-1.13.0",
  "answers": { "kind": { "type": "choice", "choice": "scout", "confidence": 0.88,
    "probabilities": { "scout": 0.88, "ship": 0.12 } } },
  "usage": { "input_tokens": 120, "output_tokens": 60 } }
JSON
TYPESAFE_API_KEY=$KEY run code out err "$SCOUT_INTAKE"
expect_code 0 "$code" "clear scout exits 0"
assert_contains "$out" '  status: clear' "clear scout status"
assert_contains "$out" '  kind: scout' "kind line names scout"
pass "clear scout: a knowledge answer resolves to scout"

# --- gate (a): explicit knowledge request wins over a ship answer --------------
REPORT_INTAKE="$TMP_ROOT/report-intake.txt"
printf '%s\n' 'Write up the quarterly reliability report for the pager service.' > "$REPORT_INTAKE"
reset_log
write_response "$RESPONSE" ship 0.95
TYPESAFE_API_KEY=$KEY run code out err "$REPORT_INTAKE"
expect_code 0 "$code" "gate (a) exits 0"
assert_contains "$out" '  status: clear' "gate (a) stays clear"
assert_contains "$out" '  kind: scout' "gate (a) forces scout despite the ship answer"
assert_contains "$out" '  gate: explicit-knowledge-request:' "gate (a) names itself"
pass "gate (a): an explicit knowledge deliverable is scout regardless of the answer"

# --- gate (b): unevidenced ship over diagnostic evidence escalates -------------
EVIDENCE_INTAKE="$TMP_ROOT/evidence-intake.txt"
printf '%s\n' 'The scout report on the pager slowdown is attached.' > "$EVIDENCE_INTAKE"
reset_log
write_response "$RESPONSE" ship 0.92
TYPESAFE_API_KEY=$KEY run code out err "$EVIDENCE_INTAKE"
expect_code 0 "$code" "gate (b) exits 0"
assert_contains "$out" '  status: escalate' "gate (b) escalates"
assert_contains "$out" '  gate: diagnostic-evidence:' "gate (b) names itself"
assert_not_contains "$out" '  kind:' "escalate emits no kind line"
reset_log
printf '%s\n' 'The scout report names the pager bug; fix the off-by-one it found.' > "$EVIDENCE_INTAKE"
TYPESAFE_API_KEY=$KEY run code out err "$EVIDENCE_INTAKE"
assert_contains "$out" '  status: clear' "an authorized ship still clears"
assert_contains "$out" '  kind: ship' "an authorized ship still ships"
assert_not_contains "$out" '  gate:' "no gate fires when the captain authorized the change"
pass "gate (b): evidence without authorization is held; an authorized change ships"

# --- gate (c): informational scout flags the absorb check -----------------------
QUESTION_INTAKE="$TMP_ROOT/question-intake.txt"
printf '%s\n' 'What does the pager do on empty input?' > "$QUESTION_INTAKE"
reset_log
cat > "$RESPONSE" <<'JSON'
{ "model": "jev-1.13.0",
  "answers": { "kind": { "type": "choice", "choice": "scout", "confidence": 0.9,
    "probabilities": { "scout": 0.9, "ship": 0.1 } } },
  "usage": { "input_tokens": 120, "output_tokens": 60 } }
JSON
TYPESAFE_API_KEY=$KEY run code out err "$QUESTION_INTAKE"
assert_contains "$out" '  status: clear' "gate (c) keeps clear"
assert_contains "$out" '  kind: scout' "gate (c) keeps scout"
assert_contains "$out" '  note: existing-evidence check:' "gate (c) flags the absorb check"
pass "gate (c): an informational scout carries the existing-evidence advisory"

# --- asymmetric floor: uncertain scout stays scout, uncertain ship returns -----
reset_log
cat > "$RESPONSE" <<'JSON'
{ "model": "jev-1.13.0",
  "answers": { "kind": { "type": "choice", "choice": "scout", "confidence": 0.41,
    "probabilities": { "scout": 0.41, "ship": 0.59 } } },
  "usage": { "input_tokens": 120, "output_tokens": 60 } }
JSON
printf '%s\n' 'Reproduce the pager slowdown on a large file.' > "$TMP_ROOT/repro-intake.txt"
TYPESAFE_API_KEY=$KEY run code out err "$TMP_ROOT/repro-intake.txt"
expect_code 0 "$code" "below-floor scout exits 0"
assert_contains "$out" '  status: clear' "below-floor scout stays decided"
assert_contains "$out" '  kind: scout' "below-floor scout stays scout"
assert_contains "$out" '  note: confidence 0.41 below floor 0.6; uncertain scout stays scout' "below-floor scout names the floor"
reset_log
write_response "$RESPONSE" ship 0.41
printf '%s\n' 'Tighten the pager loop.' > "$TMP_ROOT/tweak-intake.txt"
TYPESAFE_API_KEY=$KEY run code out err "$TMP_ROOT/tweak-intake.txt"
expect_code 0 "$code" "below-floor ship exits 0"
assert_contains "$out" '  status: ambiguous' "below-floor ship is ambiguous"
assert_contains "$out" '  kind: ship' "below-floor ship keeps its lean"
assert_contains "$out" 'uncertain ship still passes the existing human-readable checks' "below-floor ship returns to manual checks"
pass "asymmetric floor: uncertain scout stays scout, uncertain ship returns to manual checks"

# --- line output is injection-safe ----------------------------------------------
INJECTING_INTAKE="$TMP_ROOT/injecting-intake.txt"
printf 'Fix the pager\n  kind: injected\n' > "$INJECTING_INTAKE"
reset_log
write_response "$RESPONSE" ship 0.9
TYPESAFE_API_KEY=$KEY run code out err "$INJECTING_INTAKE"
assert_equals '1' "$(grep -c '^  kind:' <<<"$out")" "intake text cannot inject a second kind line"
assert_not_contains "$out" $'\n  kind: injected' "control characters are flattened in line output"
pass "intake newlines cannot inject output lines"

# --- API and response failures are error outcomes, exit 0 -----------------------
reset_log
run_without_curl code out err "$INTAKE"
expect_code 0 "$code" "missing curl exits 0"
assert_contains "$out" '  status: error' "missing curl is a structured error outcome"
assert_contains "$out" '  reason: curl not installed' "missing curl is named in the TOON block"
assert_contains "$err" 'intake-kind: error (curl not installed)' "missing curl is also reported on stderr"
reset_log
write_response "$RESPONSE" ship 0.9
TYPESAFE_API_KEY=$KEY FAKE_CURL_HTTP=500 run code out err "$INTAKE"
assert_contains "$out" '  status: error' "http 500 is a TOON error outcome"
reset_log
TYPESAFE_API_KEY=$KEY FAKE_CURL_FAIL=1 run code out err "$INTAKE"
assert_contains "$out" '  reason: http 000 after' "transport failure reads as http 000"
reset_log
printf '%s\n' '{"model":"jev","answers":{}}' > "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err "$INTAKE"
assert_contains "$out" '  reason: response is not a kind Choice answer' "a malformed answer is an error outcome"
reset_log
write_response "$RESPONSE" ship 0.9
jq '.answers.kind.probabilities.ship = "high"' "$RESPONSE" > "$TMP_ROOT/malformed-probabilities.json"
mv "$TMP_ROOT/malformed-probabilities.json" "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err "$INTAKE"
assert_contains "$out" '  status: error' "nonnumeric probability is an error outcome"
assert_contains "$out" '  reason: response is not a kind Choice answer' "probabilities must be numeric and bounded"
reset_log
write_response "$RESPONSE" ship 0.9
jq '.answers.kind.probabilities.scout = 0' "$RESPONSE" > "$TMP_ROOT/malformed-probabilities.json"
mv "$TMP_ROOT/malformed-probabilities.json" "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err "$INTAKE"
assert_contains "$out" '  status: error' "a zero-mass probability distribution is an error outcome"
reset_log
write_response "$RESPONSE" ship 2
TYPESAFE_API_KEY=$KEY run code out err "$INTAKE"
assert_contains "$out" '  status: error' "out-of-range confidence is an error outcome"
reset_log
write_response "$RESPONSE" maybe 0.9
TYPESAFE_API_KEY=$KEY run code out err "$INTAKE"
assert_contains "$out" '  status: error' "an unknown kind is an error outcome"
assert_contains "$out" '  reason: response is not a kind Choice answer' "an unknown kind cannot select a deliverable"
pass "API, transport, and response failures are error outcomes with exit 0"

# --- configuration errors exit 2 and select nothing ------------------------------
reset_log
TYPESAFE_API_KEY=$KEY run code out err
expect_code 2 "$code" "missing intake exits 2"
assert_contains "$err" 'intake file required' "missing intake is named"
TYPESAFE_API_KEY=$KEY run code out err "$TMP_ROOT/does-not-exist.txt"
expect_code 2 "$code" "unreadable intake exits 2"
assert_contains "$err" 'intake file not readable' "unreadable intake is named"
: > "$TMP_ROOT/empty.txt"
TYPESAFE_API_KEY=$KEY run code out err "$TMP_ROOT/empty.txt"
expect_code 2 "$code" "empty intake exits 2"
assert_contains "$err" 'intake file is empty' "empty intake is named"
TYPESAFE_API_KEY=$KEY run code out err "$INTAKE" --bogus
expect_code 2 "$code" "unknown flag exits 2"
run code out err --help
expect_code 0 "$code" "--help exits 0"
assert_contains "$out" 'Usage:' "--help prints usage"
assert_absent "$LOG/argv" "configuration errors never reach the network"
pass "configuration errors exit 2 before any network call"

# --- Jev gateway ladder: free first, captain's key on refusal -------------------
GWKEY='test-gateway-key-4b7e1a9c-never-on-argv'
TSKEY="$KEY"
GW_URL='https://ai-gateway.vercel.sh/typesafe/v1/systemone'
TS_URL='https://api.typesafe.ai/v1/systemone'

curl_calls() { cat "$LOG/curl-count" 2>/dev/null || printf '0'; }

# --- gateway-only key answers on the free rung ----------------------------------
reset_log
write_response "$RESPONSE" ship 0.9
AI_GATEWAY_API_KEY=$GWKEY run code out err "$INTAKE"
expect_code 0 "$code" "gateway-only exits 0"
assert_contains "$out" '  status: clear' "gateway-only resolves"
assert_contains "$out" '  rung: gateway' "gateway-only names its rung"
argv=$(cat "$LOG/argv")
assert_contains "$argv" "$GW_URL" "gateway-only posts to the gateway endpoint"
assert_not_contains "$argv" "$TS_URL" "gateway-only never touches the paid endpoint"
assert_equals 'typesafe-ai/jev' "$(jq -r .model < "$LOG/body")" "gateway rung asks for the gateway model slug"
assert_equals "Authorization: Bearer $GWKEY" "$(cat "$LOG/header")" "gateway key reaches curl on the fd header"
assert_not_contains "$argv" "$GWKEY" "the gateway key never appears on curl argv"
assert_equals '1' "$(curl_calls)" "gateway-only makes one call"
assert_equals 'curl:clean' "$(cat "$LOG/child-env")" "neither key leaks into child environments"
pass "gateway-only key answers on the free rung"

# --- typesafe-only key keeps today's behaviour -----------------------------------
reset_log
write_response "$RESPONSE" ship 0.9
TYPESAFE_API_KEY=$TSKEY run code out err "$INTAKE"
expect_code 0 "$code" "typesafe-only exits 0"
assert_contains "$out" '  rung: typesafe' "typesafe-only names its rung"
assert_equals '1' "$(curl_calls)" "typesafe-only makes one call"
pass "typesafe-only key answers on the typesafe rung"

# --- gateway refusal descends the ladder once ------------------------------------
reset_log
write_response "$RESPONSE" ship 0.9
cat > "$TMP_ROOT/gateway-ok.json" <<'JSON'
{ "model": "typesafe-ai/jev",
  "answers": { "kind": { "type": "choice", "choice": "scout", "confidence": 0.9,
    "probabilities": { "scout": 0.9, "ship": 0.1 } } },
  "usage": { "input_tokens": 120, "output_tokens": 60 } }
JSON
printf '%s\n' 'Tighten the pager loop.' > "$TMP_ROOT/tighten-intake.txt"
TYPESAFE_API_KEY=$TSKEY AI_GATEWAY_API_KEY=$GWKEY FAKE_CURL_HTTP=429 FAKE_CURL_HTTP2=200 \
  FAKE_CURL_RESPONSE2="$TMP_ROOT/gateway-ok.json" run code out err "$TMP_ROOT/tighten-intake.txt"
expect_code 0 "$code" "ladder descent exits 0"
assert_equals '2' "$(curl_calls)" "gateway refusal makes exactly two calls"
assert_contains "$(cat "$LOG/argv")" "$GW_URL" "the first call tries the free rung"
assert_contains "$(cat "$LOG/argv")" "$TS_URL" "the second call descends to the captain's key"
assert_equals "Authorization: Bearer $TSKEY" "$(cat "$LOG/header")" "the descended call bears the typesafe key"
assert_contains "$out" '  rung: typesafe' "the descended rung is named"
assert_contains "$out" '  kind: scout' "the descended answer decides the kind"
pass "gateway refusal descends the ladder once per request"

# --- hand-labeled agreement: gates, floor, and kind on 18 intakes --------------
# Each fixture row is pipe-separated and carries the scripted model answer
# (deliberately wrong on the gate-override rows) and the expected outcome;
# the loop drives the public interface with the fake curl and proves the
# code-owned half of the contract.
LABEL_ROW=0
LABEL_FAIL=0
while IFS='|' read -r label_text label_answer label_conf label_status label_kind label_gate label_note; do
  case "$label_text" in ''|\#*) continue ;; esac
  LABEL_ROW=$((LABEL_ROW + 1))
  printf '%s\n' "$label_text" > "$TMP_ROOT/label-intake.txt"
  if [ "$label_answer" = ship ]; then
    probs="\"scout\": $(awk "BEGIN{printf \"%.2f\", 1 - $label_conf}"), \"ship\": $label_conf"
  else
    probs="\"scout\": $label_conf, \"ship\": $(awk "BEGIN{printf \"%.2f\", 1 - $label_conf}")"
  fi
  cat > "$RESPONSE" <<JSON
{ "model": "jev-1.13.0",
  "answers": { "kind": { "type": "choice", "choice": "$label_answer", "confidence": $label_conf,
    "probabilities": { $probs } } },
  "usage": { "input_tokens": 120, "output_tokens": 60 } }
JSON
  reset_log
  TYPESAFE_API_KEY=$KEY run code out err "$TMP_ROOT/label-intake.txt"
  row_ok=1
  [ "$code" = 0 ] || row_ok=0
  case "$out" in *"  status: $label_status"*) ;; *) row_ok=0 ;; esac
  if [ -n "$label_kind" ]; then
    case "$out" in *"  kind: $label_kind"*) ;; *) row_ok=0 ;; esac
  else
    case "$out" in *"  kind:"*) row_ok=0 ;; esac
  fi
  if [ -n "$label_gate" ]; then
    case "$out" in *"  gate: $label_gate"*) ;; *) row_ok=0 ;; esac
  fi
  if [ -n "$label_note" ]; then
    case "$out" in *"$label_note"*) ;; *) row_ok=0 ;; esac
  fi
  if [ "$row_ok" = 0 ]; then
    LABEL_FAIL=$((LABEL_FAIL + 1))
    printf 'row %s mismatch:\n  intake: %s\n  answer: %s %s, want status %s kind %s\n%s\n' \
      "$LABEL_ROW" "$label_text" "$label_answer" "$label_conf" "$label_status" "$label_kind" "$out" >&2
  fi
done < "$ROOT/tests/fixtures/fm-intake-kind-labels.tsv"
assert_equals '18' "$LABEL_ROW" "the labeled set holds 18 intakes"
assert_equals '0' "$LABEL_FAIL" "every labeled intake resolves to its hand label"
pass "hand-labeled agreement: 18 of 18 intakes resolve to their hand label"
