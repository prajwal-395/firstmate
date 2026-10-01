#!/usr/bin/env bash
# fm-opencode-ladder-lib.sh - the opencode free-then-Codex-then-Go ladder.
# Usage: . bin/fm-opencode-ladder-lib.sh
# Sourced by bin/fm-spawn.sh. Sourcing has no side effects beyond the rung
# constants below.
#
# THE POLICY. The standing rule for opencode dispatch is a fixed three-rung
# ladder: free, Codex Plus (gpt-6-luna at the configured Codex profile effort,
# falling back to xhigh), then paid Go. Work runs
# on free first; new spawns fall through only when the preceding rung is
# exhausted. The rungs are stated once, here, as constants - there is
# no config order to derive, because the order is fixed by economics rather
# than ranked by the captain.
#
# QUOTA SOURCES. OpenCode free is reactive because quota-axi has no free row:
# bin/fm-opencode-retry.sh records the vendor retry horizon and the pane-text
# detector covers an idle refusal. Codex and OpenCode Go use fresh known zero
# quota rows only when quota-axi supplies a valid reset timestamp; Go also
# accepts reactive vendor evidence. All durable reset records use the same
# state/.opencode-cap-<rung> store and become eligible exactly at reset.
#
# RESET LIFECYCLE. Vendor retry `next` and quota-axi `resetsAt` are written as
# epoch milliseconds by bin/fm-opencode-retry.sh. A cap stops applying at that
# exact time, so new spawns return to the first rung without a manual clear.
#
# RUNNING WORKERS ARE OWNED BY THE DESCENT, NOT LEFT IN PLACE. This file
# routes the next spawn only. bin/fm-opencode-descent-lib.sh re-evaluates a
# lane already running: a lane recorded on free with a proven cap relaunches
# onto the next uncapped rung through bin/fm-control.sh relaunch, keeping its
# worktree, branch, commits, and brief and carrying a handoff note, while its
# conversation ends with the parked session. There is still no verified
# in-session model switch for opencode (agy's guarded /model walk does not
# transfer), which is why the move is a control-plane relaunch rather than a
# live tier switch. This file routes the next spawn only, and says so.
#
# THE TWO DIRECTIONS ARE NOT SYMMETRIC. Running workers descend when capped,
# but never climb back to a newly reset earlier rung. New spawns always start
# from the first rung and use the current cap records.
#
# FAILURE DIRECTION. Unknown or stale quota data never counts as exhaustion.
# A free cap may fall through despite an absent model binding; a destination
# rung with its own unexpired cap is skipped. If all three are capped, dispatch
# is refused with every rung and reset time named.
#
# THE OVERRIDE. FM_OPENCODE_LADDER_OVERRIDE, set to a non-empty reason, holds
# a free request on free past a proven cap and prints that it did. It is an
# environment variable rather than a flag deliberately, matching the agy
# ladder's override: dispatch profiles carry only harness, model, and effort,
# so holding free stays a deliberate act at the command line.
# FM_OPENCODE_LADDER=off disables the whole gate: the requested model (or free
# when none was requested) passes through unchanged, with a notice naming the
# kill-switch so a bypass never looks like a decision.

# The governed OpenCode models and final Codex Plus model.
FM_OPENCODE_LADDER_FREE='opencode/muse-spark-1.3-contributor-free'
FM_OPENCODE_LADDER_GO='opencode-go/muse-spark-1.3-contributor'
FM_OPENCODE_LADDER_PLUS_MODEL='gpt-6-luna'

# fm_opencode_ladder_plus_effort [<config-dir>]
# Use the first Codex Plus profile's declared effort from the default array.
# The normal config validator owns malformed files; an absent, unreadable, or
# unrecognized value keeps this rung usable at xhigh.
fm_opencode_ladder_plus_effort() {
  local config_dir=${1:-${FM_CONFIG_OVERRIDE:-}} config_file effort
  if [ -z "$config_dir" ] && [ -n "${FM_HOME:-}" ]; then
    config_dir="$FM_HOME/config"
  fi
  effort=
  if [ -n "$config_dir" ] && [ -f "$config_dir/crew-dispatch.json" ] && command -v jq >/dev/null 2>&1; then
    config_file="$config_dir/crew-dispatch.json"
    effort=$(jq -r --arg model "$FM_OPENCODE_LADDER_PLUS_MODEL" '
      [
        .default
        | if type == "array" then .[] else . end
        | select(type == "object" and .harness == "codex" and .model == $model)
      ]
      | .[0].effort // empty
    ' "$config_file" 2>/dev/null) || effort=
  fi
  case "$effort" in
    low|medium|high|xhigh|max) printf '%s\n' "$effort" ;;
    *) printf '%s\n' xhigh ;;
  esac
}

# The rung keys. `free`, `go`, and `plus` are the names rung-scoped record
# files are keyed on (state/.opencode-cap-<rung>).
# This is the SINGLE place the rung names live: bin/fm-opencode-retry.sh
# validates record-cap/check-cap/verdict-cap against these values by
# reading them from this file (never by retyping them), and the gate below
# queries through them, so a rename here cannot leave an unreadable record
# behind the way `.opencode-cap-opencode` was left on 2026-09-22.
FM_OPENCODE_LADDER_FREE_RUNG='free'
# shellcheck disable=SC2034 # GO_RUNG is not expanded below: the gate only ever
# queries the free rung, but `go` stays an accepted record key so a proven Go
# cap can be preserved and shown. bin/fm-opencode-retry.sh reads this line as
# part of the accepted set - that static read is the use.
FM_OPENCODE_LADDER_GO_RUNG='go'
# shellcheck disable=SC2034 # Static consumer in fm-opencode-retry.sh reads this rung name.
FM_OPENCODE_LADDER_PLUS_RUNG='plus'

# quota-axi exhaustion is deliberately strict: fresh known zero availability
# plus a valid future reset records a durable cap. Unknown or stale readings do
# not cap a rung.
# Refresh predictive rung records from one quota-axi snapshot. The record-cap
# helper remains the only durable store; only fresh, known zero availability
# with a valid future reset is authoritative enough to write one.
fm_opencode_ladder_quota_caps() {  # <state-dir>
  local state_dir=$1 report provider reset_s reset_ms rung
  [ -d "$state_dir" ] || return 0
  command -v quota-axi >/dev/null 2>&1 || return 0
  command -v jq >/dev/null 2>&1 || return 0
  report=$(quota-axi --json 2>/dev/null) || return 0
  for provider in codex opencode-go; do
    reset_s=$(printf '%s' "$report" | jq -r --arg provider "$provider" '
      .providers[]? | select(.provider == $provider and (.state.stale == false or .state.status == "fresh"))
      | . as $p | .quotaSemantics.effectiveAvailability[]?
      | select(.scope == "all_models" and .status == "known" and .effectivePercentRemaining == 0)
      | (if (.resetsAt | type) == "string" then .resetsAt
         else ([.limitingWindowIds[]? as $id | $p.windows[]? | select(.id == $id) | .resetsAt]
               | map(select(type == "string")) | max // empty) end)
      | try fromdateiso8601 catch empty' 2>/dev/null | head -n 1)
    case "$reset_s" in ''|*[!0-9]*) continue ;; esac
    [ "$reset_s" -gt "$(date +%s)" ] || continue
    reset_ms=$((reset_s * 1000))
    rung=plus
    [ "$provider" = opencode-go ] && rung=go
    "$_FM_OPENCODE_LADDER_RETRY" record-cap "$state_dir" "$rung" "$reset_ms" 2>/dev/null || true
  done
}

fm_opencode_ladder_go_reactive_capped() {  # <state-dir>
  local state_dir=$1 f id out status horizon model go_bare model_bare
  [ -d "$state_dir" ] || return 1
  if out=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" go 2>/dev/null); then
    case "$out" in *'status=blocked'*) return 0 ;; esac
  fi
  go_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_GO")
  for f in "$state_dir"/*.opencode-retry; do
    [ -e "$f" ] || continue
    id=${f##*/}; id=${id%.opencode-retry}
    out=$("$_FM_OPENCODE_LADDER_RETRY" check "$state_dir" "$id" 2>/dev/null) || continue
    status=''; horizon=''; model=''
    for word in $out; do
      case "$word" in status=*) status=${word#status=} ;; horizon_s=*) horizon=${word#horizon_s=} ;; model=*) model=${word#model=} ;; esac
    done
    [ "$status" = blocked ] || continue
    case "$horizon" in ''|*[!0-9]*) continue ;; esac
    model_bare=$(fm_opencode_ladder_bare_model "$model")
    if [ "$model_bare" = "$go_bare" ]; then
      "$_FM_OPENCODE_LADDER_RETRY" record-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" "$((($(date +%s) + horizon) * 1000))" 2>/dev/null || true
      return 0
    fi
  done
  return 1
}

fm_opencode_ladder_plus_capped() {  # <state-dir>
  local state_dir=$1 out status horizon model
  out=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" 2>/dev/null) || return 1
  case "$out" in *'status=blocked'*) return 0 ;; esac
  return 1
}

# Resolve this library's own directory so it can name the retry-evidence
# helper whether it was sourced by a bin/ script or directly by a test.
_FM_OPENCODE_LADDER_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null)" || _FM_OPENCODE_LADDER_LIB_DIR="."
_FM_OPENCODE_LADDER_RETRY="$_FM_OPENCODE_LADDER_LIB_DIR/fm-opencode-retry.sh"
# Backend capture plus the meta readers live in the backend library; callers
# on the dispatch path (bin/fm-spawn.sh) load it before this file, so the
# guarded source below fires only when this file stands alone. Without it the
# sidecar half below still works and the pane-text half degrades to unknown.
# shellcheck source=/dev/null
if ! declare -f fm_backend_capture >/dev/null 2>&1; then
  . "$_FM_OPENCODE_LADDER_LIB_DIR/fm-backend.sh"
fi

# Lines of rendered pane tail handed to the detector for the idle shape.
# bin/fm-opencode-retry.sh owns the phrases; this is only the capture width.
FM_OPENCODE_CAP_TEXT_LINES=${FM_OPENCODE_CAP_TEXT_LINES:-60}

# fm_opencode_ladder_pane_file: capture <id>'s pane tail into a temp file and
# print its path (the caller removes it). Prints nothing and returns 1 when
# the lane has no readable meta, no backend target, no capture primitive, or
# an empty capture: all of those are unknown, never open, and the gate treats
# unknown as dispatch-free (see fm_opencode_ladder_free_capped).
fm_opencode_ladder_pane_file() {  # <state-dir> <id>
  local state_dir=$1 id=$2 meta backend target cap_file
  meta="$state_dir/$id.meta"
  [ -f "$meta" ] || return 1
  command -v fm_backend_capture >/dev/null 2>&1 || return 1
  command -v fm_backend_of_meta >/dev/null 2>&1 || return 1
  command -v fm_backend_target_of_meta >/dev/null 2>&1 || return 1
  backend=$(fm_backend_of_meta "$meta" 2>/dev/null) || return 1
  target=$(fm_backend_target_of_meta "$meta" 2>/dev/null) || return 1
  [ -n "$target" ] || return 1
  cap_file=$(mktemp "${TMPDIR:-/tmp}/fm-opencode-cap.XXXXXX") || return 1
  if ! fm_backend_capture "$backend" "$target" "$FM_OPENCODE_CAP_TEXT_LINES" "fm-$id" \
    > "$cap_file" 2>/dev/null; then
    rm -f "$cap_file"
    return 1
  fi
  [ -s "$cap_file" ] || { rm -f "$cap_file"; return 1; }
  printf '%s\n' "$cap_file"
}

# fm_opencode_ladder_horizon_human: compact "~22h34m" rendering of <seconds>
# for notices. Presentation only; the threshold contract stays owned by
# bin/fm-opencode-retry.sh.
fm_opencode_ladder_horizon_human() {  # <seconds>
  local s=$1 h m
  case "$s" in ''|*[!0-9]*) printf '~?'; return ;; esac
  if [ "$s" -ge 3600 ]; then
    h=$((s / 3600)); m=$(((s % 3600) / 60))
    printf '~%sh%sm' "$h" "$m"
  elif [ "$s" -ge 60 ]; then
    m=$((s / 60)); s=$((s % 60))
    printf '~%sm%ss' "$m" "$s"
  else
    printf '~%ss' "$s"
  fi
}

# fm_opencode_ladder_bare_model: <model> without its provider prefix.
# The retry sidecar's model comes from OpenCode's own event stream (model.id),
# which carries no provider prefix (`muse-spark-1.3-contributor-free`), while
# the rung constants above carry one (`opencode/...`). Every evidence
# comparison normalises both sides through here, so either form binds the
# right rung. The suffix is what separates the tiers once the prefix is gone -
# free stays `...-free` and Go stays bare - so stripping never collapses them.
fm_opencode_ladder_bare_model() {  # <model>
  case "$1" in
    */*) printf '%s' "${1##*/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# fm_opencode_ladder_free_capped: is the free tier proven exhausted on the
# CURRENT evidence in <state-dir>? Prints one line and returns 0 when some
# lane's sidecar classifies quota-scale (`blocked`) through
# bin/fm-opencode-retry.sh and is bound to the free tier - or bound to no tier
# at all, per the stated failure direction - OR when a descent preserved a
# rung-scoped free cap (bin/fm-opencode-retry.sh record-cap) that has not
# expired - OR when some lane's pane tail
# carries the cap verbatim with no blocking sidecar (the idle shape: the lane
# took the provider error and ended its turn, so the sidecar is gone and the
# rendered text is the only evidence left; bin/fm-opencode-retry.sh owns the
# phrases). Prints nothing and returns 1 on every other path: no sidecars,
# transient backoffs only, expired caps only, a cap bound to the Go tier only,
# clean pane tails, uncapturable panes, an unreadable helper, or an unreadable
# state dir. Absence of evidence is never exhaustion: unknown (no sidecar and
# no usable capture) dispatches free, silently. That allow-on-unknown is the
# captain's standing position - missing evidence must not stall the fleet -
# and it is stated here rather than hidden in the detector so the choice stays
# visible where it licenses the mis-route it risks.
#
# The printed line is "free-capped horizon_s=<s|unknown> bound=<free|unbound>".
# Text-only evidence carries no trustworthy horizon (an idle lane's bracketed
# retry duration may never fire), so it reports horizon_s=unknown and the
# model notice owns that. A text cap on a lane recorded on the free tier binds
# free - the phrase names Go as the remedy, so it is the free tier that is
# exhausted; anywhere else it binds unbound and falls through on the same bias.
fm_opencode_ladder_free_capped() {  # <state-dir>
  local state_dir=$1 f id out word status='' horizon='' model=''
  local free_horizon='' unbound_horizon=''
  local cap_out cap_status='' cap_horizon=''
  local text_free='' text_unbound=''
  local meta harness lane_model lane_bare cap_file
  local free_bare model_bare
  free_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_FREE")
  [ -n "$state_dir" ] && [ -d "$state_dir" ] || return 1
  [ -x "$_FM_OPENCODE_LADDER_RETRY" ] || return 1
  for f in "$state_dir"/*.opencode-retry; do
    [ -e "$f" ] || continue
    id=${f##*/}
    id=${id%.opencode-retry}
    out=$("$_FM_OPENCODE_LADDER_RETRY" check "$state_dir" "$id" 2>/dev/null) || continue
    status=''; horizon=''; model=''
    for word in $out; do
      case "$word" in
        status=*) status=${word#status=} ;;
        horizon_s=*) horizon=${word#horizon_s=} ;;
        model=*) model=${word#model=} ;;
      esac
    done
    [ "$status" = blocked ] || continue
    case "$horizon" in ''|*[!0-9]*) continue ;; esac
    model_bare=$(fm_opencode_ladder_bare_model "$model")
    if [ "$model_bare" = "$free_bare" ]; then
      if [ -z "$free_horizon" ] || [ "$horizon" -gt "$free_horizon" ]; then
        free_horizon=$horizon
      fi
    elif [ -z "$model" ]; then
      if [ -z "$unbound_horizon" ] || [ "$horizon" -gt "$unbound_horizon" ]; then
        unbound_horizon=$horizon
      fi
    fi
    # A cap bound to any other model (notably the Go tier) says nothing about
    # free and is skipped, not counted.
  done
  # The rung-scoped record a descent preserved past its task's own cleanup
  # (bin/fm-opencode-retry.sh record-cap): the discovering lane may be
  # relaunched or torn down, but the vendor horizon still binds the rung.
  # Consulted after the per-task sidecars and before the pane-text scan - a
  # live sidecar speaks for itself, while text capture costs a backend read.
  if [ -z "$free_horizon" ]; then
    if cap_out=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_FREE_RUNG" 2>/dev/null); then
      cap_status=''; cap_horizon=''
      for word in $cap_out; do
        case "$word" in
          status=*) cap_status=${word#status=} ;;
          horizon_s=*) cap_horizon=${word#horizon_s=} ;;
        esac
      done
      if [ "$cap_status" = blocked ]; then
        case "$cap_horizon" in ''|*[!0-9]*) : ;; *) free_horizon=$cap_horizon ;; esac
      fi
    fi
  fi
  # The idle shape, per recorded lane: a blocking sidecar already counted above
  # stays counted; every other opencode lane gets its pane tail scanned. A lane
  # whose capture fails is unknown and skipped, never counted.
  if [ -z "$free_horizon" ]; then
    for meta in "$state_dir"/*.meta; do
      [ -e "$meta" ] || continue
      id=$(basename "$meta" .meta)
      case "$id" in ''|*[!A-Za-z0-9._-]*) continue ;; esac
      harness=$(fm_meta_get "$meta" harness 2>/dev/null) || continue
      case "$harness" in opencode*) ;; *) continue ;; esac
      # A quota-scale sidecar already counted above stays counted; a merely
      # transient one does not suppress the text scan (for an idle lane the
      # pane text wins over a short-horizon sidecar, as the descent holds).
      if out=$("$_FM_OPENCODE_LADDER_RETRY" check "$state_dir" "$id" 2>/dev/null); then
        case "$out" in status=blocked*) continue ;; esac
      fi
      cap_file=$(fm_opencode_ladder_pane_file "$state_dir" "$id" 2>/dev/null) || continue
      if "$_FM_OPENCODE_LADDER_RETRY" scan-text --file "$cap_file" >/dev/null 2>&1; then
        lane_model=$(fm_meta_get "$meta" model 2>/dev/null) || lane_model=''
        lane_bare=$(fm_opencode_ladder_bare_model "$lane_model")
        if [ "$lane_bare" = "$free_bare" ]; then
          text_free=1
        else
          text_unbound=1
        fi
      fi
      rm -f "$cap_file"
      [ -z "$text_free" ] || break
    done
  fi
  if [ -n "$free_horizon" ]; then
    printf 'free-capped horizon_s=%s bound=free\n' "$free_horizon"
    return 0
  fi
  if [ -n "$text_free" ]; then
    printf 'free-capped horizon_s=unknown bound=free\n'
    return 0
  fi
  if [ -n "$unbound_horizon" ]; then
    printf 'free-capped horizon_s=%s bound=unbound\n' "$unbound_horizon"
    return 0
  fi
  if [ -n "$text_unbound" ]; then
    printf 'free-capped horizon_s=unknown bound=unbound\n'
    return 0
  fi
  return 1
}

# fm_opencode_ladder_model: the model id a launch with <requested> should run.
# Prints exactly one line - the effective model id - on stdout. All three
# governed requests pass through the same ordered cap gate.
fm_opencode_ladder_model() {  # <requested> <state-dir>
  local requested=${1:-} state_dir=${2:-} cap='' horizon='' bound=''
  local word free_capped=0 plus_capped=0 go_capped=0
  if [ -z "$requested" ] || [ "$requested" = default ]; then
    requested=$FM_OPENCODE_LADDER_FREE
  fi
  if [ "${FM_OPENCODE_LADDER:-on}" = off ]; then
    printf '%s\n' "$requested"
    printf 'notice: opencode ladder disabled by FM_OPENCODE_LADDER=off - launching on %s unchecked against the free cap\n' \
      "$requested" >&2
    return 0
  fi
  case "$requested" in
    "$FM_OPENCODE_LADDER_FREE"|"$FM_OPENCODE_LADDER_GO"|"$FM_OPENCODE_LADDER_PLUS_MODEL") ;;
    *)
      printf '%s\n' "$requested"
      printf 'notice: opencode ladder not applied: model %s is outside the governed ladder (%s, %s, %s)\n' \
        "$requested" "$FM_OPENCODE_LADDER_FREE" "$FM_OPENCODE_LADDER_PLUS_MODEL" "$FM_OPENCODE_LADDER_GO" >&2
      return 0 ;;
  esac
  fm_opencode_ladder_quota_caps "$state_dir"
  if [ "$requested" = "$FM_OPENCODE_LADDER_GO" ]; then
    local explicit_go_cap
    explicit_go_cap=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" 2>/dev/null) || explicit_go_cap=
    case "$explicit_go_cap" in *'status=blocked'*)
      printf 'error: requested Go rung is capped until %s\n' "$(printf '%s\n' "$explicit_go_cap" | sed -n 's/.*reset_at=//p')" >&2
      return 1 ;;
    esac
    printf '%s\n' "$FM_OPENCODE_LADDER_GO"
    return 0
  fi
  if [ "$requested" = "$FM_OPENCODE_LADDER_PLUS_MODEL" ]; then
    local explicit_plus_cap explicit_go_cap
    explicit_plus_cap=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" 2>/dev/null) || explicit_plus_cap=
    case "$explicit_plus_cap" in *'status=blocked'*)
      explicit_go_cap=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" 2>/dev/null) || explicit_go_cap=
      case "$explicit_go_cap" in *'status=blocked'*) printf 'error: requested Codex Plus and Go rungs are capped\n' >&2; return 1 ;; esac
      printf '%s\n' "$FM_OPENCODE_LADDER_GO"
      printf 'notice: opencode ladder: Codex Plus is capped; dispatching on Go %s\n' "$FM_OPENCODE_LADDER_GO" >&2
      return 0 ;;
    esac
    printf '%s\n' "$FM_OPENCODE_LADDER_PLUS_MODEL"
    return 0
  fi
  if cap=$(fm_opencode_ladder_free_capped "$state_dir" 2>/dev/null); then
    free_capped=1
    for word in $cap; do case "$word" in horizon_s=*) horizon=${word#horizon_s=} ;; bound=*) bound=${word#bound=} ;; esac; done
    case "$horizon" in ''|*[!0-9]*) : ;; *)
      "$_FM_OPENCODE_LADDER_RETRY" record-cap "$state_dir" "$FM_OPENCODE_LADDER_FREE_RUNG" "$((($(date +%s) + horizon) * 1000))" 2>/dev/null || true ;;
    esac
  fi
  fm_opencode_ladder_plus_capped "$state_dir" && plus_capped=1
  { fm_opencode_ladder_go_reactive_capped "$state_dir" ||
    [ "$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" 2>/dev/null | sed -n 's/^status=//p')" = blocked ]; } && go_capped=1
  [ "$free_capped" -eq 1 ] || { printf '%s\n' "$FM_OPENCODE_LADDER_FREE"; return 0; }
  if [ "$plus_capped" -eq 1 ] && [ "$go_capped" -eq 1 ]; then
    local free_until plus_until go_until
    free_until=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_FREE_RUNG" 2>/dev/null | sed -n 's/.*reset_at=//p')
    plus_until=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" 2>/dev/null | sed -n 's/.*reset_at=//p')
    go_until=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" 2>/dev/null | sed -n 's/.*reset_at=//p')
    printf 'error: opencode ladder exhausted: free capped until %s; Codex Plus capped until %s; Go capped until %s\n' \
      "${free_until:-unknown}" "${plus_until:-unknown}" "${go_until:-unknown}" >&2
    return 1
  fi
  if [ -n "${FM_OPENCODE_LADDER_OVERRIDE:-}" ]; then
    printf '%s\n' "$FM_OPENCODE_LADDER_FREE"
    printf 'notice: opencode ladder OVERRIDDEN by FM_OPENCODE_LADDER_OVERRIDE=%s - holding free past a proven cap\n' "$FM_OPENCODE_LADDER_OVERRIDE" >&2
    return 0
  fi
  if [ "$plus_capped" -eq 0 ]; then
    printf '%s\n' "$FM_OPENCODE_LADDER_PLUS_MODEL"
    local plus_effort
    plus_effort=$(fm_opencode_ladder_plus_effort)
    if [ "$bound" = unbound ]; then
      printf 'notice: opencode ladder: free may be capped with no model binding; dispatching on Codex Plus (%s, %s effort)\n' "$FM_OPENCODE_LADDER_PLUS_MODEL" "$plus_effort" >&2
    elif [ "$horizon" = unknown ]; then
      printf 'notice: opencode ladder: free cap is showing in a lane pane with no retry horizon on record; dispatching on Codex Plus (%s, %s effort)\n' "$FM_OPENCODE_LADDER_PLUS_MODEL" "$plus_effort" >&2
    else
      printf 'notice: opencode ladder: free is capped; dispatching on Codex Plus (%s, %s effort)\n' "$FM_OPENCODE_LADDER_PLUS_MODEL" "$plus_effort" >&2
    fi
    return 0
  fi
  if [ "$go_capped" -eq 0 ]; then
    printf '%s\n' "$FM_OPENCODE_LADDER_GO"
    printf 'notice: opencode ladder: free and Codex Plus are capped; dispatching on Go %s\n' "$FM_OPENCODE_LADDER_GO" >&2
    return 0
  fi
}
