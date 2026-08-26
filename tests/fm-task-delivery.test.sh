#!/usr/bin/env bash
# Behavior tests for the explicit per-task delivery contract (AGENTS.md section 7)
# across bin/fm-spawn.sh, bin/fm-promote.sh, and bin/fm-project-mode.sh.
#
# A ship task's delivery mode and yolo posture are firstmate's decision at intake,
# so the tools refuse to guess: the spawn and a scout promotion require both flags,
# validate them against a closed set, and the spawn additionally refuses to launch
# when the brief it is about to hand the worker records a different mode. Scout
# spawns carry no delivery posture at all. The registry keeps only the captain's
# standing posture, for the mechanical consumers and for one advisory notice.
#
# The same read-the-brief pattern carries a second contract: bin/fm-brief.sh
# scaffolds four required scope fields into every ship and scout brief, and the
# spawn refuses to launch while any of them is empty. bin/fm-brief-lib.sh owns
# those field names and the emptiness test; the cases here pin what the spawn
# does with its verdict.
#
# Every spawn case here stops before any endpoint exists: the delivery checks run
# ahead of backend creation, and a fake `tmux` that exits non-zero backstops the
# cases that are meant to get past them, so no window or worktree is ever created.
set -u

# shellcheck source=tests/lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

SPAWN="$ROOT/bin/fm-spawn.sh"
PROMOTE="$ROOT/bin/fm-promote.sh"
PROJECT_MODE="$ROOT/bin/fm-project-mode.sh"
TMP_ROOT=$(fm_test_tmproot fm-task-delivery)
# The required scope-field names come from their owner, never restated here.
# shellcheck source=bin/fm-brief-lib.sh
. "$ROOT/bin/fm-brief-lib.sh"

# A home with one registered project, one project directory, and a fake tmux that
# refuses, so a spawn that clears the delivery checks still creates nothing.
# Echoes "<home>|<project-dir>|<fakebin>".
make_home() {  # <name> [<registry-line>...]
  local name=$1 home projects fakebin
  shift
  home="$TMP_ROOT/$name/home"
  projects="$TMP_ROOT/$name/projects"
  fakebin="$TMP_ROOT/$name/bin"
  mkdir -p "$home/data" "$home/state" "$home/config" "$projects/proj" "$fakebin"
  printf '#!/bin/sh\nexit 1\n' > "$fakebin/tmux"
  chmod +x "$fakebin/tmux"
  if [ "$#" -gt 0 ]; then
    printf '%s\n' "$@" > "$home/data/projects.md"
  fi
  printf '%s\n' "$home|$projects/proj|$fakebin"
}

write_brief() {  # <home> <id> [<recorded-mode>]
  local home=$1 id=$2 mode=${3:-}
  mkdir -p "$home/data/$id"
  {
    printf 'You are a crewmate.\n\n# Definition of done\n'
    [ -z "$mode" ] || printf 'Delivery contract: mode=%s\n' "$mode"
  } > "$home/data/$id/brief.md"
}

# A brief that carries the required scope contract. <empty-field> leaves exactly
# one of the four blank; omit it for a brief that answers all four. "Blocked on"
# is answered with the literal word `nothing`, the answer that keeps the field
# satisfiable for a task with no blocker at all.
write_scoped_brief() {  # <home> <id> <recorded-mode> [<empty-field>]
  local home=$1 id=$2 mode=$3 blank=${4:-} field body
  mkdir -p "$home/data/$id"
  {
    printf 'You are a crewmate.\n\n# Task\nDo the thing.\n\n'
    while IFS= read -r field; do
      [ -n "$field" ] || continue
      case "$field" in
        "Blocked on") body=nothing ;;
        *) body="An answer for $field." ;;
      esac
      [ "$field" != "$blank" ] || body=""
      printf '## %s\n%s\n\n' "$field" "$body"
    done <<EOF
$(printf '%s\n' "$FM_BRIEF_SCOPE_FIELDS" | tr '|' '\n')
EOF
    printf '# Definition of done\n'
    [ -z "$mode" ] || printf 'Delivery contract: mode=%s\n' "$mode"
  } > "$home/data/$id/brief.md"
}

run_spawn() {  # <home> <fakebin> <spawn-args...>
  local home=$1 fakebin=$2
  shift 2
  FM_ROOT_OVERRIDE='' FM_HOME="$home" \
    FM_STATE_OVERRIDE="$home/state" FM_DATA_OVERRIDE="$home/data" \
    FM_PROJECTS_OVERRIDE="$TMP_ROOT/projects-unused" FM_CONFIG_OVERRIDE="$home/config" \
    FM_SPAWN_NO_GUARD=1 FM_BACKEND=tmux PATH="$fakebin:$PATH" \
    "$SPAWN" "$@" 2>&1
}

# A ship spawn must stop when its delivery contract was never decided or cannot be
# a task mode, and must leave no task metadata behind when it does.
test_ship_spawn_requires_a_valid_delivery_contract() {
  local rec home proj fakebin label flags expect out status n=0
  rec=$(make_home required)
  IFS='|' read -r home proj fakebin <<EOF
$rec
EOF
  while IFS='|' read -r label flags expect; do
    [ -n "$label" ] || continue
    n=$((n + 1))
    write_brief "$home" "delivery-required-$n" no-mistakes
    # shellcheck disable=SC2086  # flags is an intentional word-split arg list
    out=$(run_spawn "$home" "$fakebin" "delivery-required-$n" "$proj" claude $flags)
    status=$?
    [ "$status" -ne 0 ] || fail "$label: expected a non-zero exit"
    assert_contains "$out" "$expect" "$label: refusal did not explain the contract"
    assert_absent "$home/state/delivery-required-$n.meta" "$label: refused spawn wrote task metadata"
  done <<'ROWS'
missing both flags||ship spawns require --mode
missing --yolo|--mode no-mistakes|ship spawns require --yolo
missing --mode|--yolo off|ship spawns require --mode
unknown mode|--mode nope --yolo off|must be one of no-mistakes, direct-PR, local-only
unknown yolo|--mode no-mistakes --yolo maybe|--yolo must be on or off
conditional policy as a task mode|--mode no-mistakes-prod-only --yolo off|classify this task's surface
ROWS
  pass "fm-spawn: a ship spawn requires a valid explicit mode and yolo before anything is created"
}

# A scout has no merge to govern and a secondmate's posture is fixed, so the flags
# are refused rather than accepted and quietly ignored.
test_scout_and_secondmate_refuse_delivery_flags() {
  local rec home proj fakebin out status
  rec=$(make_home refused)
  IFS='|' read -r home proj fakebin <<EOF
$rec
EOF
  write_brief "$home" delivery-scout-a1

  out=$(run_spawn "$home" "$fakebin" delivery-scout-a1 "$proj" claude --scout --mode direct-PR)
  status=$?
  [ "$status" -ne 0 ] || fail "a scout spawn carrying --mode should exit non-zero"
  assert_contains "$out" "--mode applies only to ship spawns" "scout spawn did not refuse --mode"

  out=$(run_spawn "$home" "$fakebin" delivery-scout-a1 "$proj" claude --scout --yolo on)
  status=$?
  [ "$status" -ne 0 ] || fail "a scout spawn carrying --yolo should exit non-zero"
  assert_contains "$out" "--yolo applies only to ship spawns" "scout spawn did not refuse --yolo"

  out=$(run_spawn "$home" "$fakebin" delivery-sm-a2 "$home" --secondmate --mode no-mistakes --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "a secondmate spawn carrying delivery flags should exit non-zero"
  assert_contains "$out" "applies only to ship spawns" "secondmate spawn did not refuse the delivery flags"
  pass "fm-spawn: scout and secondmate spawns refuse ship delivery flags"
}

# The brief is what the worker actually follows, so a spawn whose explicit mode
# disagrees with the brief's recorded contract must refuse instead of launching a
# worker whose instructions contradict the recorded task delivery.
test_spawn_refuses_a_brief_mode_mismatch() {
  local rec home proj fakebin out status
  rec=$(make_home agreement)
  IFS='|' read -r home proj fakebin <<EOF
$rec
EOF
  write_brief "$home" delivery-mismatch-b1 no-mistakes
  out=$(run_spawn "$home" "$fakebin" delivery-mismatch-b1 "$proj" claude --mode direct-PR --yolo off)
  status=$?
  [ "$status" -ne 0 ] || fail "a brief/spawn mode mismatch should exit non-zero"
  assert_contains "$out" "delivery mismatch for delivery-mismatch-b1" "mismatch refusal did not name the task"
  assert_contains "$out" "the brief says mode=no-mistakes but this spawn passed --mode direct-PR" \
    "mismatch refusal did not show both sides of the disagreement"
  assert_absent "$home/state/delivery-mismatch-b1.meta" "mismatched spawn wrote task metadata"

  # The agreeing case clears the check and only fails later, at the refusing tmux.
  write_brief "$home" delivery-agree-b2 direct-PR
  out=$(run_spawn "$home" "$fakebin" delivery-agree-b2 "$proj" claude --mode direct-PR --yolo off)
  assert_not_contains "$out" "delivery mismatch" "an agreeing mode was reported as a mismatch"

  # A brief scaffolded before the contract line existed warns once and continues.
  write_brief "$home" delivery-legacy-b3
  out=$(run_spawn "$home" "$fakebin" delivery-legacy-b3 "$proj" claude --mode local-only --yolo off)
  assert_contains "$out" "records no delivery contract line" "a legacy brief did not warn about its missing contract"
  assert_not_contains "$out" "delivery mismatch" "a legacy brief was treated as a mismatch"
  pass "fm-spawn: the brief's recorded mode and the spawn's explicit mode must agree"
}

# The registry is the captain's standing posture, so dropping below its rigor is
# allowed but never silent, while matching or exceeding it stays quiet. An
# unregistered project resolves to the same no-mistakes standing default
# (AGENTS.md section 7), so a downgrade there is announced too. A conditional
# policy is excluded because both of its legs are legitimate classifications.
test_spawn_notices_a_rigor_downgrade_against_the_registry() {
  local rec home proj fakebin out label mode registry expect registered n=0
  while IFS='|' read -r label registry mode expect registered; do
    [ -n "$label" ] || continue
    n=$((n + 1))
    rec=$(make_home "deviation-$n" "$registry")
    IFS='|' read -r home proj fakebin <<EOF
$rec
EOF
    write_brief "$home" "delivery-dev-$n" "$mode"
    out=$(run_spawn "$home" "$fakebin" "delivery-dev-$n" "$proj" claude --mode "$mode" --yolo off)
    case "$expect" in
      notice)
        assert_contains "$out" "less rigor than the captain's standing posture" \
          "$label: no deviation notice for a rigor downgrade"
        assert_contains "$out" "the standing posture for proj is $registered" \
          "$label: notice did not name the standing posture it compared against" ;;
      quiet)
        assert_not_contains "$out" "less rigor than the captain's standing posture" \
          "$label: printed a deviation notice that is not a downgrade" ;;
    esac
  done <<'ROWS'
no-mistakes project shipped direct-PR|- proj [no-mistakes] - fixture (added 2026-01-01)|direct-PR|notice|no-mistakes
no-mistakes project shipped local-only|- proj [no-mistakes] - fixture (added 2026-01-01)|local-only|notice|no-mistakes
no-mistakes project shipped no-mistakes|- proj [no-mistakes] - fixture (added 2026-01-01)|no-mistakes|quiet|no-mistakes
local-only project shipped no-mistakes|- proj [local-only] - fixture (added 2026-01-01)|no-mistakes|quiet|local-only
conditional policy shipped direct-PR|- proj [no-mistakes-prod-only] - fixture (added 2026-01-01)|direct-PR|quiet|no-mistakes-prod-only
unregistered project resolves to the no-mistakes standing default|- other [no-mistakes] - fixture (added 2026-01-01)|direct-PR|notice|no-mistakes
ROWS
  pass "fm-spawn: a rigor downgrade against the registered posture is announced, never blocked"
}

# A scout's deliverable is a report, so it records no delivery posture at all;
# teardown already treats an absent mode as the most protective one.
test_scout_records_no_delivery_posture() {
  local rec home proj fakebin out
  rec=$(make_home scout-meta "- proj [direct-PR] - fixture (added 2026-01-01)")
  IFS='|' read -r home proj fakebin <<EOF
$rec
EOF
  write_brief "$home" delivery-scoutmeta-c1
  out=$(run_spawn "$home" "$fakebin" delivery-scoutmeta-c1 "$proj" claude --scout)
  assert_not_contains "$out" "less rigor" "a scout spawn consulted the registered delivery posture"
  assert_not_contains "$out" "delivery mismatch" "a scout spawn checked a delivery contract it does not carry"
  pass "fm-spawn: a scout spawn resolves no delivery posture from the registry"
}

# Promotion is where a scout's ship contract is finally decided, so it requires the
# same explicit values and writes them into the task's durable record.
test_promote_requires_and_records_the_delivery_contract() {
  local home meta out status
  home="$TMP_ROOT/promote/home"
  mkdir -p "$home/state"
  meta="$home/state/promote-d1.meta"

  write_scout_meta() {
    printf 'window=fm-promote-d1\nkind=scout\nworktree=/tmp/wt\n' > "$meta"
  }

  write_scout_meta
  out=$(FM_HOME="$home" FM_STATE_OVERRIDE="$home/state" "$PROMOTE" promote-d1 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "promotion without --mode should exit non-zero"
  assert_contains "$out" "promotion requires --mode" "promote refusal did not name the missing mode"
  assert_grep 'kind=scout' "$meta" "refused promotion still changed the task record"

  out=$(FM_HOME="$home" FM_STATE_OVERRIDE="$home/state" "$PROMOTE" promote-d1 --mode direct-PR 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "promotion without --yolo should exit non-zero"
  assert_contains "$out" "promotion requires --yolo" "promote refusal did not name the missing approval posture"

  out=$(FM_HOME="$home" FM_STATE_OVERRIDE="$home/state" "$PROMOTE" promote-d1 --mode no-mistakes-prod-only --yolo off 2>&1)
  status=$?
  [ "$status" -ne 0 ] || fail "promotion on a conditional policy should exit non-zero"
  assert_contains "$out" "classify this task's surface" "promote did not refuse the conditional policy as a task mode"

  out=$(FM_HOME="$home" FM_STATE_OVERRIDE="$home/state" "$PROMOTE" promote-d1 --mode direct-PR --yolo on 2>&1)
  status=$?
  expect_code 0 "$status" "a promotion carrying both flags should succeed"
  assert_grep 'kind=ship' "$meta" "promotion did not restore ship teardown protection"
  assert_grep 'mode=direct-PR' "$meta" "promotion did not record the decided delivery mode"
  assert_grep 'yolo=on' "$meta" "promotion did not record the decided approval posture"
  assert_contains "$out" "ship instructions for mode=direct-PR" "promotion hint did not carry the decided mode"
  [ "$(grep -c '^mode=' "$meta")" = 1 ] || fail "promotion left more than one mode= line in the task record"
  pass "fm-promote: promotion requires the delivery contract and records it exactly once"
}

# The registry parser survives for the mechanical consumers only. It accepts the
# conditional policy, maps it to its most rigorous leg for them, and exposes the
# raw annotation for the one caller that must tell a policy from a flat mode.
test_project_mode_maps_the_conditional_policy() {
  local home out err
  home="$TMP_ROOT/project-mode/home"
  mkdir -p "$home/data"
  cat > "$home/data/projects.md" <<'EOF'
- prodproj [no-mistakes-prod-only] - fixture (added 2026-01-01)
- yoloproj [no-mistakes-prod-only +yolo] - fixture (added 2026-01-01)
- flatproj [direct-PR] - fixture (added 2026-01-01)
- typoproj [no-mistakez] - fixture (added 2026-01-01)
EOF
  out=$(FM_HOME="$home" "$PROJECT_MODE" prodproj 2>/dev/null)
  [ "$out" = "no-mistakes off" ] || fail "conditional policy did not map to its most rigorous leg (got '$out')"
  err=$(FM_HOME="$home" "$PROJECT_MODE" prodproj 2>&1 >/dev/null)
  [ -z "$err" ] || fail "a registered conditional policy still warned as unknown: $err"

  out=$(FM_HOME="$home" "$PROJECT_MODE" yoloproj 2>/dev/null)
  [ "$out" = "no-mistakes on" ] || fail "conditional policy dropped its +yolo posture (got '$out')"

  out=$(FM_HOME="$home" "$PROJECT_MODE" --raw prodproj 2>/dev/null)
  [ "$out" = "no-mistakes-prod-only off" ] || fail "--raw did not expose the registered annotation (got '$out')"

  out=$(FM_HOME="$home" "$PROJECT_MODE" --raw flatproj 2>/dev/null)
  [ "$out" = "direct-PR off" ] || fail "--raw altered a flat registered mode (got '$out')"

  out=$(FM_HOME="$home" "$PROJECT_MODE" typoproj 2>/dev/null)
  [ "$out" = "no-mistakes off" ] || fail "a typo'd mode no longer falls back to the most rigorous default"
  err=$(FM_HOME="$home" "$PROJECT_MODE" typoproj 2>&1 >/dev/null)
  assert_contains "$err" "unknown mode" "a typo'd registry mode stopped warning"
  pass "fm-project-mode: the conditional policy is accepted, mapped for mechanical callers, and readable raw"
}

test_project_mode_ignores_prose_bullets() {
  local home out err
  home="$TMP_ROOT/project-mode-prose/home"
  mkdir -p "$home/data"
  cat > "$home/data/projects.md" <<'EOF'
- `origin` is upstream
- fork is prajwal-395/firstmate
- Upstream enforces in CI
- Ten fixes have already landed
- myproj [direct-PR] - fixture
- oldproj - legacy style without brackets (added 2024-01-01)
EOF
  out=$(FM_HOME="$home" "$PROJECT_MODE" fork 2>/dev/null)
  [ "$out" = "no-mistakes off" ] || fail "a prose bullet was incorrectly parsed as a project (got '$out')"

  out=$(FM_HOME="$home" "$PROJECT_MODE" oldproj 2>/dev/null)
  [ "$out" = "no-mistakes off" ] || fail "a genuine legacy project was not parsed correctly (got '$out')"

  err=$(FM_HOME="$home" "$PROJECT_MODE" missingproj 2>&1 >/dev/null)
  assert_contains "$err" "myproj" "missing project warning did not list the real project"
  assert_contains "$err" "oldproj" "missing project warning did not list the legacy project"
  assert_not_contains "$err" "fork" "missing project warning listed a prose bullet"
  assert_not_contains "$err" "\`origin\`" "missing project warning listed a prose bullet"
  assert_not_contains "$err" "Upstream" "missing project warning listed a prose bullet"
  assert_not_contains "$err" "Ten" "missing project warning listed a prose bullet"
  pass "fm-project-mode: ignores column-one prose bullets in descriptions"
}

test_project_mode_handles_comma_separated_aliases() {
  local home out err
  home="$TMP_ROOT/project-mode-aliases/home"
  mkdir -p "$home/data"
  cat > "$home/data/projects.md" <<'EOF'
- video-editing-pilot,video_editing_pilot [direct-PR +yolo] - fixture (added 2026-01-01)
- singleproj [local-only] - fixture (added 2026-01-01)
EOF

  out=$(FM_HOME="$home" "$PROJECT_MODE" video-editing-pilot 2>/dev/null)
  [ "$out" = "direct-PR on" ] || fail "comma-separated alias first item not found (got '$out')"

  out=$(FM_HOME="$home" "$PROJECT_MODE" video_editing_pilot 2>/dev/null)
  [ "$out" = "direct-PR on" ] || fail "comma-separated alias second item not found (got '$out')"

  out=$(FM_HOME="$home" "$PROJECT_MODE" singleproj 2>/dev/null)
  [ "$out" = "local-only off" ] || fail "single project parsing failed (got '$out')"

  err=$(FM_HOME="$home" "$PROJECT_MODE" missingproj 2>&1 >/dev/null)
  assert_contains "$err" "video-editing-pilot video_editing_pilot singleproj" "missing project warning did not list all registered names (got '$err')"
  
  pass "fm-project-mode: handles comma-separated aliases and prints registered names on miss"
}


# The brief's four required scope fields are a dispatch gate, not decoration: a
# ship or scout spawn refuses while any is empty, and names it. Emptiness is the
# only refusal - the spawn never judges what a filled field says, so `nothing` in
# "Blocked on" and a one-word answer everywhere else both launch. A brief written
# before the fields existed carries none of the headings and stays dispatchable
# behind one loud warning, so live work never becomes undispatchable.
test_spawn_refuses_a_brief_with_an_empty_scope_field() {
  local rec home proj fakebin out status field n=0
  rec=$(make_home scope)
  IFS='|' read -r home proj fakebin <<EOF
$rec
EOF

  while IFS= read -r field; do
    [ -n "$field" ] || continue
    n=$((n + 1))
    write_scoped_brief "$home" "scope-empty-$n" no-mistakes "$field"
    out=$(run_spawn "$home" "$fakebin" "scope-empty-$n" "$proj" claude --mode no-mistakes --yolo off)
    status=$?
    [ "$status" -ne 0 ] || fail "a ship spawn on an empty '$field' should exit non-zero"
    assert_contains "$out" "empty: $field" "the refusal did not name the empty field '$field'"
    assert_contains "$out" "leaves a required scope field empty" \
      "the refusal did not explain that a required scope field is empty"
    assert_absent "$home/state/scope-empty-$n.meta" \
      "a spawn refused for an empty '$field' still wrote task metadata"
  done <<EOF
$(printf '%s\n' "$FM_BRIEF_SCOPE_FIELDS" | tr '|' '\n')
EOF
  [ "$n" -eq 4 ] || fail "expected four required scope fields, gated $n"

  # A scout brief carries the same fields and the same gate.
  write_scoped_brief "$home" scope-empty-scout "" "Out of scope"
  out=$(run_spawn "$home" "$fakebin" scope-empty-scout "$proj" claude --scout)
  status=$?
  [ "$status" -ne 0 ] || fail "a scout spawn on an empty scope field should exit non-zero"
  assert_contains "$out" "empty: Out of scope" "the scout refusal did not name the empty field"

  # All four answered, including the literal word `nothing`: the scope gate is
  # cleared and the spawn only fails later, at the refusing tmux.
  write_scoped_brief "$home" scope-filled no-mistakes
  assert_grep "nothing" "$home/data/scope-filled/brief.md" \
    "the filled fixture lost its 'nothing' blocked-on answer"
  out=$(run_spawn "$home" "$fakebin" scope-filled "$proj" claude --mode no-mistakes --yolo off)
  assert_not_contains "$out" "leaves a required scope field empty" \
    "a brief answering all four fields was refused"
  assert_not_contains "$out" "carries no scope contract" \
    "a brief carrying the scope contract was reported as predating it"

  # A brief scaffolded before the contract existed: one loud warning, still launches.
  write_brief "$home" scope-legacy no-mistakes
  out=$(run_spawn "$home" "$fakebin" scope-legacy "$proj" claude --mode no-mistakes --yolo off)
  assert_contains "$out" "carries no scope contract" \
    "a brief predating the scope contract launched silently instead of warning"
  assert_not_contains "$out" "leaves a required scope field empty" \
    "a brief predating the scope contract was refused instead of warned about"

  # A secondmate charter carries none of the four and is never gated on them.
  out=$(run_spawn "$home" "$fakebin" scope-sm "$home" --secondmate)
  assert_not_contains "$out" "leaves a required scope field empty" \
    "a secondmate spawn was gated on task-shaped scope fields a charter never carries"
  assert_not_contains "$out" "carries no scope contract" \
    "a secondmate spawn warned about a scope contract a charter never carries"

  pass "fm-spawn: a ship or scout spawn refuses an empty required scope field, accepts every answered one, and warns once on a pre-contract brief"
}

test_ship_spawn_requires_a_valid_delivery_contract
test_scout_and_secondmate_refuse_delivery_flags
test_spawn_refuses_a_brief_mode_mismatch
test_spawn_refuses_a_brief_with_an_empty_scope_field
test_spawn_notices_a_rigor_downgrade_against_the_registry
test_scout_records_no_delivery_posture
test_promote_requires_and_records_the_delivery_contract
test_project_mode_maps_the_conditional_policy
test_project_mode_ignores_prose_bullets
test_project_mode_handles_comma_separated_aliases
echo "# all fm-task-delivery tests passed"
