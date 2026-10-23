#!/usr/bin/env bash
# Scratch-home tests for model retirement refusal and catalog refresh.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TMP_ROOT=$(fm_test_tmproot fm-model-catalog)
HOME_DIR="$TMP_ROOT/home"
FAKEBIN="$TMP_ROOT/bin"
mkdir -p "$HOME_DIR/config" "$FAKEBIN"
cat > "$FAKEBIN/opencode" <<'SH'
#!/usr/bin/env bash
[ "${1:-}" = models ] || exit 2
printf '%s\n' 'opencode/deepseek-v4.1-flash-free' 'opencode/glm-5.3-flash-free' 'opencode/mimo-v2.6-flash-free' 'opencode-go/muse-spark-1.3-contributor' 'codex/gpt-6-luna'
SH
cat > "$FAKEBIN/codex" <<'SH'
#!/usr/bin/env bash
[ "${1:-}" = debug ] && [ "${2:-}" = models ] || exit 2
printf '%s\n' '{"models":[{"slug":"gpt-6-luna"}]}'
SH
chmod +x "$FAKEBIN/opencode"
chmod +x "$FAKEBIN/codex"
printf '%s\n' '{"models":[{"id":"opencode-go/minimax-m3.1-flash-free","status":"active","replaced_by":"opencode-go/muse-spark-1.3-contributor"}]}' > "$HOME_DIR/config/model-catalog.json"
PATH="$FAKEBIN:$PATH" FM_HOME="$HOME_DIR" "$ROOT/bin/fm-model-catalog-refresh.sh" >/dev/null || fail "catalog refresh failed"
jq -e '.models[] | select(.id=="opencode-go/muse-spark-1.3-contributor" and .status=="active")' "$HOME_DIR/config/model-catalog.json" >/dev/null || fail "listed model was not made active"
jq -e '.models[] | select(.id=="gpt-6-luna" and .status=="active" and .source=="codex debug models")' "$HOME_DIR/config/model-catalog.json" >/dev/null || fail "Codex model was not cataloged"
jq -e '.models[] | select(.id=="gpt-6-luna" and .harnesses==["codex"])' "$HOME_DIR/config/model-catalog.json" >/dev/null || fail "Codex harness capability was not cataloged"
jq -e '([.models[] | select(.id == "opencode/deepseek-v4.1-flash-free" or .id == "opencode/glm-5.3-flash-free" or .id == "opencode/mimo-v2.6-flash-free")] | length == 3 and all(.[]; .status == "active" and .bills == "none" and .source == "opencode models"))' "$HOME_DIR/config/model-catalog.json" >/dev/null || fail "free models were not cataloged as active OpenCode models"
jq -e 'all(.models[] | select(.id | startswith("opencode/")); .harnesses==["opencode"])' "$HOME_DIR/config/model-catalog.json" >/dev/null || fail "OpenCode harness capability was not cataloged"
jq -e '.models[] | select(.id=="opencode-go/minimax-m3.1-flash-free" and .status=="retired")' "$HOME_DIR/config/model-catalog.json" >/dev/null || fail "missing model was not retired"
jq -e '.models[] | select(.id=="opencode-go/minimax-m3.1-flash-free" and .replaced_by=="opencode-go/muse-spark-1.3-contributor")' "$HOME_DIR/config/model-catalog.json" >/dev/null || fail "replacement was not preserved"

. "$ROOT/bin/fm-model-catalog-lib.sh"
out=$(fm_model_catalog_check opencode-go/minimax-m3.1-flash-free "$HOME_DIR/config" 2>&1) && fail "retired model unexpectedly passed"
assert_contains "$out" 'replacement: opencode-go/muse-spark-1.3-contributor' "retired model refusal omitted replacement"
fm_model_catalog_check opencode-go/muse-spark-1.3-contributor "$HOME_DIR/config" || fail "active model was refused"
printf '%s\n' '{"models":[{"id":"vendor/model","status":"active","harnesses":["",2]}]}' > "$HOME_DIR/config/model-catalog.json"
out=$(fm_model_catalog_check vendor/model "$HOME_DIR/config" 2>&1) && fail "malformed harness capability list unexpectedly passed"
assert_contains "$out" 'malformed model catalog' "malformed harness capability data should be rejected"
pass "catalog refresh retires missing ids and runtime guard names replacements"
