#!/usr/bin/env bash
# Behavior tests for the one-shot crew-dispatch.json migration helper.
# Every case runs against a scratch FM_HOME; no live home is ever read.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TOOL="$ROOT/bin/fm-migrate-dispatch-policy.sh"
TMP_ROOT=$(fm_test_tmproot fm-migrate-dispatch-policy)

write_legacy() {  # <home>: a representative legacy policy with every field kind.
  local home=$1
  mkdir -p "$home/config"
  cat > "$home/config/crew-dispatch.json" <<'JSON'
{
  "rules": [
    {"when": "genuinely unshaped work", "use": {"harness": "claude", "model": "claude-opus-5-5", "effort": "medium"}},
    {"when": "Lucie work", "projects": ["business-finder", "starling-site"], "use": [{"harness": "agy", "model": "gemini-3.8-flash-high", "effort": "high"}, {"harness": "agy", "model": "claude-opus-4-6-thinking", "effort": "high"}]}
  ],
  "default": [{"harness": "codex", "model": "gpt-6-luna", "effort": "medium", "provider": "codex"}, {"harness": "opencode", "model": "opencode-go/muse-spark-1.3-contributor", "provider": "opencode-go"}],
  "agy_ladder": ["Gemini 3.8 Flash (High)", "Claude Opus 4.6 (Thinking)"],
  "exhausted_ladder_fallback": {"use": {"harness": "opencode"}, "include_agy_ladder": true, "agy_effort": "high"},
  "_see": "prose lives elsewhere",
  "_default_note": "billing order"
}
JSON
  cp "$home/config/crew-dispatch.json" "$home/source-bytes.json"
}

run_helper() {  # <home> [args...]: runs the helper, prints stdout, returns its status.
  local home=$1
  shift
  FM_HOME="$home" "$TOOL" "$@"
}

home="$TMP_ROOT/full"
write_legacy "$home"
out=$(run_helper "$home" 2>&1)
status=$?
assert_equals 0 "$status" "migration should succeed: $out"
assert_contains "$out" "wrote: $home/config/dispatch-policy.json" "policy write was not reported"
assert_contains "$out" "dropped: exhausted_ladder_fallback" "fallback drop was not reported"
assert_contains "$out" "source untouched" "source safety was not reported"
cmp -s "$home/config/crew-dispatch.json" "$home/source-bytes.json" \
  || fail "the legacy source was modified"
assert_equals 1 "$(jq -r .version "$home/config/dispatch-policy.json")" "policy version must be 1"
assert_equals genuinely-unshaped-work-1 "$(jq -r '.rules[0].id' "$home/config/dispatch-policy.json")" "first rule id must slug the when text"
assert_equals declaration "$(jq -r '.rules[0].trigger.kind' "$home/config/dispatch-policy.json")" "a selector-less rule becomes a declaration"
assert_equals lucie-work-2 "$(jq -r '.rules[1].id' "$home/config/dispatch-policy.json")" "second rule id must be unique"
assert_equals projects "$(jq -r '.rules[1].trigger.kind' "$home/config/dispatch-policy.json")" "a projects rule keeps its names"
assert_equals starling-site "$(jq -r '.rules[1].trigger.names[1]' "$home/config/dispatch-policy.json")" "project names must survive"
assert_equals deepseek "$(jq -r '.default_ladder[0].id' "$home/config/dispatch-policy.json")" "DeepSeek leads the free-model ladder"
assert_equals opencode/deepseek-v4.1-flash-free "$(jq -r '.default_ladder[0].model' "$home/config/dispatch-policy.json")" "DeepSeek keeps its model id"
assert_equals glm "$(jq -r '.default_ladder[1].id' "$home/config/dispatch-policy.json")" "GLM follows DeepSeek"
assert_equals mimo "$(jq -r '.default_ladder[2].id' "$home/config/dispatch-policy.json")" "MiMo follows GLM"
assert_equals plus "$(jq -r '.default_ladder[3].id' "$home/config/dispatch-policy.json")" "Codex Plus follows the free models"
assert_equals go "$(jq -r '.default_ladder[4].id' "$home/config/dispatch-policy.json")" "the Go filling follows Plus"
assert_equals 2 "$(jq -r '.agy_ladder | length' "$home/config/dispatch-policy.json")" "agy_ladder must be carried over"
assert_equals null "$(jq -r '.exhausted_ladder_fallback // "null"' "$home/config/dispatch-policy.json")" "the retired fallback must not be carried over"
assert_equals null "$(jq -r '._see // "null"' "$home/config/dispatch-policy.json")" "prose fields must not be carried over"
assert_equals active "$(jq -r '.models[] | select(.id == "gpt-6-luna") | .status' "$home/config/model-catalog.json")" "rung models seed the catalog as active"
assert_equals active "$(jq -r '.models[] | select(.id == "opencode-go/muse-spark-1.3-contributor") | .status' "$home/config/model-catalog.json")" "Go rung models seed the catalog as active"
assert_equals active "$(jq -r '.models[] | select(.id == "opencode/deepseek-v4.1-flash-free") | .status' "$home/config/model-catalog.json")" "DeepSeek rung models seed the catalog as active"
assert_equals active "$(jq -r '.models[] | select(.id == "claude-opus-5-5") | .status' "$home/config/model-catalog.json")" "rule models seed the catalog as active"
pass "a legacy policy migrates to version 1 with triggers, ladder fillings, and a seeded catalog"

out=$(run_helper "$home" 2>&1)
status=$?
assert_equals 0 "$status" "re-running should succeed: $out"
assert_contains "$out" "already current: $home/config/dispatch-policy.json" "re-run must report the current policy"
assert_contains "$out" "already current: $home/config/model-catalog.json" "re-run must report the current catalog"
pass "re-running over an unchanged source is idempotent"

printf '%s\n' '{"hand":"edit"}' > "$home/config/dispatch-policy.json"
out=$(run_helper "$home" 2>&1)
assert_equals 1 "$?" "a hand edit must refuse without --force: $out"
assert_contains "$out" "--force" "the refusal must name the way through"
assert_equals edit "$(jq -r .hand "$home/config/dispatch-policy.json")" "the hand edit must survive the refusal"
out=$(run_helper "$home" --force 2>&1)
assert_equals 0 "$?" "re-running with --force should succeed: $out"
assert_contains "$out" "overwrote" "the forced overwrite must be reported"
assert_equals 1 "$(jq -r .version "$home/config/dispatch-policy.json")" "the forced run must restore version 1"
pass "a differing policy refuses without --force and overwrites with it"

retired_home="$TMP_ROOT/retired"
write_legacy "$retired_home"
cat > "$retired_home/config/model-catalog.json" <<'JSON'
{"models": [{"id": "gpt-6-luna", "status": "retired", "replaced_by": "gpt-6-luna", "last_verified": "2026-10-01", "source": "hand"}]}
JSON
out=$(run_helper "$retired_home" 2>&1)
assert_equals 0 "$?" "migration with a catalog should succeed: $out"
assert_equals retired "$(jq -r '.models[] | select(.id == "gpt-6-luna") | .status' "$retired_home/config/model-catalog.json")" "a retired model must never be un-retired"
assert_equals active "$(jq -r '.models[] | select(.id == "claude-opus-5-5") | .status' "$retired_home/config/model-catalog.json")" "unseen models are still added"
pass "the catalog merge never un-retires a model"

thin_home="$TMP_ROOT/thin"
mkdir -p "$thin_home/config"
printf '%s\n' '{"rules":[],"default":[{"harness":"codex","model":"gpt-6-luna","provider":"codex"}]}' > "$thin_home/config/crew-dispatch.json"
out=$(run_helper "$thin_home" 2>&1)
assert_equals 1 "$?" "a default without a Go filling must refuse: $out"
assert_contains "$out" "default_ladder" "the refusal must name the missing ladder"
[ ! -e "$thin_home/config/dispatch-policy.json" ] || fail "a refused migration must write no policy"
pass "a default that cannot declare both rungs refuses before writing"

dup_home="$TMP_ROOT/dup"
mkdir -p "$dup_home/config"
printf '%s\n' '{"rules":[{"when":"first unshaped","use":{"harness":"claude"}},{"when":"second unshaped","use":{"harness":"codex","model":"gpt-6-luna"}}],"default":[{"harness":"codex","model":"gpt-6-luna","provider":"codex"},{"harness":"opencode","model":"opencode-go/muse-spark-1.3-contributor","provider":"opencode-go"}]}' > "$dup_home/config/crew-dispatch.json"
out=$(run_helper "$dup_home" 2>&1)
assert_equals 1 "$?" "two declaration-shaped rules must refuse: $out"
assert_contains "$out" "declaration" "the refusal must name the conflict"
[ ! -e "$dup_home/config/dispatch-policy.json" ] || fail "a refused migration must write no policy"
pass "two declaration-shaped rules refuse with both named"

empty_home="$TMP_ROOT/empty"
mkdir -p "$empty_home/config"
out=$(run_helper "$empty_home" 2>&1)
assert_equals 1 "$?" "a home with no legacy policy must refuse: $out"
assert_contains "$out" "crew-dispatch.json" "the refusal must name the missing source"
pass "a home with no legacy policy refuses without writing"

printf '# all fm-migrate-dispatch-policy tests passed\n'
