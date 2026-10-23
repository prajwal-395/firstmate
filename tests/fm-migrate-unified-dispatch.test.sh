#!/usr/bin/env bash
# Behavior tests for v1-to-v2 policy migration and catalog preservation.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TOOL="$ROOT/bin/fm-migrate-unified-dispatch.sh"
TMP_ROOT=$(fm_test_tmproot fm-migrate-unified-dispatch)
HOME_DIR="$TMP_ROOT/home"
mkdir -p "$HOME_DIR/config"
cat > "$HOME_DIR/config/dispatch-policy.json" <<'JSON'
{
  "version": 1,
  "rules": [
    {"id":"declared","when":"Genuinely open-ended work","trigger":{"kind":"declaration"},"use":{"harness":"claude","model":"claude-opus-5-5","effort":"medium","provider":"anthropic"}},
    {"id":"projects","when":"Lucie work","trigger":{"kind":"projects","names":["lucie-one","lucie-two"]},"use":[{"harness":"agy","model":"gemini-3.8-flash-high","effort":"high","provider":"google"},{"harness":"agy","model":"claude-opus-4-6-thinking","effort":"high","provider":"anthropic"}]}
  ],
  "default_ladder":[
    {"id":"free-a","harness":"pi","model":"vendor/free-a","provider":"vendor","accounts":["alternate","primary"]},
    {"id":"paid","harness":"codex","model":"codex-model","provider":"codex"}
  ],
  "default":[{"harness":"codex","model":"codex-model","provider":"codex","accounts":["primary","alternate"]}]
}
JSON
cat > "$HOME_DIR/config/model-catalog.json" <<'JSON'
{"models":[{"id":"vendor/free-a","status":"retired","replaced_by":"vendor/free-b"}]}
JSON
catalog_before=$(cksum "$HOME_DIR/config/model-catalog.json")
mkdir -p "$HOME_DIR/state"
cat > "$HOME_DIR/state/live-worker.meta" <<'META'
harness=codex
model=codex-model
kind=ship
opencode_workspace=
dispatch_ladder=opencode
META
out=$(FM_HOME="$HOME_DIR" "$TOOL" --force 2>&1)
assert_equals 0 "$?" "migration should succeed: $out"
policy="$HOME_DIR/config/dispatch-policy.json"
assert_equals 2 "$(jq -r .version "$policy")" "the converted policy uses v2"
assert_equals codex-model "$(jq -r '.default_ladder[0].model' "$policy")" "on disagreement the resolver's default wins so routing is unchanged"
assert_equals primary "$(jq -r '.default_ladder[0].accounts[0]' "$policy")" "account order is preserved"
assert_equals claude-opus-4-6-thinking "$(jq -r '.rules[1].ladder[1].model' "$policy")" "rule ladder order is preserved"
assert_equals captain "$(jq -r '.constraints[] | select(.id == "worker-claude-approval") | .approval' "$policy")" "Anthropic worker approval is enforced"
assert_equals true "$(jq -r '.constraints[] | select(.id == "research-no-claude") | .deny' "$policy")" "research homes deny Anthropic"
assert_equals 0 "$(jq -r '.providers[] | select(.id == "vendor") | .refusal_patterns | length' "$policy")" "custom providers are represented without invented refusal wording"
assert_equals "$catalog_before" "$(cksum "$HOME_DIR/config/model-catalog.json")" "the existing model catalog is preserved byte-for-byte"
assert_equals 1 "$(sed -n 's/^dispatch_unified=//p' "$HOME_DIR/state/live-worker.meta")" "active legacy tasks enter generic descent"
assert_equals codex "$(sed -n 's/^dispatch_route_provider=//p' "$HOME_DIR/state/live-worker.meta")" "active legacy tasks retain provider identity"
assert_equals '' "$(sed -n 's/^dispatch_ladder=//p' "$HOME_DIR/state/live-worker.meta")" "the legacy ladder marker is removed"
pass "v1 migration retains routes and catalog, translates active tasks, and adds required constraints"

out=$(FM_HOME="$HOME_DIR" "$TOOL" 2>&1)
assert_equals 0 "$?" "re-running a v2 migration should succeed: $out"
assert_contains "$out" "already current" "the migration is idempotent"
pass "migration recognizes an already-current v2 policy"

jq '.default_ladder[0].account_strategy = "reset-whenever"' "$ROOT/docs/examples/dispatch-policy.json" > "$TMP_ROOT/invalid-policy.json"
validation_error=$("$ROOT/bin/fm-dispatch-policy-validate.sh" "$TMP_ROOT/invalid-policy.json" 2>&1)
validation_status=$?
assert_equals 1 "$validation_status" "unknown account strategies fail closed"
assert_contains "$validation_error" 'account_strategy must be ordered' "validator explains the allowed account strategy choices"
pass "unrecognized account-pick strategies are rejected before dispatch"

jq '.default_ladder[0].accounts[0] = "bad/workspace"' "$ROOT/docs/examples/dispatch-policy.json" > "$TMP_ROOT/invalid-account.json"
validation_error=$("$ROOT/bin/fm-dispatch-policy-validate.sh" "$TMP_ROOT/invalid-account.json" 2>&1)
validation_status=$?
assert_equals 1 "$validation_status" "unsafe account labels fail closed"
pass "account labels cannot escape their config-owned database namespace"

# The live fleet v1 shape (sanitized copy of a real home policy: `when`
# prose trimmed, routing fields byte-identical): provider-less rule rungs,
# a single-object `use`, a `default` array beside an id-carrying
# `default_ladder`, and a display-name `agy_ladder`. The migration must
# accept exactly this and resolve the same first rungs the live policy
# routes today.
REAL_HOME="$TMP_ROOT/real-home"
mkdir -p "$REAL_HOME/config" "$REAL_HOME/state" "$REAL_HOME/data"
cat > "$REAL_HOME/config/dispatch-policy.json" <<'JSON'
{
  "version": 1,
  "rules": [
    {"id":"work-is-genuinely-unshap-1","when":"Work that is genuinely unshaped.","trigger":{"kind":"declaration"},"use":{"harness":"claude","model":"claude-opus-5-5","effort":"medium"}},
    {"id":"work-belongs-to-a-lucie--2","when":"Work that belongs to a Lucie project.","trigger":{"kind":"projects","names":["business-finder","business-finder-lucie","foothills-site","forest-city-site","lucie-eval-system","lucie-geo-backend","lucie-geo-cta","lucie-lead-analysis","lucie-outreach","oakmont-site","starling-site","wfa-site"]},"use":[{"harness":"agy","model":"gemini-3.8-flash-high","effort":"high"},{"harness":"agy","model":"claude-opus-4-6-thinking","effort":"high"}]}
  ],
  "default":[{"harness":"codex","model":"gpt-6-luna","effort":"medium","provider":"codex"},{"harness":"opencode","model":"opencode-go/muse-spark-1.3-contributor","provider":"opencode-go"}],
  "default_ladder":[{"harness":"codex","model":"gpt-6-luna","effort":"medium","provider":"codex","id":"plus"},{"harness":"opencode","model":"opencode-go/muse-spark-1.3-contributor","provider":"opencode-go","id":"go"}],
  "agy_ladder":["Gemini 3.8 Flash (High)","Claude Opus 4.6 (Thinking)"]
}
JSON
printf '{"models":[]}\n' > "$REAL_HOME/config/model-catalog.json"
out=$(FM_HOME="$REAL_HOME" "$TOOL" --force 2>&1)
assert_equals 0 "$?" "the live fleet v1 shape must migrate: $out"
real_policy="$REAL_HOME/config/dispatch-policy.json"
assert_equals claude "$(jq -r '.rules[0].ladder[0].provider' "$real_policy")" "a provider-less claude rung inherits its single provider family"
assert_equals agy "$(jq -r '.rules[1].ladder[0].provider' "$real_policy")" "a provider-less agy rung inherits its single provider family"
assert_equals agy "$(jq -r '.rules[1].ladder[1].provider' "$real_policy")" "every provider-less rule rung is filled, not just the first"
assert_equals plus "$(jq -r '.default_ladder[0].id' "$real_policy")" "matching default/default_ladder sequences keep their curated rung ids"
assert_equals go "$(jq -r '.default_ladder[1].id' "$real_policy")" "the second curated rung id survives migration"
assert_equals 0 "$(jq -r 'keys | index("agy_ladder") // 0' "$real_policy")" "the display-name ladder leaves no v2 field behind"
mkdir -p "$TMP_ROOT/real-bin"
cat > "$TMP_ROOT/real-bin/quota-axi" <<'SH'
#!/usr/bin/env bash
printf '%s\n' '{"schemaVersion":5,"providers":[]}'
SH
chmod +x "$TMP_ROOT/real-bin/quota-axi"
printf '# Task\n## Captain'"'"'s intent\nDo the thing.\n## Firstmate spec\nDo it.\n' > "$TMP_ROOT/real-brief.md"
resolve() { PATH="$TMP_ROOT/real-bin:$PATH" FM_HOME="$REAL_HOME" "$ROOT/bin/fm-dispatch-resolve.sh" --json "$TMP_ROOT/real-brief.md" "$@" 2>/dev/null; }
assert_equals codex/gpt-6-luna "$(resolve | jq -r '"\(.profile.harness)/\(.profile.model)"')" "the migrated default starts where the live default starts"
assert_equals agy/gemini-3.8-flash-high "$(resolve --project starling-site | jq -r '"\(.profile.harness)/\(.profile.model)"')" "the migrated Lucie route starts on the live first Lucie rung"
assert_equals claude/claude-opus-5-5 "$(resolve --declare work-is-genuinely-unshap-1 --captain-approved | jq -r '"\(.profile.harness)/\(.profile.model)"')" "the migrated declaration route keeps its live worker behind captain approval"
pass "the live fleet v1 shape migrates without changing what it routes today"
