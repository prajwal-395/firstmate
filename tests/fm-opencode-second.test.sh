#!/usr/bin/env bash
# Behavior tests for bin/fm-opencode-second-lib.sh - the second OpenCode Go
# workspace pin.
#
# The load-bearing contracts:
#   1. The workspace id is read from gitignored config/opencode-second-org
#      only; absence reads as unconfigured (the three-rung ladder), while a
#      malformed file is refused rather than guessed at.
#   2. A lane's workspace comes from its task meta marker
#      (opencode_workspace=secondary); anything else is the main workspace,
#      which is also what every record written before markers existed reads
#      as.
#   3. The pinned database is built once (migrated through opencode itself,
#      seeded from the live login, pinned to the configured workspace), an
#      existing database whose pin drifted is healed back, and the login
#      rows are re-copied from the live database on every pinned launch so
#      the pin never carries a stale token.
#   4. Before a launch rides the pin, the pinned config must resolve the
#      opencode provider to the secondary workspace; anything else refuses
#      the launch, so a mis-pinned session can never bill the wrong
#      workspace.
# Contracts 3 and 4 run against a fixture opencode on PATH (canned
# `debug paths` and `debug config`) plus a fixture live database, so they
# prove the mechanics without touching this machine's login.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"
# shellcheck source=bin/fm-opencode-second-lib.sh
. "$ROOT/bin/fm-opencode-second-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-opencode-second)
mkdir -p "$TMP_ROOT"
TMP_ROOT=$(cd "$TMP_ROOT" && pwd)
trap 'rm -rf "$TMP_ROOT"' EXIT

ORG_ID='org_01FIXTURESECONDARY00'

empty_config() {  # <name> -> config dir with no second workspace
  local dir="$TMP_ROOT/cfg-$1"
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s\n' "$dir"
}

second_config() {  # <name> [org-id] -> config dir with a second workspace
  local dir="$TMP_ROOT/cfg-$1" org=${2:-$ORG_ID}
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s' "$org" > "$dir/opencode-second-org"
  printf '%s\n' "$dir"
}

test_absent_config_is_unconfigured() {
  local cfg
  cfg=$(empty_config absent)
  fm_opencode_second_configured "$cfg" && fail "an absent org file must read as unconfigured"
  fm_opencode_second_org "$cfg" >/dev/null 2>&1 && fail "an absent org file must not yield an id"
  pass "an absent org file reads as unconfigured"
}

test_org_id_round_trips() {
  local cfg got
  cfg=$(second_config roundtrip)
  got=$(fm_opencode_second_org "$cfg") || fail "a well-formed org file must yield its id"
  [ "$got" = "$ORG_ID" ] || fail "org id mismatch: got '$got'"
  fm_opencode_second_configured "$cfg" || fail "a well-formed org file must read as configured"
  pass "a well-formed org file yields its workspace id"
}

test_malformed_org_is_refused() {
  local cfg case_name
  for case_name in empty spaces token slash newline toolong; do
    cfg=$(empty_config "bad-$case_name")
    case "$case_name" in
      empty) : > "$cfg/opencode-second-org" ;;
      spaces) printf '   \n' > "$cfg/opencode-second-org" ;;
      token) printf 'not an id!' > "$cfg/opencode-second-org" ;;
      slash) printf 'org_01/abc' > "$cfg/opencode-second-org" ;;
      newline) printf 'org_01abc\norg_01def\n' > "$cfg/opencode-second-org" ;;
      toolong) printf 'org_%s' "$(head -c 130 /dev/zero | tr '\0' 'a')" > "$cfg/opencode-second-org" ;;
    esac
    fm_opencode_second_org "$cfg" >/dev/null 2>&1 && fail "malformed org file ($case_name) must be refused"
    fm_opencode_second_configured "$cfg" && fail "malformed org file ($case_name) must read as unconfigured"
  done
  pass "a malformed org file is refused rather than guessed at"
}

test_lane_workspace_defaults_to_main() {
  local state="$TMP_ROOT/state-ws-default"
  mkdir -p "$state"
  printf 'harness=opencode\nmodel=opencode-go/muse-spark-1.3-contributor\nkind=scout\n' > "$state/lane1.meta"
  [ "$(fm_opencode_lane_workspace "$state" lane1)" = main ] \
    || fail "a lane with no marker must read as the main workspace"
  [ "$(fm_opencode_lane_workspace "$state" missing)" = main ] \
    || fail "a lane with no meta must read as the main workspace"
  pass "lanes without the marker read as the main workspace"
}

test_lane_workspace_marker_selects_second() {
  local state="$TMP_ROOT/state-ws-second"
  mkdir -p "$state"
  printf 'harness=opencode\nmodel=opencode-go/muse-spark-1.3-contributor\nkind=scout\nopencode_workspace=secondary\n' > "$state/lane1.meta"
  [ "$(fm_opencode_lane_workspace "$state" lane1)" = secondary ] \
    || fail "a lane with the marker must read as the second workspace"
  printf 'harness=opencode\nmodel=opencode-go/muse-spark-1.3-contributor\nkind=scout\nopencode_workspace=bogus\n' > "$state/lane2.meta"
  [ "$(fm_opencode_lane_workspace "$state" lane2)" = main ] \
    || fail "a lane with a bogus marker must read as the main workspace"
  pass "the marker selects the second workspace and nothing else does"
}

test_db_build_refuses_when_unconfigured() {
  local cfg
  cfg=$(empty_config noprefix)
  fm_opencode_second_db "$cfg" >/dev/null 2>&1 \
    && fail "the pin build must fail with no second workspace"
  pass "the pin build fails closed with no second workspace"
}

# --- pinned-database tests (need sqlite3; the live-login build also needs opencode)

# install_fixture_opencode <name> <provider-name>: shadow the real opencode
# with a fixture that serves a canned console config. `debug paths` points
# at a scratch data dir holding the fixture live database, recorded in
# FIXTURE_DATADIR; `debug config` migrates the OPENCODE_DB file the way the
# real CLI does on first load (an empty file gains the console schema) and
# then resolves the opencode provider to <provider-name>. Call it bare, not
# in a command substitution: it exports PATH and records SAVED_PATH for
# restore_fixture_opencode, and neither survives a subshell.
install_fixture_opencode() {  # <name> <provider-name>
  local name=$1 provider=$2 bindir datadir
  bindir="$TMP_ROOT/fbin-$name"
  datadir="$TMP_ROOT/fdata-$name"
  rm -rf "$bindir" "$datadir"
  mkdir -p "$bindir" "$datadir"
  cat > "$bindir/opencode" <<FIXTURE_EOF
#!/usr/bin/env bash
set -u
if [ "\${1:-}" = debug ] && [ "\${2:-}" = paths ]; then
  printf 'data %s\n' "$datadir"
  exit 0
fi
if [ "\${1:-}" = debug ] && [ "\${2:-}" = config ]; then
  if [ -n "\${OPENCODE_DB:-}" ] && command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "\$OPENCODE_DB" 'CREATE TABLE IF NOT EXISTS account (id TEXT PRIMARY KEY, access_token TEXT NOT NULL, refresh_token TEXT NOT NULL); CREATE TABLE IF NOT EXISTS account_state (id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);' >/dev/null 2>&1 || true
  fi
  printf '{\n  "provider": {\n    "opencode": {\n      "name": "%s",\n      "models": {}\n    }\n  }\n}\n' "$provider"
  exit 0
fi
exit 1
FIXTURE_EOF
  chmod +x "$bindir/opencode"
  SAVED_PATH=$PATH
  PATH="$bindir:$PATH"
  export PATH
  FIXTURE_DATADIR=$datadir
  export FIXTURE_DATADIR
}

restore_fixture_opencode() {
  PATH=$SAVED_PATH
  export PATH
}

# fixture_login_db <data-dir> <access-token> <active-org>: seed a minimal
# console login database (account plus account_state) under <data-dir>.
fixture_login_db() {  # <data-dir> <access-token> <active-org>
  sqlite3 "$1/opencode.db" \
    "CREATE TABLE account(id TEXT PRIMARY KEY, access_token TEXT NOT NULL, refresh_token TEXT NOT NULL);
     CREATE TABLE account_state(id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);
     INSERT INTO account VALUES('acc_01FIXTURE', '$2', 'refresh-01FIXTURE');
     INSERT INTO account_state VALUES(1, 'acc_01FIXTURE', '$3');" \
    || fail "the fixture login database refused its seed"
}

test_db_heals_a_drifted_pin() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "pin healing needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db got live_token
  install_fixture_opencode heal-pin 'secondary / OpenCode'
  live_token='live-access-HEAL01'
  fixture_login_db "$FIXTURE_DATADIR" "$live_token" 'wrk_01FIXTUREMAIN'
  cfg=$(second_config heal)
  db="$cfg/opencode-second.db"
  sqlite3 "$db" \
    "CREATE TABLE account(id TEXT PRIMARY KEY, access_token TEXT NOT NULL, refresh_token TEXT NOT NULL);
     CREATE TABLE account_state(id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);
     INSERT INTO account VALUES('acc_01FIXTURE', 'pinned-access-OLD', 'refresh-01FIXTURE');
     INSERT INTO account_state VALUES(1, 'acc_01FIXTURE', 'wrk_01MAIN');" \
    || fail "fixture pin refused its seed"
  got=$(fm_opencode_second_db "$cfg") || {
    restore_fixture_opencode
    fail "healing a drifted pin must succeed"
  }
  restore_fixture_opencode
  [ "$got" = "$db" ] || fail "the pin path mismatch: got '$got'"
  [ "$(sqlite3 "$db" 'SELECT active_org_id FROM account_state WHERE id=1;')" = "$ORG_ID" ] \
    || fail "the drifted pin was not healed back to the configured workspace"
  [ "$(sqlite3 "$db" 'SELECT access_token FROM account LIMIT 1;')" = "$live_token" ] \
    || fail "the healed pin kept its stale token instead of the live one"
  pass "an existing database whose pin drifted is healed back"
}

test_db_refreshes_a_stale_token() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "pin refresh needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db got live_token
  install_fixture_opencode refresh-token 'secondary / OpenCode'
  live_token='live-access-ROTATED02'
  fixture_login_db "$FIXTURE_DATADIR" "$live_token" 'wrk_01FIXTUREMAIN'
  cfg=$(second_config refresh)
  db="$cfg/opencode-second.db"
  sqlite3 "$db" \
    "CREATE TABLE account(id TEXT PRIMARY KEY, access_token TEXT NOT NULL, refresh_token TEXT NOT NULL);
     CREATE TABLE account_state(id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);
     INSERT INTO account VALUES('acc_01FIXTURE', 'pinned-access-STALE', 'refresh-01FIXTURE');
     INSERT INTO account_state VALUES(1, 'acc_01FIXTURE', '$ORG_ID');" \
    || fail "fixture pin refused its seed"
  got=$(fm_opencode_second_db "$cfg") || {
    restore_fixture_opencode
    fail "a pin with a stale token must still publish after refresh"
  }
  restore_fixture_opencode
  [ "$got" = "$db" ] || fail "the pin path mismatch: got '$got'"
  [ "$(sqlite3 "$db" 'SELECT access_token FROM account LIMIT 1;')" = "$live_token" ] \
    || fail "the live token rotation never reached the pin"
  [ "$(sqlite3 "$db" 'SELECT active_org_id FROM account_state WHERE id=1;')" = "$ORG_ID" ] \
    || fail "the refresh lost the workspace pin"
  pass "a stale pin token is re-copied from the live login on every launch"
}

test_db_refuses_a_wrong_workspace() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "pin proof needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db err
  install_fixture_opencode wrong-workspace 'main / OpenCode'
  fixture_login_db "$FIXTURE_DATADIR" 'live-access-MAIN03' 'wrk_01FIXTUREMAIN'
  cfg=$(second_config refuse)
  db="$cfg/opencode-second.db"
  sqlite3 "$db" \
    "CREATE TABLE account(id TEXT PRIMARY KEY, access_token TEXT NOT NULL, refresh_token TEXT NOT NULL);
     CREATE TABLE account_state(id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);
     INSERT INTO account VALUES('acc_01FIXTURE', 'live-access-MAIN03', 'refresh-01FIXTURE');
     INSERT INTO account_state VALUES(1, 'acc_01FIXTURE', '$ORG_ID');" \
    || fail "fixture pin refused its seed"
  if err=$(fm_opencode_second_db "$cfg" 2>&1); then
    restore_fixture_opencode
    fail "a pin resolving the main workspace must refuse the launch"
  fi
  restore_fixture_opencode
  case "$err" in
    *'refusing the launch'*) ;;
    *) fail "the refusal must name itself; got: $err" ;;
  esac
  pass "a pin resolving the wrong workspace refuses the launch"
}

test_db_builds_from_scratch() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "pin building needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db got live_token
  install_fixture_opencode build-fresh 'secondary / OpenCode'
  live_token='live-access-FRESH04'
  fixture_login_db "$FIXTURE_DATADIR" "$live_token" 'wrk_01FIXTUREMAIN'
  cfg=$(second_config build)
  [ -e "$cfg/opencode-second.db" ] && {
    restore_fixture_opencode
    fail "the fixture config must start with no pin"
  }
  got=$(fm_opencode_second_db "$cfg") || {
    restore_fixture_opencode
    fail "building the pin must succeed"
  }
  [ "$got" = "$cfg/opencode-second.db" ] || {
    restore_fixture_opencode
    fail "the pin path mismatch: got '$got'"
  }
  [ -s "$cfg/opencode-second.db" ] || {
    restore_fixture_opencode
    fail "the pinned database is empty"
  }
  [ "$(sqlite3 "$cfg/opencode-second.db" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null)" = "$ORG_ID" ] || {
    restore_fixture_opencode
    fail "the pinned database does not name the configured workspace"
  }
  [ "$(sqlite3 "$cfg/opencode-second.db" 'SELECT access_token FROM account LIMIT 1;' 2>/dev/null)" = "$live_token" ] || {
    restore_fixture_opencode
    fail "the pinned database does not carry the live login"
  }
  OPENCODE_DB="$cfg/opencode-second.db" opencode debug config >/dev/null 2>&1 || {
    restore_fixture_opencode
    fail "opencode rejects the pinned database"
  }
  restore_fixture_opencode
  rm -f "$cfg/opencode-second.db"
  pass "the pin builds from scratch and opencode accepts it"
}

test_absent_config_is_unconfigured
test_org_id_round_trips
test_malformed_org_is_refused
test_lane_workspace_defaults_to_main
test_lane_workspace_marker_selects_second
test_db_build_refuses_when_unconfigured
test_db_heals_a_drifted_pin
test_db_refreshes_a_stale_token
test_db_refuses_a_wrong_workspace
test_db_builds_from_scratch
