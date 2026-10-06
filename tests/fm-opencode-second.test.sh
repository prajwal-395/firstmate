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
#      seeded from the live login, pinned to the configured workspace) and an
#      existing database whose pin drifted is healed back.
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

# --- pinned-database tests (need sqlite3; the build path also needs opencode)

test_db_heals_a_drifted_pin() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "pin healing needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db got
  cfg=$(second_config heal)
  db="$cfg/opencode-second.db"
  sqlite3 "$db" 'CREATE TABLE account_state (id INTEGER PRIMARY KEY, active_account_id TEXT, active_org_id TEXT);' || fail "fixture schema refused"
  sqlite3 "$db" "INSERT INTO account_state VALUES (1, 'acc_01TEST', 'wrk_01MAIN');" || fail "fixture seed refused"
  got=$(fm_opencode_second_db "$cfg") || fail "healing a drifted pin must succeed"
  [ "$got" = "$db" ] || fail "the pin path mismatch: got '$got'"
  [ "$(sqlite3 "$db" 'SELECT active_org_id FROM account_state WHERE id=1;')" = "$ORG_ID" ] \
    || fail "the drifted pin was not healed back to the configured workspace"
  pass "an existing database whose pin drifted is healed back"
}

test_db_builds_from_live_login() {
  command -v sqlite3 >/dev/null 2>&1 || {
    pass "pin building needs sqlite3 (absent here; live proof covers the real path)"
    return 0
  }
  command -v opencode >/dev/null 2>&1 || {
    pass "pin building needs the opencode CLI (absent here; live proof covers the real path)"
    return 0
  }
  local cfg db live got
  cfg=$(second_config build)
  live=$(fm_opencode_second_live_db) || fail "this machine has no console login to seed from"
  db=$(fm_opencode_second_db "$cfg") || fail "building the pin must succeed"
  [ "$db" = "$cfg/opencode-second.db" ] || fail "the pin path mismatch: got '$db'"
  [ -s "$db" ] || fail "the pinned database is empty"
  got=$(sqlite3 "$db" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null) || fail "the pinned database has no workspace pin"
  [ "$got" = "$ORG_ID" ] || fail "the pinned database names '$got', not the configured workspace"
  OPENCODE_DB="$db" opencode debug config >/dev/null 2>&1 || fail "opencode rejects the pinned database"
  rm -f "$db"
  pass "the pin builds from the live login and opencode accepts it"
}

test_absent_config_is_unconfigured
test_org_id_round_trips
test_malformed_org_is_refused
test_lane_workspace_defaults_to_main
test_lane_workspace_marker_selects_second
test_db_build_refuses_when_unconfigured
test_db_heals_a_drifted_pin
test_db_builds_from_live_login
