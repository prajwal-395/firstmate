#!/usr/bin/env bash
# Behavior tests for bin/fm-mate-route.sh.
#
# Drives the public argv and environment interface with a fake curl on PATH
# that records the request body it read from stdin and answers with a canned
# typesafe.ai response. No case touches the network, and the absent-key case
# proves the tool makes no call at all. Each gate test answers the model with
# a confident choice the gate must overrule, so the test fails when the gate
# is removed.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

TOOL="$ROOT/bin/fm-mate-route.sh"
TMP_ROOT=$(fm_test_tmproot fm-mate-route)
HOME_DIR="$TMP_ROOT/home"
FAKEBIN=$(fm_fakebin "$TMP_ROOT")
LOG="$TMP_ROOT/log"
INTAKE="$TMP_ROOT/intake.txt"
REGISTRY="$HOME_DIR/data/secondmates.md"
BASE_PATH=$PATH
mkdir -p "$HOME_DIR/data" "$HOME_DIR/config" "$LOG"

cat > "$INTAKE" <<'TXT'
Cut a 30-second teaser from the latest timeline and check the captions land on the beat.
TXT

# The registry carries a projects-only decoy: "teaser" appears in vep's
# projects list but in no scope text, so a router that lets the model see
# projects: could learn the wrong key while the scope-only router cannot.
cat > "$REGISTRY" <<'TXT'
# Secondmates
- vep - Video work end to end. (home: /tmp/fm-mate-route-test/vep; scope: All video work end to end: ingest, timeline construction, captions, overlays, grading and delivery; projects: teaser-pipeline, video-editing-pilot; added 2026-09-14)
- lucie - Client code work. (home: /tmp/fm-mate-route-test/lucie; scope: All Lucie Content client code work: lead generation, GEO systems and client marketing sites; projects: business-finder-lucie; added 2026-08-18)
TXT

cat > "$HOME_DIR/data/projects.md" <<'TXT'
- video-editing-pilot [direct-PR] - Video pipeline. (added 2026-09-14)
- vault-notes [local-only] - Captain-private notes. (added 2026-09-15)
TXT

write_response() {  # <path> <choice> <confidence>
  cat > "$1" <<JSON
{ "model": "jev-1.13.0",
  "answers": { "mate": { "type": "choice", "choice": "$2", "confidence": $3,
    "probabilities": { "default": 0.05, "lucie": 0.05, "vep": 0.90 } } },
  "usage": { "input_tokens": 200, "output_tokens": 60 } }
JSON
}

cat > "$FAKEBIN/curl" <<'SH'
#!/usr/bin/env bash
# Fake curl: records the stdin body, then answers with FAKE_CURL_RESPONSE.
set -u
out=''
while [ $# -gt 0 ]; do
  case "$1" in
    -o) out=$2; shift 2 ;;
    *) shift ;;
  esac
done
cat > "$FAKE_CURL_LOG/body"
if [ "${FAKE_CURL_FAIL:-0}" = 1 ]; then
  exit 7
fi
cp "${FAKE_CURL_RESPONSE:?}" "$out"
printf '%s' "${FAKE_CURL_HTTP:-200}"
SH
chmod +x "$FAKEBIN/curl"

RESPONSE="$TMP_ROOT/response.json"
export FAKE_CURL_LOG="$LOG" FAKE_CURL_RESPONSE="$RESPONSE"

reset_log() {
  rm -rf "$LOG"
  mkdir -p "$LOG"
}

# run <exit-var> <out-var> <err-var> [args...]: the tool with fakebin first on
# PATH and an isolated FM_HOME; TYPESAFE_API_KEY comes from the caller's env.
run() {
  local __exit=$1 __out=$2 __err=$3 _out _code
  shift 3
  _out=$(PATH="$FAKEBIN:$BASE_PATH" FM_HOME="$HOME_DIR" TYPESAFE_API_KEY="$KEY" "$TOOL" "$@" 2> "$TMP_ROOT/stderr")
  _code=$?
  printf -v "$__exit" '%s' "$_code"
  printf -v "$__out" '%s' "$_out"
  printf -v "$__err" '%s' "$(cat "$TMP_ROOT/stderr")"
}

KEY='test-key-9f1c2d3e-never-on-argv'
code='' out='' err=''
NL_MATE='
  mate:'

# --- absent key: off, silent on stdout, no network ---------------------------
reset_log
out=$(PATH="$FAKEBIN:$BASE_PATH" FM_HOME="$HOME_DIR" "$TOOL" "$INTAKE" 2> "$TMP_ROOT/stderr")
code=$?
err=$(cat "$TMP_ROOT/stderr")
expect_code 0 "$code" "absent key exits 0"
assert_equals '' "$out" "absent key prints nothing on stdout"
assert_contains "$err" "mate-route: off" "absent key prints the off line on stderr"
assert_absent "$LOG/body" "absent key never calls curl"

# --- clear route: confident scope match routes the mate -----------------------
reset_log
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project video-editing-pilot
expect_code 0 "$code" "clear route exits 0"
assert_contains "$out" '  status: clear' "confident match is clear"
assert_contains "$out" '  mate: vep' "confident match routes vep"
assert_contains "$out" '  answer: vep' "the answer names vep"

# --- scope-text options: criteria carry scope, never projects -----------------
body=$(cat "$LOG/body")
assert_equals 'Which ONE secondmate scope best fits this work? Pick an entry only when its scope text covers the work; pick default when no scope covers it.' "$(jq -r '.questions.mate.instructions' <<<"$body")" "the mate instructions are fixed"
assert_equals 'All video work end to end: ingest, timeline construction, captions, overlays, grading and delivery' "$(jq -r '.questions.mate.criteria.vep' <<<"$body")" "the vep criterion is its scope text"
assert_equals 'No listed scope applies; the main home keeps the work.' "$(jq -r '.questions.mate.criteria.default' <<<"$body")" "the default criterion keeps the work home"
assert_not_contains "$body" "teaser-pipeline" "the projects-only decoy never reaches the model"
assert_not_contains "$body" "projects" "the projects key never reaches the model"
assert_contains "$body" "Resolved project: video-editing-pilot" "the resolved project travels in state"

# --- gate (a) local-only: safety boundary overrules a confident answer --------
reset_log
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project vault-notes
expect_code 0 "$code" "local-only exits 0"
assert_contains "$out" '  status: clear' "local-only is a decided route"
assert_contains "$out" '  mate: main' "local-only stays with the main home"
assert_contains "$out" '  gate: local-only' "local-only names its gate"

# --- gate (c) liveness, blocked: falls through to the main home ---------------
reset_log
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project video-editing-pilot --blocked vep
expect_code 0 "$code" "blocked target exits 0"
assert_contains "$out" '  status: clear' "blocked fall-through is decided"
assert_contains "$out" '  mate: main' "blocked target keeps the work home"
assert_contains "$out" '  gate: liveness' "blocked fall-through names its gate"

# --- gate (c) liveness, unreachable: falls through to the main home -----------
reset_log
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project video-editing-pilot --unreachable vep
expect_code 0 "$code" "unreachable target exits 0"
assert_contains "$out" '  mate: main' "unreachable target keeps the work home"
assert_contains "$out" '  gate: liveness' "unreachable fall-through names its gate"

# --- gate (d) redirect: the captain wins over a confident answer ---------------
reset_log
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project video-editing-pilot --redirect lucie
expect_code 0 "$code" "redirect exits 0"
assert_contains "$out" '  status: clear' "redirect is a decided route"
assert_contains "$out" '  mate: lucie' "redirect routes the captain's mate"
assert_contains "$out" '  gate: captain-redirect' "redirect names its gate"

# --- gate (d) redirect to main: the work stays ---------------------------------
reset_log
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project video-editing-pilot --redirect main
expect_code 0 "$code" "redirect-main exits 0"
assert_contains "$out" '  mate: main' "redirect to main keeps the work home"
assert_contains "$out" '  gate: captain-redirect' "redirect to main names its gate"

# --- gate (d) redirect to a blocked mate: escalate, never guess ----------------
reset_log
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project video-editing-pilot --redirect lucie --blocked lucie
expect_code 0 "$code" "redirect-blocked exits 0"
assert_contains "$out" '  status: escalate' "redirect to a blocked mate escalates"
assert_not_contains "$out" "$NL_MATE" "escalation hands back the unrouted intake"

# --- fallback-first: low confidence returns unrouted, never a guess ------------
reset_log
write_response "$RESPONSE" vep 0.41
run code out err "$INTAKE" --project video-editing-pilot
expect_code 0 "$code" "low confidence exits 0"
assert_contains "$out" '  status: ambiguous' "low confidence is ambiguous"
assert_contains "$out" 'below floor' "low confidence names the floor"
assert_not_contains "$out" "$NL_MATE" "ambiguity hands back the unrouted intake"

# --- default answer: no scope covers the work, the main home keeps it ----------
reset_log
cat > "$RESPONSE" <<'JSON'
{ "model": "jev-1.13.0",
  "answers": { "mate": { "type": "choice", "choice": "default", "confidence": 0.88,
    "probabilities": { "default": 0.88, "lucie": 0.06, "vep": 0.06 } } },
  "usage": { "input_tokens": 200, "output_tokens": 60 } }
JSON
run code out err "$INTAKE" --project video-editing-pilot
expect_code 0 "$code" "default answer exits 0"
assert_contains "$out" '  status: clear' "default answer is decided"
assert_contains "$out" '  mate: main' "default answer keeps the work home"

# --- error: API failure routes as today, unrouted -------------------------------
reset_log
write_response "$RESPONSE" vep 0.92
FAKE_CURL_FAIL=1 run code out err "$INTAKE" --project video-editing-pilot
unset FAKE_CURL_FAIL
expect_code 0 "$code" "curl failure exits 0"
assert_contains "$out" '  status: error' "curl failure is a structured error"
assert_not_contains "$out" "$NL_MATE" "error hands back the unrouted intake"

# --- empty registry: escalate without a network call ----------------------------
reset_log
printf '# Secondmates\n' > "$REGISTRY"
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --project video-editing-pilot
expect_code 0 "$code" "empty registry exits 0"
assert_contains "$out" '  status: escalate' "empty registry escalates"
assert_contains "$out" 'no secondmate scopes registered' "empty registry names its reason"
assert_absent "$LOG/body" "empty registry never calls the model"

# --- usage error: redirect to an unknown mate exits 2 --------------------------
reset_log
printf '%s\n' "- vep - Video work end to end. (home: /tmp/fm-mate-route-test/vep; scope: video work; projects: video-editing-pilot; added 2026-09-14)" > "$REGISTRY"
write_response "$RESPONSE" vep 0.92
run code out err "$INTAKE" --redirect nosuchmate
expect_code 2 "$code" "unknown redirect exits 2"
