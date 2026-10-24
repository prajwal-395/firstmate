#!/usr/bin/env bash
# Scratch-home regression for Pi OpenCode Go models sourced from dispatch and catalog data.
set -u
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
TMP_ROOT=$(fm_test_tmproot fm-pi-opencode-go)
HOME_DIR="$TMP_ROOT/home"
AGENT_DIR="$TMP_ROOT/pi-agent"
mkdir -p "$HOME_DIR/config" "$AGENT_DIR"
cat > "$HOME_DIR/config/dispatch-policy.json" <<'JSON'
{"version":2,"rules":[],"default_ladder":[{"id":"test-go-rung","harness":"pi","model":"opencode-go/test-model","provider":"opencode-go"}],"constraints":[],"providers":[]}
JSON
cat > "$HOME_DIR/config/model-catalog.json" <<'JSON'
{"models":[{"id":"opencode-go/test-model","status":"active","pi":{"name":"Catalog Model","api":"openai-responses","reasoning":true,"input":["text","image"],"contextWindow":123456,"maxTokens":7890,"cost":{"input":0.4,"output":0.5,"cacheRead":0,"cacheWrite":0}}}]}
JSON
FM_HOME="$HOME_DIR" bash "$ROOT/bin/fm-pi-opencode-go.sh" install "$AGENT_DIR" || fail "Pi provider install failed"
jq -e '.providers["opencode-go"].models == [{"name":"Catalog Model","api":"openai-responses","reasoning":true,"input":["text","image"],"contextWindow":123456,"maxTokens":7890,"cost":{"input":0.4,"output":0.5,"cacheRead":0,"cacheWrite":0},"id":"test-model"}]' "$AGENT_DIR/models.json" >/dev/null || fail "Pi provider model did not match the selected dispatch rung's catalog entry"
pass "Pi OpenCode Go provider model is built from dispatch policy and catalog metadata"
