#!/usr/bin/env bash
# tests/fm-backend.test.sh - herdr-only runtime-backend contract.
#
# This fork keeps exactly one runtime session backend, herdr. The tmux,
# zellij, orca, and cmux adapters were removed and survive only in git
# history. This suite unit-tests bin/fm-backend.sh's selection, meta, and
# dispatch helpers for that contract:
#
#   1. fm_backend_name resolves FM_BACKEND env > config/backend >
#      HERDR_ENV=1 auto-detection > default herdr, silently.
#   2. fm_backend_detect reports herdr for HERDR_ENV=1 and nothing else:
#      TMUX and CMUX_WORKSPACE_ID markers are ignored, not resolved.
#   3. fm_backend_validate / fm_backend_validate_spawn accept herdr, refuse
#      the removed backends with a message naming herdr, and refuse unknown
#      backends (including the blocked `codex-app`) loudly.
#   4. Meta helpers default an absent backend= to herdr.
#   5. fm-spawn.sh refuses --backend tmux|cmux|orca|zellij with that same
#      herdr-naming refusal instead of failing obscurely late.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

# shellcheck source=/dev/null
. "$ROOT/bin/fm-backend.sh"

TMP_ROOT=$(fm_test_tmproot fm-backend-tests)

# --- fm-backend.sh unit tests ------------------------------------------------

test_backend_name_precedence() {
  local dir cfg
  dir="$TMP_ROOT/name-precedence"; cfg="$dir/config"
  mkdir -p "$cfg"

  # Markers explicitly unset in a subshell so this stays deterministic
  # regardless of the runtime this test suite itself executes inside.
  # fm_backend_name reads FM_BACKEND_CONFIG_DIR (bound once, at fm-backend.sh
  # source time, from FM_CONFIG_OVERRIDE); a later FM_CONFIG_OVERRIDE=... prefix
  # on the function call itself does not re-bind it, so these calls set
  # FM_BACKEND_CONFIG_DIR directly.
  [ "$(unset TMUX HERDR_ENV CMUX_WORKSPACE_ID; FM_BACKEND='' FM_BACKEND_CONFIG_DIR="$cfg" fm_backend_name)" = herdr ] \
    || fail "fm_backend_name should default to herdr with no env/config/detection markers"

  printf 'herdr\n' > "$cfg/backend"
  [ "$(unset TMUX HERDR_ENV CMUX_WORKSPACE_ID; FM_BACKEND='' FM_BACKEND_CONFIG_DIR="$cfg" fm_backend_name)" = herdr ] \
    || fail "fm_backend_name should read config/backend"

  [ "$(unset TMUX HERDR_ENV CMUX_WORKSPACE_ID; FM_BACKEND=herdr FM_BACKEND_CONFIG_DIR="$cfg" fm_backend_name)" = herdr ] \
    || fail "FM_BACKEND env should win over config/backend"

  pass "fm_backend_name: FM_BACKEND env > config/backend > default herdr"
}

# fm_backend_detect: environment-marker runtime auto-detection. HERDR_ENV=1
# selects herdr; every removed backend's marker is ignored, so results never
# depend on the ambient shell this suite runs inside.
test_backend_detect_precedence() {
  local out

  if out=$(unset TMUX HERDR_ENV CMUX_WORKSPACE_ID; fm_backend_detect); then
    fail "fm_backend_detect should return 1 (undetected) with no markers set, got '$out'"
  fi

  out=$(unset TMUX CMUX_WORKSPACE_ID; HERDR_ENV=1 fm_backend_detect) \
    || fail "fm_backend_detect should succeed when HERDR_ENV=1"
  [ "$out" = herdr ] || fail "fm_backend_detect should report herdr for HERDR_ENV=1 alone, got '$out'"

  # Removed backends are not auto-detectable: their markers resolve to nothing.
  if out=$(unset HERDR_ENV CMUX_WORKSPACE_ID; TMUX='fake,1,0' fm_backend_detect); then
    fail "fm_backend_detect must not resolve \$TMUX to a removed backend, got '$out'"
  fi
  if out=$(unset TMUX HERDR_ENV; CMUX_WORKSPACE_ID='fake-uuid' fm_backend_detect); then
    fail "fm_backend_detect must not resolve CMUX_WORKSPACE_ID to a removed backend, got '$out'"
  fi
  if out=$(TMUX='fake,1,0' HERDR_ENV=1 CMUX_WORKSPACE_ID='fake-uuid' fm_backend_detect); then
    [ "$out" = herdr ] || fail "fm_backend_detect should report herdr when HERDR_ENV=1 is set alongside removed-backend markers, got '$out'"
  else
    fail "fm_backend_detect should succeed when HERDR_ENV=1 is set alongside removed-backend markers"
  fi

  pass "fm_backend_detect: no markers -> undetected, HERDR_ENV=1 -> herdr, removed-backend markers ignored"
}

# fm_backend_name's auto-detect step: fires only when FM_BACKEND/config/backend
# are both absent, and stays silent - herdr is the default path, so there is
# no opt-out to announce.
test_backend_name_autodetect_notice() {
  local dir cfg out errfile

  dir="$TMP_ROOT/name-autodetect"; cfg="$dir/config-empty"; mkdir -p "$cfg"
  errfile="$dir/err.txt"

  : > "$errfile"
  out=$(unset TMUX HERDR_ENV CMUX_WORKSPACE_ID; FM_BACKEND='' FM_BACKEND_CONFIG_DIR="$cfg" fm_backend_name 2>"$errfile")
  [ "$out" = herdr ] || fail "fm_backend_name should default to herdr with no detection markers, got '$out'"
  [ -s "$errfile" ] && fail "fm_backend_name must stay silent with no detection markers"$'\n'"$(cat "$errfile")"

  : > "$errfile"
  out=$(unset TMUX CMUX_WORKSPACE_ID; HERDR_ENV=1 FM_BACKEND='' FM_BACKEND_CONFIG_DIR="$cfg" fm_backend_name 2>"$errfile")
  [ "$out" = herdr ] || fail "fm_backend_name should auto-detect herdr from HERDR_ENV=1, got '$out'"
  [ -s "$errfile" ] && fail "fm_backend_name must stay silent when auto-detecting herdr"$'\n'"$(cat "$errfile")"

  pass "fm_backend_name: default and auto-detected herdr resolve silently"
}

# Explicit configuration (FM_BACKEND env or config/backend) always wins over
# runtime auto-detection, even when a detection marker points the other way.
test_backend_name_explicit_beats_detection() {
  local dir cfg out

  dir="$TMP_ROOT/name-explicit-beats-detect"
  cfg="$dir/config-herdr"; mkdir -p "$cfg"; printf 'herdr\n' > "$cfg/backend"
  mkdir -p "$dir/config-empty"

  # fm_backend_name reads FM_BACKEND_CONFIG_DIR (bound once, at fm-backend.sh
  # source time, from FM_CONFIG_OVERRIDE); a later FM_CONFIG_OVERRIDE=... prefix
  # on the function call itself does not re-bind it, so these calls set
  # FM_BACKEND_CONFIG_DIR directly to control which config dir is checked.
  out=$(unset TMUX; HERDR_ENV=1 FM_BACKEND=herdr FM_BACKEND_CONFIG_DIR="$dir/config-empty" fm_backend_name)
  [ "$out" = herdr ] || fail "FM_BACKEND=herdr should resolve to herdr with an ambient HERDR_ENV=1 marker, got '$out'"

  out=$(unset TMUX; HERDR_ENV=1 FM_BACKEND='' FM_BACKEND_CONFIG_DIR="$cfg" fm_backend_name)
  [ "$out" = herdr ] || fail "config/backend=herdr should resolve to herdr with an ambient HERDR_ENV=1 marker, got '$out'"

  pass "fm_backend_name: an explicit FM_BACKEND or config/backend setting resolves alongside runtime auto-detection"
}

test_backend_validate_refuses_unknown() {
  fm_backend_validate herdr 2>/dev/null || fail "fm_backend_validate should accept herdr"
  local out
  for removed in tmux zellij orca cmux; do
    out=$(fm_backend_validate "$removed" 2>&1) && fail "fm_backend_validate should refuse removed backend '$removed'"
    assert_contains "$out" "not supported in this fork (supported: herdr)" \
      "fm_backend_validate did not name herdr as the supported backend for '$removed'"
  done
  # bogus names a backend with no adapter at all; herdr is the only known
  # adapter.
  out=$(fm_backend_validate bogus 2>&1) && fail "fm_backend_validate should refuse bogus (no such adapter)"
  assert_contains "$out" "unknown backend 'bogus'" "fm_backend_validate did not name the rejected backend"
  out=$(fm_backend_validate codex-app 2>&1) && fail "fm_backend_validate should refuse codex-app"
  assert_contains "$out" "unknown backend 'codex-app'" "fm_backend_validate accepted codex-app"
  out=$(fm_backend_validate "herdr herdr" 2>&1) && fail "fm_backend_validate should refuse a multi-token backend name"
  assert_contains "$out" "unknown backend 'herdr herdr'" "fm_backend_validate accepted a multi-token backend name"
  pass "fm_backend_validate: herdr accepted, removed backends refused naming herdr, unknown and blocked codex-app backends refused loudly"
}

test_backend_source_shell_portable() {
  local out status
  # zsh does not word-split unquoted expansions; sourcing fm-backend.sh from
  # an interactive zsh session must still recognize known backend names.
  if command -v zsh >/dev/null 2>&1; then
    zsh -c "cd '$ROOT' && source bin/fm-backend.sh && fm_backend_source herdr && whence -w fm_backend_herdr_capture >/dev/null" 2>/dev/null \
      || fail "zsh: fm_backend_source herdr should load the adapter when sourced"
    out=$(zsh -c "cd '$ROOT' && source bin/fm-backend.sh && fm_backend_source bogus" 2>&1) \
      && fail "zsh: fm_backend_source bogus should fail"
    assert_contains "$out" "unknown backend 'bogus'" \
      "zsh: fm_backend_source did not reject bogus with the expected error"
    pass "zsh: fm_backend_source recognizes known backends and rejects unknown ones"
  else
    pass "zsh: shell-portable backend matching skipped (zsh not found)"
  fi

  bash -c "cd '$ROOT' && source bin/fm-backend.sh && fm_backend_source herdr && declare -F fm_backend_herdr_capture >/dev/null" 2>/dev/null \
    || fail "bash: fm_backend_source herdr should load the adapter when sourced"
  out=$(bash -c "cd '$ROOT' && source bin/fm-backend.sh && fm_backend_source bogus" 2>&1) \
    && fail "bash: fm_backend_source bogus should fail"
  assert_contains "$out" "unknown backend 'bogus'" \
    "bash: fm_backend_source did not reject bogus with the expected error"
  pass "bash: fm_backend_source recognizes known backends and rejects unknown ones"
}

test_backend_validate_spawn_accepts_herdr_only() {
  local out
  fm_backend_validate_spawn herdr 2>/dev/null || fail "fm_backend_validate_spawn should accept herdr"
  for removed in tmux zellij orca cmux; do
    out=$(fm_backend_validate_spawn "$removed" 2>&1) && fail "fm_backend_validate_spawn should refuse removed backend '$removed'"
    assert_contains "$out" "not supported in this fork (supported: herdr)" \
      "fm_backend_validate_spawn did not name herdr as the supported backend for '$removed'"
  done
  out=$(fm_backend_validate_spawn bogus 2>&1) && fail "fm_backend_validate_spawn should still refuse unknown backends"
  assert_contains "$out" "unknown backend 'bogus'" "fm_backend_validate_spawn did not preserve unknown-backend validation"
  out=$(fm_backend_validate_spawn codex-app 2>&1) && fail "fm_backend_validate_spawn should refuse codex-app"
  assert_contains "$out" "unknown backend 'codex-app'" "fm_backend_validate_spawn accepted codex-app"
  out=$(fm_backend_validate_spawn "herdr herdr" 2>&1) && fail "fm_backend_validate_spawn should refuse a multi-token backend name"
  assert_contains "$out" "unknown backend 'herdr herdr'" "fm_backend_validate_spawn accepted a multi-token backend name"
  pass "fm_backend_validate_spawn: herdr is the only spawn-supported backend"
}

test_meta_get_and_backend_of_meta() {
  local meta=$TMP_ROOT/meta-get.meta edge=$TMP_ROOT/meta-get-edge.meta
  fm_write_meta "$meta" "window=default:wA:p2" "harness=claude"
  [ "$(fm_meta_get "$meta" window)" = "default:wA:p2" ] || fail "fm_meta_get did not read window="
  [ "$(fm_meta_get "$meta" missing)" = "" ] || fail "fm_meta_get should print nothing for an absent key"
  [ "$(fm_backend_of_meta "$meta")" = herdr ] || fail "fm_backend_of_meta should default absent backend= to herdr"

  printf 'backend=herdr\n' >> "$meta"
  [ "$(fm_backend_of_meta "$meta")" = herdr ] || fail "fm_backend_of_meta should read an explicit backend=herdr"

  printf 'token=first\ntoken=last=value' > "$edge"
  [ "$(fm_meta_get "$edge" token)" = "last=value" ] \
    || fail "fm_meta_get did not preserve last-value or no-final-newline semantics"

  pass "fm_meta_get / fm_backend_of_meta: read last key=value and default backend to herdr"
}

test_resolve_selector_three_forms() {
  local state=$TMP_ROOT/resolve-state out
  mkdir -p "$state"
  fm_write_meta "$state/dotfiles-d6.meta" "window=default:wA:p2" "backend=herdr"
  fm_write_meta "$state/fm-turnend-all-harnesses-v9.meta" "window=default:wB:p3" "backend=herdr"

  [ "$(fm_backend_resolve_selector 'sess:pane' "$state")" = "sess:pane" ] \
    || fail "explicit session:pane should be used as-is"

  [ "$(fm_backend_resolve_selector 'dotfiles-d6' "$state")" = "default:wA:p2" ] \
    || fail "bare non-fm task id should resolve through exact metadata"
  [ "$(fm_backend_of_selector 'dotfiles-d6' 'default:wA:p2' "$state")" = herdr ] \
    || fail "bare non-fm task id should use its recorded backend"
  [ "$(fm_backend_expected_label_of_selector 'dotfiles-d6' "$state")" = "fm-dotfiles-d6" ] \
    || fail "bare non-fm task id should report the spawned fm-<id> label"

  [ "$(fm_backend_resolve_selector 'fm-turnend-all-harnesses-v9' "$state")" = "default:wB:p3" ] \
    || fail "exact fm-* task id should resolve through its exact metadata"
  [ "$(fm_backend_of_selector 'fm-turnend-all-harnesses-v9' 'default:wB:p3' "$state")" = herdr ] \
    || fail "exact fm-* task id should use exact metadata without stripping fm-"
  [ "$(fm_backend_expected_label_of_selector 'fm-turnend-all-harnesses-v9' "$state")" = "fm-fm-turnend-all-harnesses-v9" ] \
    || fail "exact fm-* task id should report the spawned fm-<id> label"

  out=$(fm_backend_resolve_selector 'fm-missing' "$state" 2>&1) && fail "fm-<id> with no meta should fail"
  assert_contains "$out" "no metadata for fm-missing" "missing-meta error text changed"

  # An ad hoc bare name with no metadata is refused: with one backend there is
  # no live-inventory bare-name fallback.
  out=$(fm_backend_resolve_selector 'adhoc' "$state" 2>&1) && fail "an ad hoc bare name with no meta should fail"
  assert_contains "$out" "no metadata for adhoc" "ad hoc bare-name refusal text changed"

  pass "fm_backend_resolve_selector: session:pane literal, exact task id first, legacy fm-<id> label fallback, ad hoc bare names refused without metadata"
}

test_backend_of_selector_matches_explicit_target_meta() {
  local state=$TMP_ROOT/backend-selector-state
  mkdir -p "$state"
  fm_write_meta "$state/herdr-task.meta" "window=default:w1:p2" "backend=herdr"
  fm_write_meta "$state/dotfiles-d6.meta" "window=default:wA:p2" "backend=herdr"
  fm_write_meta "$state/fm-turnend-all-harnesses-v9.meta" "window=default:wB:p3" "backend=herdr"
  fm_write_meta "$state/default-backend-task.meta" "window=default:w9:p9"

  [ "$(fm_backend_of_selector 'dotfiles-d6' 'default:wA:p2' "$state")" = herdr ] \
    || fail "bare non-fm task id selector should use its recorded backend"
  [ "$(fm_backend_of_selector 'fm-turnend-all-harnesses-v9' 'default:wB:p3' "$state")" = herdr ] \
    || fail "exact fm-* task id selector should use exact metadata before legacy stripping"
  [ "$(fm_backend_of_selector 'fm-herdr-task' 'default:w1:p2' "$state")" = herdr ] \
    || fail "legacy fm-<id> selector should use its recorded backend"
  [ "$(fm_backend_of_selector 'default:w1:p2' 'default:w1:p2' "$state")" = herdr ] \
    || fail "explicit backend target matching metadata should use that task's backend"
  [ "$(fm_backend_of_selector 'default:w9:p9' 'default:w9:p9' "$state")" = herdr ] \
    || fail "explicit target with absent backend= should default to herdr"
  [ "$(fm_backend_of_selector 'manual:outside' 'manual:outside' "$state")" = herdr ] \
    || fail "explicit target with no matching metadata should keep the herdr default"

  pass "fm_backend_of_selector: exact task ids, legacy fm-<id> labels, and matching explicit targets inherit metadata backend"
}

# --- backend selection loudly refuses an unknown backend --------------------

test_spawn_refuses_unknown_backend_flag() {
  local out status
  # bogus names a backend with no adapter at all.
  out=$(FM_ROOT_OVERRIDE='' FM_HOME='' FM_STATE_OVERRIDE='' FM_DATA_OVERRIDE='' \
    FM_PROJECTS_OVERRIDE='' FM_CONFIG_OVERRIDE='' FM_SPAWN_NO_GUARD=1 \
    "$ROOT/bin/fm-spawn.sh" nope-backend-z1 projects/none claude --mode no-mistakes --yolo off --backend bogus 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "fm-spawn --backend bogus should refuse"
  assert_contains "$out" "unknown backend 'bogus'" "fm-spawn did not name the rejected backend"
  pass "fm-spawn.sh --backend bogus is refused loudly"
}

test_spawn_refuses_codex_app_backend_flag() {
  local out status
  out=$(FM_ROOT_OVERRIDE='' FM_HOME='' FM_STATE_OVERRIDE='' FM_DATA_OVERRIDE='' \
    FM_PROJECTS_OVERRIDE='' FM_CONFIG_OVERRIDE='' FM_SPAWN_NO_GUARD=1 \
    "$ROOT/bin/fm-spawn.sh" nope-codex-app-z1 projects/none claude --mode no-mistakes --yolo off --backend codex-app 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "fm-spawn --backend codex-app should refuse"
  assert_contains "$out" "unknown backend 'codex-app'" "fm-spawn did not preserve the blocked codex-app contract"
  pass "fm-spawn.sh --backend codex-app is refused"
}

test_spawn_refuses_unknown_fm_backend_env() {
  local out status
  out=$(FM_ROOT_OVERRIDE='' FM_HOME='' FM_STATE_OVERRIDE='' FM_DATA_OVERRIDE='' \
    FM_PROJECTS_OVERRIDE='' FM_CONFIG_OVERRIDE='' FM_SPAWN_NO_GUARD=1 FM_BACKEND=bogus \
    "$ROOT/bin/fm-spawn.sh" nope-backend-z2 projects/none claude --mode no-mistakes --yolo off 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "FM_BACKEND=bogus should refuse"
  assert_contains "$out" "unknown backend 'bogus'" "fm-spawn did not name the rejected FM_BACKEND"
  pass "fm-spawn.sh honors FM_BACKEND and refuses an unimplemented value loudly"
}

# An explicit --backend for a removed backend is refused with a clear message
# naming herdr as the supported backend, rather than failing obscurely late.
test_spawn_refuses_removed_backend_flag() {
  local out status removed
  for removed in tmux cmux orca zellij; do
    out=$(FM_ROOT_OVERRIDE='' FM_HOME='' FM_STATE_OVERRIDE='' FM_DATA_OVERRIDE='' \
      FM_PROJECTS_OVERRIDE='' FM_CONFIG_OVERRIDE='' FM_SPAWN_NO_GUARD=1 \
      "$ROOT/bin/fm-spawn.sh" "nope-removed-$removed" projects/none claude --mode no-mistakes --yolo off --backend "$removed" 2>&1)
    status=$?
    [ "$status" -ne 0 ] || fail "fm-spawn --backend $removed should refuse"
    assert_contains "$out" "not supported in this fork (supported: herdr)" \
      "fm-spawn --backend $removed did not name herdr as the supported backend"
  done
  pass "fm-spawn.sh --backend tmux|cmux|orca|zellij is refused naming herdr"
}

test_backend_name_precedence
test_backend_detect_precedence
test_backend_name_autodetect_notice
test_backend_name_explicit_beats_detection
test_backend_validate_refuses_unknown
test_backend_source_shell_portable
test_backend_validate_spawn_accepts_herdr_only
test_meta_get_and_backend_of_meta
test_resolve_selector_three_forms
test_backend_of_selector_matches_explicit_target_meta
test_spawn_refuses_unknown_backend_flag
test_spawn_refuses_codex_app_backend_flag
test_spawn_refuses_unknown_fm_backend_env
test_spawn_refuses_removed_backend_flag
