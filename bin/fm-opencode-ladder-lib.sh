#!/usr/bin/env bash
# fm-opencode-ladder-lib.sh - the opencode Go-then-Codex Plus ladder.
# Usage: . bin/fm-opencode-ladder-lib.sh
# Sourced by bin/fm-spawn.sh. Sourcing has no side effects beyond the rung
# constants below.
#
# THE POLICY. The standing rule for opencode dispatch is Codex Plus
# (gpt-6-luna at the configured Codex profile effort, falling back to
# xhigh) first, then the paid Go tier
# (muse-spark-1.3-contributor, on Pi, at the configured workspace pin).
# Work runs on Codex Plus first; new spawns fall
# through only when the preceding rung is exhausted. Order is exhaustion,
# not load balancing. The Go tier has one member per configured workspace:
# the main workspace always, plus the second workspace named in
# config/opencode-second-org when one is configured
# (bin/fm-opencode-second-lib.sh); without one the tier is main alone. The
# tier drains the plan whose OVERALL (monthly) quota resets soonest first,
# and only when that priority plan hits a shorter limit (the 5-hour window,
# then the weekly window) does traffic switch to the other plan until that
# limit resets, then switch back - so the least quota is lost unused at each
# full reset. The choice among uncapped members is owned in full by
# fm_opencode_ladder_pick_go_workspace below. A proven cap excludes first;
# window timing only chooses among workspaces that can still serve, and an
# unstarted 5-hour window on an idle plan never counts as resetting soonest
# because idle windows exclude nothing. The rungs are stated once, here, as
# constants - there is no config order to derive, because the tier order is
# fixed by economics rather than ranked by the captain, while the workspace
# inside the Go tier is ranked by billing timing, never by fixed order.
#
# QUOTA SOURCES. The Go tier uses fresh exhausted quota rows: runway
# exhausted_now first, then a fresh known zero reading, mirroring the typed
# dispatch resolver. Codex Plus uses the same predictive shape. An exhausted row without a parseable future reset still records
# its rung cap with a conservative bounded horizon rather than leaving the
# rung open; the main
# Go workspace also accepts reactive vendor evidence. Workspace-keyed Go
# rows feed their matching rung, while an unlabelled row retains its
# long-standing main-workspace meaning. Reactive evidence carries a
# horizon but never the window that caused it, so a second-workspace cap is
# undifferentiated: the switch-back horizon IS that limit's reset whatever
# window it was. Each plan's monthly reset comes from the gitignored
# config/opencode-go-resets (one `<main|secondary> <ISO-8601>` line per plan,
# projected forward by calendar month) with the matching fresh quota-axi
# monthly window filling either plan's gap when that file is silent; a plan
# with neither source reads as unknown. All durable reset records use
# the same state/.opencode-cap-<rung> store and become eligible exactly at
# reset. Each Go workspace keeps its own cap record and reset time, so
# capping one never marks the other capped.
#
# RESET LIFECYCLE. Vendor retry `next` and quota-axi `resetsAt` are written as
# epoch milliseconds by bin/fm-opencode-retry.sh. A cap stops applying at that
# exact time, so new spawns return to the first rung without a manual clear.
#
# RUNNING WORKERS ARE OWNED BY THE DESCENT, NOT LEFT IN PLACE. This file
# routes the next spawn only. bin/fm-opencode-descent-lib.sh re-evaluates a
# lane already running: a lane recorded on a rung with a proven cap relaunches
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
# A Plus cap may fall through despite an absent model binding; a destination
# rung with its own unexpired cap is skipped. If every rung is capped -
# Codex Plus and the whole Go tier (main capped plus second capped,
# or main capped with no second workspace) - return 3 marks proven
# exhaustion so fm-spawn can try a declared default fallback; when
# none exists, dispatch is refused with every rung and reset time named.
# The exhausted-ladder fallback is reachable only there, never
# while either Go workspace can still serve.
#
# THE OVERRIDE. FM_OPENCODE_LADDER_OVERRIDE, set to a non-empty reason, holds
# a Plus request on Codex Plus past a proven cap and prints that it did. It is an
# environment variable rather than a flag deliberately, matching the agy
# ladder's override: dispatch profiles carry only harness, model, and effort,
# so holding Plus stays a deliberate act at the command line.
# FM_OPENCODE_LADDER=off disables the whole gate: the requested model (or Codex
# Plus when none was requested) passes through unchanged, with a notice naming the
# kill-switch so a bypass never looks like a decision.

# The governed OpenCode model and Codex Plus model. The retired free rung
# (opencode-go/minimax-m3.1-flash-free, deprecated by OpenCode on 2026-10-22)
# is intentionally absent: no rung names it.
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
  if [ -n "$config_dir" ] && [ -f "$config_dir/dispatch-policy.json" ] && command -v jq >/dev/null 2>&1; then
    config_file="$config_dir/dispatch-policy.json"
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

# The rung keys. `go`, `plus`, and `go-second` are the names rung-scoped record
# files are keyed on (state/.opencode-cap-<rung>). `free` stays an accepted
# record key so rung records written before the free rung retired on
# 2026-10-22 remain readable; the gate below never queries or writes it.
# This is the SINGLE place the rung names live: bin/fm-opencode-retry.sh
# validates record-cap/check-cap/verdict-cap against these values by
# reading them from this file (never by retyping them), and the gate below
# queries through them, so a rename here cannot leave an unreadable record
# behind the way `.opencode-cap-opencode` was left on 2026-09-22.
# shellcheck disable=SC2034 # Static consumer in fm-opencode-retry.sh reads this rung name.
FM_OPENCODE_LADDER_FREE_RUNG='free'
# shellcheck disable=SC2034 # GO_RUNG and FREE_RUNG are not expanded below: the gate queries
# go, plus, and go-second, while `free` stays accepted only so pre-retirement
# records remain readable. bin/fm-opencode-retry.sh reads these lines as
# part of the accepted set - those static reads are the use.
FM_OPENCODE_LADDER_GO_RUNG='go'
# shellcheck disable=SC2034 # Static consumer in fm-opencode-retry.sh reads this rung name.
FM_OPENCODE_LADDER_PLUS_RUNG='plus'
# The second Go workspace rung. `go-second` keeps its own cap record
# (state/.opencode-cap-go-second), written only from reactive vendor evidence
# on lanes the task meta pins to that workspace. bin/fm-opencode-retry.sh
# reads this line as part of the accepted set - that static read is the use.
# shellcheck disable=SC2034
FM_OPENCODE_LADDER_GO_SECOND_RUNG='go-second'

# quota-axi availability is deliberately strict: a fresh exhausted reading
# (runway exhausted_now first, then a fresh known zero reading, mirroring the
# typed dispatch resolver) records a durable cap, while a fresh known positive
# reading retires it. Unknown or stale readings leave the rung record alone.
# Refresh predictive rung records from one quota-axi snapshot. The retry helper
# remains the only durable store and owns both recording and retiring caps.
# Row selection is account-aware: Codex Plus reads the codex-home row (falling
# back to default, then to an unlabelled schema-5 row), each Go workspace reads
# its own opencode-go row. A healthy second account never masks the serving row's
# exhaustion. An exhausted row without a parseable future reset still records
# the cap with a conservative bounded horizon (FM_OPENCODE_CODEX_LIMIT_FALLBACK_SECS,
# default 3600) rather than leaving the rung looking open; the notice marks the
# horizon as a bound rather than a reset.
fm_opencode_ladder_quota_cap_one() {  # <state-dir> <report> <rung> <provider> <workspace> <model> <fallback-s>
  local state_dir=$1 report=$2 rung=$3 provider=$4 workspace=$5 model=$6 fallback_s=$7
  local row reset_s reset_ms bare now_s
  bare=$(fm_opencode_ladder_bare_model "$model")
  row=$(printf '%s' "$report" | jq -c --arg provider "$provider" --arg workspace "$workspace" --arg bare "$bare" '
    [.providers[]? | select(.provider == $provider and (.state.stale == false or .state.status == "fresh"))] as $rows
    | (if $provider == "codex" then
         ([$rows[] | select(.accountKey == "codex-home")] | first)
         // ([$rows[] | select(.accountKey == "default")] | first)
         // ([$rows[] | select(has("accountKey") | not)] | first)
       elif $provider == "opencode-go" and $workspace == "second" then
         ([$rows[] | select(.accountKey == "second")] | first)
       elif $provider == "opencode-go" then
         ([$rows[] | select(.accountKey == "main")] | first)
         // ([$rows[] | select(has("accountKey") | not)] | first)
       else
         ([$rows[] | select(.accountKey == "default")] | first)
         // ([$rows[] | select(has("accountKey") | not)] | first)
         // ($rows | first)
       end) as $row
    | $row // empty
    | . as $p
    | {windows: ($p.windows // []),
       avail: ([$p.quotaSemantics.effectiveAvailability[]?
                 | select(.scope == "all_models" and .status == "known"
                          and (.effectivePercentRemaining | type == "number" and . > 0))] | length),
       bad: (([$p.quotaSemantics.effectiveAvailability[]?
                | select(.scope == "all_models" or .scope == "all_products"
                         or ((($bare // "") != "") and (.scope == ("model:" + $bare) or .scope == ("product:" + $bare))))
                | select((.runway.status // "") == "exhausted_now")] | first)
              // ([$p.quotaSemantics.effectiveAvailability[]?
                | select(.scope == "all_models" or .scope == "all_products"
                         or ((($bare // "") != "") and (.scope == ("model:" + $bare) or .scope == ("product:" + $bare))))
                | select(.status == "known" and (.effectivePercentRemaining | type) == "number"
                         and .effectivePercentRemaining <= 0)] | first))} as $v
    | {has_row: true, avail: $v.avail, bad: $v.bad,
       reset: (if $v.bad == null then null
               else ($v.bad
                     | (if (.resetsAt | type) == "string" then .resetsAt
                        else ([.limitingWindowIds[]? as $id | $v.windows[]? | select(.id == $id) | .resetsAt]
                              | map(select(type == "string")) | max // null) end)) end)}' 2>/dev/null) || return 0
  [ -n "$row" ] || return 0
  if [ "$(printf '%s' "$row" | jq -r '.bad != null' 2>/dev/null)" = true ]; then
    reset_s=$(printf '%s' "$row" | jq -r '.reset | try fromdateiso8601 catch empty' 2>/dev/null)
    case "$reset_s" in ''|*[!0-9]*) reset_s= ;; esac
    now_s=$(date +%s)
    if [ -z "$reset_s" ] || [ "$reset_s" -le "$now_s" ]; then
      reset_s=$((now_s + fallback_s))
      printf 'notice: opencode ladder: %s quota is exhausted with no parseable reset; recording a conservative %ss cap instead of leaving the rung open\n' \
        "$rung" "$fallback_s" >&2
    fi
    reset_ms=$((reset_s * 1000))
    "$_FM_OPENCODE_LADDER_RETRY" record-cap "$state_dir" "$rung" "$reset_ms" 2>/dev/null || true
    return 0
  fi
  if [ "$(printf '%s' "$row" | jq -r '(.avail // 0) > 0' 2>/dev/null)" = true ]; then
    "$_FM_OPENCODE_LADDER_RETRY" clear-cap "$state_dir" "$rung" 2>/dev/null || true
  fi
}

fm_opencode_ladder_quota_caps() {  # <state-dir>
  local state_dir=$1 report fallback_s
  [ -d "$state_dir" ] || return 0
  command -v quota-axi >/dev/null 2>&1 || return 0
  command -v jq >/dev/null 2>&1 || return 0
  report=$(quota-axi --json 2>/dev/null) || return 0
  printf '%s' "$report" | jq -e '.providers | type == "array"' >/dev/null 2>&1 || return 0
  fallback_s=${FM_OPENCODE_CODEX_LIMIT_FALLBACK_SECS:-3600}
  case "$fallback_s" in ''|*[!0-9]*) fallback_s=3600 ;; esac
  fm_opencode_ladder_quota_cap_one "$state_dir" "$report" plus codex main "$FM_OPENCODE_LADDER_PLUS_MODEL" "$fallback_s"
  fm_opencode_ladder_quota_cap_one "$state_dir" "$report" go opencode-go main "$FM_OPENCODE_LADDER_GO" "$fallback_s"
  fm_opencode_ladder_quota_cap_one "$state_dir" "$report" "$FM_OPENCODE_LADDER_GO_SECOND_RUNG" opencode-go second "$FM_OPENCODE_LADDER_GO" "$fallback_s"
}

fm_opencode_ladder_go_reactive_capped() {  # <state-dir>
  local state_dir=$1 f id out status horizon model go_bare model_bare lane_ws
  [ -d "$state_dir" ] || return 1
  if out=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" go 2>/dev/null); then
    case "$out" in *'status=blocked'*) return 0 ;; esac
  fi
  go_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_GO")
  for f in "$state_dir"/*.opencode-retry; do
    [ -e "$f" ] || continue
    id=${f##*/}; id=${id%.opencode-retry}
    # A secondary-workspace lane's evidence belongs to the fourth rung, never
    # to the main Go rung: without the split one capped workspace would mark
    # the other capped. A cap is recorded only against the workspace a lane
    # provably ran on: a retry record with no live task meta is orphan
    # evidence and attributes to no rung, so it is skipped. Among live lanes
    # a secondary marker selects the fourth rung; anything else - a
    # markerless lane reads as main, exactly as today - may feed this rung.
    [ -f "$state_dir/$id.meta" ] || continue
    lane_ws=$(fm_opencode_lane_workspace "$state_dir" "$id" 2>/dev/null) || continue
    [ "$lane_ws" = "$FM_OPENCODE_WORKSPACE_SECOND" ] && continue
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

# fm_opencode_ladder_go_second_reactive_capped: the fourth rung's reactive
# verdict. A blocked sidecar bound to the Go tier on a secondary-workspace
# lane preserves a go-second rung cap; the rung's own record also counts, so
# a cap outlives its discovering lane. Returns 0 when the fourth rung is
# proven capped, 1 otherwise. Reactive evidence remains a fallback for the
# fourth rung when predictive quota evidence cannot establish exhaustion.
fm_opencode_ladder_go_second_reactive_capped() {  # <state-dir>
  local state_dir=$1 f id out status horizon model go_bare model_bare lane_ws
  [ -d "$state_dir" ] || return 1
  if out=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_SECOND_RUNG" 2>/dev/null); then
    case "$out" in *'status=blocked'*) return 0 ;; esac
  fi
  go_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_GO")
  for f in "$state_dir"/*.opencode-retry; do
    [ -e "$f" ] || continue
    id=${f##*/}; id=${id%.opencode-retry}
    # Orphan retry evidence attributes to no rung either: only a live lane
    # with the secondary marker feeds the fourth rung.
    [ -f "$state_dir/$id.meta" ] || continue
    lane_ws=$(fm_opencode_lane_workspace "$state_dir" "$id" 2>/dev/null) || continue
    [ "$lane_ws" = "$FM_OPENCODE_WORKSPACE_SECOND" ] || continue
    out=$("$_FM_OPENCODE_LADDER_RETRY" check "$state_dir" "$id" 2>/dev/null) || continue
    status=''; horizon=''; model=''
    for word in $out; do
      case "$word" in status=*) status=${word#status=} ;; horizon_s=*) horizon=${word#horizon_s=} ;; model=*) model=${word#model=} ;; esac
    done
    [ "$status" = blocked ] || continue
    case "$horizon" in ''|*[!0-9]*) continue ;; esac
    model_bare=$(fm_opencode_ladder_bare_model "$model")
    if [ "$model_bare" = "$go_bare" ]; then
      "$_FM_OPENCODE_LADDER_RETRY" record-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_SECOND_RUNG" "$((($(date +%s) + horizon) * 1000))" 2>/dev/null || true
      return 0
    fi
  done
  return 1
}

# fm_opencode_ladder_go_main_capped: is the first Go rung proven capped on
# the CURRENT evidence in <state-dir>? Reactive vendor evidence on
# main-workspace lanes, or the rung's own unexpired record.
fm_opencode_ladder_go_main_capped() {  # <state-dir>
  local state_dir=$1
  fm_opencode_ladder_go_reactive_capped "$state_dir" && return 0
  [ "$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" 2>/dev/null | sed -n 's/^status=//p')" = blocked ]
}

# fm_opencode_go_resets_file [config-dir]: path of the billing-reset file.
fm_opencode_go_resets_file() {  # [config-dir]
  local config_dir
  config_dir=$(fm_opencode_second_config_dir "${1:-}") || return 1
  printf '%s/opencode-go-resets\n' "$config_dir"
}

# fm_opencode_go_monthly_anchor [config-dir] <workspace>: the configured
# monthly-reset anchor instant for <workspace>, or nothing. The file holds
# one `<main|secondary> <ISO-8601>` line per plan; blanks, `#` comments,
# unknown names, and unparsable dates are ignored, never fatal: a plan with
# no usable line reads as unknown and the tier choice degrades rather than
# refusing the launch.
fm_opencode_go_monthly_anchor() {  # [config-dir] <workspace>
  local config_arg=${1:-} workspace=${2:-} file line name stamp rest
  [ "$workspace" = "$FM_OPENCODE_WORKSPACE_MAIN" ] \
    || [ "$workspace" = "$FM_OPENCODE_WORKSPACE_SECOND" ] || return 1
  file=$(fm_opencode_go_resets_file "$config_arg") || return 1
  [ -f "$file" ] && [ -r "$file" ] || return 1
  stamp=
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in ''|'#'*) continue ;; esac
    name=${line%% *}; rest=${line#* }
    [ "$name" = "$line" ] && continue
    [ "$name" = "$workspace" ] || continue
    stamp=${rest%% *}
  done < "$file" 2>/dev/null || true
  [ -n "$stamp" ] || return 1
  printf '%s\n' "$stamp"
}

# fm_opencode_go_next_monthly <anchor-iso> [<now-epoch>]: the next monthly
# reset at or after <now> for a plan whose billing month rolls on the
# anchor's calendar day and time. Prints epoch seconds, nothing when the
# anchor is unparsable. Month ends clamp (a 31st anchor resets on Feb 28 in
# a common year); callers must not assume the anchor itself is future.
# Calendar math runs in jq over UTC epochs, so neither GNU nor BSD date(1)
# is needed and the answer is identical on both.
fm_opencode_go_next_monthly() {  # <anchor-iso> [<now-epoch>]
  local anchor=${1:-} now=${2:-} epoch
  [ -n "$anchor" ] || return 1
  [ -n "$now" ] || now=$(date +%s)
  case "$now" in ''|*[!0-9]*) return 1 ;; esac
  epoch=$(TZ=UTC0 jq -nr --arg anchor "$anchor" --argjson now "$now" '
    def days_in_month($y; $m):
      [31, (if (($y % 4 == 0 and $y % 100 != 0) or $y % 400 == 0) then 29 else 28 end),
       31, 30, 31, 30, 31, 31, 30, 31, 30, 31][$m - 1];
    def days_from_civil($y; $m; $d):
      (($y - (if $m <= 2 then 1 else 0 end))) as $y2
      | (((if $y2 >= 0 then $y2 else $y2 - 399 end) / 400 | floor)) as $era
      | ($y2 - $era * 400) as $yoe
      | ((153 * ($m + (if $m > 2 then -3 else 9 end)) + 2) / 5 | floor) as $doy_base
      | ($doy_base + $d - 1) as $doy
      | ($yoe * 365 + ($yoe / 4 | floor) - ($yoe / 100 | floor) + $doy) as $doe
      | ($era * 146097 + $doe - 719468);
    (try ($anchor | fromdateiso8601) catch empty) as $t
    | select($t != null)
    | ($t | gmtime) as $g
    | (if $now <= $t then $t else
         (first(range(1; 49) as $k
           | ((($g[0] * 12 + $g[1]) + $k)) as $tm
           | (($tm / 12 | floor)) as $y | (($tm % 12) + 1) as $m
           | ([ $g[2], days_in_month($y; $m) ] | min) as $d
           | ((days_from_civil($y; $m; $d) * 86400) + $g[3] * 3600 + $g[4] * 60 + ($g[5] | floor))
           | select(. >= $now))) // empty
       end)' 2>/dev/null) || return 1
  case "$epoch" in ''|*[!0-9]*) return 1 ;; esac
  printf '%s\n' "$epoch"
}

# fm_opencode_ladder_go_monthly <workspace> [config-dir]: the plan's next
# overall (monthly) reset epoch, or nothing when unknown. A fresh quota-axi
# row for that workspace wins; the configured anchor fills the gap.
fm_opencode_ladder_go_monthly() {  # <workspace> [config-dir]
  local workspace=${1:-} config_arg=${2:-} account anchor report monthly now
  [ -n "$workspace" ] || return 1
  now=$(date +%s)
  account=main
  [ "$workspace" = "$FM_OPENCODE_WORKSPACE_SECOND" ] && account=second
  if command -v quota-axi >/dev/null 2>&1 && command -v jq >/dev/null 2>&1; then
    report=$(quota-axi --json 2>/dev/null) || report=
    if [ -n "$report" ]; then
      monthly=$(printf '%s' "$report" | jq -r --arg account "$account" '
        .providers[]?
        | select(.provider == "opencode-go" and (.state.stale == false or .state.status == "fresh")
                 and ((.accountKey // "main") == $account))
        | .windows[]?
        | select(.id == "monthly")
        | .resetsAt? // empty
        | sub("\\.[0-9]+(?=Z|[+-][0-9]{2}:[0-9]{2}$)"; "")
        | try fromdateiso8601 catch empty' 2>/dev/null | head -n 1) || monthly=
      case "$monthly" in ''|*[!0-9]*) : ;; *)
        [ "$monthly" -gt "$now" ] && { printf '%s\n' "$monthly"; return 0; } ;;
      esac
    fi
  fi
  anchor=$(fm_opencode_go_monthly_anchor "$config_arg" "$workspace" 2>/dev/null) || return 1
  fm_opencode_go_next_monthly "$anchor" "$now"
}

# fm_opencode_ladder_go_priority <state-dir> [config-dir]: the plan to drain
# first - the one whose overall (monthly) quota resets soonest, so the least
# quota is lost unused at each full reset. Prints main|secondary. A known
# reset beats an unknown one; an exact tie, or no timing on either side,
# stays on main: it is the long-standing default lane, and every lane
# recorded before workspace markers existed reads as main. Absence of a
# configured second workspace is main without consulting timing.
fm_opencode_ladder_go_priority() {  # <state-dir> [config-dir]
  local state_dir=$1 config_arg=${2:-} main_reset='' second_reset=''
  fm_opencode_second_configured "$config_arg" \
    || { printf '%s\n' "$FM_OPENCODE_WORKSPACE_MAIN"; return 0; }
  main_reset=$(fm_opencode_ladder_go_monthly "$FM_OPENCODE_WORKSPACE_MAIN" "$config_arg" 2>/dev/null) || main_reset=
  second_reset=$(fm_opencode_ladder_go_monthly "$FM_OPENCODE_WORKSPACE_SECOND" "$config_arg" 2>/dev/null) || second_reset=
  case "$main_reset" in ''|*[!0-9]*) main_reset= ;; esac
  case "$second_reset" in ''|*[!0-9]*) second_reset= ;; esac
  if [ -n "$second_reset" ] && { [ -z "$main_reset" ] || [ "$second_reset" -lt "$main_reset" ]; }; then
    printf '%s\n' "$FM_OPENCODE_WORKSPACE_SECOND"
  else
    printf '%s\n' "$FM_OPENCODE_WORKSPACE_MAIN"
  fi
}

# fm_opencode_ladder_pick_go_workspace <state-dir> [config-dir]: the Go
# workspace the next spawn or descending relaunch serves. Prints
# main|secondary. This is the SINGLE owner of the tier choice: a proven cap
# excludes first, the priority plan serves while uncapped, and a priority
# plan under a shorter cap (5-hour, then weekly) yields to the other plan
# until that limit resets - at which point the next evaluation returns on
# its own, because the expired record excludes nothing. Both capped prints
# main; callers detect tier exhaustion through their own cap checks rather
# than through this choice, and the free-model fallback stays reachable only
# there.
fm_opencode_ladder_pick_go_workspace() {  # <state-dir> [config-dir]
  local state_dir=$1 config_arg=${2:-} priority other_ws
  local priority_capped=0 other_capped=0
  fm_opencode_second_configured "$config_arg" \
    || { printf '%s\n' "$FM_OPENCODE_WORKSPACE_MAIN"; return 0; }
  priority=$(fm_opencode_ladder_go_priority "$state_dir" "$config_arg" 2>/dev/null) \
    || priority=$FM_OPENCODE_WORKSPACE_MAIN
  if [ "$priority" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
    other_ws=$FM_OPENCODE_WORKSPACE_MAIN
  else
    priority=$FM_OPENCODE_WORKSPACE_MAIN
    other_ws=$FM_OPENCODE_WORKSPACE_SECOND
  fi
  if [ "$priority" = "$FM_OPENCODE_WORKSPACE_MAIN" ]; then
    fm_opencode_ladder_go_main_capped "$state_dir" && priority_capped=1
    fm_opencode_ladder_go_second_reactive_capped "$state_dir" && other_capped=1
  else
    fm_opencode_ladder_go_second_reactive_capped "$state_dir" && priority_capped=1
    fm_opencode_ladder_go_main_capped "$state_dir" && other_capped=1
  fi
  if [ "$priority_capped" -eq 0 ]; then
    printf '%s\n' "$priority"; return 0
  fi
  if [ "$other_capped" -eq 0 ]; then
    printf '%s\n' "$other_ws"; return 0
  fi
  printf '%s\n' "$FM_OPENCODE_WORKSPACE_MAIN"
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
# The second-workspace pin (workspace config, pinned database, lane markers).
# Guarded like every other shared source here: callers on the dispatch path
# (bin/fm-spawn.sh) load it before this file.
# shellcheck source=/dev/null
if ! declare -f fm_opencode_second_org >/dev/null 2>&1; then
  . "$_FM_OPENCODE_LADDER_LIB_DIR/fm-opencode-second-lib.sh"
fi

# Lines of rendered pane tail handed to the detector for the idle shape.
# bin/fm-opencode-retry.sh owns the phrases; this is only the capture width.
FM_OPENCODE_CAP_TEXT_LINES=${FM_OPENCODE_CAP_TEXT_LINES:-60}

# fm_opencode_ladder_pane_file: capture <id>'s pane tail into a temp file and
# print its path (the caller removes it). Prints nothing and returns 1 when
# the lane has no readable meta, no backend target, no capture primitive, or
# an empty capture: all of those are unknown, never open, and the gate treats
# unknown as dispatch-Go (see fm_opencode_ladder_model).
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
# which carries no provider prefix (`muse-spark-1.3-contributor`), while
# the rung constants above carry one (`opencode-go/...`). Every evidence
# comparison normalises both sides through here, so either form binds the
# right rung.
fm_opencode_ladder_bare_model() {  # <model>
  case "$1" in
    */*) printf '%s' "${1##*/}" ;;
    *) printf '%s' "$1" ;;
  esac
}

# fm_opencode_model_bills_go <model>: 0 when <model> runs on the Go tier and
# therefore bills a Go workspace. The provider-qualified id and its bare
# form both count: a bare `--model muse-spark-1.3-contributor` passthrough
# resolves to the same tier. Anything else - Codex Plus, an ungoverned
# passthrough, empty, `default` - bills no Go workspace, so the workspace
# pin never applies to it (bin/fm-opencode-second-lib.sh owns the pin).
fm_opencode_model_bills_go() {  # <model>
  local model=${1:-} bare go_bare
  case "$model" in ''|default) return 1 ;; esac
  [ "$model" = "$FM_OPENCODE_LADDER_GO" ] && return 0
  bare=$(fm_opencode_ladder_bare_model "$model") || return 1
  go_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_GO") || return 1
  [ -n "$bare" ] && [ "$bare" = "$go_bare" ]
}

# fm_opencode_model_requires_console_login <model>: retired with the free
# Minimax rung on 2026-10-22. The free model needed console sign-in without
# billing Go; every governed model now bills Go and carries its login through
# the workspace pin instead. Kept as a predicate so callers need no branch:
# it answers 1 for every model.
fm_opencode_model_requires_console_login() {  # <model>
  return 1
}

# fm_opencode_ladder_free_capped: retired with the free rung on 2026-10-22.
# The free tier no longer exists, so no evidence can prove it exhausted.
# Kept as a predicate so out-of-tree callers fail closed: it always answers 1.
fm_opencode_ladder_free_capped() {  # <state-dir>
  return 1
}

# fm_opencode_ladder_model: the model id a launch with <requested> should run.
# Prints exactly one line - the effective model id - on stdout. All governed
# requests pass through the same ordered cap gate: Codex Plus first, then Go.
# Returns 3 only when
# every ladder rung is proven capped; callers may then consult the
# separately declared exhausted-ladder fallback. Other refusals return 1.
fm_opencode_ladder_model() {  # <requested> <state-dir>
  local requested=${1:-} state_dir=${2:-} explicit=1
  local plus_capped=0 go_capped=0 second_ready=0
  fm_opencode_ladder_policy_load
  # The entire declared ladder is checked before launch, so a retired rung is
  # surfaced with its replacement before the next worker is sent to it.
  # shellcheck source=bin/fm-model-catalog-lib.sh
  . "$_FM_OPENCODE_LADDER_LIB_DIR/fm-model-catalog-lib.sh"
  fm_model_catalog_check "$FM_OPENCODE_LADDER_PLUS_MODEL" || return $?
  fm_model_catalog_check "$FM_OPENCODE_LADDER_GO" || return $?
  if [ -z "$requested" ] || [ "$requested" = default ]; then
    requested=$FM_OPENCODE_LADDER_PLUS_MODEL
    explicit=0
  fi
  if [ "${FM_OPENCODE_LADDER:-on}" = off ]; then
    printf '%s\n' "$requested"
    printf 'notice: opencode ladder disabled by FM_OPENCODE_LADDER=off - launching on %s unchecked against the ladder caps\n' \
      "$requested" >&2
    return 0
  fi
  case "$requested" in
    "$FM_OPENCODE_LADDER_GO"|"$FM_OPENCODE_LADDER_PLUS_MODEL") ;;
    *)
      printf '%s\n' "$requested"
      printf 'notice: opencode ladder not applied: model %s is outside the governed ladder (%s, %s)\n' \
        "$requested" "$FM_OPENCODE_LADDER_PLUS_MODEL" "$FM_OPENCODE_LADDER_GO" >&2
      return 0 ;;
  esac
  fm_opencode_ladder_quota_caps "$state_dir"
  if [ "$explicit" -eq 1 ] && [ "$requested" = "$FM_OPENCODE_LADDER_GO" ]; then
    # An explicit Go request always stands, even past a proven cap: the
    # captain named the tier, so the gate does not substitute another one.
    # (The workspace pick downstream still rides the live plan.)
    printf '%s\n' "$FM_OPENCODE_LADDER_GO"
    return 0
  fi
  if [ "$explicit" -eq 1 ] && [ "$requested" = "$FM_OPENCODE_LADDER_PLUS_MODEL" ]; then
    local explicit_plus_cap explicit_go_cap
    explicit_plus_cap=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" 2>/dev/null) || explicit_plus_cap=
    case "$explicit_plus_cap" in *'status=blocked'*)
      explicit_go_cap=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" 2>/dev/null) || explicit_go_cap=
      case "$explicit_go_cap" in *'status=blocked'*)
        if [ "$(fm_opencode_ladder_pick_go_workspace "$state_dir" 2>/dev/null)" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
          printf '%s\n' "$FM_OPENCODE_LADDER_GO"
          printf 'notice: opencode ladder: Codex Plus and Go are capped; dispatching on Go %s on the second workspace\n' "$FM_OPENCODE_LADDER_GO" >&2
          return 0
        fi
        printf 'error: requested Codex Plus and Go rungs are capped\n' >&2; return 1 ;;
      esac
      printf '%s\n' "$FM_OPENCODE_LADDER_GO"
      printf 'notice: opencode ladder: Codex Plus is capped; dispatching on Go %s\n' "$FM_OPENCODE_LADDER_GO" >&2
      return 0 ;;
    esac
    printf '%s\n' "$FM_OPENCODE_LADDER_PLUS_MODEL"
    return 0
  fi
  fm_opencode_ladder_plus_capped "$state_dir" && plus_capped=1
  fm_opencode_ladder_go_main_capped "$state_dir" && go_capped=1
  # The Go tier's workspace is one decision owned by
  # fm_opencode_ladder_pick_go_workspace: the monthly-soonest plan serves
  # while uncapped, and a plan under a shorter cap yields to the other until
  # that limit resets. The pick runs the secondary reactive scan itself for
  # its preservation side effect, so a proven secondary cap is recorded when
  # observed even while the pick stays on main. Without a configured second
  # workspace nothing here runs at all.
  if fm_opencode_second_configured; then
    [ "$(fm_opencode_ladder_pick_go_workspace "$state_dir" 2>/dev/null)" = "$FM_OPENCODE_WORKSPACE_SECOND" ] \
      && second_ready=1
  fi
  [ "$plus_capped" -eq 1 ] || { printf '%s\n' "$FM_OPENCODE_LADDER_PLUS_MODEL"; return 0; }
  if [ "$go_capped" -eq 1 ] && [ "$second_ready" -eq 0 ] && [ -z "${FM_OPENCODE_LADDER_OVERRIDE:-}" ]; then
    local plus_until go_until
    plus_until=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" 2>/dev/null | sed -n 's/.*reset_at=//p')
    go_until=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_RUNG" 2>/dev/null | sed -n 's/.*reset_at=//p')
    printf 'error: opencode ladder exhausted: Codex Plus capped until %s; Go capped until %s' \
      "${plus_until:-unknown}" "${go_until:-unknown}" >&2
    if fm_opencode_second_configured; then
      local go_second_until
      go_second_until=$("$_FM_OPENCODE_LADDER_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_SECOND_RUNG" 2>/dev/null | sed -n 's/.*reset_at=//p')
      printf '; Go (second workspace) capped until %s' "${go_second_until:-unknown}" >&2
    fi
    printf '\n' >&2
    return 3
  fi
  if [ -n "${FM_OPENCODE_LADDER_OVERRIDE:-}" ]; then
    printf '%s\n' "$FM_OPENCODE_LADDER_PLUS_MODEL"
    printf 'notice: opencode ladder OVERRIDDEN by FM_OPENCODE_LADDER_OVERRIDE=%s - holding Codex Plus past a proven cap\n' "$FM_OPENCODE_LADDER_OVERRIDE" >&2
    return 0
  fi
  if [ "$go_capped" -eq 0 ]; then
    printf '%s\n' "$FM_OPENCODE_LADDER_GO"
    printf 'notice: opencode ladder: Codex Plus is capped; dispatching on Go %s\n' "$FM_OPENCODE_LADDER_GO" >&2
    return 0
  fi
  if [ "$second_ready" -eq 1 ]; then
    printf '%s\n' "$FM_OPENCODE_LADDER_GO"
    printf 'notice: opencode ladder: Codex Plus and Go are capped; dispatching on Go %s on the second workspace\n' "$FM_OPENCODE_LADDER_GO" >&2
    return 0
  fi
}
