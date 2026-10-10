#!/usr/bin/env bash
# Behavior tests for bin/fm-opencode-second-lib.sh - the OpenCode Go
# workspace pins.
#
# The load-bearing contracts:
#   1. Each workspace id is read from its gitignored org file
#      (config/opencode-second-org, config/opencode-main-org) only; absence
#      of the second id reads as unconfigured (the three-rung ladder), while
#      a malformed file is refused rather than guessed at. The main id is
#      discovered from the console login and recorded on first use when no
#      file holds it; an undiscoverable main id refuses the launch.
#   2. A lane's workspace comes from its task meta marker
#      (opencode_workspace=secondary); anything else is the main workspace,
#      which is also what every record written before markers existed reads
#      as.
#   3. Each pinned database is built once (migrated through opencode itself,
#      seeded from the live login, pinned to the workspace id), an existing
#      database whose pin drifted is healed back, and the login rows are
#      re-copied from the live database on every pinned launch so the pin
#      never carries a stale token. The pin is a copy, never the login: a
#      pin path naming the live database is refused before anything reads or
#      writes either file, and refresh never writes the live login.
#   4. Before a launch rides a pin, the pinned config must resolve the
#      opencode provider to that launch's own workspace; anything else
#      refuses the launch, so a mis-pinned session can never bill the wrong
#      workspace.
# Contracts 3 and 4 run against a fixture opencode on PATH (canned
# `debug paths`, `debug config`, and `console orgs`) plus a fixture live
# database, so they prove the mechanics without touching this machine's
# login.
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

MAIN_ORG_ID='org_01FIXTUREMAIN00000'

main_config() {  # <name> [org-id] -> config dir with a main id file
  local dir="$TMP_ROOT/cfg-$1" org=${2:-$MAIN_ORG_ID}
  rm -rf "$dir"
  mkdir -p "$dir"
  printf '%s' "$org" > "$dir/opencode-main-org"
  printf '%s\n' "$dir"
}

# install_mapping_fixture_opencode <name>: fixture opencode whose
# `debug config` resolves the workspace name from the pin database's own
# active workspace id, and whose `console orgs` lists the main row beside
# the secondary row the way the real CLI does. Call it bare, not in a
# command substitution: it exports PATH and records SAVED_PATH for
# restore_fixture_opencode, and neither survives a subshell.
install_mapping_fixture_opencode() {  # <name>
  local name=$1 bindir datadir
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
  active=\$(sqlite3 "\${OPENCODE_DB:-/nonexistent}" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null) || active=
  if [ "\$active" = "$MAIN_ORG_ID" ]; then
    printf '{\n  "provider": {\n    "opencode": {\n      "name": "main / OpenCode",\n      "models": {}\n    }\n  }\n}\n'
  elif [ "\$active" = "$ORG_ID" ]; then
    printf '{\n  "provider": {\n    "opencode": {\n      "name": "secondary / OpenCode",\n      "models": {}\n    }\n  }\n}\n'
  else
    printf '{\n  "provider": {\n    "opencode": {\n      "name": "unknown / OpenCode",\n      "models": {}\n    }\n  }\n}\n'
  fi
  exit 0
fi
if [ "\${1:-}" = console ] && [ "\${2:-}" = orgs ]; then
  printf '\033[0m\n  \033[92m\xe2\x97\x8f\033[0m \033[96m\033[1mmain\033[0m  \033[90mfixture@example.invalid\033[0m  \033[90mhttps://opencode.ai/console\033[0m  \033[90m%s\033[0m\n    secondary  \033[90mfixture@example.invalid\033[0m  \033[90mhttps://opencode.ai/console\033[0m  \033[90m%s\033[0m\n' "$MAIN_ORG_ID" "$ORG_ID"
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

# install_mainless_fixture_opencode <name>: like the mapping fixture, but the
# console login names no main workspace, so main discovery must refuse.
install_mainless_fixture_opencode() {  # <name>
  local name=$1 bindir datadir
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
  printf '{\n  "provider": {\n    "opencode": {\n      "name": "secondary / OpenCode",\n      "models": {}\n    }\n  }\n}\n'
  exit 0
fi
if [ "\${1:-}" = console ] && [ "\${2:-}" = orgs ]; then
  printf 'secondary  fixture@example.invalid  https://opencode.ai/console  %s\n' "$ORG_ID"
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

test_main_org_id_round_trips() {
  local cfg got
  cfg=$(main_config main-roundtrip)
  got=$(fm_opencode_main_org "$cfg") || fail "a well-formed main id file must yield its id"
  [ "$got" = "$MAIN_ORG_ID" ] || fail "main id mismatch: got '$got'"
  fm_opencode_main_configured "$cfg" || fail "a well-formed main id file must read as configured"
  pass "a well-formed main id file yields its workspace id"
}

test_main_org_malformed_is_refused() {
  local cfg case_name
  for case_name in empty spaces token slash newline toolong; do
    cfg=$(empty_config "main-bad-$case_name")
    case "$case_name" in
      empty) : > "$cfg/opencode-main-org" ;;
      spaces) printf '   \n' > "$cfg/opencode-main-org" ;;
      token) printf 'not an id!' > "$cfg/opencode-main-org" ;;
      slash) printf 'org_01/abc' > "$cfg/opencode-main-org" ;;
      newline) printf 'org_01abc\norg_01def\n' > "$cfg/opencode-main-org" ;;
      toolong) printf 'org_%s' "$(head -c 130 /dev/zero | tr '\0' 'a')" > "$cfg/opencode-main-org" ;;
    esac
    fm_opencode_main_org "$cfg" >/dev/null 2>&1 && fail "malformed main id file ($case_name) must be refused"
    fm_opencode_main_configured "$cfg" && fail "malformed main id file ($case_name) must read as unconfigured"
  done
  pass "a malformed main id file is refused rather than guessed at"
}

test_main_org_discover_reads_the_console_login() {
  local got
  install_mapping_fixture_opencode discover-main
  got=$(fm_opencode_main_org_discover) || {
    restore_fixture_opencode
    fail "discovery must read the main row from the console login"
  }
  restore_fixture_opencode
  [ "$got" = "$MAIN_ORG_ID" ] || fail "discovery mismatch: got '$got'"
  pass "main discovery reads the console login's main row"
}

test_main_org_discover_refuses_without_a_main_row() {
  local err
  install_mainless_fixture_opencode discover-mainless
  if err=$(fm_opencode_main_org_discover 2>&1); then
    restore_fixture_opencode
    fail "discovery with no main row must refuse"
  fi
  restore_fixture_opencode
  case "$err" in
    *'refusing to guess'*) ;;
    *) fail "the refusal must say it refuses to guess; got: $err" ;;
  esac
  case "$err" in
    *"$ORG_ID"*) fail "the refusal must never print a workspace id" ;;
  esac
  pass "main discovery refuses a login with no main row"
}

test_main_org_ensure_records_the_discovery() {
  local cfg got
  install_mapping_fixture_opencode ensure-main
  cfg=$(empty_config ensure-main)
  got=$(fm_opencode_main_org_ensure "$cfg") || {
    restore_fixture_opencode
    fail "ensure must discover and record the main id"
  }
  [ "$got" = "$MAIN_ORG_ID" ] || {
    restore_fixture_opencode
    fail "ensure mismatch: got '$got'"
  }
  [ "$(cat "$cfg/opencode-main-org")" = "$MAIN_ORG_ID" ] || {
    restore_fixture_opencode
    fail "ensure did not record the discovered id alone in its file"
  }
  got=$(fm_opencode_main_org_ensure "$cfg") || {
    restore_fixture_opencode
    fail "a recorded main id must win on the next call"
  }
  restore_fixture_opencode
  [ "$got" = "$MAIN_ORG_ID" ] || fail "the recorded id did not win: got '$got'"
  pass "main ensure records the discovery once and reuses the file"
}

test_main_db_builds_from_scratch_and_proves_main() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "main pin building needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db got live_token
  install_mapping_fixture_opencode build-main
  live_token='live-access-MAIN05'
  fixture_login_db "$FIXTURE_DATADIR" "$live_token" 'wrk_01FIXTUREOTHER'
  cfg=$(empty_config build-main)
  [ -e "$cfg/opencode-main-org" ] && {
    restore_fixture_opencode
    fail "the fixture config must start with no main id"
  }
  got=$(fm_opencode_main_db "$cfg") || {
    restore_fixture_opencode
    fail "building the main pin must succeed"
  }
  [ "$got" = "$cfg/opencode-main.db" ] || {
    restore_fixture_opencode
    fail "the pin path mismatch: got '$got'"
  }
  [ "$(sqlite3 "$cfg/opencode-main.db" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null)" = "$MAIN_ORG_ID" ] || {
    restore_fixture_opencode
    fail "the main pin does not name the discovered workspace"
  }
  [ "$(sqlite3 "$cfg/opencode-main.db" 'SELECT access_token FROM account LIMIT 1;' 2>/dev/null)" = "$live_token" ] || {
    restore_fixture_opencode
    fail "the main pin does not carry the live login"
  }
  [ "$(cat "$cfg/opencode-main-org")" = "$MAIN_ORG_ID" ] || {
    restore_fixture_opencode
    fail "building the main pin must record the discovered id"
  }
  restore_fixture_opencode
  rm -f "$cfg/opencode-main.db" "$cfg/opencode-main-org"
  pass "the main pin builds from scratch and proves the main workspace"
}

test_main_db_heals_a_drifted_pin() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "main pin healing needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db got live_token
  install_mapping_fixture_opencode heal-main
  live_token='live-access-HEAL06'
  fixture_login_db "$FIXTURE_DATADIR" "$live_token" 'wrk_01FIXTUREOTHER'
  cfg=$(main_config heal-main)
  db="$cfg/opencode-main.db"
  sqlite3 "$db" \
    "CREATE TABLE account(id TEXT PRIMARY KEY, access_token TEXT NOT NULL, refresh_token TEXT NOT NULL);
     CREATE TABLE account_state(id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);
     INSERT INTO account VALUES('acc_01FIXTURE', 'pinned-access-OLD', 'refresh-01FIXTURE');
     INSERT INTO account_state VALUES(1, 'acc_01FIXTURE', '$ORG_ID');" \
    || fail "fixture pin refused its seed"
  got=$(fm_opencode_main_db "$cfg") || {
    restore_fixture_opencode
    fail "healing a drifted main pin must succeed"
  }
  restore_fixture_opencode
  [ "$got" = "$db" ] || fail "the pin path mismatch: got '$got'"
  [ "$(sqlite3 "$db" 'SELECT active_org_id FROM account_state WHERE id=1;')" = "$MAIN_ORG_ID" ] \
    || fail "the drifted main pin was not healed back to the main workspace"
  [ "$(sqlite3 "$db" 'SELECT access_token FROM account LIMIT 1;')" = "$live_token" ] \
    || fail "the healed main pin kept its stale token instead of the live one"
  pass "a drifted main pin is healed back to the main workspace"
}

test_main_db_refuses_a_wrong_workspace() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "main pin proof needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg err
  install_fixture_opencode main-wrong-workspace 'secondary / OpenCode'
  fixture_login_db "$FIXTURE_DATADIR" 'live-access-MAIN07' 'wrk_01FIXTUREMAIN'
  cfg=$(main_config refuse-main)
  if err=$(fm_opencode_main_db "$cfg" 2>&1); then
    restore_fixture_opencode
    fail "a main pin resolving the secondary workspace must refuse the launch"
  fi
  restore_fixture_opencode
  case "$err" in
    *'refusing the launch'*) ;;
    *) fail "the refusal must name itself; got: $err" ;;
  esac
  case "$err" in
    *"$MAIN_ORG_ID"*|*"$ORG_ID"*) fail "the refusal must never print a workspace id" ;;
  esac
  pass "a main pin resolving the wrong workspace refuses the launch"
}

test_refresh_never_touches_the_live_login() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "refresh isolation needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db live live_before live_active_before
  install_mapping_fixture_opencode refresh-isolation
  fixture_login_db "$FIXTURE_DATADIR" 'live-access-ISOL08' "$ORG_ID"
  cfg=$(main_config refresh-isolation)
  db="$cfg/opencode-main.db"
  sqlite3 "$db" \
    "CREATE TABLE account(id TEXT PRIMARY KEY, access_token TEXT NOT NULL, refresh_token TEXT NOT NULL);
     CREATE TABLE account_state(id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);
     INSERT INTO account VALUES('acc_01FIXTURE', 'pinned-access-STALE', 'refresh-01FIXTURE');
     INSERT INTO account_state VALUES(1, 'acc_01FIXTURE', '$MAIN_ORG_ID');" \
    || fail "fixture pin refused its seed"
  live="$FIXTURE_DATADIR/opencode.db"
  live_before=$(sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5sum 2>/dev/null || sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5 2>/dev/null) || fail "the live login could not be checksummed"
  live_active_before=$(sqlite3 "$live" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null) || fail "the live active workspace is unreadable"
  fm_opencode_second_refresh "$db" "$live" "$MAIN_ORG_ID" || fail "refreshing the pin must succeed"
  [ "$(sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5sum 2>/dev/null || sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5 2>/dev/null)" = "$live_before" ] \
    || fail "refresh rewrote the live login database"
  [ "$(sqlite3 "$live" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null)" = "$live_active_before" ] \
    || fail "refresh moved the live active workspace"
  restore_fixture_opencode
  pass "refresh re-copies the login without touching the live database"
}

test_pin_path_naming_live_is_refused() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "pin-path guard needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg live live_before err
  install_mapping_fixture_opencode pin-names-live
  fixture_login_db "$FIXTURE_DATADIR" 'live-access-GUARD09' "$ORG_ID"
  live="$FIXTURE_DATADIR/opencode.db"
  cfg=$(main_config pin-names-live)
  ln -s "$live" "$cfg/opencode-main.db" || fail "the fixture symlink could not be built"
  live_before=$(sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5sum 2>/dev/null || sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5 2>/dev/null) || fail "the live login could not be checksummed"
  if err=$(fm_opencode_main_db "$cfg" 2>&1); then
    restore_fixture_opencode
    fail "a pin path naming the live database must be refused"
  fi
  case "$err" in
    *'refusing to touch it'*) ;;
    *) restore_fixture_opencode; fail "the refusal must name the live-database guard; got: $err" ;;
  esac
  [ "$(sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5sum 2>/dev/null || sqlite3 "$live" '.dump account account_state' 2>/dev/null | md5 2>/dev/null)" = "$live_before" ] || {
    restore_fixture_opencode
    fail "the refused pin still touched the live login"
  }
  [ -L "$cfg/opencode-main.db" ] || {
    restore_fixture_opencode
    fail "the refused pin deleted the pin path instead of leaving it alone"
  }
  if fm_opencode_second_refresh "$live" "$live" "$MAIN_ORG_ID" 2>/dev/null; then
    restore_fixture_opencode
    fail "refresh onto the live database must be refused"
  fi
  restore_fixture_opencode
  pass "a pin path naming the live database is refused before any write"
}

test_same_file_predicate() {
  local dir a b
  dir="$TMP_ROOT/samefile"
  rm -rf "$dir"
  mkdir -p "$dir"
  a="$dir/a.db"
  b="$dir/b.db"
  printf 'x' > "$a"
  printf 'x' > "$b"
  fm_opencode_same_file "$a" "$a" || fail "identical strings must read as the same file"
  fm_opencode_same_file "$a" "$b" && fail "distinct files must not read as the same file"
  ln -s "$a" "$dir/link.db" || fail "the fixture symlink could not be built"
  fm_opencode_same_file "$a" "$dir/link.db" || fail "a symlink must read as its target"
  fm_opencode_same_file "$a" "$dir/missing.db" && fail "a missing file must not read as the same file"
  fm_opencode_same_file "$dir/missing-a.db" "$dir/missing-b.db" && fail "two missing files must not read as the same file"
  pass "the same-file guard tells copies apart from the live database"
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
test_main_org_id_round_trips
test_main_org_malformed_is_refused
test_main_org_discover_reads_the_console_login
test_main_org_discover_refuses_without_a_main_row
test_main_org_ensure_records_the_discovery
test_main_db_builds_from_scratch_and_proves_main
test_main_db_heals_a_drifted_pin
test_main_db_refuses_a_wrong_workspace
test_refresh_never_touches_the_live_login
test_pin_path_naming_live_is_refused
test_same_file_predicate
