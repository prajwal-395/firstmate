#!/usr/bin/env bash
# fm-opencode-descent-lib.sh - move capped workers along the
# Codex Plus-then-Go ladder.
# Usage: . bin/fm-opencode-descent-lib.sh
# Sourced by bin/fm-watch.sh. Defining functions is all this file does; the
# only things it pulls in on its own are the libraries below, and only when a
# caller has not already loaded them.
#
# THE HALF THE DISPATCH GATE CANNOT COVER. bin/fm-opencode-ladder-lib.sh routes
# the NEXT spawn and never looks at a lane again. A lane that starts on Go
# and hits the cap mid-run does not fail: the vendor holds the session in
# `retry` and re-schedules it for hours (the 2026-09-07 twelve-lane stall),
# while supervision reads the lane as working for the whole backoff. This file
# is the re-evaluation the ladder was missing, on the watcher's own cadence,
# against the same cap evidence the dispatch gate uses - and it reuses
# bin/fm-opencode-retry.sh as that detector rather than writing a second one.
#
# THE HINGE QUESTION, ANSWERED WITH EVIDENCE. PR 96 declined to move running
# workers for lack of a verified in-session model switch. Re-verified on
# opencode 1.18.29, that is still true, in both directions:
#
#   - The TUI has an interactive `/models` picker (Select model, Search/Recent
#     rows, esc to close - driven live in a scratch pane with no prompt ever
#     submitted, so zero quota spent). It lists both ladder tiers, so a human
#     can switch mid-session. But it is a rendered picker with no verified
#     walk: row order comes from the account catalogue, and no confirmation
#     signals have been proven. Driving it unsupervised would risk parking a
#     live worker on the wrong model with no proven gain.
#   - Upstream confirms there is no programmatic path either: issue #24006
#     (runtime model switching via plugin API) is closed not_planned, the
#     `chat.params` hook output carries temperature/topP/topK/maxOutputTokens
#     but no model, and the plugin event reference (opencode.ai/docs/plugins,
#     2026-09-08) lists observer-style events plus a per-tool
#     `tool.execute.before` deny - which gates one tool while the loop keeps
#     spending, exactly the insufficiency bin/fm-agy-descent-lib.sh documents
#     for agy's own deny primitive.
#
# So the honest move is not an in-session switch but the verified control-plane
# relaunch: bin/fm-control.sh relaunch is transactional, keeps the same
# worktree, branch, commits, and brief, and carries a handoff note because the
# replacement inherits the local copy but none of the conversation. A capped
# lane is parked doing nothing, so ending its session loses no in-flight work
# beyond context the replacement re-derives from the brief and the commits.
# That is a heavier act than agy's conversation-preserving switch, which is
# why every move below is reported as a wake the supervisor sees.
#
# THE TRIGGER IS THE VERIFIED RUNG RESET, WITH A SESSION LATCH FOR OPENCODE.
# OpenCode Go sidecars require the busy latch to remain on that exact
# retry event, so a stale sidecar beside a resumed turn never moves a worker.
# Codex Plus uses a fresh quota-axi zero with a reset, or a usage-limit pane
# message that states its reset time. bin/fm-opencode-retry.sh owns rung-record
# validation and bin/fm-busy-lib.sh owns the OpenCode session latch.
#
# WHAT MOVES, AND WHAT ONLY SURFACES. A capped Plus lane moves to the Go
# tier; a capped Go lane moves to the Go tier's other workspace while that
# plan is uncapped. The Go tier's workspace is the
# dispatch pick (bin/fm-opencode-ladder-lib.sh
# fm_opencode_ladder_pick_go_workspace), never a fixed order: the
# monthly-soonest plan serves while uncapped, and a plan under a shorter cap
# yields to the other until that limit resets, and the lane's model id is
# unchanged - the task meta's workspace marker is what moves it. A capped
# secondary lane returns to main while main is uncapped, the mirror move:
# parked is parked in either direction. A secondmate or a lane
# with no recorded model is refused rather than moved blind. Off-ladder
# models are never governed. A move the durable record did not follow is
# reported as `unrecorded`. Each episode gets one automatic attempt, and a
# refusal wake hands the retry to firstmate.
#
# THERE IS NO CLIMB-BACK FOR RUNNING WORKERS, BY DESIGN. A healthy lane on a
# later rung is making progress; moving it back would risk its conversation
# for zero gain. New spawns start at Codex Plus after earlier cap records expire.
#
# THE FINDING OUTLIVES THE TASK. An OpenCode sidecar describes one session's
# backoff and is cleared with it after a move - but the cap it proved is a
# property of the rung and its reset time, so the tick records it
# rung-scoped through bin/fm-opencode-retry.sh record-cap at proof time,
# before any per-lane decision. The dispatch gate reads that record, so a cap
# a descent paid for still routes the next spawn after the discovering task
# is relaunched or torn down. Text-only evidence carries no trustworthy
# horizon and is never preserved.
#
# THE WATCHER IS THE ONLY DRIVER, AND THAT IS SUFFICIENT. agy needed a
# turn-end driver because a worker inside one long turn keeps spending while
# the watcher waits between firstmate's turns. A capped opencode lane spends
# nothing - the vendor parked it - so there is no spend to race, and a capped
# lane never reaches a turn end that could drive anything. The watcher's poll
# is rate-limited by FM_OPENCODE_DESCENT_INTERVAL and refreshes the Codex and
# Go cap records only when a governed lane is present.
#
# NO POINT-OF-SPEND GATE EXISTS FOR OPENCODE, AND NONE IS FAKED HERE. agy's
# gate works because agy invokes it PostInvocation inside the worker's own
# loop AND because quota evidence exists to decide on before the floor is
# crossed. OpenCode Go has neither half: no hook ends its agent
# loop (see the surface above), and the Go tier counts reactive vendor evidence beside quota-axi. The
# retry
# event fires after the refusal, when the vendor has already parked the
# session - gating there would be polling pretending to be synchronous, which
# the brief forbids. This evaluation plus the sidecar detector is the whole
# of the opencode ladder's running-worker enforcement.
#
# THE FAILURE DIRECTION, STATED NOT IMPLIED. A wrong relaunch restarts a
# conversation whose work products (worktree, commits, brief, note) are all
# preserved; a missed cap stalls the lane for roughly a day. This file is
# biased toward relaunching, for the captain's own throughput-first reason.
#
# THE OVERRIDE. FM_CAPTAIN_OVERRIDE_REASON is the launch gate's deliberate
# escape and it is honoured here too: a worker held on Go past a proven cap
# on the captain's explicit request is not dragged off it behind their back.
# Its use is printed, exactly as the launch gate prints it. bin/fm-spawn.sh
# records the captain's words per task where this evaluation reads it back, because
# no free-standing variable ever reaches the watcher: the watcher is a long-lived
# process that predates the instruction.
#
# FM_OPENCODE_DESCENT=off disables the whole evaluation, leaving the dispatch
# gate as the only enforcement. In production the off state is filed as
# <state-dir>/.opencode-descent-off, which the watcher reads: the variable
# alone never reaches it, for the same reason. The variable stays as an
# additional source for one-shot runs and the regression suite.
# FM_OPENCODE_DESCENT_CONTROL_BIN overrides the
# control-plane binary the relaunch runs through; production leaves it at the
# default, tests point it at a stub so no real agent is ever stopped.

_FM_OPENCODE_DESCENT_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null)" || _FM_OPENCODE_DESCENT_LIB_DIR="."
if ! declare -f fm_opencode_ladder_model >/dev/null 2>&1; then
  # shellcheck source=bin/fm-opencode-ladder-lib.sh
  . "$_FM_OPENCODE_DESCENT_LIB_DIR/fm-opencode-ladder-lib.sh"
fi
# fm_meta_get lives in the backend library; the watcher loads it before this
# file, so the guarded source below fires only when this file stands alone.
# shellcheck source=/dev/null
if ! declare -f fm_meta_get >/dev/null 2>&1; then
  . "$_FM_OPENCODE_DESCENT_LIB_DIR/fm-backend.sh"
fi
# fm_busy_record_read lives in the busy library, loaded the same way.
# shellcheck source=/dev/null
if ! declare -f fm_busy_record_read >/dev/null 2>&1; then
  . "$_FM_OPENCODE_DESCENT_LIB_DIR/fm-busy-lib.sh"
fi
# fm_stopped_declared lives in the stopped library, loaded the same way. The
# tick defers to its incarnation-bound predicate rather than re-reading the
# record, so a relaunch that spends the record re-arms candidacy by itself.
# shellcheck source=/dev/null
if ! declare -f fm_stopped_declared >/dev/null 2>&1; then
  . "$_FM_OPENCODE_DESCENT_LIB_DIR/fm-stopped-lib.sh"
fi
_FM_OPENCODE_DESCENT_RETRY="$_FM_OPENCODE_DESCENT_LIB_DIR/fm-opencode-retry.sh"
# The control-plane binary the relaunch runs through. Resolved at call time,
# not source time, so a caller (notably a test) may point it at a stub by
# exporting FM_OPENCODE_DESCENT_CONTROL_BIN before the tick runs; production
# leaves it at the default beside this library.
fm_opencode_descent_control() {
  printf '%s' "${FM_OPENCODE_DESCENT_CONTROL_BIN:-$_FM_OPENCODE_DESCENT_LIB_DIR/fm-control.sh}"
}

# FM_OPENCODE_DESCENT: `off` disables the whole evaluation, leaving the
# dispatch gate as the only enforcement.
FM_OPENCODE_DESCENT=${FM_OPENCODE_DESCENT:-on}

# FM_OPENCODE_DESCENT_INTERVAL: seconds between evaluations. Evidence is local
# sidecars, so the cost is file reads; a minute matches the agy descent while
# keeping the relaunch decision off every watcher poll.
FM_OPENCODE_DESCENT_INTERVAL=${FM_OPENCODE_DESCENT_INTERVAL:-60}

# fm_opencode_descent_wake_reason: the supervision wake one outcome line
# becomes. One owner for the wording, because the watcher publishes it and
# firstmate reads it; a supervisor should not need the tick to explain
# itself twice.
fm_opencode_descent_wake_reason() {  # <outcome-line>
  printf 'check: opencode ladder %s' "$1"
}

fm_opencode_descent_mark() {  # <state-dir> <name>
  printf '%s' "$1/.opencode-descent-$2"
}

# fm_opencode_descent_escalate_once: 0 the first time a task needs escalating
# in this cap episode, 1 afterwards. The episode is identified by the vendor's
# own scheduled-retry timestamp (<next>): the same refusal re-observed is the
# same episode, while fresh refusal evidence carries a new timestamp and
# re-arms both the report and the single automatic attempt. A lane that stops
# meeting the trigger clears the marker outright. Without this a refusal would
# wake the captain once a minute for as long as the condition lasted.
fm_opencode_descent_escalate_once() {  # <state-dir> <id> <next>
  local marker prev
  marker=$(fm_opencode_descent_mark "$1" "escalated-$2")
  prev=$(cat "$marker" 2>/dev/null || true)
  if [ "$prev" = "$3" ] && [ -n "$3" ]; then
    return 1
  fi
  printf '%s' "$3" > "$marker" 2>/dev/null || true
  return 0
}

# fm_opencode_descent_off_path: the filed descent off-switch for <state-dir>.
# Existence is the signal, exactly as with the escalated- markers below; see
# the FM_OPENCODE_DESCENT comment for why the production off state lives here
# rather than in the environment.
fm_opencode_descent_off_path() {  # <state-dir>
  printf '%s/.opencode-descent-off' "$1"
}

# fm_opencode_descent_off: 0 when the descent evaluation is turned off for
# <state-dir>, by the filed record the watcher reads or by the variable only
# a manual run inherits. The caller already takes <state-dir>, so this costs
# one file test on the decision path and no new plumbing.
fm_opencode_descent_off() {  # <state-dir>
  [ "${FM_OPENCODE_DESCENT:-on}" = off ] && return 0
  [ -f "$(fm_opencode_descent_off_path "$1")" ]
}

# fm_opencode_pin_mark: the recorded deliberate placement of <id>. The content
# is the reason firstmate placed it there.
fm_opencode_pin_mark() {  # <state-dir> <id>
  printf '%s/.opencode-pin-%s' "$1" "$2"
}

# fm_opencode_pin_task: pin <id> where firstmate deliberately put it, for
# <reason>. bin/fm-spawn.sh records this when a validated --captain-route/--captain-said
# pair holds a launch on the first rung past a proven cap; the tick reads it back instead of
# a variable it cannot see. A pin is not drift, so the tick moves a pinned
# worker nowhere. Per task, not per home: the hold names one lane the captain
# placed, and a home-wide file would freeze every lane past its cap,
# including lanes the captain never asked about.
fm_opencode_pin_task() {  # <state-dir> <id> <reason>
  printf '%s' "$3" > "$(fm_opencode_pin_mark "$1" "$2")" 2>/dev/null
}

# fm_opencode_pin_clear: release <id>'s pin. A launch without the override
# calls this, so a fresh placement under the ordinary rules is not frozen by
# an earlier instruction; teardown calls it with the rest of the task's
# records.
fm_opencode_pin_clear() {  # <state-dir> <id>
  rm -f "$(fm_opencode_pin_mark "$1" "$2")" 2>/dev/null || true
}

# fm_opencode_task_pin: print <id>'s recorded pin reason, or fail when
# unpinned.
fm_opencode_task_pin() {  # <state-dir> <id>
  local path reason
  path=$(fm_opencode_pin_mark "$1" "$2")
  [ -f "$path" ] || return 1
  reason=$(cat "$path" 2>/dev/null || true)
  [ -n "$reason" ] || return 1
  printf '%s' "$reason"
}

fm_opencode_descent_clear_task() {  # <state-dir> <id>
  rm -f "$(fm_opencode_descent_mark "$1" "escalated-$2")" 2>/dev/null || true
}

# fm_opencode_descent_due: 0 when FM_OPENCODE_DESCENT_INTERVAL has elapsed
# since the last evaluation. The marker is stamped by the caller only after an
# evaluation actually ran, so a home that runs no opencode worker never starts
# a clock.
fm_opencode_descent_due() {  # <state-dir> [<now>]
  local marker=$1/.opencode-descent-last now=${2:-} stamp
  [ -f "$marker" ] || return 0
  [ -n "$now" ] || now=$(date +%s)
  stamp=$(cat "$marker" 2>/dev/null || true)
  case "$stamp" in
    ''|*[!0-9]*) return 0 ;;
  esac
  [ $((now - stamp)) -ge "$FM_OPENCODE_DESCENT_INTERVAL" ]
}

# fm_opencode_descent_when: the human time phrase for a cap horizon. A numeric
# horizon renders as "next retry in ~22h34m"; text-only evidence carries no
# trustworthy horizon (an idle lane's bracketed retry duration may never
# fire), so it owns that instead of printing a broken "next retry in ~?".
fm_opencode_descent_when() {  # <horizon>
  case "$1" in
    ''|*[!0-9]*)
      printf 'the cap is showing in its pane with no retry horizon on record' ;;
    *)
      printf 'next retry in %s' "$(fm_opencode_ladder_horizon_human "$1")" ;;
  esac
}

# fm_opencode_descent_cap: the quota-scale verdict for one lane, reusing both
# owners and re-deriving neither. Prints "<horizon_s|unknown> <bound>
# <episode>" and returns 0 on either shape:
#   structural - the sidecar classifies blocked through bin/fm-opencode-retry.sh
#     AND the busy record still latches the session-retry event that wrote it
#     (the same two halves bin/fm-crew-state.sh requires before it calls a
#     lane blocked). <bound> is `go`, `other`, or `unbound`, from the
#     sidecar's own model binding; <episode> is the vendor's own
#     scheduled-retry timestamp, which identifies the cap episode.
#   idle (2026-09-14) - no quota-scale sidecar, but the busy record says the
#     turn ended on the plugin's idle event AND the lane's pane tail carries
#     the cap verbatim through bin/fm-opencode-retry.sh scan-text (the text
#     wins over a merely transient sidecar for an idle lane: the lane is not
#     retrying, it has stopped). <bound> is `go` when the lane is recorded
#     on the Go tier, else `unbound`; <horizon_s> is the literal word `unknown`;
#     <episode> is the text fingerprint scan-text reports. Busy lanes are
#     deliberately excluded: a working lane with old cap text in its scrollback
#     is making progress, and moving it would spend the captain's money for
#     nothing.
# Anything else returns 1: a stale sidecar beside a resumed turn, a transient
# backoff, expired evidence, a clean pane tail, an uncapturable pane, or no
# evidence at all.
fm_opencode_descent_cap() {  # <state-dir> <id>
  local state_dir=$1 id=$2 out rec word bound='' horizon='' status='' next=''
  local r_state='' r_source='' r_event='' episode=''
  local meta lane_model lane_bare cap_file scan_out
  local go_bare model_bare
  # The sidecar's model is the plugin's own vocabulary (model.id, no provider
# prefix); the rung constants carry one. Both sides normalise through the
# dispatch gate's shared helper, so either form binds the right rung - the
# same mismatch PR 129 fixed at dispatch, which this file inherited from the
# same port with its own prefixed-only copy.
  go_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_GO")
  [ -n "$state_dir" ] && [ -d "$state_dir" ] || return 1
  [ -x "$_FM_OPENCODE_DESCENT_RETRY" ] || return 1
  if out=$("$_FM_OPENCODE_DESCENT_RETRY" check "$state_dir" "$id" 2>/dev/null); then
    for word in $out; do
      case "$word" in
        status=*) status=${word#status=} ;;
        horizon_s=*) horizon=${word#horizon_s=} ;;
        model=*)
          model_bare=$(fm_opencode_ladder_bare_model "${word#model=}")
          case "$model_bare" in
            "$go_bare") bound=go ;;
            *) bound=other ;;
          esac
          ;;
      esac
    done
    if [ "$status" = blocked ]; then
      case "$horizon" in ''|*[!0-9]*) : ;; *)
        [ -n "$bound" ] || bound=unbound
        # The latch: the record the plugin maintains must still name the retry
        # event. A genuinely resumed turn writes session-busy first, which
        # drops a stale sidecar even before its horizon expires.
        if rec=$(fm_busy_record_read "$state_dir" "$id" 2>/dev/null); then
          r_state=${rec%% *}; rec=${rec#* }
          r_source=${rec%% *}; rec=${rec#* }
          r_event=${rec%% *}
          if [ "$r_state" = busy ] && [ "$r_source" = opencode-plugin ] \
            && [ "$r_event" = session-retry ]; then
            # The episode identity comes from the sidecar the check just
            # classified: the check passed, so the record is present, one
            # line, and well-formed, and its vendor timestamp names the
            # refusal this episode belongs to.
            next=$(tr ' ' '\n' < "$state_dir/$id.opencode-retry" 2>/dev/null \
              | sed -n 's/^next=\([0-9][0-9]*\)$/\1/p' | head -n 1)
            if [ -n "$next" ]; then
              printf '%s %s %s\n' "$horizon" "$bound" "$next"
              return 0
            fi
          fi
        fi
        ;;
      esac
    fi
  fi
  # The idle shape: the turn ended on the plugin's idle event, so the sidecar
  # is gone by design - the pane tail is the only evidence left. Detection
  # stays owned by bin/fm-opencode-retry.sh; this only supplies the capture.
  rec=$(fm_busy_record_read "$state_dir" "$id" 2>/dev/null) || return 1
  r_state=${rec%% *}; rec=${rec#* }
  r_source=${rec%% *}; rec=${rec#* }
  r_event=${rec%% *}
  [ "$r_state" = idle ] || return 1
  [ "$r_source" = opencode-plugin ] || return 1
  case "$r_event" in session-idle|session-status-idle) ;; *) return 1 ;; esac
  command -v fm_opencode_ladder_pane_file >/dev/null 2>&1 || return 1
  cap_file=$(fm_opencode_ladder_pane_file "$state_dir" "$id" 2>/dev/null) || return 1
  scan_out=$("$_FM_OPENCODE_DESCENT_RETRY" scan-text --file "$cap_file" 2>/dev/null) \
    || { rm -f "$cap_file"; return 1; }
  rm -f "$cap_file"
  episode=''
  for word in $scan_out; do
    case "$word" in episode=*) episode=${word#episode=} ;; esac
  done
  [ -n "$episode" ] || return 1
  bound=unbound
  meta="$state_dir/$id.meta"
  if [ -f "$meta" ]; then
    lane_model=$(fm_meta_get "$meta" model 2>/dev/null) || lane_model=''
    lane_bare=$(fm_opencode_ladder_bare_model "$lane_model")
    [ "$lane_bare" = "$go_bare" ] && bound=go
  fi
  printf 'unknown %s %s\n' "$bound" "$episode"
}

# FM_OPENCODE_CODEX_LIMIT_FALLBACK_SECS: the bounded horizon recorded when a
# Codex usage-limit notice carries no parseable future reset (see
# fm_opencode_descent_codex_limit_cap). The limit is proven but its clock is
# not, so the rung reads capped for this long rather than looking open.
# Default 3600: long enough to route the next spawn off the rung, short
# enough never to pose as the vendor's own window.
FM_OPENCODE_CODEX_LIMIT_FALLBACK_SECS=${FM_OPENCODE_CODEX_LIMIT_FALLBACK_SECS:-3600}

# fm_opencode_descent_codex_human_epoch: parse one human local-time reset
# match (Codex v0.162.1: 'Oct 14th, 2026 3:34 AM' - ordinal day, 12-hour
# clock, local time zone) to epoch seconds. Prints the epoch and returns 0,
# or returns 1 when the match parses to no clock. GNU date reads the match
# directly; BSD date needs the ordinal, comma, and `at` normalised away
# first, across the abbreviated and full month names with and without
# seconds. One owner for the human form so the cap preserver below never
# grows a second parser beside it.
fm_opencode_descent_codex_human_epoch() {  # <match>
  local match=$1 norm epoch
  [ -n "$match" ] || return 1
  norm=$(printf '%s' "$match" | sed -E -e 's/([0-9])[Ss][Tt]/\1/g' -e 's/([0-9])[Nn][Dd]/\1/g' -e 's/([0-9])[Rr][Dd]/\1/g' -e 's/([0-9])[Tt][Hh]/\1/g' -e 's/,//g' -e 's/ [Aa][Tt] / /g' -e 's/  +/ /g')
  [ -n "$norm" ] || return 1
  epoch=$(date -d "$norm" +%s 2>/dev/null) \
    || epoch=$(date -j -f '%b %d %Y %I:%M %p' "$norm" +%s 2>/dev/null) \
    || epoch=$(date -j -f '%B %d %Y %I:%M %p' "$norm" +%s 2>/dev/null) \
    || epoch=$(date -j -f '%b %d %Y %I:%M:%S %p' "$norm" +%s 2>/dev/null) \
    || epoch=$(date -j -f '%B %d %Y %I:%M:%S %p' "$norm" +%s 2>/dev/null) \
    || return 1
  case "$epoch" in ''|*[!0-9]*) return 1 ;; esac
  printf '%s\n' "$epoch"
}

# A Codex usage-limit notice can carry an absolute reset time even
# when quota-axi has not yet refreshed its account window. Preserve it in the
# shared plus-rung record only when both the limit phrase and a future reset
# timestamp are present in that lane's pane. Two reset forms are recognised:
# the ISO-8601 UTC instant and the human local-time form Codex v0.162.1
# prints ('try again at Oct 14th, 2026 3:34 AM' - ordinal day, 12-hour clock,
# local time zone; full and abbreviated month names, an optional comma, an
# optional `at`, and optional seconds all parse through
# fm_opencode_descent_codex_human_epoch). Both are the vendor's own stated
# reset, so neither guesses a horizon. A limit phrase with no parseable
# future reset still records the plus cap with the conservative bounded
# horizon from FM_OPENCODE_CODEX_LIMIT_FALLBACK_SECS and says so on stderr,
# rather than leaving the rung looking open: the limit is proven even when
# its clock is not, and the notice marks the horizon as a bound rather than
# a reset. A reset that parses but already passed is expiry, not an
# unreadable clock, and records nothing.
fm_opencode_descent_codex_limit_cap() {  # <state-dir> <id>
  local state_dir=$1 id=$2 cap_file reset reset_s human epoch now_s fallback_s
  fallback_s=${FM_OPENCODE_CODEX_LIMIT_FALLBACK_SECS:-3600}
  case "$fallback_s" in ''|*[!0-9]*) fallback_s=3600 ;; esac
  cap_file=$(fm_opencode_ladder_pane_file "$state_dir" "$id" 2>/dev/null) || return 1
  if ! grep -Eiq 'usage limit|limit reached|limit exceeded' "$cap_file"; then
    rm -f "$cap_file"
    return 1
  fi
  reset=$(grep -Eo '20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' "$cap_file" | tail -n 1)
  if [ -n "$reset" ]; then
    reset_s=$(jq -nr --arg reset "$reset" 'try ($reset | fromdateiso8601) catch empty' 2>/dev/null)
    case "$reset_s" in
      ''|*[!0-9]*) : ;;
      *)
        if [ "$reset_s" -gt "$(date +%s)" ]; then
          rm -f "$cap_file"
          "$_FM_OPENCODE_DESCENT_RETRY" record-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" "$((reset_s * 1000))" >/dev/null 2>&1
          return $?
        fi
        rm -f "$cap_file"
        return 1 ;;
    esac
  fi
  human=$(grep -Eoi '(Jan(uary)?|Feb(ruary)?|Mar(ch)?|Apr(il)?|May|Jun(e)?|Jul(y)?|Aug(ust)?|Sep(t(ember)?)?|Oct(ober)?|Nov(ember)?|Dec(ember)?) +[0-9]{1,2}(st|nd|rd|th)?,? +[0-9]{4}( +at)? +[0-9]{1,2}:[0-9]{2}(:[0-9]{2})? +[AaPp][Mm]' "$cap_file" | tail -n 1)
  if [ -n "$human" ]; then
    if epoch=$(fm_opencode_descent_codex_human_epoch "$human" 2>/dev/null); then
      if [ "$epoch" -gt "$(date +%s)" ]; then
        rm -f "$cap_file"
        "$_FM_OPENCODE_DESCENT_RETRY" record-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" "$((epoch * 1000))" >/dev/null 2>&1
        return $?
      fi
      rm -f "$cap_file"
      return 1
    fi
  fi
  rm -f "$cap_file"
  now_s=$(date +%s)
  printf 'notice: opencode ladder: Codex Plus usage limit is showing with no parseable reset; recording a conservative %ss cap instead of leaving the rung open\n' "$fallback_s" >&2
  "$_FM_OPENCODE_DESCENT_RETRY" record-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" "$(((now_s + fallback_s) * 1000))" >/dev/null 2>&1
}

# fm_opencode_descent_skip_lane: 0 when <id> must never be moved by this
# evaluation, even past a proven cap. Two shapes, both terminal for the
# ladder's purposes. A worker firstmate itself stopped through the control
# plane is parked on purpose: relaunching it spends its declared-stop record
# and restarts work the captain stood down (2026-10-15: this tick relaunched
# two such lanes and a finished scout). The verdict is the stopped library's
# own incarnation-bound predicate, never a re-read of the file. A lane whose
# status log already reports done is finished: nothing the ladder does can
# improve it, and a move would restart it from the brief and the commits. The
# done read takes only the last status verb (bin/fm-classify-lib.sh's
# status_line_verb owns verb semantics); an absent or unreadable status file
# never skips. Silent by contract: neither shape is an episode worth waking
# over, and the caller clears the episode marker before continuing.
fm_opencode_descent_skip_lane() {  # <state-dir> <id>
  local state_dir=$1 id=$2 status_file=$1/$2.status line verb
  fm_stopped_declared "$state_dir" "$id" && return 0
  [ -f "$status_file" ] && [ ! -L "$status_file" ] || return 1
  line=$(grep -v '^[[:space:]]*$' "$status_file" 2>/dev/null | tail -n 1) || return 1
  [ -n "$line" ] || return 1
  verb=${line%%:*}
  verb=${verb%%\[*}
  verb=${verb#"${verb%%[![:space:]]*}"}
  verb=${verb%"${verb##*[![:space:]]}"}
  [ "$verb" = "done" ]
}

# fm_opencode_descent_tick: one evaluation of this home's live opencode lanes.
#
# One line per outcome on stdout, and nothing at all in the ordinary case
# where every running lane is healthy or already where the ladder puts it:
#
#   relaunched <id> <from> -> <to>   the lane was moved and the record followed
#   unrecorded <id> <reason>         it moved, but the move was not written down
#   refused <id> <reason>            it was not, and the captain needs to know
#   override <id> <reason>           the captain's own override is holding it
#
# A lane firstmate itself stopped, or one that already reported done, stays
# silent (fm_opencode_descent_skip_lane owns why).
#
# The caller decides what a line means for supervision; bin/fm-watch.sh turns
# each into a wake. Keeping the queue out of this file is what lets the whole
# decision be exercised without the watcher's wake, lock, and recovery graph.
fm_opencode_descent_tick() {  # <state-dir> [<now>]
  local state_dir=$1 now=${2:-} rc=0
  local meta id harness model model_bare kind dispatch_ladder cap horizon bound note ctl_out reason
  local plus_bare target_model target_harness target_rung target_cap
  local target_workspace lane_ws recorded_ws expected_ws
  local recorded after next rest hold_reason when
  local go_bare
  local -a relaunch_args
  # The recorded model is the routed tier the way the launch left it, which
  # may be the plugin's own bare vocabulary when the request named it so; the
  # governance below normalises through the dispatch gate's shared helper, or
  # a bare-recorded lane would read as off-ladder and stay silent past its cap.
  plus_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_PLUS_MODEL")
  go_bare=$(fm_opencode_ladder_bare_model "$FM_OPENCODE_LADDER_GO")

  fm_opencode_descent_off "$state_dir" && return 0
  [ -n "$state_dir" ] && [ -d "$state_dir" ] || return 0
  [ -n "$now" ] || now=$(date +%s)

  # Cheap pre-filter: does this home run any governed ladder lane? A home
  # with none must not start the evaluation clock either.
  harness=''
  for meta in "$state_dir"/*.meta; do
    [ -e "$meta" ] || continue
    harness=$(fm_meta_get "$meta" harness 2>/dev/null) || harness=''
    model=$(fm_meta_get "$meta" model 2>/dev/null) || model=''
    dispatch_ladder=$(fm_meta_get "$meta" dispatch_ladder 2>/dev/null) || dispatch_ladder=''
    case "$harness" in
      opencode*) harness=found; break ;;
      codex) [ "$model" = "$FM_OPENCODE_LADDER_PLUS_MODEL" ] && { harness=found; break; } ;;
      pi)
        [ "$dispatch_ladder" = opencode ] || continue
        fm_opencode_pi_go_enabled || continue
        harness=found
        break
        ;;
    esac
  done
  [ "$harness" = found ] || return 0
  fm_opencode_descent_due "$state_dir" "$now" || return 0
  printf '%s' "$now" > "$state_dir/.opencode-descent-last" 2>/dev/null || true
  fm_opencode_ladder_quota_caps "$state_dir"

  for meta in "$state_dir"/*.meta; do
    [ -e "$meta" ] || continue
    id=$(basename "$meta" .meta)
    case "$id" in ''|*[!A-Za-z0-9._-]*) continue ;; esac
    # Parked and finished lanes are never candidates, even past a proven cap.
    if fm_opencode_descent_skip_lane "$state_dir" "$id"; then
      fm_opencode_descent_clear_task "$state_dir" "$id"
      continue
    fi
    harness=$(fm_meta_get "$meta" harness 2>/dev/null) || continue
    model=$(fm_meta_get "$meta" model 2>/dev/null) || model=''
    dispatch_ladder=$(fm_meta_get "$meta" dispatch_ladder 2>/dev/null) || dispatch_ladder=''
    case "$harness" in
      opencode*) ;;
      codex) [ "$model" = "$FM_OPENCODE_LADDER_PLUS_MODEL" ] || continue ;;
      pi)
        [ "$dispatch_ladder" = opencode ] || continue
        fm_opencode_pi_go_enabled || continue
        ;;
      *) continue ;;
    esac
    kind=$(fm_meta_get "$meta" kind 2>/dev/null) || kind=''
    model_bare=$(fm_opencode_ladder_bare_model "$model")
    case "$model_bare" in
      "$plus_bare"|"$go_bare") ;;
      # An empty record stays governed (it is refused loudly below, never
      # moved blind); anything else bare or prefixed outside the pair stays
      # silent as off-ladder.
      '') [ -z "$model" ] || { fm_opencode_descent_clear_task "$state_dir" "$id"; continue; } ;;
      *) fm_opencode_descent_clear_task "$state_dir" "$id"; continue ;;
    esac

    # The trigger: a quota-scale cap on the supervision-grade verdict. A lane
    # that does not meet it ends whatever episode it was in.
    if [ "$model_bare" = "$plus_bare" ]; then
      fm_opencode_descent_codex_limit_cap "$state_dir" "$id" || true
      cap=$("$_FM_OPENCODE_DESCENT_RETRY" check-cap "$state_dir" "$FM_OPENCODE_LADDER_PLUS_RUNG" 2>/dev/null) || cap=
      case "$cap" in *'status=blocked'*) horizon=${cap##*horizon_s=} ;;
        *) fm_opencode_descent_clear_task "$state_dir" "$id"; continue ;; esac
      cap="$horizon plus plus-record"
    else
      if ! cap=$(fm_opencode_descent_cap "$state_dir" "$id"); then
        fm_opencode_descent_clear_task "$state_dir" "$id"
        continue
      fi
    fi
    horizon=${cap%% *}; rest=${cap#* }
    bound=${rest%% *}; next=${rest#* }
    when=$(fm_opencode_descent_when "$horizon")

    # Preserve the finding where the next spawn can still read it. The
    # per-task sidecar above describes this task's own session and is cleared
    # with it after a move; the cap itself is a property of the rung and the
    # vendor horizon, so it is recorded rung-scoped through
    # bin/fm-opencode-retry.sh record-cap before any per-lane decision below.
    # A Go-tier cap on a secondary-workspace lane belongs to the fourth rung,
    # never to the main Go rung: one capped workspace must not mark the other
    # capped. Text-only evidence carries no trustworthy horizon and is never
    # preserved. Failures are swallowed: preservation must never break the
    # evaluation, and the per-task evidence still drives this lane.
    case "$horizon" in
      ''|*[!0-9]*) : ;;
      *)
        case "$bound" in
          go|unbound)
            if [ "$(fm_opencode_lane_workspace "$state_dir" "$id" 2>/dev/null)" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
              "$_FM_OPENCODE_DESCENT_RETRY" record-cap "$state_dir" "$FM_OPENCODE_LADDER_GO_SECOND_RUNG" "$next" 2>/dev/null || true
            else
              "$_FM_OPENCODE_DESCENT_RETRY" record-cap "$state_dir" go "$next" 2>/dev/null || true
            fi ;;
        esac
        ;;
    esac

    # One automatic attempt per cap episode. A failed move is not retried
    # here - each attempt stops a live worker, so the refusal wake hands the
    # retry to firstmate with full context. Fresh refusal evidence carries a
    # new vendor timestamp and re-arms both the report and the attempt; the
    # same refusal re-observed stays quiet. The marker is cleared above the
    # moment the lane stops meeting the trigger.
    if [ -f "$(fm_opencode_descent_mark "$state_dir" "escalated-$id")" ] \
      && [ "$(cat "$(fm_opencode_descent_mark "$state_dir" "escalated-$id")" 2>/dev/null || true)" = "$next" ]; then
      continue
    fi

    # The captain's own escape, honoured before any move: a worker held on
    # Go past a proven cap on explicit request is not dragged off it, and
    # the hold is said once per episode. The hold is read from the task's
    # recorded captain-route pin because the tick runs outside the launch
    # process.
    hold_reason=$(fm_opencode_task_pin "$state_dir" "$id" 2>/dev/null) || hold_reason=
    if [ -n "$hold_reason" ]; then
      if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
        printf 'override %s %s is capped (%s) but the captain'"'"'s hold is keeping it there: %s\n' \
          "$id" "${model:-<no model>}" "$when" "$hold_reason"
      fi
      continue
    fi

    # Nowhere to move to, or nobody the descent may move: all loud, all once
    # per episode, none of them ever reaching the control plane.
    if [ -z "$model" ]; then
      if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
        printf 'refused %s records no model and a quota-scale cap is proven (%s); refusing to move it blind - firstmate decision needed\n' \
          "$id" "$when"
      fi
      continue
    fi
    target_model=$FM_OPENCODE_LADDER_PLUS_MODEL
    target_harness=codex
    target_workspace=$FM_OPENCODE_WORKSPACE_MAIN
    [ "$model_bare" = "$plus_bare" ] && { target_model=$FM_OPENCODE_LADDER_GO; target_harness=opencode; }
    target_rung=plus
    [ "$target_model" = "$FM_OPENCODE_LADDER_GO" ] && target_rung=go
    target_cap=$("$_FM_OPENCODE_DESCENT_RETRY" check-cap "$state_dir" "$target_rung" 2>/dev/null) || target_cap=
    # A capped Go lane moves forward: to Codex Plus while that rung is
    # uncapped, else to the Go tier's other workspace while that plan is
    # uncapped. A parked secondary lane returns to main while main is
    # uncapped, the mirror move: the lane is parked with no forward progress,
    # not healthy, so no climb-back rule is broken. The model id is the same
    # on both workspaces; the workspace marker is what moves.
    if [ "$model_bare" = "$go_bare" ]; then
      lane_ws=$(fm_opencode_lane_workspace "$state_dir" "$id" 2>/dev/null) || lane_ws=$FM_OPENCODE_WORKSPACE_MAIN
      if [ "$lane_ws" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
        if ! fm_opencode_ladder_go_main_capped "$state_dir"; then
          target_model=$FM_OPENCODE_LADDER_GO
          target_harness=opencode
          target_workspace=$FM_OPENCODE_WORKSPACE_MAIN
          target_rung=$FM_OPENCODE_LADDER_GO_RUNG
          target_cap=
        else
          # Main is capped too: Codex Plus is the only move left. The
          # Plus verdict is already in target_cap above.
          case "$target_cap" in *'status=blocked'*)
            if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
              printf 'refused %s is on Go on the second workspace %s and it is capped too (%s); Go on the main workspace and Codex Plus are capped as well - firstmate decision needed\n' \
                "$id" "$model" "$when"
            fi
            continue ;;
          esac
        fi
      else
        # A main-workspace lane moves to Codex Plus while that rung is
        # uncapped - the Plus verdict is already in target_cap above - else
        # to the second workspace while that plan serves.
        case "$target_cap" in *'status=blocked'*)
          if [ "$(fm_opencode_ladder_pick_go_workspace "$state_dir" 2>/dev/null)" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
            target_model=$FM_OPENCODE_LADDER_GO
            target_harness=opencode
            target_workspace=$FM_OPENCODE_WORKSPACE_SECOND
            target_rung=$FM_OPENCODE_LADDER_GO_SECOND_RUNG
            target_cap=
          else
            if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
              if fm_opencode_second_configured; then
                printf 'refused %s is on the Go tier %s and it is capped too (%s); Codex Plus and Go on the second workspace are capped as well - firstmate decision needed\n' \
                  "$id" "$model" "$when"
              else
                printf 'refused %s is on the Go tier %s and it is capped too (%s); Codex Plus is capped as well and the ladder has no later rung - firstmate decision needed\n' \
                  "$id" "$model" "$when"
              fi
            fi
            continue
          fi ;;
        esac
      fi
    fi
    if [ "$kind" = secondmate ]; then
      if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
        printf 'refused %s is a secondmate on capped Go (%s); automatic relaunch never touches a persistent supervision agent - firstmate decision needed\n' \
          "$id" "$when"
      fi
      continue
    fi

    case "$target_cap" in
      *'status=blocked'*)
        if [ "$target_model" = "$FM_OPENCODE_LADDER_PLUS_MODEL" ] && [ "$model_bare" = "$go_bare" ]; then
          target_model=$FM_OPENCODE_LADDER_GO
          target_harness=opencode
          target_rung=go
          target_cap=$("$_FM_OPENCODE_DESCENT_RETRY" check-cap "$state_dir" "$target_rung" 2>/dev/null) || target_cap=
        fi
        case "$target_cap" in *'status=blocked'*)
          # The last overflow: the Go tier's other workspace, through the same
          # pick the dispatch gate uses. The model id stays the Go id;
          # the workspace marker carries the move.
          if [ "$target_model" = "$FM_OPENCODE_LADDER_GO" ] \
            && [ "$(fm_opencode_ladder_pick_go_workspace "$state_dir" 2>/dev/null)" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
            target_workspace=$FM_OPENCODE_WORKSPACE_SECOND
            target_rung=$FM_OPENCODE_LADDER_GO_SECOND_RUNG
            target_cap=
          fi ;;
        esac
        case "$target_cap" in *'status=blocked'*)
          if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
            printf 'refused %s cannot descend from %s because every later rung is capped\n' "$id" "$model"
          fi
          continue ;;
        esac
        ;;
    esac

    # The Go tier's workspace follows the dispatch pick even when the main
    # workspace is uncapped: monthly priority can serve the second plan
    # first, and a relaunch must land where a new spawn would. A lane the
    # branches above already placed on the second workspace keeps it.
    if [ "$target_model" = "$FM_OPENCODE_LADDER_GO" ] \
      && [ "$target_workspace" = "$FM_OPENCODE_WORKSPACE_MAIN" ] \
      && [ "$(fm_opencode_ladder_pick_go_workspace "$state_dir" 2>/dev/null)" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
      target_workspace=$FM_OPENCODE_WORKSPACE_SECOND
      target_rung=$FM_OPENCODE_LADDER_GO_SECOND_RUNG
    fi

    # The move: the proven Go cap relaunches the lane onto the next uncapped
    # rung. The handoff note owns the ambiguity when the cap evidence carries
    # no model binding, exactly as the dispatch gate's own notice does.
    case "$horizon" in
      ''|*[!0-9]*)
        note="opencode descent: Go tier $FM_OPENCODE_LADDER_GO is proven exhausted (Go-tier cap shown in the lane's pane, no retry horizon on record" ;;
      *)
        note="opencode descent: Go tier $FM_OPENCODE_LADDER_GO is proven exhausted (quota-scale retry backoff, $when" ;;
    esac
    if [ "$bound" = unbound ]; then
      note="$note, with no model binding on the cap evidence - biasing toward Go rather than stalling"
    fi
    if [ "$target_workspace" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
      note="$note); the capped session is parked with no forward progress. Relaunched onto $target_harness rung $target_model on the second workspace. Continue the task from this note plus the brief and committed work."
    else
      note="$note); the capped session is parked with no forward progress. Relaunched onto $target_harness rung $target_model. Continue the task from this note plus the brief and committed work."
    fi
    relaunch_args=("$id" relaunch --harness "$target_harness" --model "$target_model" --note "$note")
    [ "$target_harness" != codex ] || relaunch_args+=(--effort "$(fm_opencode_ladder_plus_effort)")
    # The workspace flag rides every Go-tier move in both directions: a move
    # to main must clear a stale secondary marker, and an absent flag would
    # preserve it (bin/fm-spawn.sh). Codex targets carry no flag, exactly as
    # before - leaving the tier drops the pin by itself.
    if [ "$target_model" = "$FM_OPENCODE_LADDER_GO" ]; then
      if [ "$target_workspace" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
        relaunch_args+=(--opencode-workspace secondary)
      else
        relaunch_args+=(--opencode-workspace main)
      fi
    fi
    ctl_out=$(FM_HOME="${FM_HOME:-}" FM_STATE_OVERRIDE="$state_dir" \
      "$(fm_opencode_descent_control)" "${relaunch_args[@]}" 2>&1) && rc=0 || rc=$?
    if [ "$rc" -ne 0 ]; then
      reason=${ctl_out%%$'\n'*}
      [ -n "$reason" ] || reason="the control plane refused without a reason"
      if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
        printf 'refused %s could not be relaunched from %s onto %s: %s\n' \
          "$id" "$model" "$target_model" "$reason"
      fi
      continue
    fi
    # THE MOVE IS NOT FINISHED UNTIL IT IS WRITTEN DOWN. Recovery relaunches
    # from the durable record, so a record still naming the capped tier would bring the
    # next relaunch straight back onto the capped tier. A move within the Go
    # tier keeps the same model id, so the workspace marker is verified too:
    # a record still pinned to the main workspace would bring the next
    # relaunch straight back onto the capped rung.
    recorded=$(fm_meta_get "$meta" model 2>/dev/null) || recorded=''
    recorded_ws=$(fm_meta_get "$meta" opencode_workspace 2>/dev/null) || recorded_ws=''
    [ "$target_workspace" = "$FM_OPENCODE_WORKSPACE_SECOND" ] && expected_ws=$FM_OPENCODE_WORKSPACE_SECOND || expected_ws=''
    if [ "$recorded" = "$target_model" ] && [ "$recorded_ws" = "$expected_ws" ]; then
      # The dead session's backoff describes nobody now; leaving it behind
      # would brand the replacement lane capped on its first evaluation.
      "$_FM_OPENCODE_DESCENT_RETRY" clear "$state_dir" "$id" 2>/dev/null || true
      fm_opencode_descent_clear_task "$state_dir" "$id"
      if [ "$target_workspace" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
        printf 'relaunched %s %s -> %s on the second workspace\n' "$id" "$model" "$target_model"
      else
        printf 'relaunched %s %s -> %s\n' "$id" "$model" "$target_model"
      fi
    else
      after=${recorded:-<unreadable>}
      if fm_opencode_descent_escalate_once "$state_dir" "$id" "$next"; then
        printf 'unrecorded %s was relaunched onto %s but its durable record still names %s; a further relaunch would bring it back onto the capped tier - investigate before moving it again\n' \
          "$id" "$target_model" "$after"
      fi
    fi
  done
  return 0
}
