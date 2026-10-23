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
  "default":[{"harness":"codex","model":"codex-model","provider":"codex"}]
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
assert_equals vendor/free-a "$(jq -r '.default_ladder[0].model' "$policy")" "the first default rung is preserved"
assert_equals alternate "$(jq -r '.default_ladder[0].accounts[0]' "$policy")" "account order is preserved"
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
