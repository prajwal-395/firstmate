#!/usr/bin/env bash
# Exercise the managed Pi provider and its request-time OpenCode token path.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

HELPER="$ROOT/bin/fm-pi-opencode-go.sh"
TMP_ROOT=$(fm_test_tmproot fm-pi-opencode-go)
FAKEBIN="$TMP_ROOT/bin"
AGENT="$TMP_ROOT/shared-home/.pi/agent"
DATA="$TMP_ROOT/opencode-data"
CONFIG="$TMP_ROOT/config"
LIVE_DB="$DATA/opencode.db"
TASK_DB="$TMP_ROOT/task.db"
# The credential path resolves the machine's console login when its own
# lookup finds nothing, so every case runs against a scratch home and a
# scratch data directory. No fixture in this suite may reach a real path.
SCRATCH_HOME="$TMP_ROOT/scratch-home"
SCRATCH_XDG="$TMP_ROOT/scratch-xdg"
mkdir -p "$FAKEBIN" "$DATA" "$CONFIG" "$SCRATCH_HOME" "$SCRATCH_XDG/opencode"

cat > "$FAKEBIN/opencode" <<'SH'
#!/usr/bin/env bash
set -u
case "${1:-} ${2:-}" in
  'debug paths') printf '%s %s\n' "$1" "$2" >> "$FM_TEST_OPENCODE_CALLS"; printf 'data %s\n' "$FM_TEST_OPENCODE_DATA" ;;
  'debug config')
    printf '%s %s\n' "$1" "$2" >> "$FM_TEST_OPENCODE_CALLS"
    [ -n "${OPENCODE_DB:-}" ] || exit 1
    # A refresh never touches a database outside this suite's own scratch
    # tree, whatever the caller passed in.
    case "$OPENCODE_DB" in "$FM_TEST_SCRATCH_ROOT"/*) ;; *) exit 1 ;; esac
    [ -z "${FM_TEST_OPENCODE_DELAY:-}" ] || sleep "$FM_TEST_OPENCODE_DELAY"
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
CALLS="$TMP_ROOT/opencode-calls"
: > "$CALLS"
# The whole suite runs against a scratch home and a scratch data directory,
# and names the console database outright, so a credential lookup resolves
# inside this suite's own tree or resolves nothing at all.
export HOME="$SCRATCH_HOME"
export XDG_DATA_HOME="$SCRATCH_XDG"
export FM_TEST_SCRATCH_ROOT="$TMP_ROOT"
export FM_OPENCODE_LIVE_DB="$LIVE_DB"

mkdir -p "$AGENT"
cat > "$AGENT/models.json" <<'JSON'
{"$schema":"https://pi.dev/schemas/models.schema.json","providers":{"local":{"baseUrl":"http://localhost:11434/v1","api":"openai-completions","apiKey":"local","models":[{"id":"qwen"}]}}}
JSON
"$HELPER" install "$AGENT" || fail "provider installation failed"
jq -e --arg prefix '!bash "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/fm-opencode-go/' \
  '.providers.local.models[0].id == "qwen" and .providers["opencode-go"].baseUrl == "https://opencode.ai/inference/go/openai/v1" and .providers["opencode-go"].api == "openai-completions" and (.providers["opencode-go"].apiKey | startswith($prefix) and endswith("/fm-pi-opencode-go.sh\" token")) and .providers["opencode-go"].headers["x-opencode-org-id"] == "${FM_OPENCODE_ORG_ID}" and .providers["opencode-go"].headers["x-opencode-session"] == "${FM_OPENCODE_SESSION}" and ([.providers["opencode-go"].models[].id] == ["muse-spark-1.3-contributor"]) and .providers["opencode-go"].models[0].api == "openai-responses"' "$AGENT/models.json" >/dev/null \
  || fail "provider install did not preserve Pi settings and configure the Go model"
pass "Pi provider install preserves other providers and adds the OpenCode Go model with per-task headers"

# A provider file written before the free rung retired still lists the
# retired model; reinstalling drops it while keeping the Go model, and
# workers already running keep their loaded helper untouched.
RETIRED="$TMP_ROOT/retired"
mkdir -p "$RETIRED"
cat > "$RETIRED/models.json" <<'JSON'
{"providers":{"opencode-go":{"baseUrl":"https://opencode.ai/inference/go/openai/v1","api":"openai-completions","apiKey":"!bash /old/home/bin/fm-pi-opencode-go.sh token","models":[{"id":"minimax-m3.1-flash-free"},{"id":"muse-spark-1.3-contributor"}]}}}
JSON
"$HELPER" install "$RETIRED" || fail "provider reinstall over a retired model entry failed"
jq -e 'any(.providers["opencode-go"].models[]?; .id == "minimax-m3.1-flash-free") | not' "$RETIRED/models.json" >/dev/null \
  || fail "reinstall did not drop the retired free model from the provider"
jq -e 'any(.providers["opencode-go"].models[]?; .id == "muse-spark-1.3-contributor")' "$RETIRED/models.json" >/dev/null \
  || fail "reinstall dropped the Go model alongside the retired one"
pass "provider reinstall drops the retired free model and keeps the Go model"

# Migrate the exact managed form written by older code roots, then prove two
# distinct checkouts can safely converge the same shared Pi agent directory.
LEGACY="$TMP_ROOT/legacy"
mkdir -p "$LEGACY"
printf '%s\n' '{"providers":{"opencode-go":{"baseUrl":"https://opencode.ai/inference/go/openai/v1","api":"openai-completions","apiKey":"!bash /old/home/bin/fm-pi-opencode-go.sh token","models":[{"id":"captain-custom-model","name":"Custom Go model"}]}}}' > "$LEGACY/models.json"
"$HELPER" install "$LEGACY" || fail "legacy managed provider entry did not migrate"
jq -e --arg prefix '!bash "${PI_CODING_AGENT_DIR:-$HOME/.pi/agent}/fm-opencode-go/' \
  '(.providers["opencode-go"].apiKey | startswith($prefix) and endswith("/fm-pi-opencode-go.sh\" token"))' \
  "$LEGACY/models.json" >/dev/null \
  || fail "legacy code-root-bound entry was not migrated to the agent-dir-local helper"
jq -e 'any(.providers["opencode-go"].models[]?; .id == "captain-custom-model")' "$LEGACY/models.json" >/dev/null \
  || fail "installer dropped a user-defined OpenCode Go model"
OTHER_ROOT="$TMP_ROOT/other-code-root"
mkdir -p "$OTHER_ROOT/bin"
cp "$ROOT/bin/fm-pi-opencode-go.sh" "$OTHER_ROOT/bin/"
cp "$ROOT/bin/fm-opencode-second-lib.sh" "$OTHER_ROOT/bin/"
cp "$ROOT/bin/fm-backend.sh" "$OTHER_ROOT/bin/"
cp "$ROOT/bin/fm-timeout-lib.sh" "$OTHER_ROOT/bin/"
"$OTHER_ROOT/bin/fm-pi-opencode-go.sh" install "$LEGACY" || fail "a second code root could not install into the shared Pi directory"
pass "managed global provider entries migrate safely and converge across different code roots"

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
# The scratch data directory also holds the fallback login database, so a
# lookup that ignores the OpenCode CLI still lands inside this suite.
cp "$LIVE_DB" "$SCRATCH_XDG/opencode/opencode.db"

out=$(PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_OPENCODE_CALLS="$CALLS" FM_OPENCODE_TASK_DB="$TASK_DB" \
  FM_OPENCODE_ORG_ID=org_test_second FM_OPENCODE_CONFIG_DIR="$CONFIG" \
  "$HELPER" token) || fail "request-time token refresh failed"
[ "$out" = refreshed-test-token ] || fail "request-time helper did not return the refreshed credential"
[ "$(sqlite3 "$TASK_DB" 'SELECT token_expiry FROM account LIMIT 1;')" -gt "$(($(date +%s) * 1000))" ] \
  || fail "task database retained an expired credential"
[ "$(sqlite3 "$TASK_DB" 'SELECT active_org_id FROM account_state WHERE id=1;')" = org_test_second ] \
  || fail "task database was not pinned to its selected workspace"
pass "request-time auth uses OpenCode refresh, copies the fresh login to the task database, and pins its workspace"

# The home's pinned workspace databases carry the same console login, so a
# re-login reaches them too: a pin that keeps the old token reports a
# rejected provider to everything that reads it.
PIN_DB="$CONFIG/opencode-second.db"
printf '%s\n' org_test_second > "$CONFIG/opencode-second-org"
sqlite3 "$PIN_DB" <<'SQL'
CREATE TABLE account(id TEXT PRIMARY KEY,email TEXT NOT NULL,url TEXT NOT NULL,access_token TEXT NOT NULL,refresh_token TEXT NOT NULL,token_expiry INTEGER,time_created INTEGER NOT NULL,time_updated INTEGER NOT NULL);
CREATE TABLE account_state(id INTEGER PRIMARY KEY,active_account_id TEXT,active_org_id TEXT);
INSERT INTO account VALUES('acct','test@example.invalid','https://opencode.ai','stale-pinned-token','refresh-token',1,1,1);
INSERT INTO account_state VALUES(1,'acct','org_test_second');
SQL
sqlite3 "$LIVE_DB" "UPDATE account SET access_token='old-token', token_expiry=1;"
sqlite3 "$TASK_DB" "UPDATE account SET access_token='stale-task-token', token_expiry=1;"
out=$(PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_OPENCODE_CALLS="$CALLS" FM_OPENCODE_TASK_DB="$TASK_DB" \
  FM_OPENCODE_ORG_ID=org_test_second FM_OPENCODE_CONFIG_DIR="$CONFIG" \
  "$HELPER" token) || fail "the request-time refresh of the pinned databases failed"
[ "$(sqlite3 "$PIN_DB" 'SELECT access_token FROM account LIMIT 1;')" = refreshed-test-token ] \
  || fail "the pinned workspace database did not carry the live login after the refresh"
[ "$(sqlite3 "$PIN_DB" 'SELECT active_org_id FROM account_state WHERE id=1;')" = org_test_second ] \
  || fail "the pinned workspace database lost its workspace pin"
pass "the request-time refresh also refreshes the home's pinned workspace databases from the live login"

# The shared provider command must resolve the token helper from each worker's
# own Pi agent directory, without depending on a Firstmate code-root variable.
API_KEY_COMMAND=$(jq -r '.providers["opencode-go"].apiKey | ltrimstr("!")' "$AGENT/models.json")
HOME_A="$TMP_ROOT/home-a"
HOME_B="$TMP_ROOT/home-b"
CONFIG_A="$HOME_A/config"
CONFIG_B="$HOME_B/config"
TASK_DB_A="$HOME_A/task.db"
TASK_DB_B="$HOME_B/task.db"
mkdir -p "$HOME_A/.pi/agent" "$HOME_B/.pi/agent" "$CONFIG_A" "$CONFIG_B"
cp "$TASK_DB" "$TASK_DB_A"
cp "$TASK_DB" "$TASK_DB_B"
"$HELPER" install "$HOME_A/.pi/agent" || fail "home A could not install its Pi helper"
"$OTHER_ROOT/bin/fm-pi-opencode-go.sh" install "$HOME_B/.pi/agent" \
  || fail "home B with another code root could not install its Pi helper"
API_KEY_COMMAND_A=$(jq -r '.providers["opencode-go"].apiKey | ltrimstr("!")' "$HOME_A/.pi/agent/models.json")
API_KEY_COMMAND_B=$(jq -r '.providers["opencode-go"].apiKey | ltrimstr("!")' "$HOME_B/.pi/agent/models.json")
[ "$API_KEY_COMMAND_A" = "$API_KEY_COMMAND_B" ] \
  || fail "different code roots wrote incompatible commands into their Pi provider files"
for home in "$HOME_A" "$HOME_B"; do
  if [ "$home" = "$HOME_A" ]; then db=$TASK_DB_A; config=$CONFIG_A; command_text=$API_KEY_COMMAND_A
  else db=$TASK_DB_B; config=$CONFIG_B; command_text=$API_KEY_COMMAND_B
  fi
  out=$(PATH="$FAKEBIN:$PATH" HOME="$home" FM_OPENCODE_TASK_DB="$db" \
    FM_OPENCODE_ORG_ID=org_test_second FM_OPENCODE_CONFIG_DIR="$config" \
    env -u PI_CODING_AGENT_DIR -u FM_PI_OPENCODE_GO_HELPER bash -c "$command_text") \
    || fail "a Pi worker from '$home' could not resolve its local helper"
  [ "$out" = refreshed-test-token ] || fail "a Pi worker from '$home' returned the wrong task token"
done
pass "workers from two homes and code roots resolve the shared provider command with no launcher-only helper variable"

# Pi's apiKey command has a hard 10s timeout. Concurrent requests with a
# comfortably fresh task token must avoid both OpenCode and the shared lock.
: > "$CALLS"
started=$(date +%s)
pids=()
for i in $(seq 1 20); do
  (
    PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_OPENCODE_CALLS="$CALLS" \
      FM_OPENCODE_TASK_DB="$TASK_DB" FM_OPENCODE_ORG_ID=org_test_second \
      FM_OPENCODE_CONFIG_DIR="$CONFIG" "$HELPER" token > "$TMP_ROOT/token-$i" 2> "$TMP_ROOT/token-$i.err"
  ) &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || fail "a concurrent valid-token request failed"
done
elapsed=$(($(date +%s) - started))
[ "$elapsed" -lt 10 ] || fail "concurrent valid-token requests exceeded Pi's 10s budget (${elapsed}s)"
[ ! -s "$CALLS" ] || fail "concurrent valid-token requests invoked OpenCode"
for i in $(seq 1 20); do
  [ "$(cat "$TMP_ROOT/token-$i")" = refreshed-test-token ] || fail "concurrent request $i returned the wrong token"
done
pass "concurrent token calls with a valid token return within Pi's budget without invoking OpenCode"

# Below the fast-path margin, overlapping calls should still share one refresh.
sqlite3 "$TASK_DB" "UPDATE account SET access_token='near-expiry-token', token_expiry=$(( $(date +%s) * 1000 + 15000 ));"
: > "$CALLS"
pids=()
for i in $(seq 1 20); do
  (
    PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_OPENCODE_CALLS="$CALLS" \
      FM_TEST_OPENCODE_DELAY=0.25 FM_OPENCODE_TASK_DB="$TASK_DB" FM_OPENCODE_ORG_ID=org_test_second \
      FM_OPENCODE_CONFIG_DIR="$CONFIG" "$HELPER" token > "$TMP_ROOT/refresh-token-$i" 2> "$TMP_ROOT/refresh-token-$i.err"
  ) &
  pids+=("$!")
done
for pid in "${pids[@]}"; do
  wait "$pid" || fail "a concurrent near-expiry-token request failed"
done
[ "$(grep -c '^debug config$' "$CALLS")" -eq 1 ] || fail "overlapping near-expiry requests did not share one OpenCode refresh (expiry=$(sqlite3 "$TASK_DB" 'SELECT token_expiry FROM account LIMIT 1;') now=$(($(date +%s) * 1000)) calls: $(tr '\n' ';' < "$CALLS"))"
for i in $(seq 1 20); do
  [ "$(cat "$TMP_ROOT/refresh-token-$i")" = refreshed-test-token ] || fail "near-expiry request $i returned the wrong token"
done
pass "concurrent near-expiry token calls recheck under the lock and share one OpenCode refresh"

sqlite3 "$LIVE_DB" 'UPDATE account SET token_expiry=1;'
sqlite3 "$TASK_DB" 'UPDATE account SET token_expiry=1;'
out=$(PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_OPENCODE_CALLS="$CALLS" FM_TEST_NO_REFRESH=1 \
  FM_OPENCODE_TASK_DB="$TASK_DB" FM_OPENCODE_ORG_ID=org_test_second \
  FM_OPENCODE_CONFIG_DIR="$CONFIG" "$HELPER" token 2>/dev/null) && fail "expired console token was accepted"
[ -z "$out" ] || fail "expired console token was emitted"
pass "an expired token is refused when OpenCode cannot refresh it"

bad="$TMP_ROOT/conflicting"
mkdir -p "$bad"
printf '%s\n' '{"providers":{"opencode-go":{"baseUrl":"https://other.invalid/v1","api":"openai-completions","apiKey":"manual"}}}' > "$bad/models.json"
"$HELPER" install "$bad" >/dev/null 2>&1 && fail "conflicting custom provider was overwritten"
pass "a conflicting user-defined opencode-go provider is preserved by refusing the install"

# A busy machine is what produces the contention: many workers read and
# refresh the same task database at once, so the request path meets a locked
# task database. The token it already holds must still be served, inside Pi's
# budget, without starting OpenCode.
CONTENDED_DB="$TMP_ROOT/contended.db"
CONTENDED_LOG="$CONTENDED_DB.token-errors.log"
sqlite3 "$CONTENDED_DB" <<'SQL'
CREATE TABLE account(id TEXT PRIMARY KEY,email TEXT NOT NULL,url TEXT NOT NULL,access_token TEXT NOT NULL,refresh_token TEXT NOT NULL,token_expiry INTEGER,time_created INTEGER NOT NULL,time_updated INTEGER NOT NULL);
CREATE TABLE account_state(id INTEGER PRIMARY KEY,active_account_id TEXT,active_org_id TEXT);
INSERT INTO account VALUES('acct','test@example.invalid','https://opencode.ai','contended-valid-token','refresh-token',4102444800000,1,1);
INSERT INTO account_state VALUES(1,'acct','org_test_second');
SQL
: > "$CALLS"
{ printf 'BEGIN EXCLUSIVE;\n'; sleep 2; printf 'COMMIT;\n'; } | sqlite3 "$CONTENDED_DB" >/dev/null 2>&1 &
lock_holder=$!
sleep 0.3
sqlite3 "$CONTENDED_DB" 'SELECT 1 FROM account LIMIT 1;' >/dev/null 2>&1 \
  && fail "the contended task database was not actually locked, so the case proved nothing"
started=$(date +%s)
out=$(PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_OPENCODE_CALLS="$CALLS" \
  FM_OPENCODE_TASK_DB="$CONTENDED_DB" FM_OPENCODE_ORG_ID=org_test_second \
  FM_OPENCODE_CONFIG_DIR="$CONFIG" "$HELPER" token 2>/dev/null) \
  || fail "a request against a contended task database failed while a valid token was present"
wait "$lock_holder"
elapsed=$(($(date +%s) - started))
[ "$out" = contended-valid-token ] || fail "a contended request returned the wrong token"
[ "$elapsed" -lt 10 ] || fail "a contended request exceeded Pi's 10s budget (${elapsed}s)"
[ ! -s "$CALLS" ] || fail "a contended request started OpenCode instead of answering from the task database"
[ ! -e "$CONTENDED_LOG" ] || fail "a served request wrote a failure record"
pass "a valid token is served through task-database contention inside Pi's budget without OpenCode"

# The fast path must not depend on the shared libraries: a token that is
# already in the task database is answered before anything is sourced.
INSTALLED_HELPER=$(find "$AGENT/fm-opencode-go" -name fm-pi-opencode-go.sh -type f | head -n 1)
[ -n "$INSTALLED_HELPER" ] || fail "the installed Pi helper was not found"
NOLIB_DIR="$TMP_ROOT/helper-without-libraries"
cp -R "$(dirname "$INSTALLED_HELPER")" "$NOLIB_DIR"
rm -f "$NOLIB_DIR/fm-opencode-second-lib.sh" "$NOLIB_DIR/fm-timeout-lib.sh" "$NOLIB_DIR/fm-backend.sh"
out=$(PATH="$FAKEBIN:$PATH" FM_TEST_OPENCODE_DATA="$DATA" FM_TEST_OPENCODE_CALLS="$CALLS" \
  FM_OPENCODE_TASK_DB="$CONTENDED_DB" FM_OPENCODE_ORG_ID=org_test_second \
  FM_OPENCODE_CONFIG_DIR="$CONFIG" "$NOLIB_DIR/fm-pi-opencode-go.sh" token 2>/dev/null) \
  || fail "the request path needed the shared libraries to answer a valid token"
[ "$out" = contended-valid-token ] || fail "the library-free request path returned the wrong token"
pass "a valid token is answered before any shared library is sourced"

# Pi discards the command's stderr, so a failure that leaves no record is a
# failure nobody can diagnose. The record sits beside the task's state and
# never carries the token.
LOGGED_DB="$TMP_ROOT/logged.db"
sqlite3 "$LOGGED_DB" <<'SQL'
CREATE TABLE account(id TEXT PRIMARY KEY,email TEXT NOT NULL,url TEXT NOT NULL,access_token TEXT NOT NULL,refresh_token TEXT NOT NULL,token_expiry INTEGER,time_created INTEGER NOT NULL,time_updated INTEGER NOT NULL);
CREATE TABLE account_state(id INTEGER PRIMARY KEY,active_account_id TEXT,active_org_id TEXT);
INSERT INTO account VALUES('acct','test@example.invalid','https://opencode.ai','SUPER-SECRET-TOKEN','refresh-token',1,1,1);
INSERT INTO account_state VALUES(1,'acct','org_test_second');
SQL
out=$(PATH="/usr/bin:/bin" FM_OPENCODE_TASK_DB="$LOGGED_DB" FM_OPENCODE_ORG_ID=org_test_second \
  FM_OPENCODE_CONFIG_DIR="$CONFIG" "$HELPER" token 2>/dev/null) \
  && fail "an expired token was accepted without the OpenCode CLI"
[ -z "$out" ] || fail "an expired token was emitted"
LOGGED_LOG="$LOGGED_DB.token-errors.log"
[ -f "$LOGGED_LOG" ] || fail "a failed request left no cause beside the task's state"
grep -q 'step=' "$LOGGED_LOG" || fail "the recorded cause names no failing step"
grep -q 'elapsed_ms=' "$LOGGED_LOG" || fail "the recorded cause carries no elapsed time"
grep -q "$LOGGED_DB" "$LOGGED_LOG" || fail "the recorded cause does not name the task database"
grep -q 'SUPER-SECRET-TOKEN' "$LOGGED_LOG" && fail "the recorded cause leaked the token"
pass "a failed request records its cause beside the task's state and never the token"

# A named console database is taken as it stands. When it is missing, the
# lookup refuses instead of falling back to the machine's own data
# directory, so a scratch home can never reach the account of the person
# running the machine. The scratch data directory does hold a usable login,
# so a fallback here would succeed and hide the defect.
ABSENT_DB="$TMP_ROOT/absent-live.db"
ABSENT_TASK_DB="$TMP_ROOT/absent-task.db"
sqlite3 "$ABSENT_TASK_DB" <<'SQL'
CREATE TABLE account(id TEXT PRIMARY KEY,email TEXT NOT NULL,url TEXT NOT NULL,access_token TEXT NOT NULL,refresh_token TEXT NOT NULL,token_expiry INTEGER,time_created INTEGER NOT NULL,time_updated INTEGER NOT NULL);
CREATE TABLE account_state(id INTEGER PRIMARY KEY,active_account_id TEXT,active_org_id TEXT);
INSERT INTO account VALUES('acct','test@example.invalid','https://opencode.ai','expired-token','refresh-token',1,1,1);
INSERT INTO account_state VALUES(1,'acct','org_test_second');
SQL
out=$(PATH="$FAKEBIN:$PATH" FM_OPENCODE_LIVE_DB="$ABSENT_DB" \
  FM_OPENCODE_TASK_DB="$ABSENT_TASK_DB" FM_OPENCODE_ORG_ID=org_test_second \
  FM_OPENCODE_CONFIG_DIR="$CONFIG" "$HELPER" token 2>/dev/null) \
  && fail "a named console database that is absent was replaced by another one"
[ -z "$out" ] || fail "a token was emitted without a console database to refresh it"
ABSENT_LOG="$ABSENT_TASK_DB.token-errors.log"
[ -f "$ABSENT_LOG" ] || fail "the refused console database left no cause behind"
grep -q 'step=live-db-unavailable' "$ABSENT_LOG" \
  || fail "the recorded cause does not name the absent console database"
pass "a named console database that is absent is refused rather than replaced by the machine's own"

printf '%s\n' 'ok - Pi OpenCode Go provider and credential flow'
