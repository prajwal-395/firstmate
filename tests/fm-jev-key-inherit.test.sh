#!/usr/bin/env bash
# Behavior tests for local-only Jev key inheritance into secondmate homes.
#
# Exactly TYPESAFE_API_KEY and AI_GATEWAY_API_KEY propagate from the primary
# home's .env into a LOCAL secondmate home's .env; nothing else from .env ever
# propagates, remote routes never receive keys, and research-charter homes
# stay keyless.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# shellcheck source=/dev/null
. "$ROOT/bin/fm-config-inherit-lib.sh"

TMP_ROOT=$(fm_test_tmproot fm-jev-key-inherit)

file_mode() {
  if [ "$(uname)" = Darwin ]; then
    stat -f %Lp "$1" 2>/dev/null
  else
    stat -c %a "$1" 2>/dev/null
  fi
}

new_home_pair() {
  local name=$1 base primary second
  base="$TMP_ROOT/$name"
  primary="$base/primary"
  second="$base/second"
  mkdir -p "$primary/data" "$primary/config" "$second/data" "$second/config"
  printf '%s\n' "code charter" > "$second/data/charter.md"
  printf '%s\n' "$primary|$second"
}

primary_env() {
  local primary=$1
  cat > "$primary/.env" <<'EOF'
FMX_PAIRING_TOKEN=primary-relay-token
MAIL_PASSWORD=primary-mail-secret
TYPESAFE_API_KEY=primary-ts-key
AI_GATEWAY_API_KEY=primary-gw-key
FUTURE_SOME_KEY=primary-future-secret
EOF
}

test_keys_propagate_and_other_lines_preserved() {
  local rec primary second report
  rec=$(new_home_pair propagate)
  primary=${rec%%|*}
  second=${rec#*|}
  primary_env "$primary"
  printf '%s\n' '# mate local' 'FMX_PAIRING_TOKEN=mate-relay-token' > "$second/.env"
  report="$TMP_ROOT/propagate.report"

  FM_CONFIG_INHERIT_REPORT="$report" propagate_secondmate_secrets "$primary" "$second" \
    || fail "secrets propagation failed"

  assert_grep 'TYPESAFE_API_KEY=primary-ts-key' "$second/.env" "typesafe key did not converge"
  assert_grep 'AI_GATEWAY_API_KEY=primary-gw-key' "$second/.env" "gateway key did not converge"
  assert_grep '# mate local' "$second/.env" "mate comment line was not preserved"
  assert_grep 'FMX_PAIRING_TOKEN=mate-relay-token' "$second/.env" "mate relay token was overwritten"
  assert_no_grep 'primary-relay-token' "$second/.env" "primary relay token propagated"
  assert_no_grep 'primary-mail-secret' "$second/.env" "primary mail credential propagated"
  assert_no_grep 'primary-future-secret' "$second/.env" "unknown future key propagated"
  [ "$(file_mode "$second/.env")" = 600 ] \
    || fail "destination .env mode should be 600, got $(file_mode "$second/.env")"
  assert_grep $'TYPESAFE_API_KEY\tpushed\t' "$report" "typesafe key should report pushed"
  assert_grep $'AI_GATEWAY_API_KEY\tpushed\t' "$report" "gateway key should report pushed"
  pass "both keys propagate while relay, mail, and future keys never do"
}

test_reconvergence_is_idempotent() {
  local rec primary second report
  rec=$(new_home_pair idempotent)
  primary=${rec%%|*}
  second=${rec#*|}
  primary_env "$primary"
  report="$TMP_ROOT/idempotent.report"

  FM_CONFIG_INHERIT_REPORT="$report" propagate_secondmate_secrets "$primary" "$second" \
    || fail "first secrets propagation failed"
  : > "$report"
  FM_CONFIG_INHERIT_REPORT="$report" propagate_secondmate_secrets "$primary" "$second" \
    || fail "second secrets propagation failed"

  assert_grep $'TYPESAFE_API_KEY\tunchanged\t' "$report" "converged key should report unchanged"
  assert_grep $'AI_GATEWAY_API_KEY\tunchanged\t' "$report" "converged key should report unchanged"
  pass "reconvergence is idempotent"
}

test_rotation_and_removal_mirror() {
  local rec primary second report
  rec=$(new_home_pair rotation)
  primary=${rec%%|*}
  second=${rec#*|}
  primary_env "$primary"
  report="$TMP_ROOT/rotation.report"

  FM_CONFIG_INHERIT_REPORT=/dev/null propagate_secondmate_secrets "$primary" "$second" \
    || fail "initial secrets propagation failed"
  printf '%s\n' 'TYPESAFE_API_KEY=rotated-ts-key' > "$primary/.env"

  FM_CONFIG_INHERIT_REPORT="$report" propagate_secondmate_secrets "$primary" "$second" \
    || fail "rotation propagation failed"

  assert_grep 'TYPESAFE_API_KEY=rotated-ts-key' "$second/.env" "rotated value did not converge"
  assert_no_grep 'primary-ts-key' "$second/.env" "stale value survived rotation"
  assert_no_grep 'AI_GATEWAY_API_KEY' "$second/.env" "removed primary key survived downstream"
  assert_grep $'AI_GATEWAY_API_KEY\tpushed\tmirrored primary absence' "$report" \
    "removed key should report mirrored absence"
  pass "rotation propagates and primary removal mirrors downstream"
}

test_absent_both_sides_is_noop() {
  local rec primary second report
  rec=$(new_home_pair noop)
  primary=${rec%%|*}
  second=${rec#*|}
  report="$TMP_ROOT/noop.report"

  FM_CONFIG_INHERIT_REPORT="$report" propagate_secondmate_secrets "$primary" "$second" \
    || fail "absent-both-sides propagation failed"

  assert_absent "$second/.env" "absent keys should not create a destination .env"
  pass "absent keys on both sides create nothing"
}

test_environment_never_sources_keys() {
  local rec primary second
  rec=$(new_home_pair env-source)
  primary=${rec%%|*}
  second=${rec#*|}
  printf '%s\n' 'TYPESAFE_API_KEY=file-ts-key' > "$primary/.env"

  TYPESAFE_API_KEY=env-canary AI_GATEWAY_API_KEY=env-gw-canary \
    propagate_secondmate_secrets "$primary" "$second" \
    || fail "propagation with ambient keys failed"

  assert_grep 'TYPESAFE_API_KEY=file-ts-key' "$second/.env" "primary .env value did not win"
  assert_no_grep 'env-canary' "$second/.env" "ambient environment key leaked downstream"
  assert_no_grep 'env-gw-canary' "$second/.env" "ambient gateway key leaked downstream"
  pass "only the primary .env sources keys, never the process environment"
}

test_research_charter_home_never_written() {
  local rec primary second before report
  rec=$(new_home_pair research)
  primary=${rec%%|*}
  second=${rec#*|}
  primary_env "$primary"
  printf '%s\n' 'You are a research second mate.' > "$second/data/charter.md"
  printf '%s\n' '# research local' 'UNRELATED=keep' > "$second/.env"
  before=$(cat "$second/.env")
  report="$TMP_ROOT/research.report"

  FM_CONFIG_INHERIT_REPORT="$report" propagate_secondmate_secrets "$primary" "$second" \
    || fail "research-home propagation should skip, not fail"

  [ "$(cat "$second/.env")" = "$before" ] \
    || fail "research home .env was written: $(cat "$second/.env")"
  assert_grep $'TYPESAFE_API_KEY\tskipped\tresearch charter stays keyless' "$report" \
    "research skip should be reported"

  rm -f "$second/.env"
  FM_CONFIG_INHERIT_REPORT=/dev/null propagate_secondmate_secrets "$primary" "$second" \
    || fail "keyless research-home propagation should skip, not fail"
  assert_absent "$second/.env" "keyless research home gained a .env"
  pass "a research-charter home is never written to"
}

test_unsafe_destination_refuses() {
  local rec primary second
  rec=$(new_home_pair unsafe)
  primary=${rec%%|*}
  second=${rec#*|}
  primary_env "$primary"
  printf '%s\n' 'TYPESAFE_API_KEY=stale' > "$second/.env-real"
  ln -s .env-real "$second/.env"

  if propagate_secondmate_secrets "$primary" "$second" 2>/dev/null; then
    fail "symlinked destination .env should refuse propagation"
  fi
  assert_grep 'TYPESAFE_API_KEY=stale' "$second/.env-real" "refusal changed the destination"
  pass "an unsafe destination .env refuses without changes"
}

test_remote_transfer_never_carries_keys() {
  local items home out
  items=$(fm_config_inherit_items)
  case "$items" in
    *TYPESAFE_API_KEY*|*AI_GATEWAY_API_KEY*)
      fail "derived remote transfer set contains a secret key: $items" ;;
  esac
  case "$items" in
    *.env*) fail "derived remote transfer set contains a .env path: $items" ;;
  esac

  home="$TMP_ROOT/remote-receiver"
  mkdir -p "$home/config"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-remote-inherit.sh" put .env 0 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 1 < /dev/null 2>&1)
  [ $? -ne 0 ] || fail "remote receiver accepted a .env path"
  assert_contains "$out" "not inherited material" "receiver should refuse .env as non-inherited material"
  out=$(FM_HOME="$home" "$ROOT/bin/fm-remote-inherit.sh" put config/TYPESAFE_API_KEY 0 e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855 1 < /dev/null 2>&1)
  [ $? -ne 0 ] || fail "remote receiver accepted a secret key path"
  assert_contains "$out" "not inherited material" "receiver should refuse keys as non-inherited material"
  pass "remote transfer structurally excludes keys and .env paths"
}

test_remote_sender_refuses_secrets_overlap() {
  local out rc
  out=$(FM_INHERITABLE_CONFIG="crew-harness TYPESAFE_API_KEY" \
    FM_INHERITABLE_SECRETS="TYPESAFE_API_KEY AI_GATEWAY_API_KEY" \
    "$ROOT/bin/fm-remote-inherit-push.sh" probe-mate 1 2>&1)
  rc=$?
  [ "$rc" -ne 0 ] || fail "remote sender ran despite secrets overlap: $out"
  assert_contains "$out" "local-only secrets allowlist" "overlap refusal should name the cause"
  pass "the remote sender fails closed when the declarations overlap"
}

test_secrets_never_enter_reread_instruction() {
  local rec primary second report instruction
  rec=$(new_home_pair reread)
  primary=${rec%%|*}
  second=${rec#*|}
  primary_env "$primary"
  printf '%s\n' 'rules-value' > "$primary/config/crew-dispatch.json"
  report="$TMP_ROOT/reread.report"
  instruction="$TMP_ROOT/reread.instruction"

  FM_CONFIG_INHERIT_REPORT="$report" propagate_secondmate_inheritance "$primary" "$second" \
    || fail "full inheritance propagation failed"
  assert_grep 'TYPESAFE_API_KEY=primary-ts-key' "$second/.env" "keys did not flow through inheritance"
  fm_config_write_reread_instruction "$second" "$report" "$instruction" \
    || fail "reread instruction build failed"

  assert_grep 'rules-value' "$instruction" "changed config bytes missing from instruction"
  assert_no_grep 'primary-ts-key' "$instruction" "secret value leaked into reread instruction"
  assert_no_grep 'primary-gw-key' "$instruction" "secret value leaked into reread instruction"
  pass "secrets flow through inheritance but never enter reread instructions"
}

test_keys_propagate_and_other_lines_preserved
test_reconvergence_is_idempotent
test_rotation_and_removal_mirror
test_absent_both_sides_is_noop
test_environment_never_sources_keys
test_research_charter_home_never_written
test_unsafe_destination_refuses
test_remote_transfer_never_carries_keys
test_remote_sender_refuses_secrets_overlap
test_secrets_never_enter_reread_instruction
