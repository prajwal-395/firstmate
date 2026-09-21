#!/usr/bin/env bash
# Behavior tests for bin/fm-stow-owner-resolve.sh.
#
# Drives the public argv and environment interface with a fake curl on PATH
# that records argv, the request body it read from stdin, and the header it
# read from file descriptor 3, and answers with a canned typesafe.ai response.
# A fake quota-axi records any call so the suite can prove the router never
# reads quota. No case touches the network, and the absent-key case proves
# the tool makes no call at all.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TOOL="$ROOT/bin/fm-stow-owner-resolve.sh"
TMP_ROOT=$(fm_test_tmproot fm-stow-owner-resolve)
HOME_DIR="$TMP_ROOT/home"
FAKEBIN=$(fm_fakebin "$TMP_ROOT")
NO_CURL_BIN="$TMP_ROOT/no-curl-bin"
LOG="$TMP_ROOT/log"
LEARNING="$TMP_ROOT/learning.md"
BASE_PATH=$PATH
mkdir -p "$HOME_DIR/config" "$LOG" "$NO_CURL_BIN"
for command_name in bash cat chmod cp dirname jq mktemp rm head tr; do
  ln -s "$(command -v "$command_name")" "$NO_CURL_BIN/$command_name"
done

cat > "$LEARNING" <<'MD'
Treehouse pool slots share one repo, so workers must create their task branch before editing.
MD

write_response() {  # <path> <choice> <confidence>
  cat > "$1" <<JSON
{ "model": "jev-1.13.0",
  "answers": { "owner": { "type": "choice", "choice": "$2", "confidence": $3,
    "probabilities": { "captain-md": 0.01, "captain-shared-md": 0.01, "learnings-md": 0.90, "backlog-note": 0.01, "scout-report": 0.01, "project-agents-md": 0.02, "firstmate-tracked": 0.02, "elsewhere-or-drop": 0.02 } } },
  "usage": { "input_tokens": 312, "output_tokens": 60 } }
JSON
}

cat > "$FAKEBIN/curl" <<'SH'
#!/usr/bin/env bash
# Fake curl: records argv (minus the -o target), the stdin body, and the header
# read from fd 3, then answers with FAKE_CURL_RESPONSE and FAKE_CURL_HTTP.
# Sequence support for ladder tests: FAKE_CURL_HTTP2/FAKE_CURL_RESPONSE2 answer
# the second call onward; per-call bodies and headers land in body-N/header-N
# while body/header keep the latest call for the single-call assertions.
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

cat > "$FAKEBIN/quota-axi" <<'SH'
#!/usr/bin/env bash
set -u
printf '%s\n' "$*" >> "${QUOTA_AXI_CALLS:?}"
cat "${QUOTA_AXI_FIXTURE:?}" 2>/dev/null || true
SH
chmod +x "$FAKEBIN/quota-axi"

RESPONSE="$TMP_ROOT/response.json"
export FAKE_CURL_LOG="$LOG" FAKE_CURL_RESPONSE="$RESPONSE" QUOTA_AXI_CALLS="$LOG/quota-axi.calls" QUOTA_AXI_FIXTURE="$TMP_ROOT/quota.json" CHILD_ENV_LOG="$LOG/child-env"

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

# --- absent key: off, silent on stdout, no network, no quota read -----------
reset_log
write_response "$RESPONSE" learnings-md 0.9
run code out err "$LEARNING"
expect_code 0 "$code" "absent key exits 0"
assert_equals '' "$out" "absent key prints nothing on stdout"
assert_contains "$err" 'stow-owner-resolve: off (TYPESAFE_API_KEY absent from the environment and' "absent key explains itself on stderr"
assert_absent "$LOG/argv" "absent key never calls curl"
assert_absent "$LOG/quota-axi.calls" "absent key never reads quota-axi"
pass "absent key is off: one stderr line, exit 0, no network call"

# --- .env key, and the environment wins over it ------------------------------
printf '%s\n' '# local secrets' 'FMX_PAIRING_TOKEN=abc' "export TYPESAFE_API_KEY=\"$KEY\"" > "$HOME_DIR/.env"
reset_log
run code out err "$LEARNING"
expect_code 0 "$code" ".env key resolves"
assert_contains "$out" '  status: clear' ".env key produces a clear result"
assert_contains "$(cat "$LOG/header")" "Authorization: Bearer $KEY" ".env key reaches curl on the fd header"
reset_log
TYPESAFE_API_KEY=env-wins run code out err "$LEARNING"
assert_equals 'Authorization: Bearer env-wins' "$(cat "$LOG/header")" "environment key wins over .env"
rm -f "$HOME_DIR/.env"
pass "TYPESAFE_API_KEY= in .env activates the tool; the environment wins over it"

# --- clear: request shape, secret handling, owner machine lines --------------
reset_log
write_response "$RESPONSE" learnings-md 0.9
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
expect_code 0 "$code" "clear exits 0"
assert_contains "$out" 'stow-owner-resolve:' "TOON block header"
assert_contains "$out" '  status: clear' "clear status"
assert_contains "$out" '  match: learnings-md (Fleet-local operational facts.)   confidence: 0.9' "match and confidence line"
assert_equals '1' "$(grep -c '^  owner:' <<<"$out")" "exactly one machine owner line"
assert_contains "$out" '  owner: learnings-md' "machine owner line names the slug"
assert_contains "$out" '  write: direct' "default write path is direct"
assert_absent "$LOG/quota-axi.calls" "the router never reads quota-axi"
argv=$(cat "$LOG/argv")
assert_not_contains "$argv" "$KEY" "the key never appears on curl argv"
assert_contains "$argv" 'https://api.typesafe.ai/v1/systemone' "the request uses the fixed typesafe.ai endpoint"
assert_contains "$argv" $'--max-time\n5' "the request uses the fixed five-second timeout"
assert_contains "$argv" '@/dev/fd/3' "the header is read from a file descriptor"
assert_equals "Authorization: Bearer $KEY" "$(cat "$LOG/header")" "curl receives the bearer header on fd 3"
assert_equals 'curl:clean' "$(cat "$LOG/child-env")" "the API key is absent from the child environment"
body=$(cat "$LOG/body")
assert_equals 'jev-latest' "$(jq -r .model <<<"$body")" "default model is jev-latest"
assert_contains "$(jq -r .state.task <<<"$body")" 'Treehouse pool slots share one repo' "the candidate learning text rides in the state"
assert_equals '["owner"]' "$(jq -c '.questions | keys' <<<"$body")" "only the owner Choice is asked"
assert_equals '["backlog-note","captain-md","captain-shared-md","elsewhere-or-drop","firstmate-tracked","learnings-md","project-agents-md","scout-report"]' "$(jq -c '.questions.owner.criteria | keys' <<<"$body")" "one option per knowledge owner plus the fixed neutral none"
assert_equals 'Which ONE owner does this learning belong to? Pick the most specific owner.' "$(jq -r '.questions.owner.instructions' <<<"$body")" "the fixed owner instructions travel"
assert_equals 'No listed owner applies; file elsewhere or drop.' "$(jq -r '.questions.owner.criteria["elsewhere-or-drop"]' <<<"$body")" "the fixed neutral none criterion is the elsewhere-or-drop option"
assert_equals 'Home-domain captain preferences and working style.' "$(jq -r '.questions.owner.criteria["captain-md"]' <<<"$body")" "owner criteria come from the AGENTS.md routing list"
assert_not_contains "$body" 'pinned' "tier defaults never leave the machine"
assert_not_contains "$body" 'reinforcement' "the evidence gate never leaves the machine"
pass "clear: one owner Choice request, key on the fd header only, machine owner and write lines"

# --- gate (a): secondmate read-only reroute -----------------------------------
reset_log
write_response "$RESPONSE" captain-shared-md 0.92
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING" --secondmate-home
expect_code 0 "$code" "secondmate reroute exits 0"
assert_contains "$out" '  status: clear' "the reroute stays clear"
assert_contains "$out" '  owner: captain-shared-md' "the answer owner is preserved"
assert_contains "$out" '  write: route-to-primary' "a secondmate home routes shared findings to the primary owner"
assert_contains "$out" 'primary-owned read-only' "the reroute names the read-only ownership exception"
reset_log
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
assert_contains "$out" '  write: direct' "a primary home files a shared finding directly"
assert_contains "$out" '  owner: captain-shared-md' "the primary home keeps the same owner"
pass "gate (a): secondmate home plus captain-shared-md reroutes to the primary owner"

# --- gate (b): project memory is never written directly ------------------------
reset_log
write_response "$RESPONSE" project-agents-md 0.88
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
expect_code 0 "$code" "project gate exits 0"
assert_contains "$out" '  status: clear' "the project answer stays clear"
assert_contains "$out" '  owner: project-agents-md' "the answer owner is preserved"
assert_contains "$out" '  write: via-delivery-path' "project findings travel the delivery path"
assert_contains "$out" 'never written directly' "the project-memory gate names the no-direct-edit boundary"
reset_log
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING" --secondmate-home
assert_contains "$out" '  write: via-delivery-path' "the project gate holds in a secondmate home too"
pass "gate (b): project-agents-md answers route through the delivery path"

# --- ambiguous: fixed confidence floor -----------------------------------------
reset_log
write_response "$RESPONSE" learnings-md 0.41
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
expect_code 0 "$code" "ambiguous exits 0"
assert_contains "$out" '  status: ambiguous' "below the floor is ambiguous"
assert_contains "$out" '  reason: confidence 0.41 below floor 0.6' "ambiguous names the floor"
assert_not_contains "$out" '  owner:' "ambiguous emits no owner line"
assert_not_contains "$out" '  write:' "ambiguous emits no write line"
pass "ambiguous: confidence below the fixed floor hands the finding back to manual routing"

# --- API and response failures are error outcomes, exit 0 ----------------------
reset_log
run_without_curl code out err "$LEARNING"
expect_code 0 "$code" "missing curl exits 0"
assert_contains "$out" '  status: error' "missing curl is a structured error outcome"
assert_contains "$out" '  reason: curl not installed' "missing curl is named in the TOON block"
assert_contains "$err" 'stow-owner-resolve: error (curl not installed)' "missing curl is also reported on stderr"
reset_log
write_response "$RESPONSE" learnings-md 0.9
TYPESAFE_API_KEY=$KEY FAKE_CURL_HTTP=500 run code out err "$LEARNING"
expect_code 0 "$code" "http 500 exits 0"
assert_contains "$out" '  status: error' "http 500 is an error outcome"
assert_contains "$out" '  reason: http 500 after' "http status is reported"
reset_log
printf '%s\n' '{"model":"jev","answers":{}}' > "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
assert_contains "$out" '  reason: response is not an owner Choice answer' "a malformed answer is an error outcome"
reset_log
write_response "$RESPONSE" learnings-md 0.9
jq 'del(.answers.owner.probabilities["learnings-md"])' "$RESPONSE" > "$TMP_ROOT/malformed-probabilities.json"
mv "$TMP_ROOT/malformed-probabilities.json" "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
assert_contains "$out" '  status: error' "a missing probability choice is an error outcome"
assert_contains "$out" '  reason: response is not an owner Choice answer' "probabilities must name every offered choice"
reset_log
write_response "$RESPONSE" learnings-md 0.9
jq '.answers.owner.probabilities[] = 0' "$RESPONSE" > "$TMP_ROOT/zero-probabilities.json"
mv "$TMP_ROOT/zero-probabilities.json" "$RESPONSE"
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
assert_contains "$out" '  reason: response is not an owner Choice answer' "probabilities must sum to approximately one"
reset_log
write_response "$RESPONSE" learnings-md 2
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
assert_contains "$out" '  reason: response is not an owner Choice answer' "out-of-range confidence is a malformed answer"
reset_log
write_response "$RESPONSE" mystery-owner 0.9
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
assert_contains "$out" '  status: error' "an unknown owner id is an error outcome"
assert_contains "$out" '  reason: owner mystery-owner is not a known owner' "the unknown owner id is named"
assert_not_contains "$out" '  owner:' "an unknown owner emits no owner line"
pass "API, transport, and response failures are error outcomes with exit 0"

# --- configuration errors exit 2 and select nothing ------------------------------
reset_log
TYPESAFE_API_KEY=$KEY run code out err
expect_code 2 "$code" "missing learning file exits 2"
assert_contains "$err" 'learning file required' "missing learning file is named"
TYPESAFE_API_KEY=$KEY run code out err "$TMP_ROOT/no-such-learning.md"
expect_code 2 "$code" "unreadable learning file exits 2"
assert_contains "$err" 'learning file not readable' "unreadable learning file is named"
printf '' > "$TMP_ROOT/empty.md"
TYPESAFE_API_KEY=$KEY run code out err "$TMP_ROOT/empty.md"
expect_code 2 "$code" "empty learning file exits 2"
assert_contains "$err" 'learning file is empty' "empty learning file is named"
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING" --bogus
expect_code 2 "$code" "unknown flag exits 2"
assert_contains "$err" 'unknown flag --bogus' "unknown flag is named"
assert_absent "$LOG/argv" "configuration errors never reach the network"
run code out err --help
expect_code 0 "$code" "--help exits 0"
assert_contains "$out" 'Usage:' "--help prints usage"
pass "configuration errors exit 2 before any network call"

# --- Jev gateway ladder: free first, captain's key on refusal -----------------
GWKEY='test-gateway-key-4b7e1a9c-never-on-argv'
GW_URL='https://ai-gateway.vercel.sh/typesafe/v1/systemone'
TS_URL='https://api.typesafe.ai/v1/systemone'

curl_calls() { cat "$LOG/curl-count" 2>/dev/null || printf '0'; }

# --- gateway-only key answers on the free rung --------------------------------
reset_log
write_response "$RESPONSE" learnings-md 0.9
AI_GATEWAY_API_KEY=$GWKEY run code out err "$LEARNING"
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
assert_equals 'curl:clean' "$(cat "$LOG/child-env")" "the key leaks into no child environment"
pass "gateway-only key answers on the free rung"

# --- typesafe-only key keeps today's behaviour ---------------------------------
reset_log
write_response "$RESPONSE" learnings-md 0.9
TYPESAFE_API_KEY=$KEY run code out err "$LEARNING"
expect_code 0 "$code" "typesafe-only exits 0"
assert_contains "$out" '  rung: typesafe' "typesafe-only names its rung"
assert_equals 'jev-latest' "$(jq -r .model < "$LOG/body")" "typesafe rung asks for jev-latest"
pass "typesafe-only key answers on the typesafe rung"

# --- gateway 429 descends to the captain's key once ----------------------------
reset_log
write_response "$RESPONSE" learnings-md 0.9
TYPESAFE_API_KEY=$KEY AI_GATEWAY_API_KEY=$GWKEY FAKE_CURL_HTTP=429 FAKE_CURL_HTTP2=200 run code out err "$LEARNING"
expect_code 0 "$code" "ladder fallback exits 0"
assert_contains "$out" '  status: clear' "ladder fallback resolves"
assert_contains "$out" '  rung: typesafe' "ladder fallback names the serving rung"
assert_equals '2' "$(curl_calls)" "ladder fallback makes exactly two calls"
assert_equals 'typesafe-ai/jev' "$(jq -r .model < "$LOG/body-1")" "the first call tries the gateway model"
assert_equals 'jev-latest' "$(jq -r .model < "$LOG/body-2")" "the second call descends to the typesafe model"
assert_equals "Authorization: Bearer $GWKEY" "$(cat "$LOG/header-1")" "the first call carries the gateway key"
assert_equals "Authorization: Bearer $KEY" "$(cat "$LOG/header")" "the second call carries the typesafe key"
pass "gateway 429 descends the ladder once in the same call"
