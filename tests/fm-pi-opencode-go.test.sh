#!/usr/bin/env bash
# Exercise the managed Pi provider and its request-time OpenCode token path.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HELPER="$ROOT/bin/fm-pi-opencode-go.sh"
TMP_ROOT=$(fm_test_tmproot fm-pi-opencode-go)
FAKEBIN="$TMP_ROOT/bin"
AGENT="$TMP_ROOT/pi-agent"
DATA="$TMP_ROOT/opencode-data"
CONFIG="$TMP_ROOT/config"
LIVE_DB="$DATA/opencode.db"
TASK_DB="$TMP_ROOT/task.db"
mkdir -p "$FAKEBIN" "$DATA" "$CONFIG"

cat > "$FAKEBIN/opencode" <<'SH'
#!/usr/bin/env bash
set -u
case "${1:-} ${2:-}" in
  'debug paths') printf 'data %s\n' "$FM_TEST_OPENCODE_DATA" ;;
  'debug config')
    [ -n "${OPENCODE_DB:-}" ] || exit 1
    if [ "${FM_TEST_NO_REFRESH:-0}" != 1 ]; then
      sqlite3 "$OPENCODE_DB" "UPDATE account SET access_token='refreshed-test-token', token_expiry=$(( $(date +%s) * 1000 + 600000 ));"
    fi
    ;;
  *) exit 2 ;;
esac
SH
chmod +x "$FAKEBIN/opencode"

cat > "$FAKEBIN/sqlite3" <<'SH'
#!/usr/bin/env bash
exec /usr/bin/sqlite3 "$@"
SH
chmod +x "$FAKEBIN/sqlite3"

mkdir -p "$AGENT"
cat > "$AGENT/models.json" <<'JSON'
{"$schema":"https://pi.dev/schemas/models.schema.json","providers":{"local":{"baseUrl":"http://localhost:11434/v1","api":"openai-completions","apiKey":"local","models":[{"id":"qwen"}]}}}
JSON
"$HELPER" install "$AGENT" || fail "provider installation failed"
jq -e '.providers.local.models[0].id == "qwen" and .providers["opencode-go"].baseUrl == "https://opencode.ai/inference/go/openai/v1" and .providers["opencode-go"].api == "openai-completions" and (.providers["opencode-go"].apiKey | startswith("!bash ")) and .providers["opencode-go"].headers["x-opencode-org-id"] == "${FM_OPENCODE_ORG_ID}" and .providers["opencode-go"].headers["x-opencode-session"] == "${FM_OPENCODE_SESSION}" and ([.providers["opencode-go"].models[].id] == ["minimax-m3.1-flash-free", "muse-spark-1.3-contributor"]) and .providers["opencode-go"].models[1].api == "openai-responses"' "$AGENT/models.json" >/dev/null \
  || fail "provider install did not preserve Pi settings and configure both ladder models"
pass "Pi provider install preserves other providers and adds both OpenCode Go models with per-task headers"

sqlite3 "$LIVE_DB" <<'SQL'
CREATE TABLE account(id TEXT PRIMARY KEY,email TEXT NOT NULL,url TEXT NOT NULL,access_token TEXT NOT NULL,refresh_token TEXT NOT NULL,token_expiry INTEGER,time_created INTEGER NOT NULL,time_updated INTEGER NOT NULL);
CREATE TABLE account_state(id INTEGER PRIMARY KEY,active_account_id TEXT,active_org_id TEXT);
INSERT INTO account VALUES('acct','test@example.invalid','https://opencode.ai','old-token','refresh-token',1,1,1);
INSERT INTO account_state VALUES(1,'acct','old-org');
SQL
sqlite3 "$TASK_DB" <<'SQL'
CREATE TABLE account(id TEXT PRIMARY KEY,email TEXT NOT NULL,url TEXT NOT NULL,access_token TEXT NOT NULL,refresh_token TEXT NOT NULL,token_expiry INTEGER,time_created INTEGER NOT NULL,time_updated INTEGER NOT NULL);
CREATE TABLE account_state(id INTEGER PRIMARY KEY,active_account_id TEXT,active_org_id TEXT);
SQL

out=$(PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_OPENCODE_TASK_DB="$TASK_DB" \
  FM_OPENCODE_ORG_ID=org_test_second FM_OPENCODE_CONFIG_DIR="$CONFIG" \
  "$HELPER" token) || fail "request-time token refresh failed"
[ "$out" = refreshed-test-token ] || fail "request-time helper did not return the refreshed credential"
[ "$(sqlite3 "$TASK_DB" 'SELECT token_expiry FROM account LIMIT 1;')" -gt "$(($(date +%s) * 1000))" ] \
  || fail "task database retained an expired credential"
[ "$(sqlite3 "$TASK_DB" 'SELECT active_org_id FROM account_state WHERE id=1;')" = org_test_second ] \
  || fail "task database was not pinned to its selected workspace"
pass "request-time auth uses OpenCode refresh, copies the fresh login to the task database, and pins its workspace"

sqlite3 "$LIVE_DB" 'UPDATE account SET token_expiry=1;'
sqlite3 "$TASK_DB" 'UPDATE account SET token_expiry=1;'
out=$(PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_NO_REFRESH=1 \
  FM_OPENCODE_TASK_DB="$TASK_DB" FM_OPENCODE_ORG_ID=org_test_second \
  FM_OPENCODE_CONFIG_DIR="$CONFIG" "$HELPER" token 2>/dev/null) && fail "expired console token was accepted"
[ -z "$out" ] || fail "expired console token was emitted"
pass "an expired token is refused when OpenCode cannot refresh it"

bad="$TMP_ROOT/conflicting"
mkdir -p "$bad"
printf '%s\n' '{"providers":{"opencode-go":{"baseUrl":"https://other.invalid/v1","api":"openai-completions","apiKey":"manual"}}}' > "$bad/models.json"
"$HELPER" install "$bad" >/dev/null 2>&1 && fail "conflicting custom provider was overwritten"
pass "a conflicting user-defined opencode-go provider is preserved by refusing the install"

printf '%s\n' 'ok - Pi OpenCode Go provider and credential flow'
