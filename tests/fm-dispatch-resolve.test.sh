#!/usr/bin/env bash
# Behavior tests for deterministic policy resolution and persisted explanations.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TOOL="$ROOT/bin/fm-dispatch-resolve.sh"
TMP_ROOT=$(fm_test_tmproot fm-dispatch-resolve)
HOME_DIR="$TMP_ROOT/home"
FAKEBIN="$TMP_ROOT/bin"
BRIEF="$TMP_ROOT/brief.md"
LOG="$TMP_ROOT/curl.log"
mkdir -p "$HOME_DIR/config" "$HOME_DIR/state" "$FAKEBIN"
export HOME="$HOME_DIR/user"
export XDG_CONFIG_HOME="$HOME_DIR/xdg-config"
mkdir -p "$HOME" "$XDG_CONFIG_HOME"
cat > "$BRIEF" <<'MD'
# Task
## Captain's intent
Implement a small deterministic dispatch regression.
## Firstmate spec
Use the policy route.
MD
cat > "$FAKEBIN/quota-axi" <<'SH'
#!/usr/bin/env bash
if [ -n "${FM_TEST_QUOTA:-}" ]; then cat "$FM_TEST_QUOTA"; else printf '%s\n' '{"schemaVersion":5,"providers":[]}'; fi
SH
cat > "$FAKEBIN/curl" <<'SH'
#!/usr/bin/env bash
printf called >> "$FM_TEST_CURL_LOG"
exit 1
SH
chmod +x "$FAKEBIN/quota-axi" "$FAKEBIN/curl"

write_policy() {
  cat > "$HOME_DIR/config/dispatch-policy.json" <<'JSON'
{
  "version": 1,
  "rules": [
    {"id":"lucie","when":"Lucie registered project work","trigger":{"kind":"projects","names":["starling-site"]},"use":{"harness":"grok","model":"grok-4","effort":"high"}},
    {"id":"unshaped","when":"Work not shaped into another route","trigger":{"kind":"declaration"},"use":{"harness":"claude","model":"claude-opus-5-5","effort":"medium"}}
  ],
  "default":[
    {"harness":"codex","model":"gpt-6-luna","effort":"medium","provider":"codex"},
    {"harness":"opencode","model":"opencode-go/muse-spark-1.3-contributor","provider":"opencode-go"}
  ],
  "default_ladder":[
    {"id":"plus","harness":"codex","model":"gpt-6-luna","effort":"medium","provider":"codex"},
    {"id":"go","harness":"opencode","model":"opencode-go/muse-spark-1.3-contributor","provider":"opencode-go"}
  ]
}
JSON
}
write_policy

run_resolver() {
  PATH="$FAKEBIN:$PATH" FM_HOME="$HOME_DIR" FM_TEST_CURL_LOG="$LOG" \
    "$TOOL" --json "$BRIEF" "$@"
}

out=$(run_resolver --project starling-site)
assert_equals clear "$(jq -r .status <<<"$out")" "project trigger resolves"
assert_equals rule:1 "$(jq -r .source <<<"$out")" "project trigger wins before declaration"
assert_equals grok "$(jq -r .profile.harness <<<"$out")" "registered project route selects its profile"
assert_contains "$(jq -c .explanation.candidates <<<"$out")" 'grok-4' "candidate evidence is returned"
pass "exact registered project trigger resolves before the declaration trigger"

out=$(run_resolver)
assert_equals clear "$(jq -r .status <<<"$out")" "declaration trigger resolves"
assert_equals rule:2 "$(jq -r .source <<<"$out")" "declaration trigger is selected"
assert_equals claude "$(jq -r .profile.harness <<<"$out")" "unshaped work uses its declared profile"
[ ! -e "$LOG" ] || fail "deterministic resolution called curl"
pass "declaration trigger routes unshaped work without network judgment"

jq 'del(.rules)' "$HOME_DIR/config/dispatch-policy.json" > "$TMP_ROOT/default.json"
cp "$TMP_ROOT/default.json" "$HOME_DIR/config/dispatch-policy.json"
out=$(run_resolver)
assert_equals default "$(jq -r .source <<<"$out")" "default route resolves"
assert_equals codex "$(jq -r .profile.harness <<<"$out")" "the Codex Plus rung is first"
assert_equals opencode "$(jq -r .profile.dispatch_ladder <<<"$out")" "the internal ladder gate is present"
pass "default ladder derives its route from the declared policy"

cat > "$HOME_DIR/config/model-catalog.json" <<'JSON'
{"models":[{"id":"opencode-go/muse-spark-1.3-contributor","status":"active"},{"id":"gpt-6-luna","status":"active"},{"id":"retired-model","status":"retired","replaced_by":"replacement-model"}]}
JSON
jq '.default[0].model="retired-model"' "$HOME_DIR/config/dispatch-policy.json" > "$TMP_ROOT/retired.json"
cp "$TMP_ROOT/retired.json" "$HOME_DIR/config/dispatch-policy.json"
out=$(run_resolver)
assert_equals error "$(jq -r .status <<<"$out")" "retired rung refuses dispatch"
assert_contains "$(jq -r .reason <<<"$out")" 'retired-model' "retired model is named"
assert_contains "$(jq -r .reason <<<"$out")" 'replacement-model' "replacement is named"
pass "retired model IDs are refused before spawn with their replacement"

jq '. + {exhausted_ladder_fallback:{use:{"harness":"opencode"}}}' "$TMP_ROOT/default.json" > "$HOME_DIR/config/dispatch-policy.json"
out=$(run_resolver 2>&1)
assert_equals 2 "$?" "retired fallback declaration is rejected"
assert_contains "$out" 'exhausted_ladder_fallback has been retired' "fallback refusal is actionable"
pass "the exhausted-ladder fallback cannot be re-enabled by policy"

printf '# all fm-dispatch-resolve tests passed\n'
