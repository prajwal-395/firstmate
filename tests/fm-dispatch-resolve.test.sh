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
  "version": 2,
  "rules": [
    {"id":"lucie","when":"Lucie registered project work","trigger":{"kind":"projects","names":["starling-site"]},"ladder":[{"id":"lucie-grok","harness":"grok","model":"grok-4","provider":"grok","effort":"high"}]},
    {"id":"unshaped","when":"Work not shaped into another route","trigger":{"kind":"declaration"},"ladder":[{"id":"unshaped-opus","harness":"claude","model":"claude-opus-5-5","provider":"anthropic","effort":"medium"}]}
  ],
  "default_ladder":[
    {"id":"deepseek","harness":"opencode","model":"opencode/deepseek-v4.1-flash-free","provider":"opencode"},
    {"id":"glm","harness":"opencode","model":"opencode/glm-5.3-flash-free","provider":"opencode"},
    {"id":"mimo","harness":"opencode","model":"opencode/mimo-v2.6-flash-free","provider":"opencode"},
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
assert_equals default "$(jq -r .status <<<"$out")" "an undeclared task takes the default"
assert_equals default "$(jq -r .source <<<"$out")" "an existing declaration rule never fires by itself"
assert_equals opencode "$(jq -r .profile.harness <<<"$out")" "the default ladder starts at the first configured free OpenCode rung"
[ ! -e "$LOG" ] || fail "deterministic resolution called curl"
pass "an existing declaration rule never fires without an explicit declaration"

out=$(run_resolver --declare unshaped)
assert_equals clear "$(jq -r .status <<<"$out")" "a declared task selects its rule"
assert_equals rule:2 "$(jq -r .source <<<"$out")" "the named declaration trigger is selected"
assert_equals claude "$(jq -r .profile.harness <<<"$out")" "declared unshaped work uses its declared profile"
pass "an explicit declaration routes unshaped work without network judgment"

out=$(run_resolver --project starling-site --declare unshaped)
assert_equals rule:1 "$(jq -r .source <<<"$out")" "an exact project match wins over a declaration"
assert_equals grok "$(jq -r .profile.harness <<<"$out")" "the project profile is selected"
pass "an exact project match wins over an explicit declaration"

out=$(run_resolver --declare bogus 2>&1)
assert_equals 2 "$?" "an unknown declaration id is rejected"
assert_contains "$out" 'unshaped' "the refusal names the actual declaration trigger"
pass "an unknown declaration id fails closed with the actual trigger named"

out=$(run_resolver)
assert_equals default "$(jq -r .source <<<"$out")" "default route resolves"
assert_equals opencode "$(jq -r .profile.harness <<<"$out")" "the first configured policy rung is selected"
assert_equals true "$(jq -r .profile.dispatch_unified <<<"$out")" "the unified policy route is marked for generic enforcement"
assert_equals false "$(jq -r '.profile | has("dispatch_ladder")' <<<"$out")" "the route contains no legacy ladder marker"
pass "the v2 policy directly determines the route without legacy ladder state"

jq '{version:2,rules:[],default_ladder:.default_ladder,constraints:[],providers:[]}' "$HOME_DIR/config/dispatch-policy.json" > "$TMP_ROOT/default.json"
cp "$TMP_ROOT/default.json" "$HOME_DIR/config/dispatch-policy.json"
out=$(run_resolver --declare unshaped 2>&1)
assert_equals 2 "$?" "a declaration with no declaration trigger is rejected"
assert_contains "$out" 'declares no declaration trigger' "the refusal states the policy has none"
pass "declaring against a policy with no declaration trigger fails closed"

cat > "$HOME_DIR/config/model-catalog.json" <<'JSON'
{"models":[{"id":"opencode/deepseek-v4.1-flash-free","status":"active"},{"id":"opencode/glm-5.3-flash-free","status":"active"},{"id":"opencode/mimo-v2.6-flash-free","status":"active"},{"id":"opencode-go/muse-spark-1.3-contributor","status":"active"},{"id":"gpt-6-luna","status":"active"},{"id":"retired-model","status":"retired","replaced_by":"replacement-model"}]}
JSON
jq '.default_ladder[0].model="retired-model"' "$HOME_DIR/config/dispatch-policy.json" > "$TMP_ROOT/retired.json"
cp "$TMP_ROOT/retired.json" "$HOME_DIR/config/dispatch-policy.json"
out=$(run_resolver)
assert_equals glm "$(jq -r .explanation.chosen.profile.id <<<"$out")" "a retired rung falls through to the next eligible rung"
assert_contains "$(jq -r '.explanation.candidates[0].reason' <<<"$out")" 'retired-model' "retired model is named in its candidate explanation"
assert_contains "$(jq -r '.explanation.candidates[0].reason' <<<"$out")" 'replacement-model' "replacement is named in its candidate explanation"
pass "a retired model rung is explained and skipped in favor of the next eligible rung"

cat > "$HOME_DIR/config/model-catalog.json" <<'JSON'
{"models":[{"id":"vendor/free-model","status":"active","harnesses":["codex"]},{"id":"gpt-6-luna","status":"active","harnesses":["codex"]}]}
JSON
cat > "$HOME_DIR/config/dispatch-policy.json" <<'JSON'
{"version":2,"rules":[],"default_ladder":[{"id":"pi-free","harness":"pi","model":"vendor/free-model","provider":"vendor"},{"id":"codex-next","harness":"codex","model":"gpt-6-luna","provider":"codex","effort":"medium"}],"constraints":[],"providers":[]}
JSON
out=$(run_resolver)
assert_equals codex-next "$(jq -r .explanation.chosen.profile.id <<<"$out")" "an unsupported model/harness pair falls through to the next rung"
assert_contains "$(jq -r '.explanation.candidates[0].reason' <<<"$out")" 'not verified for harness pi' "the fallback explanation names the unsupported harness"
pass "a rung whose model is not verified for its harness is skipped with an explanation"
cat > "$HOME_DIR/config/model-catalog.json" <<'JSON'
{"models":[{"id":"opencode/deepseek-v4.1-flash-free","status":"active"},{"id":"opencode/glm-5.3-flash-free","status":"active"},{"id":"opencode/mimo-v2.6-flash-free","status":"active"},{"id":"opencode-go/muse-spark-1.3-contributor","status":"active"},{"id":"gpt-6-luna","status":"active"},{"id":"retired-model","status":"retired","replaced_by":"replacement-model"}]}
JSON

jq '. + {unrecognized_route_switch:true}' "$TMP_ROOT/default.json" > "$HOME_DIR/config/dispatch-policy.json"
out=$("$ROOT/bin/fm-dispatch-policy-validate.sh" "$HOME_DIR/config/dispatch-policy.json" 2>&1)
assert_equals 1 "$?" "unknown policy fields are rejected"
assert_contains "$out" 'unknown top-level policy fields' "schema refusal names the unknown field category"
pass "the v2 policy schema rejects unsupported routing switches"

cp "$ROOT/docs/examples/dispatch-policy.json" "$HOME_DIR/config/dispatch-policy.json"
out=$(run_resolver)
assert_equals default "$(jq -r .status <<<"$out")" "the unified policy chooses from its default ladder"
assert_equals deepseek-free "$(jq -r .explanation.chosen.profile.id <<<"$out")" "an arbitrary-length ladder starts at its first eligible rung"
assert_equals secondary "$(jq -r .profile.workspace <<<"$out")" "the first configured workspace is preserved in the selected route"
pass "version 2 resolves the first eligible rung and retains its configured workspace"

now_ms=$(( $(date +%s) * 1000 ))
reset_ms=$((now_ms + 60000))
"$ROOT/bin/fm-dispatch-reset.sh" record "$HOME_DIR/state" opencode deepseek-v4.1-flash-free secondary "$reset_ms" test
out=$(run_resolver)
assert_equals main "$(jq -r .profile.workspace <<<"$out")" "an active reset skips only its provider/model/workspace tuple"
"$ROOT/bin/fm-dispatch-reset.sh" record "$HOME_DIR/state" opencode opencode/deepseek-v4.1-flash-free main "$reset_ms" test
out=$(run_resolver)
assert_equals glm-free "$(jq -r .explanation.chosen.profile.id <<<"$out")" "both accounts for a rung precede the next model"
pass "generic exhaustion records affect launch selection by provider, model, and workspace"

for model in opencode/deepseek-v4.1-flash-free opencode/glm-5.3-flash-free opencode/mimo-v2.6-flash-free; do
  for workspace in secondary main; do
    "$ROOT/bin/fm-dispatch-reset.sh" record "$HOME_DIR/state" opencode "$model" "$workspace" "$reset_ms" test
  done
done
cat > "$TMP_ROOT/account-quota.json" <<'JSON'
{"schemaVersion":6,"providers":[
  {"provider":"codex","accountKey":"default","quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"runway":{"status":"exhausted_now"},"resetsAt":"2099-01-01T00:00:00Z"}]}},
  {"provider":"opencode-go","accountKey":"main","quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"},"resetsAt":"2099-02-01T00:00:00Z"}]}},
  {"provider":"opencode-go","accountKey":"secondary","quotaSemantics":{"status":"known","effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":80,"runway":{"status":"through_reset"},"resetsAt":"2099-01-01T00:00:00Z"}]}}
]}
JSON
export FM_TEST_QUOTA="$TMP_ROOT/account-quota.json"
out=$(run_resolver)
unset FM_TEST_QUOTA
assert_equals muse-go "$(jq -r .explanation.chosen.profile.id <<<"$out")" "exhausted earlier rungs fall through to the configured Go rung"
assert_equals secondary "$(jq -r .profile.workspace <<<"$out")" "reset-soonest strategy orders account choices from quota-axi"
pass "the configured reset-soonest account strategy selects from quota reset evidence"

out=$(run_resolver --declare open-ended)
assert_equals escalate "$(jq -r .status <<<"$out")" "Opus requires affirmative captain approval"
out=$(PATH="$FAKEBIN:$PATH" FM_HOME="$HOME_DIR" FM_TEST_CURL_LOG="$LOG" "$TOOL" --json "$BRIEF" --declare open-ended --captain-approved)
assert_equals claude-opus-5-5 "$(jq -r .profile.model <<<"$out")" "explicit approval enables the declared open-ended Opus route"
mkdir -p "$HOME_DIR/data"
printf '%s\n' 'Research charter: this home cannot use Claude.' > "$HOME_DIR/data/charter.md"
out=$(run_resolver --declare open-ended --captain-approved)
assert_equals escalate "$(jq -r .status <<<"$out")" "a research home cannot route to Anthropic even with captain approval"
pass "captain approval and research-home provider constraints are enforced at resolution"

# Defect: the constraints say provider "anthropic" while Claude rungs and
# quota-axi say "claude", so an Opus rung spelled "claude" launched unapproved.
rm -f "$HOME_DIR/data/charter.md"
jq '(.rules[] | select(.id == "open-ended") | .ladder[]) |= (.provider = "claude" | del(.approval))' \
  "$ROOT/docs/examples/dispatch-policy.json" > "$HOME_DIR/config/dispatch-policy.json"
out=$(run_resolver --declare open-ended)
assert_equals escalate "$(jq -r .status <<<"$out")" "an Opus rung with provider claude requires captain approval"
printf '%s\n' 'Research charter: this home cannot use Claude.' > "$HOME_DIR/data/charter.md"
out=$(run_resolver --declare open-ended --captain-approved)
assert_equals escalate "$(jq -r .status <<<"$out")" "a research home cannot route to a provider claude rung"
pass "approval and research-deny constraints match a Claude rung spelled provider claude"

printf '# all fm-dispatch-resolve tests passed\n'
