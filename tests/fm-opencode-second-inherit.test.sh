#!/usr/bin/env bash
# Behavior tests for second-workspace inheritance into secondmate homes.
#
# The workspace id in config/opencode-second-org propagates from the primary
# home into each secondmate home through the declared inherited-material
# contract, so secondmate homes serve the Go tier too, with the billing
# resets in config/opencode-go-resets riding along. The pinned
# database in config/opencode-second.db never propagates: each home builds
# its own from its own machine's live console login, so a stale or foreign
# token can never ride along.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# shellcheck source=/dev/null
. "$ROOT/bin/fm-config-inherit-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-opencode-second-inherit)

ORG_ID='org_01FIXTURESECONDARY00'

new_config_pair() {
  local name=$1 base src dest
  base="$TMP_ROOT/$name"
  src="$base/src"
  dest="$base/dest"
  mkdir -p "$src" "$dest"
  printf '%s\n' "$src|$dest"
}

test_org_id_is_declared_inherited_material() {
  local items
  items=$(fm_config_inherit_items) || fail "could not list declared inherited material"
  printf '%s\n' "$items" | grep -qx 'config/opencode-second-org' \
    || fail "config/opencode-second-org is not declared inherited material"
  printf '%s\n' "$items" | grep -qx 'config/opencode-go-resets' \
    || fail "config/opencode-go-resets is not declared inherited material"
  printf '%s\n' "$items" | grep -qx 'config/opencode-second.db' \
    && fail "config/opencode-second.db must never be declared inherited material"
  pass "the org id is inherited material while the pinned database is not"
}

test_org_id_propagates_byte_exact() {
  local rec src dest report
  rec=$(new_config_pair propagates)
  src=${rec%%|*}
  dest=${rec#*|}
  printf '%s' "$ORG_ID" > "$src/opencode-second-org"
  report="$TMP_ROOT/propagates.report"

  FM_INHERITABLE_CONFIG="opencode-second-org" FM_CONFIG_INHERIT_REPORT="$report" \
    propagate_inheritable_config "$src" "$dest" \
    || fail "org id propagation failed"
  [ -f "$dest/opencode-second-org" ] || fail "org id did not arrive in the destination home"
  cmp -s "$src/opencode-second-org" "$dest/opencode-second-org" \
    || fail "org id did not converge byte-exact"
  assert_grep $'opencode-second-org\tpushed\t' "$report" "org id should report pushed"
  pass "the org id propagates byte-exact into the secondmate home"
}

test_org_id_reconvergence_is_idempotent() {
  local rec src dest report
  rec=$(new_config_pair idempotent)
  src=${rec%%|*}
  dest=${rec#*|}
  printf '%s' "$ORG_ID" > "$src/opencode-second-org"
  printf '%s' "$ORG_ID" > "$dest/opencode-second-org"
  report="$TMP_ROOT/idempotent.report"

  FM_INHERITABLE_CONFIG="opencode-second-org" FM_CONFIG_INHERIT_REPORT="$report" \
    propagate_inheritable_config "$src" "$dest" \
    || fail "org id reconvergence failed"
  cmp -s "$src/opencode-second-org" "$dest/opencode-second-org" \
    || fail "idempotent reconvergence changed the destination bytes"
  assert_grep $'opencode-second-org\tunchanged\t' "$report" "org id should report unchanged"
  pass "reconvergence over identical bytes is idempotent"
}

test_primary_absence_mirrors_downstream() {
  local rec src dest report
  rec=$(new_config_pair absence)
  src=${rec%%|*}
  dest=${rec#*|}
  printf '%s' "$ORG_ID" > "$dest/opencode-second-org"
  report="$TMP_ROOT/absence.report"

  FM_INHERITABLE_CONFIG="opencode-second-org" FM_CONFIG_INHERIT_REPORT="$report" \
    propagate_inheritable_config "$src" "$dest" \
    || fail "absence mirroring failed"
  [ ! -e "$dest/opencode-second-org" ] \
    || fail "clearing the primary org id must clear it downstream too"
  pass "primary absence removes the downstream org id"
}

test_pinned_database_is_never_touched() {
  local rec src dest
  rec=$(new_config_pair database)
  src=${rec%%|*}
  dest=${rec#*|}
  printf '%s' "$ORG_ID" > "$src/opencode-second-org"
  printf 'foreign-pinned-bytes' > "$src/opencode-second.db"
  printf 'home-pinned-bytes' > "$dest/opencode-second.db"

  FM_INHERITABLE_CONFIG="opencode-second-org" \
    propagate_inheritable_config "$src" "$dest" \
    || fail "propagation alongside a database failed"
  [ "$(cat "$dest/opencode-second.db")" = "home-pinned-bytes" ] \
    || fail "the destination home's own pinned database was overwritten"
  [ "$(cat "$dest/opencode-second-org")" = "$ORG_ID" ] \
    || fail "the org id did not propagate alongside the database"
  pass "each home keeps building its own pinned database"
}

test_org_id_is_declared_inherited_material
test_org_id_propagates_byte_exact
test_org_id_reconvergence_is_idempotent
test_primary_absence_mirrors_downstream
test_pinned_database_is_never_touched
