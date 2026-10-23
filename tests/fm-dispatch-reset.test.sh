#!/usr/bin/env bash
# Behavior tests for provider/model/workspace exhaustion keying and expiry.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TOOL="$ROOT/bin/fm-dispatch-reset.sh"
TMP_ROOT=$(fm_test_tmproot fm-dispatch-reset)
mkdir -p "$TMP_ROOT/state"
now_ms=$(( $(date +%s) * 1000 ))
reset_ms=$((now_ms + 60000))

"$TOOL" record "$TMP_ROOT/state" new-provider vendor/model-x acct-west "$reset_ms" 'limit; resets tomorrow'
out=$("$TOOL" check "$TMP_ROOT/state" new-provider vendor/model-x acct-west)
assert_contains "$out" "reset_ms=$reset_ms" "the tuple should have an active reset"
assert_equals 1 "$("$TOOL" check "$TMP_ROOT/state" new-provider vendor/model-x acct-east >/dev/null 2>&1; echo $?)" "a different account must remain available"
assert_equals 1 "$("$TOOL" check "$TMP_ROOT/state" other-provider vendor/model-x acct-west >/dev/null 2>&1; echo $?)" "a different provider must remain available"
pass "exhaustion is keyed by provider, model, and configured account value"

"$TOOL" record "$TMP_ROOT/state" opencode deepseek-v4.1-flash-free secondary "$reset_ms" provider-refusal
out=$("$TOOL" check "$TMP_ROOT/state" opencode opencode/deepseek-v4.1-flash-free secondary)
assert_contains "$out" "reset_ms=$reset_ms" "the metadata model id finds a refusal recorded with OpenCode's bare model id"
pass "OpenCode refusal lookup accepts the provider-qualified metadata model id"

"$TOOL" clear "$TMP_ROOT/state" new-provider vendor/model-x acct-west
assert_equals 1 "$("$TOOL" check "$TMP_ROOT/state" new-provider vendor/model-x acct-west >/dev/null 2>&1; echo $?)" "clearing one tuple must retire its active reset"
pass "clearing one provider/model/account tuple leaves no active exhaustion"

printf '%s\n' '{"version":1,"provider":"p","model":"m","workspace":"w","reset_ms":"bad","observed_ms":1}' > "$TMP_ROOT/state/dispatch-exhaustions.jsonl"
assert_equals 1 "$("$TOOL" check "$TMP_ROOT/state" p m w >/dev/null 2>&1; echo $?)" "malformed records must fail closed"
pass "malformed exhaustion state is rejected"

cat > "$TMP_ROOT/policy.json" <<'JSON'
{"version":2,"rules":[],"default_ladder":[],"providers":[{"id":"new-provider","refusal_patterns":[{"pattern":"budget exceeded","reset":"provider-reset-time-or-quota-axi"},{"pattern":"free usage exceeded","reset":"retry-after-iso8601-or-next-utc-day"},{"pattern":"usage limit at","reset":"retry-after-iso8601"}]}]}
JSON
mkdir -p "$TMP_ROOT/observe-state"
out=$("$TOOL" observe "$TMP_ROOT/observe-state" "$TMP_ROOT/policy.json" new-provider vendor/model-y acct-west "$reset_ms" 'Budget exceeded; resume after reset; secret=must-not-persist')
assert_equals 0 "$?" "a configured provider refusal should be stored: $out"
assert_equals provider-refusal "$(jq -r 'select(.model == "vendor/model-y").evidence' "$TMP_ROOT/observe-state/dispatch-exhaustions.jsonl")" "only a typed summary is retained"
assert_not_contains "$(cat "$TMP_ROOT/observe-state/dispatch-exhaustions.jsonl")" 'secret=must-not-persist' "provider refusal text must not be copied into the home state"
"$TOOL" record "$TMP_ROOT/observe-state" new-provider vendor/model-y acct-west "$reset_ms" duplicate
assert_equals 1 "$(jq -s 'length' "$TMP_ROOT/observe-state/dispatch-exhaustions.jsonl")" "repeated observations replace the tuple instead of growing the store"
assert_equals 1 "$("$TOOL" observe "$TMP_ROOT/observe-state" "$TMP_ROOT/policy.json" new-provider vendor/model-z acct-west "$reset_ms" 'Rate limit; retry soon' >/dev/null 2>&1; echo $?)" "unconfigured refusal wording must not be recorded"
pass "provider refusal wording is read from the dispatch policy"
printf '%s\n' 'Free usage exceeded; keep this evidence local.' > "$TMP_ROOT/refusal.txt"
"$TOOL" observe-file "$TMP_ROOT/observe-state" "$TMP_ROOT/policy.json" new-provider vendor/model-file acct-west - "$TMP_ROOT/refusal.txt"
file_reset=$($TOOL check "$TMP_ROOT/observe-state" new-provider vendor/model-file acct-west)
assert_contains "$file_reset" 'evidence=provider-refusal' "file-based refusal evidence is summarized without copying its contents"
pass "provider refusal files use configured wording and reset strategy without forwarding pane text"
provider_reset_ms=$(( ($(date +%s) + 7200) * 1000 ))
provider_reset_iso=$(date -u -r "$((provider_reset_ms / 1000))" '+%Y-%m-%dT%H:%M:%SZ')
printf 'Usage limit at %s\n' "$provider_reset_iso" > "$TMP_ROOT/provider-reset.txt"
"$TOOL" observe-file "$TMP_ROOT/observe-state" "$TMP_ROOT/policy.json" new-provider vendor/model-exact acct-west - "$TMP_ROOT/provider-reset.txt"
assert_equals "$provider_reset_ms" "$("$TOOL" check "$TMP_ROOT/observe-state" new-provider vendor/model-exact acct-west | sed -n 's/.*reset_ms=\([0-9]*\).*/\1/p')" "provider timestamps take precedence over the fallback reset strategy"
pass "provider refusal timestamps populate the generic reset store when configured"
observed_reset=$($TOOL observe "$TMP_ROOT/observe-state" "$TMP_ROOT/policy.json" new-provider vendor/model-observed acct-west - "Usage limit at $provider_reset_iso")
assert_equals 0 "$?" "text observations with a configured reset timestamp should be stored: $observed_reset"
assert_equals "$provider_reset_ms" "$("$TOOL" check "$TMP_ROOT/observe-state" new-provider vendor/model-observed acct-west | sed -n 's/.*reset_ms=\([0-9]*\).*/\1/p')" "observe should parse its reset timestamp before deleting its temporary evidence"
pass "provider reset extraction works for in-memory refusal observations"

cat > "$TMP_ROOT/quota-policy.json" <<'JSON'
{"version":2,"rules":[],"default_ladder":[{"id":"arbitrary","harness":"pi","model":"vendor/quota-model","provider":"new-provider","accounts":["acct-one","acct-two"]}]}
JSON
future_iso=$(date -u -r "$(( $(date +%s) + 3600 ))" '+%Y-%m-%dT%H:%M:%SZ')
cat > "$TMP_ROOT/quota.json" <<JSON
{"schemaVersion":6,"providers":[
  {"provider":"new-provider","accountKey":"acct-one","quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":0,"resetsAt":"$future_iso"}]}},
  {"provider":"new-provider","accountKey":"acct-two","quotaSemantics":{"effectiveAvailability":[{"scope":"all_models","status":"known","effectivePercentRemaining":90,"resetsAt":"$future_iso"}]}}
]}
JSON
"$TOOL" sync-quota "$TMP_ROOT/observe-state" "$TMP_ROOT/quota-policy.json" "$TMP_ROOT/quota.json"
out=$("$TOOL" check "$TMP_ROOT/observe-state" new-provider vendor/quota-model acct-one)
assert_contains "$out" "evidence=quota-axi" "quota-axi reset evidence is recorded"
assert_equals 1 "$("$TOOL" check "$TMP_ROOT/observe-state" new-provider vendor/quota-model acct-two >/dev/null 2>&1; echo $?)" "positive quota remains eligible"
pass "quota-axi exhaustion feeds the generic provider/model/account reset store"
