#!/usr/bin/env bash
# Behavior tests for bin/fm-drain-triage.sh.
#
# Drives the public argv and environment interface with a fake curl on PATH
# that records argv, the request body it read from stdin, and the header it
# read from file descriptor 3, and answers with a canned typesafe.ai response.
# No case touches the network, and the absent-key case proves the tool makes
# no call at all.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TOOL="$ROOT/bin/fm-drain-triage.sh"
TMP_ROOT=$(fm_test_tmproot fm-drain-triage)
HOME_DIR="$TMP_ROOT/home"
FAKEBIN=$(fm_fakebin "$TMP_ROOT")
LOG="$TMP_ROOT/log"
ROWS="$TMP_ROOT/rows.tsv"
RESPONSE="$TMP_ROOT/response.json"
BASE_PATH=$PATH
mkdir -p "$HOME_DIR/state" "$LOG"

printf 'done: shipped the fix\n' > "$HOME_DIR/state/a.status"
printf 'working: implementing\n' > "$HOME_DIR/state/b.status"
printf '#!/usr/bin/env bash\nprintf "merged: https://example.test/pr/1\\n"\n' > "$HOME_DIR/state/a.check.sh"
chmod 0700 "$HOME_DIR/state/a.check.sh"

write_rows() {
  printf '1700000000\t1\tsignal\ta.status\tsignal: a\n1700000001\t2\tsignal\tb.status\tsignal: b\n1700000002\t3\tcheck\ta.check.sh\tcheck: a.check.sh: merged: https://example.test/pr/1\n' > "$ROWS"
}

write_response() {  # <choice-1> <conf-1> <choice-2> <conf-2> <choice-3> <conf-3>
  local json='"row_1": {"type": "choice", "choice": "'$1'", "confidence": '$2', "probabilities": {"suppress": 0.9, "wake": 0.1}}'
  json="$json, \"row_2\": {\"type\": \"choice\", \"choice\": \"$3\", \"confidence\": $4, \"probabilities\": {\"suppress\": 0.2, \"wake\": 0.8}}"
  json="$json, \"row_3\": {\"type\": \"choice\", \"choice\": \"$5\", \"confidence\": $6, \"probabilities\": {\"suppress\": 0.85, \"wake\": 0.15}}"
  printf '{ "model": "jev-1.13.0", "answers": {%s}, "usage": {"input_tokens": 410, "output_tokens": 40} }\n' "$json" > "$RESPONSE"
}

cat > "$FAKEBIN/curl" <<'SH'
#!/usr/bin/env bash
# Fake curl: records argv (minus the -o target), the stdin body, and the header
# read from fd 3, then answers with FAKE_CURL_RESPONSE and FAKE_CURL_HTTP.
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

KEY='test-key-9f1c2d3e-never-on-argv'
code='' out='' err=''

# --- absent key: off, silent on stdout, no network --------------------------------
reset_log
write_rows
write_response suppress 0.9 wake 0.85 wake 0.88
run code out err --rows-file "$ROWS"
expect_code 0 "$code" "absent key exits 0"
assert_equals '' "$out" "absent key prints nothing on stdout"
assert_contains "$err" 'drain-triage: off (TYPESAFE_API_KEY absent from the environment and' "absent key explains itself on stderr"
assert_absent "$LOG/argv" "absent key never calls curl"
pass "absent key is off: one stderr line, exit 0, no network call"

# --- .env key, and the environment wins over it ------------------------------------
printf '%s\n' "export TYPESAFE_API_KEY=\"$KEY\"" > "$HOME_DIR/.env"
reset_log
run code out err --rows-file "$ROWS"
expect_code 0 "$code" ".env key resolves"
assert_contains "$out" '  status: clear' ".env key produces a clear result"
assert_contains "$(cat "$LOG/header")" "Authorization: Bearer $KEY" ".env key reaches curl on the fd header"
reset_log
TYPESAFE_API_KEY=env-wins run code out err --rows-file "$ROWS"
assert_equals 'Authorization: Bearer env-wins' "$(cat "$LOG/header")" "environment key wins over .env"
rm -f "$HOME_DIR/.env"
pass "TYPESAFE_API_KEY= in .env activates the tool and the environment wins"

# --- clear: request shape, secret handling, per-row verdicts ----------------------
reset_log
write_response suppress 0.9 wake 0.85 wake 0.88
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS"
expect_code 0 "$code" "clear exits 0"
assert_contains "$out" 'drain-triage:' "TOON block header"
assert_contains "$out" '  status: clear' "clear status"
assert_contains "$out" '  row: 1 signal a.status -> suppress confidence=0.9' "closed signal row suppresses"
assert_contains "$out" '  row: 2 signal b.status -> wake confidence=0.85' "open signal row wakes"
assert_contains "$out" '  row: 3 check a.check.sh -> wake confidence=' "check row keeps its own verdict"
assert_contains "$out" '  counts: suppressed=1 actionable=2 rows=3' "counts name suppressed and actionable rows"
argv=$(cat "$LOG/argv")
assert_not_contains "$argv" "$KEY" "the key never appears on curl argv"
assert_contains "$argv" 'https://api.typesafe.ai/v1/systemone' "the request uses the fixed typesafe.ai endpoint"
assert_contains "$argv" $'--max-time\n5' "the request uses the fixed five-second timeout"
assert_contains "$argv" '@/dev/fd/3' "the header is read from a file descriptor"
assert_equals "Authorization: Bearer $KEY" "$(cat "$LOG/header")" "curl receives the bearer header on fd 3"
assert_equals 'curl:clean' "$(cat "$LOG/child-env")" "the API key is absent from the child environment"
body=$(cat "$LOG/body")
assert_equals 'jev-latest' "$(jq -r .model <<<"$body")" "default model is jev-latest"
assert_equals 'main' "$(jq -r .state.drain.actor <<<"$body")" "the actor rides in the state"
assert_equals '["row_1","row_2","row_3"]' "$(jq -c '.questions | keys' <<<"$body")" "one Choice question per queued row"
assert_equals 'choice' "$(jq -r '.questions.row_1.type' <<<"$body")" "each row question is a typed Choice"
assert_equals '["suppress","wake"]' "$(jq -c '.questions.row_1.criteria | keys' <<<"$body")" "each row offers exactly suppress and wake"
assert_contains "$(jq -r '.questions.row_1.instructions' <<<"$body")" 'a.status' "the row rides in its own instructions"
assert_contains "$(jq -r '.questions.row_1.instructions' <<<"$body")" 'done: shipped the fix' "the closing marker rides in the row instructions"
assert_contains "$(jq -r '.state.drain.rows[0].evidence' <<<"$body")" 'done: shipped the fix' "signal evidence is the status file's last lines"
pass "clear: one Jev call, one Choice per row, per-row verdicts with counts"

# --- heartbeat rows never reach the model ------------------------------------------
reset_log
printf '1700000000\t7\theartbeat\theartbeat\theartbeat\n1700000001\t8\tsignal\tb.status\tsignal: b\n' > "$ROWS"
cat > "$RESPONSE" <<'JSON'
{ "model": "jev-1.13.0",
  "answers": { "row_8": {"type": "choice", "choice": "wake", "confidence": 0.9, "probabilities": {"suppress": 0.1, "wake": 0.9}} },
  "usage": {"input_tokens": 200, "output_tokens": 20} }
JSON
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS"
expect_code 0 "$code" "heartbeat batch exits 0"
assert_contains "$out" '  row: 7 heartbeat heartbeat -> wake confidence=- (decided in code: heartbeat always wakes)' "heartbeat wakes in code"
assert_contains "$out" '  row: 8 signal b.status -> wake confidence=0.9' "the asked row keeps its verdict"
assert_contains "$out" '  counts: suppressed=0 actionable=2 rows=2' "heartbeat counts as actionable"
assert_not_contains "$(cat "$LOG/body")" 'row_7' "no question is asked about the heartbeat row"
pass "heartbeat rows always wake in code and never reach the model"

# --- heartbeat-only batch asks nothing ---------------------------------------------
reset_log
printf '1700000000\t7\theartbeat\theartbeat\theartbeat\n' > "$ROWS"
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS"
expect_code 0 "$code" "heartbeat-only exits 0"
assert_contains "$out" '  status: clear' "heartbeat-only is clear"
assert_contains "$out" '  reason: no rows to ask the model; all decided in code' "heartbeat-only names its code path"
assert_contains "$out" '  counts: suppressed=0 actionable=1 rows=1' "heartbeat-only counts one actionable row"
assert_absent "$LOG/argv" "heartbeat-only never calls curl"
pass "a heartbeat-only batch wakes in code with no model call"

# --- ambiguous: below-floor rows wake ----------------------------------------------
reset_log
write_rows
write_response suppress 0.41 wake 0.85 wake 0.88
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS"
expect_code 0 "$code" "ambiguous exits 0"
assert_contains "$out" '  status: ambiguous' "a below-floor row makes the batch ambiguous"
assert_contains "$out" '  row: 1 signal a.status -> wake confidence=0.41 (confidence 0.41 below floor 0.6: wake as today)' "the below-floor row wakes with its floor note"
assert_contains "$out" '  row: 2 signal b.status -> wake confidence=0.85' "above-floor rows keep their verdicts"
assert_contains "$out" '  counts: suppressed=0 actionable=3 rows=3' "a suppressed vote below the floor never counts as suppressed"
assert_contains "$out" '  reason: 1 row(s) below the confidence floor 0.6: wake as today' "ambiguous names the floor"
pass "ambiguous: confidence below the fixed floor wakes as today"

# --- API and response failures are error outcomes, exit 0 ---------------------------
reset_log
write_rows
write_response suppress 0.9 wake 0.85 wake 0.88
TYPESAFE_API_KEY=$KEY FAKE_CURL_HTTP=500 run code out err --rows-file "$ROWS"
expect_code 0 "$code" "http 500 exits 0"
assert_contains "$out" '  status: error' "http 500 is an error outcome"
assert_contains "$out" '  reason: http 500 after' "http status is reported"
assert_contains "$out" '  counts: suppressed=0 actionable=3 rows=3' "error counts every row actionable"
assert_contains "$err" 'drain-triage: error (http 500' "error also goes to stderr"
reset_log
printf '%s\n' '{"model":"jev","answers":{}}' > "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS"
assert_contains "$out" '  status: error' "a malformed answer is an error outcome"
assert_contains "$out" '  reason: response is not a per-row Choice answer' "a malformed answer is named"
reset_log
write_response suppress 0.9 wake 0.85 wake 0.88
jq '.answers.row_1.probabilities = {"suppress": 0.9}' "$RESPONSE" > "$TMP_ROOT/bad-prob.json"
cp "$TMP_ROOT/bad-prob.json" "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS"
assert_contains "$out" '  reason: response is not a per-row Choice answer' "probabilities must name both options"
reset_log
write_response suppress 0.9 wake 0.85 wake 0.88
jq '.answers.extra = .answers.row_1' "$RESPONSE" > "$TMP_ROOT/extra.json"
cp "$TMP_ROOT/extra.json" "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS"
assert_contains "$out" '  reason: response is not a per-row Choice answer' "unasked answers are rejected"
pass "API, transport, and response failures are error outcomes with exit 0"

# --- row cap: overflow wakes in code -------------------------------------------------
reset_log
write_rows
cat > "$RESPONSE" <<'JSON'
{ "model": "jev-1.13.0",
  "answers": { "row_1": {"type": "choice", "choice": "suppress", "confidence": 0.9, "probabilities": {"suppress": 0.9, "wake": 0.1}} },
  "usage": {"input_tokens": 200, "output_tokens": 20} }
JSON
TYPESAFE_API_KEY=$KEY FM_DRAIN_TRIAGE_MAX_ROWS=1 run code out err --rows-file "$ROWS"
expect_code 0 "$code" "capped batch exits 0"
assert_contains "$out" '  row: 1 signal a.status -> suppress confidence=0.9' "the first row is asked"
assert_contains "$out" '  row: 2 signal b.status -> wake confidence=- (decided in code: over row cap 1, unasked)' "overflow rows wake unasked in code"
assert_equals '["row_1"]' "$(jq -c '.questions | keys' < "$LOG/body")" "only capped rows are asked"
pass "rows past the cap wake in code as unasked"

# --- gateway ladder: free first, captain's key on refusal ----------------------------
GWKEY='test-gateway-key-4b7e1a9c-never-on-argv'
reset_log
write_response suppress 0.9 wake 0.85 wake 0.88
AI_GATEWAY_API_KEY=$GWKEY run code out err --rows-file "$ROWS"
expect_code 0 "$code" "gateway-only exits 0"
assert_contains "$out" '  rung: gateway' "gateway-only names its rung"
assert_contains "$(cat "$LOG/argv")" 'https://ai-gateway.vercel.sh/typesafe/v1/systemone' "gateway-only posts to the gateway endpoint"
assert_equals 'typesafe-ai/jev' "$(jq -r .model < "$LOG/body")" "gateway rung asks for the gateway model slug"
assert_equals "Authorization: Bearer $GWKEY" "$(cat "$LOG/header")" "gateway key reaches curl on the fd header"
assert_equals '1' "$(cat "$LOG/curl-count")" "gateway-only makes one call"
reset_log
TYPESAFE_API_KEY=$KEY AI_GATEWAY_API_KEY=$GWKEY FAKE_CURL_HTTP=429 FAKE_CURL_HTTP2=200 FAKE_CURL_RESPONSE2="$RESPONSE" run code out err --rows-file "$ROWS"
expect_code 0 "$code" "gateway 429 with fallback exits 0"
assert_contains "$out" '  rung: typesafe' "a declined gateway falls back to the captain's key"
assert_contains "$out" '  status: clear' "the fallback still resolves"
assert_equals '2' "$(cat "$LOG/curl-count")" "the ladder makes one call per rung"
pass "gateway-first ladder with one fallback on refusal"

# --- configuration errors exit 2 -------------------------------------------------------
reset_log
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$TMP_ROOT/missing.tsv"
expect_code 2 "$code" "missing rows file exits 2"
assert_contains "$err" 'rows file not readable' "missing rows file is named"
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS" --actor skipper
expect_code 2 "$code" "bad actor exits 2"
TYPESAFE_API_KEY=$KEY run code out err --rows-file "$ROWS" --bogus
expect_code 2 "$code" "unknown flag exits 2"
assert_absent "$LOG/argv" "configuration errors never reach the network"
run code out err --help
expect_code 0 "$code" "--help exits 0"
assert_contains "$out" 'Usage:' "--help prints usage"
pass "configuration errors exit 2 before any network call"

printf '# all fm-drain-triage tests passed\n'
