#!/usr/bin/env bash
# fm-crew-state.sh - deterministic read of a crew's CURRENT state.
#
# Why this exists: state/<id>.status is an append-only, best-effort EVENT LOG.
# Crews append only wake-worthy transitions (done/needs-decision/blocked/paused/failed)
# and nothing when they silently resume, so `tail -1` of that log reports the
# last EVENT, not the current STATE. After firstmate resolves a needs-decision
# or blocked and the crew resumes (responds to the gate, the pipeline fixes, it
# re-validates), the log's last line stays stale. This helper never infers the
# current state from a tail of the log: it reads the authoritative source (a
# no-mistakes run-step attributed under bin/fm-nm-run-lib.sh's contract, else
# the pane busy-signature) and reconciles the possibly-stale log against it.
# A ship `done:` is current-state done only when bin/fm-dod-lib.sh accepts the
# named head as reachable outside the worker's disposable copy; otherwise blocked.
#
# The determinism lives entirely here - run-step / pane / log reads, fixed
# mapping logic, and terminal passed-run PR detail from bounded evidence only,
# with no heuristics and no LLM.
# For a terminal passed no-mistakes run, a matching merge-poll retirement
# receipt is local merged evidence; otherwise a 5s-bounded forge read is tried.
# FM_CREW_STATE_NO_FORGE=1 keeps the receipt read but skips the forge fallback.
# An absent or unreadable PR identity yields an honest unknown, never an
# optimistic merged claim.
# Output is one stable, parseable, token-tight line firstmate can read every
# heartbeat:
#
#   state: <working|parked|done|blocked|paused|failed|stopped|exited|unknown> · source: <run-step|pane|status-log|declared-stop|remote-endpoint|none> · <detail>
#
# Logic, in order:
#   1. Resolve worktree + backend target + kind from state/<id>.meta. A meta
#      recording remote_host= is a remote secondmate: its worktree and endpoint
#      live on that host, so the local worktree and pane reads are skipped and
#      the remote host is asked for the endpoint's recovery-grade state
#      (fm-on.sh + fm-remote-secondmate-control.sh state). alive falls through
#      to the routed status log; dead/missing report the remote verdict; an
#      unreachable or unreadable remote reports unknown-remote, never a false
#      gone/dead.
#   2. Matching no-mistakes run for this crew's branch AND current code identity,
#      active or terminal (from `axi status`, or the coarse `no-mistakes runs`
#      fallback)? Branch name alone is not enough: a historical run on a reused
#      branch whose head was rewritten or diverged must not be attributed.
#      A run EXECUTING on this crew's branch (pending, running, fixing or ci -
#      the detail-object vocabulary, which carries all four; the selected route
#      re-reads it by id and the legacy route passes the same detail SHAPE, and
#      neither is the overview table's narrower status column)
#      is authoritative REGARDLESS of head (fm_nm_run_is_executing in
#      bin/fm-nm-run-lib.sh) as long as an explicit probe has not ANSWERED that
#      the daemon is down (nm_daemon_answered_down): the pipeline rebases the
#      branch and
#      commits its fix rounds in its own checkout, so a live run's head
#      routinely differs from the local head, and reading an older run that
#      still matches the local head would report a working crew as failed - but
#      a record still saying `running` because the daemon died under it is
#      evidence from a dead instrument, exactly as for a terminal record, and
#      must not answer once the worktree has moved off the run head. Every
#      other run -
#      terminal, or parked at a gate - matches only when its head equals the
#      worktree HEAD, or the worktree HEAD is an ancestor of the run head
#      (pipeline fix commits advanced the run on the same line of history);
#      local work that advanced past the run head, or diverged from it,
#      invalidates attribution. While the pipeline owns the branch
#      (branch_sync.state=pipeline_owned), its own custody attribution also
#      binds ANY ACTIVE run - executing or parked - without head equality
#      (fm_nm_run_is_pipeline_owned_active in bin/fm-nm-run-lib.sh), and that
#      route is deliberately OUTSIDE the daemon rule below: while the pipeline
#      holds custody its own attribution is the attribution, and second-guessing
#      it here is a change to a route this fix does not otherwise touch.
#      A parked run head whose commit object the task copy never fetched cannot
#      be verified locally; that row is recognized only as a provable
#      pipeline-owned continuation - the branch's ACTIVE newest ledger row,
#      anchored by the row immediately before it having ended at exactly this
#      worktree's head (rule owned by fm_nm_runs_status_for_worktree in
#      bin/fm-nm-run-lib.sh). The coarse runs-ledger fallback has NO
#      branch-name-only acceptance: an executing `axi status` record is the one
#      live bind, so a ledger row that cannot be tied to this worktree's head
#      never answers on branch name alone. A record whose daemon has ANSWERED
#      down reads unknown and names the dead instrument on exactly ONE route:
#      the id-addressed selected run whose head this copy cannot resolve and
#      whose continuation the ledger anchor proves. The coarse ledger fallback
#      carries NO such verdict - it reports the same status word for a
#      head-matching row and an anchored one, so any rule there would also catch
#      head-tied rows, and a record whose head still equals or precedes the
#      worktree HEAD keeps its original working reading, as it always has.
#      A record whose
#      identity is proven by NEITHER head nor ledger anchor is not this
#      worktree's run to report on: it leaves HAVE_RUN=0 so the pane and status
#      log answer, because a stale record naming this branch must never override
#      a crew that is visibly working.
#      A run PARKED at a gate is exempt from the dead-instrument verdict: an
#      open decision stays open when the instrument dies, so it keeps its gate
#      and findings.
#      fm_nm_select_run in bin/fm-nm-run-lib.sh owns complete run selection
#      and ambiguity reporting. The selected run's id-addressed status must
#      agree on id, branch, and live/terminal class before attribution;
#      disagreement reports unknown with available candidate ids.
#      The run-step is AUTHORITATIVE: running/fixing -> working, ci -> working
#      (the id-addressed detail read carries step words the overview does not),
#      awaiting_approval/fix_review -> parked (with gate findings), terminal
#      passed/checks-passed/passed-with-override/passed-with-skips -> done,
#      failed -> failed, cancelled -> unknown (no verdict unless the green
#      delivery safeguard below applies). A cancelled outcome takes precedence
#      over an interrupted step's failed status or outstanding gate findings;
#      it does not rewrite historical events or backlog records.
#      passed-with-override is a passing outcome
#      carrying an explicitly approved Test or CI exception (no-mistakes' own
#      vocabulary), read identically to a clean passed. passed-with-skips is
#      also a passing outcome (publication or CI verification was
#      automatically skipped, no-mistakes' own vocabulary), read as done but
#      with that skip kept visible in the detail, unlike a clean passed.
#      EXCEPT: while
#      the active step is ci, `axi status` alone cannot tell "still waiting on
#      checks" from "checks green, waiting on merge" (see nm_ci_checks_state) -
#      a check of the full ci-step log overrides working -> done once checks read
#      green, so a green PR is never silently read as still-validating. And a
#      terminal failed or cancelled run whose only unfinished step is the ci
#      monitor, after every substantive step completed (an explicitly skipped
#      rebase is allowed) and the ci log's last marker reads checks green,
#      also reads done only when the bounded forge read confirms the PR is
#      open (held-for-merge) or merged. Closed, missing, unreadable, or skipped
#      forge evidence leaves the original failed or unknown classification.
#      A monitor whose only remaining job is to observe a merge decision must not
#      convert the absence of that decision into a failure verdict
#      (nm_reclassify_failed_run_as_held_green). In the
#      coarse runs-ledger fallback (no steps table, no ci log), a terminal
#      FAILED record whose daemon an explicit probe proves down reads unknown,
#      never failed: an instrument failure must not read as work failure
#      (nm_daemon_probe_down).
#   3. Reconcile the status log through fm-classify-lib.sh's status_current_line:
#      open decisions survive unrelated events and continuation prose cannot
#      hide a declaration. Ship/scout terminal declarations supersede stale log
#      decisions. If it says needs-decision/blocked but
#      the run-step shows the run moved on, the log is deterministically stale and
#      is flagged superseded. A genuinely parked run plus a needs-decision log
#      agree, and are reported as parked. A `blocked:` line that reports a
#      refused or missing daemon socket remains blocked even if an attributed
#      run record is stale or terminal, for as long as that blocker is still the
#      log's latest event. The same holds for any open decision when the run
#      record itself is UNVERIFIED (its daemon answered down): the crew saw its
#      gate or blocker first hand, so needs-decision stays parked and blocked
#      stays blocked, with the unverified record named as the reason.
#      Other daemon, timeout, or unreachability
#      claims are superseded BECAUSE THE RUN IS ALIVE when the run is
#      running/fixing with recent reported activity: a killed or timed-out drive
#      call is not daemon death, so that claim is answered by steering the crew
#      to reattach, not by escalating.
#   4. No run for this crew (pre-validation, or kind=scout): ask the endpoint
#      what is actually there before believing any record about it. A readable
#      target proves only that the SHELL survives, so a frozen agent reports
#      suspended, an agent firstmate itself stopped through the control plane
#      reports `stopped`, and an agent that EXITED with no terminal status line
#      reports `exited` - none of them may fall through to a record its own
#      writer is no longer around to advance. An agent that is merely idle or
#      unreachable is never reported gone, and neither is one whose record was
#      published so recently that its harness cannot be expected to have
#      started yet (the registration grace at within_spawn_grace, below). Then
#      the recorded backend's pane busy state, then the status log's last line
#      only when its verb maps to a recognized run-state. A busy opencode lane
#      whose own record event is still session-retry and whose retry-backoff
#      horizon is quota-scale reads `blocked`, not working
#      (fm_crew_opencode_cap_detail, contract in bin/fm-opencode-retry.sh).
#      Decision-only events such as `resolved` never become current state or
#      detail.
#   5. Missing meta or torn-down worktree: report unknown · none. If no run is
#      attributed to this crew, a dead endpoint also reports unknown · none rather
#      than trusting a stale status log. On tmux and herdr, which own a
#      recovery-grade classifier, only its positive death evidence reads as gone
#      (the endpoint is authoritatively absent, or its pane holds no agent); an
#      endpoint that merely failed to answer reports unknown · none as
#      unreachable, and an alive endpoint whose scrollback read failed is still
#      classified by step 4. Backends with no classifier keep reading a failed
#      capture as gone. The fallback's own comment owns the per-verdict rules.
#
# Read-only and side-effect free. Always exits 0 on a successful read regardless
# of state; exit 2 only on a usage error (no id).
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

# shellcheck source=bin/fm-tmux-lib.sh
. "$SCRIPT_DIR/fm-tmux-lib.sh"
# shellcheck source=bin/fm-backend.sh
. "$SCRIPT_DIR/fm-backend.sh"
# shellcheck source=bin/fm-classify-lib.sh
. "$SCRIPT_DIR/fm-classify-lib.sh"
# shellcheck source=bin/fm-busy-lib.sh
. "$SCRIPT_DIR/fm-busy-lib.sh"
# shellcheck source=bin/fm-nm-run-lib.sh
. "$SCRIPT_DIR/fm-nm-run-lib.sh"
# shellcheck source=bin/fm-stopped-lib.sh
. "$SCRIPT_DIR/fm-stopped-lib.sh"

ID=${1:-}
[ -n "$ID" ] || { echo "usage: fm-crew-state.sh <id>" >&2; exit 2; }

# Fleet snapshot composition supplies its captured metadata path here so every
# state read resolves the same task generation selected by that snapshot.
META=${FM_CREW_STATE_META_OVERRIDE:-"$STATE/$ID.meta"}
LOG=${FM_CREW_STATE_STATUS_OVERRIDE:-"$STATE/$ID.status"}
NM_TIMEOUT=${FM_CREW_STATE_NM_TIMEOUT:-10}
case "$NM_TIMEOUT" in ''|*[!0-9]*) NM_TIMEOUT=10 ;; esac
# How many of the most recent `no-mistakes runs` rows each ledger read
# (fm_nm_runs_status_for_worktree in bin/fm-nm-run-lib.sh) scans for the legacy
# fallback or an unfetched-head continuation (docs/configuration.md owns the
# setting). Generous enough to
# still find a branch's own run on a busy multi-crew fleet without listing the
# entire history every call.
FM_CREW_STATE_RUNS_LIMIT=${FM_CREW_STATE_RUNS_LIMIT:-200}
case "$FM_CREW_STATE_RUNS_LIMIT" in ''|*[!0-9]*) FM_CREW_STATE_RUNS_LIMIT=200 ;; esac
# Seconds after a task's endpoint record is published during which an
# agent-free endpoint is NOT yet evidence that the agent left. See
# within_spawn_grace below for what it guards; the value is bounded from both
# sides by constants this fleet already lives with:
#   floor - bin/fm-spawn.sh already allows a launched harness 30s to become
#     usable in its endpoint (kimi_wait_for_ready: 60 polls x 0.5s), which is
#     firstmate's own standing statement of how slow a start may legitimately
#     be. The grace has to cover at least that, plus the launch line's delivery,
#     which happens after the record is published.
#   ceiling - the watcher does not escalate an idle worker as a possible wedge
#     until FM_STALE_ESCALATE_SECS (240s, bin/fm-watch.sh), so any grace well
#     under that costs a worker that really did die on arrival no supervision
#     latency it did not already have.
# 60 doubles the known startup budget for cold-start headroom (first run after
# an upgrade, a cold binary, a loaded machine) and stays at a quarter of the
# escalation floor. Cheap to be generous here and expensive to be tight: being
# late to notice a dead-on-arrival worker costs one poll interval, while being
# early to call a starting worker gone licenses a second agent on its worktree.
FM_CREW_STATE_SPAWN_GRACE=${FM_CREW_STATE_SPAWN_GRACE:-60}
case "$FM_CREW_STATE_SPAWN_GRACE" in ''|*[!0-9]*) FM_CREW_STATE_SPAWN_GRACE=60 ;; esac
SEP=' · '

# Emit the one canonical line and exit 0. Detail is optional.
emit() {  # <state> <source> [detail]
  local line="state: $1${SEP}source: $2"
  [ -n "${3:-}" ] && line="$line${SEP}$3"
  printf '%s\n' "$line"
  exit 0
}

# --- meta resolution --------------------------------------------------------

[ -f "$META" ] || emit unknown none "no metadata for $ID"

meta_value() {  # <key>
  grep "^$1=" "$META" 2>/dev/null | tail -1 | cut -d= -f2- || true
}

WT=$(meta_value worktree)
KIND=$(meta_value kind)
HARNESS=$(meta_value harness)
REMOTE_HOST=$(meta_value remote_host)
[ -n "$KIND" ] || KIND=ship

# Mate-watcher quiet detail (heartbeat backstop): for a local secondmate task,
# the same model-aware verdict the primary watcher poll tick consults is
# evaluated here, so every heartbeat read of this task also sees a quiet mate
# home. Only a down verdict appends anything - healthy and idle mates leave
# every emitted line byte-identical. Endpoint facts (suspended, exited,
# stopped) dominate and are left untouched: this rides only the routine
# fallbacks below. The verdict is read-only; queueing and episode markers
# stay with the poll tick and locked session start.
MATE_QUIET_DETAIL=
if [ "$KIND" = secondmate ] && [ -z "$REMOTE_HOST" ]; then
  # fm-wake-lib.sh is a canonical lint root itself. Stop duplicate
  # source-graph expansion here: following its wake graph from this large
  # runtime exceeds the bounded CI lint worker while adding no uncovered
  # file (precedent: fm-watch.sh cuts fm-push-transition-lib.sh the same
  # way for the same reason).
  # shellcheck source=/dev/null
  command -v fm_mate_watcher_health >/dev/null 2>&1 || . "$SCRIPT_DIR/fm-wake-lib.sh"
  MATE_HOME=$(meta_value home)
  MATE_TASK=$(basename "$META")
  MATE_TASK=${MATE_TASK%.meta}
  if [ -n "$MATE_HOME" ] && [ -n "$MATE_TASK" ] \
    && [ -f "$MATE_HOME/.fm-secondmate-home" ] && [ ! -L "$MATE_HOME/.fm-secondmate-home" ] \
    && [ "$(cat "$MATE_HOME/.fm-secondmate-home" 2>/dev/null || true)" = "$MATE_TASK" ]; then
    MATE_MODEL=$(fm_supervision_model_for_harness "$HARNESS")
    fm_mate_watcher_health "$MATE_HOME/state" "$MATE_HOME/bin/fm-watch.sh" \
      "${WATCHER_STALE_GRACE:-300}" "$MATE_HOME" "$MATE_HOME" "$MATE_MODEL"
    if [ "$FM_MATE_WATCHER_DOWN" = true ]; then
      MATE_QUIET_DETAIL="mate watcher quiet: $FM_MATE_WATCHER_DESC"
    fi
  fi
fi

# A torn-down (or never-created) worktree has no current state to read. A
# remote secondmate's recorded worktree is a path on ITS host, so the local
# probe proves nothing for it - the remote arm below reads the true source.
if [ -z "$REMOTE_HOST" ] && { [ -z "$WT" ] || [ ! -d "$WT" ]; }; then
  emit unknown none "worktree gone (torn down?)"
fi

# --- spawn-registration grace ----------------------------------------------
# Epoch seconds at which THIS incarnation's endpoint record was published.
# bin/fm-spawn.sh stamps spawned_at= on every fresh spawn and every relaunch. A
# record written by an older spawn carries none, and the record file's own mtime
# bounds the same thing instead - a later writer (bin/fm-pr-check.sh records pr=
# into it) can only move that forward, which lengthens the grace rather than
# shortening it, and long is the safe direction for a guard whose failure mode
# is a duplicate agent.
record_published_at() {
  local at
  at=$(meta_value spawned_at)
  case "$at" in
    ''|*[!0-9]*) ;;
    *) printf '%s' "$at"; return 0 ;;
  esac
  if [ "$(uname 2>/dev/null || true)" = Darwin ]; then
    stat -f %m "$META" 2>/dev/null
  else
    stat -c %Y "$META" 2>/dev/null
  fi
}

# True while this task's record is too young for an agent-free endpoint to mean
# anything. The record is published the moment the endpoint exists, and the
# harness only enters that endpoint's foreground process group once it has
# actually started - so until it does, the endpoint is shells alone, which is
# byte-for-byte what a departed agent leaves behind. fm_backend_agent_state is
# right either way; it is being asked a question it cannot answer.
#
# That gap is why this exists and why it guards `exited` alone: `exited` is the
# one verdict that licenses a relaunch, and a relaunch onto a worker that is
# merely still starting puts a SECOND agent on one worktree. Elapsed time is
# the only thing that separates the two cases - no further endpoint evidence
# can, because a harness that has not started has painted nothing to read.
# A timestamp that cannot be read at all, or one in the future (clock skew),
# holds the grace rather than dropping it, and says which case it is in
# SPAWN_GRACE_REASON so the emitted detail names what is actually unknown
# instead of implying a measured age.
SPAWN_GRACE_REASON=
within_spawn_grace() {
  local at now age
  SPAWN_GRACE_REASON=
  at=$(record_published_at)
  case "$at" in
    ''|*[!0-9]*)
      SPAWN_GRACE_REASON="this task's record carries no readable publication time, so how long ago it was published cannot be established"
      return 0
      ;;
  esac
  now=$(date +%s)
  age=$((now - at))
  [ "$age" -lt "$FM_CREW_STATE_SPAWN_GRACE" ] || return 1
  [ "$age" -ge 0 ] || age=0
  SPAWN_GRACE_REASON="this task's record was published ${age}s ago, inside the ${FM_CREW_STATE_SPAWN_GRACE}s a launched harness is allowed to finish starting"
  return 0
}

# --- status log ------------------------------------------------------------

# Last non-empty status line, and its leading verb (the word before the colon).
# A line predating THIS incarnation's publication is not this crew's: after a
# relaunch the log still ends with the previous incarnation's last line until
# the replacement appends its own, and reading it would misattribute the old
# verdict to the new worker. The publication clock is spawned_at= content when
# the record carries it (bin/fm-spawn.sh stamps it on every fresh spawn and
# every relaunch, and content survives later meta rewrites and snapshot
# copies), falling back to the record file's own mtime for older records -
# whose staleness proof is weaker but still rejects an ancient log.
# The mtime fallback compares against the ORIGINAL record's mtime: the fleet
# snapshot must preserve it when capturing metadata (cp -p), because a
# freshly copied meta would discard lines this incarnation actually wrote.
log_last_line() {
  [ -f "$LOG" ] || return 1

  local at log_mtime
  at=$(record_published_at)
  case "$at" in
    ''|*[!0-9]*) ;;
    *)
      if [ "$(uname 2>/dev/null || true)" = Darwin ]; then
        log_mtime=$(stat -f %m "$LOG" 2>/dev/null || true)
      else
        log_mtime=$(stat -c %Y "$LOG" 2>/dev/null || true)
      fi
      if [ -n "$log_mtime" ] && [ "$log_mtime" -lt "$at" ]; then
        return 1
      fi
      ;;
  esac

  grep -v '^[[:space:]]*$' "$LOG" 2>/dev/null | tail -1
}
# Map a status-log verb onto a canonical state for the fallback path. `paused` is
# the deliberate-external-wait verb (fm-classify-lib.sh's FM_CLASSIFY_PAUSED_VERB):
# a crew with no active run and an idle pane that declared a known external wait
# reports `paused` distinctly, so a supervisor reading this sees a declared pause
# and its reason rather than a wedge-suspect idle.
# A ship `done:` is not current-state done while bin/fm-dod-lib.sh refuses the
# named-head reachability gate: that claim is blocked so a disposable copy is
# not treated as finished-and-safe.
emit_ship_status_done() {  # [extra-detail]
  local extra=${1:-} reason
  if reason=$(fm_dod_accept_ship_done "$KIND" "$(meta_value mode)" "$WT" "$(meta_value project)" "$LOG_LINE" "$STATE" "$ID" "$META"); then
    emit "done" status-log "$(status_line_note "$LOG_LINE")${extra:+${SEP}$extra}"
  fi
  emit blocked status-log "$reason"
}

map_log_state() {  # <line>
  if status_is_paused "$1"; then
    echo paused
    return
  fi
  case "$(status_line_verb "$1")" in
    working)        echo working ;;
    needs-decision) echo parked ;;
    blocked)        echo blocked ;;
    done)           echo "done" ;;
    failed)         echo failed ;;
    *)              echo unknown ;;
  esac
}

LOG_LINE=$(status_current_line "$LOG" "$KIND")
LOG_VERB=$(status_line_verb "$LOG_LINE")

# --- remote secondmate: the true source is the remote endpoint ---------------
# A remote mate's recorded worktree and backend target live on its own host, so
# the local worktree probe above and the local pane reads below would misreport
# a healthy remote mate as gone or dead. Ask the remote host for the endpoint's
# recovery-grade state over the same fm-on.sh transport fm-send uses, then read
# current activity from the routed status log exactly as for a local
# secondmate (an idle endpoint is healthy for a secondmate either way). An
# unreachable host or unreadable endpoint is reported as unknown-remote -
# explicitly NOT proof of death - so a transport blip never reads as a torn
# down or dead mate; only the remote host's own dead/missing verdict may say
# the endpoint is actually gone.
if [ -n "$REMOTE_HOST" ]; then
  if ! REMOTE_STATE=$(FM_HOME="$FM_HOME" "$SCRIPT_DIR/fm-on.sh" "$ID" \
    fm-remote-secondmate-control.sh state "$ID" < /dev/null 2>/dev/null); then
    REMOTE_STATE=
  fi
  REMOTE_STATE=$(printf '%s\n' "$REMOTE_STATE" | tail -1)
  case "$REMOTE_STATE" in
    alive)
      if [ -n "$LOG_VERB" ]; then
        LOG_STATE=$(map_log_state "$LOG_LINE")
        if [ "$LOG_STATE" != unknown ]; then
          emit "$LOG_STATE" status-log "$(status_line_note "$LOG_LINE")${SEP}remote endpoint alive on $REMOTE_HOST"
        fi
      fi
      emit unknown remote-endpoint "alive on $REMOTE_HOST (an idle secondmate is healthy)"
      ;;
    dead|missing)
      emit unknown remote-endpoint "remote endpoint $REMOTE_STATE on $REMOTE_HOST"
      ;;
    '')
      emit unknown remote-endpoint "unknown-remote: $REMOTE_HOST unreachable or endpoint unreadable (not proof of death)"
      ;;
    *)
      emit unknown remote-endpoint "unknown-remote: endpoint state '$REMOTE_STATE' on $REMOTE_HOST (not proof of death)"
      ;;
  esac
fi

# pane_readable is consulted ONLY in the no-run fallback below. The run-step path
# stays authoritative regardless of pane liveness - judge by the run-step, not the
# shell - so a finished crew whose endpoint has closed still reports its run-step
# state (e.g. done) instead of being masked as unknown. Backend-aware
# (fm_backend_of_meta defaults absent backend= to tmux, the P1 contract): a
# herdr task is read through fm_backend_capture instead of a bare tmux probe.
TASK_BACKEND=$(fm_backend_of_meta "$META")
BACKEND_TARGET=$(fm_backend_target_of_meta "$META")
EXPECTED_LABEL="fm-$ID"
pane_readable() {  # <target>
  case "$TASK_BACKEND" in
    tmux) tmux display-message -p -t "$1" '#{pane_id}' >/dev/null 2>&1 ;;
    *) fm_backend_capture "$TASK_BACKEND" "$1" 1 "$EXPECTED_LABEL" >/dev/null 2>&1 ;;
  esac
}
# crew_busy_verdict: the crew's semantic busy state from the one contract
# owner (bin/fm-busy-lib.sh), as "<busy|idle|unknown> <source>". A converted
# adapter answers from its own lifecycle record; Grok answers from its
# isolated rendered-tail fallback; a herdr crew's native `busy` is accepted
# when no record exists, but its native `idle` is NOT, because agent.get
# reports generation state (idle while a crew blocks on its own long-running
# foreground tool call) rather than turn state. The tail is captured
# unconditionally (not just for Grok) so this authoritative read also sees
# fm_busy_lib's launch-prompt backstop: without it, a launch parked on a
# recognized interactive prompt would report `working` here while the
# watcher's own poll (which always captures a tail) already classifies it
# unknown - the exact split issue #1792 describes for a different cause.
crew_busy_verdict() {  # <target>
  local tail40
  tail40=$(fm_backend_capture "$TASK_BACKEND" "$1" 40 "$EXPECTED_LABEL" 2>/dev/null) || tail40=''
  fm_busy_classify "$TASK_BACKEND" "$1" "$HARNESS" "$ID" "$STATE" "$tail40"
}

# fm_crew_opencode_cap_detail: the usage-cap override. A capped opencode lane
# still reports busy - the vendor holds the session in `retry` with an
# hours-long backoff instead of failing - so a busy verdict alone cannot tell
# it from a working lane. Prints the blocked detail and returns 0 on either
# shape: the structural one (all three hold: the task runs on opencode, its
# semantic record's own event is still the latched `session-retry` - a
# genuinely resumed turn writes `session-busy` first, which drops this verdict
# even if a stale sidecar survives - and bin/fm-opencode-retry.sh, contract
# owned there, classifies the vendor's own backoff horizon as quota-scale),
# or the idle one (2026-09-14: the record says the turn ended on the plugin's
# idle event and the pane tail carries the cap verbatim through the same
# helper's scan-text; a working lane's scrollback never counts). Anything else
# returns 1, keeping the existing working verdict. Reads only the task's own
# record, sidecar, and pane tail.
fm_retry_horizon_human() {  # <seconds>
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

fm_crew_opencode_cap_detail() {
  local rec rest r_state r_source r_event retry_out f cap_tail
  local r_status='' r_attempt='' r_horizon='' r_model='' r_match=''
  case "$HARNESS" in opencode*) ;; *) return 1 ;; esac
  rec=$(fm_busy_record_read "$STATE" "$ID") || return 1
  r_state=${rec%% *}; rest=${rec#* }
  r_source=${rest%% *}; rest=${rest#* }
  r_event=${rest%% *}
  [ "$r_source" = opencode-plugin ] || return 1
  if [ "$r_state" = busy ] && [ "$r_event" = session-retry ]; then
    retry_out=$("$SCRIPT_DIR/fm-opencode-retry.sh" check "$STATE" "$ID" 2>/dev/null) || return 1
    for f in $retry_out; do
      case "$f" in
        status=*) r_status=${f#status=} ;;
        attempt=*) r_attempt=${f#attempt=} ;;
        horizon_s=*) r_horizon=${f#horizon_s=} ;;
        model=*) r_model=${f#model=} ;;
      esac
    done
    [ "$r_status" = blocked ] || return 1
    case "$r_attempt" in ''|*[!0-9]*) return 1 ;; esac
    case "$r_horizon" in ''|*[!0-9]*) return 1 ;; esac
    if [ -n "$r_model" ]; then
      r_model=", model $r_model"
    fi
    printf 'opencode quota-scale retry backoff (attempt %s, next retry in %s%s): vendor holds the session in retry, no forward progress until then' \
      "$r_attempt" "$(fm_retry_horizon_human "$r_horizon")" "$r_model"
    return 0
  fi
  # The idle shape: the turn ended on the plugin's idle event with no sidecar
  # left, and the pane tail still shows the cap verbatim. A busy lane never
  # reaches here, so old scrollback on a working lane cannot report blocked.
  [ "$r_state" = idle ] || return 1
  case "$r_event" in session-idle|session-status-idle) ;; *) return 1 ;; esac
  case "${TASK_BACKEND:-}" in '' ) return 1 ;; esac
  case "${BACKEND_TARGET:-}" in '' ) return 1 ;; esac
  cap_tail=$(mktemp "${TMPDIR:-/tmp}/fm-opencode-cap.XXXXXX") || return 1
  if ! fm_backend_capture "$TASK_BACKEND" "$BACKEND_TARGET" \
    "${FM_OPENCODE_CAP_TEXT_LINES:-60}" "$EXPECTED_LABEL" > "$cap_tail" 2>/dev/null; then
    rm -f "$cap_tail"
    return 1
  fi
  retry_out=$("$SCRIPT_DIR/fm-opencode-retry.sh" scan-text --file "$cap_tail" 2>/dev/null) \
    || { rm -f "$cap_tail"; return 1; }
  rm -f "$cap_tail"
  for f in $retry_out; do
    case "$f" in match=*) r_match=${f#match=} ;; esac
  done
  [ -n "$r_match" ] || return 1
  printf 'opencode free-tier cap shown in pane (idle after the refusal, %s, no retry horizon on record): vendor parked the session with no forward progress' \
    "$r_match"
}

# --- no-mistakes run lookup (authoritative when a run matches this branch) --
# trim, strip_quotes, the bounded nm_run call, nm_field's TOON parse, and the
# attribution helpers below are thin wrappers over the ONE owner in
# bin/fm-nm-run-lib.sh, shared with fm-teardown.sh's pre-teardown run abort.

trim() { fm_nm_trim "$@"; }
strip_quotes() { fm_nm_strip_quotes "$@"; }
nm_run() {  # <args...>
  fm_nm_run "$WT" "$NM_TIMEOUT" "$@"
}

# Scalar value of a TOON key in the captured run output ($RUN_OUT).
RUN_OUT=""
nm_field() {  # <key>
  fm_nm_field "$RUN_OUT" "$1"
}

pr_read_record_bounded() {  # <owner> <repo> <number>
  local record state merged
  # shellcheck disable=SC2016  # The inner script expands after bash -c receives positional args.
  if ! record=$(fm_run_timed 5 bash -c '
    . "$1"
    fm_pr_github_read_record "$2" "$3" "$4" || exit 1
    printf "state=%s\nmerged=%s\n" "$FM_PR_RECORD_STATE" "$FM_PR_RECORD_MERGED"
  ' _ "$SCRIPT_DIR/fm-pr-lib.sh" "$1" "$2" "$3" 2>/dev/null); then
    return 1
  fi
  state=$(printf '%s\n' "$record" | sed -n 's/^state=//p' | head -1)
  merged=$(printf '%s\n' "$record" | sed -n 's/^merged=//p' | head -1)
  [ -n "$state" ] || return 1
  [ "$merged" = true ] || [ "$merged" = false ] || return 1
  FM_PR_RECORD_STATE=$state
  FM_PR_RECORD_MERGED=$merged
}

mr_read_record_bounded() {  # <host> <path> <number>
  local record state merged
  # shellcheck disable=SC2016  # The inner script expands after bash -c receives positional args.
  if ! record=$(fm_run_timed 5 bash -c '
    . "$1"
    fm_pr_gitlab_read_record "$2" "$3" "$4" || exit 1
    printf "state=%s\nmerged=%s\n" "$FM_PR_RECORD_STATE" "$FM_PR_RECORD_MERGED"
  ' _ "$SCRIPT_DIR/fm-pr-lib.sh" "$1" "$2" "$3" 2>/dev/null); then
    return 1
  fi
  state=$(printf '%s\n' "$record" | sed -n 's/^state=//p' | head -1)
  merged=$(printf '%s\n' "$record" | sed -n 's/^merged=//p' | head -1)
  [ -n "$state" ] || return 1
  [ "$merged" = true ] || [ "$merged" = false ] || return 1
  FM_PR_RECORD_STATE=$state
  FM_PR_RECORD_MERGED=$merged
}

change_read_record_bounded() {  # <host> <number>
  local record state merged
  # shellcheck disable=SC2016  # The inner script expands after bash -c receives positional args.
  if ! record=$(fm_run_timed 5 bash -c '
    . "$1"
    fm_pr_gerrit_read_record "$2" "$3" || exit 1
    printf "state=%s\nmerged=%s\n" "$FM_PR_RECORD_STATE" "$FM_PR_RECORD_MERGED"
  ' _ "$SCRIPT_DIR/fm-pr-lib.sh" "$1" "$2" 2>/dev/null); then
    return 1
  fi
  state=$(printf '%s\n' "$record" | sed -n 's/^state=//p' | head -1)
  merged=$(printf '%s\n' "$record" | sed -n 's/^merged=//p' | head -1)
  [ -n "$state" ] || return 1
  [ "$merged" = true ] || [ "$merged" = false ] || return 1
  FM_PR_RECORD_STATE=$state
  FM_PR_RECORD_MERGED=$merged
}

passed_pr_detail() {
  local provider url host path number owner repo raw_pr state_lc
  raw_pr=$(strip_quotes "$(nm_field pr)")
  if fm_pr_url_parse "$raw_pr"; then
    provider=$FM_PR_PROVIDER
    url=$FM_PR_URL
    host=$FM_PR_HOST
    path=$FM_PR_PATH
    number=$FM_PR_NUMBER
  elif fm_pr_metadata_identity_parse "$META"; then
    provider=$FM_PR_META_PROVIDER
    url=$FM_PR_META_URL
    host=$FM_PR_META_HOST
    path=$FM_PR_META_PATH
    number=$FM_PR_META_NUMBER
  else
    printf 'run passed: PR state unknown (no PR identity)'
    return
  fi
  if fm_pr_poll_retirement_receipt_valid "$STATE" "$ID" \
    && [ "$FM_PR_RETIRE_PROVIDER" = "$provider" ] \
    && [ "$FM_PR_RETIRE_URL" = "$url" ] \
    && [ "$FM_PR_RETIRE_HOST" = "$host" ] \
    && [ "$FM_PR_RETIRE_PATH" = "$path" ] \
    && [ "$FM_PR_RETIRE_NUMBER" = "$number" ]; then
    printf 'run passed: PR merged'
    return
  fi
  if [ "${FM_CREW_STATE_NO_FORGE:-0}" = 1 ]; then
    printf 'run passed: PR state unknown (forge read skipped)'
    return
  fi

  case "$provider" in
    github)
      owner=${path%%/*}
      repo=${path#*/}
      if ! pr_read_record_bounded "$owner" "$repo" "$number"; then
        printf 'run passed: PR state unknown (unreadable)'
        return
      fi
      if [ "$FM_PR_RECORD_MERGED" = true ]; then
        printf 'run passed: PR merged'
        return
      fi
      state_lc=$(printf '%s' "$FM_PR_RECORD_STATE" | tr '[:upper:]' '[:lower:]')
      case "$state_lc" in
        open)   printf 'run passed: PR open' ;;
        closed) printf 'run passed: PR closed' ;;
        *)      printf 'run passed: PR state %s' "$state_lc" ;;
      esac
      ;;
    gitlab)
      if ! mr_read_record_bounded "$host" "$path" "$number"; then
        printf 'run passed: PR state unknown (unreadable)'
        return
      fi
      if [ "$FM_PR_RECORD_MERGED" = true ]; then
        printf 'run passed: PR merged'
        return
      fi
      state_lc=$(printf '%s' "$FM_PR_RECORD_STATE" | tr '[:upper:]' '[:lower:]')
      case "$state_lc" in
        open|opened) printf 'run passed: PR open' ;;
        closed)      printf 'run passed: PR closed' ;;
        *)           printf 'run passed: PR state %s' "$state_lc" ;;
      esac
      ;;
    gerrit)
      if ! change_read_record_bounded "$host" "$number"; then
        printf 'run passed: PR state unknown (unreadable)'
        return
      fi
      if [ "$FM_PR_RECORD_MERGED" = true ]; then
        printf 'run passed: PR merged'
        return
      fi
      # Gerrit spells an open change NEW and a closed one ABANDONED.
      state_lc=$(printf '%s' "$FM_PR_RECORD_STATE" | tr '[:upper:]' '[:lower:]')
      case "$state_lc" in
        new)       printf 'run passed: PR open' ;;
        abandoned) printf 'run passed: PR closed' ;;
        *)         printf 'run passed: PR state %s' "$state_lc" ;;
      esac
      ;;
    *)
      printf 'run passed: PR state unknown (unreadable: %s)' "$url"
      ;;
  esac
}
# Finding count from a findings[N]{...} table header; empty when none.
nm_findings_count() {
  printf '%s\n' "$RUN_OUT" | grep -oE 'findings\[[0-9]+\]' | head -1 | grep -oE '[0-9]+'
}
nm_gate_step_row() {
  local row step rest status findings
  row=$(printf '%s\n' "$RUN_OUT" | grep -E "$FM_NM_GATE_ROW_RE" | head -1)
  [ -n "$row" ] || return 0
  row=$(trim "$row")
  step=$(trim "${row%%,*}")
  rest=${row#*,}
  status=$(strip_quotes "$(trim "${rest%%,*}")")
  rest=${rest#*,}
  findings=$(trim "${rest%%,*}")
  printf '%s|%s|%s' "$step" "$status" "$findings"
}
nm_gate_status() {
  local s row
  s=$(printf '%s\n' "$RUN_OUT" | grep -E "$FM_NM_GATE_SCALAR_RE" | head -1)
  if [ -n "$s" ]; then
    s=$(strip_quotes "$(trim "${s#*:}")")
    printf '%s' "$s"
    return
  fi
  row=$(nm_gate_step_row)
  [ -n "$row" ] && { row=${row#*|}; printf '%s' "${row%%|*}"; }
}
nm_has_gate() {
  printf '%s\n' "$RUN_OUT" | grep -Eq "$FM_NM_GATE_LINE_RE"
}
nm_gate_line_name() {
  local gate step
  gate=$(strip_quotes "$(nm_field gate)")
  [ -n "$gate" ] && { printf '%s' "$gate"; return; }
  step=$(printf '%s\n' "$RUN_OUT" | sed -n '/^[[:space:]]*gate:[[:space:]]*$/,/^[^[:space:]][^:]*:/s/^[[:space:]]*step:[[:space:]]*\(.*\)/\1/p' | head -1)
  step=$(strip_quotes "$step")
  [ -n "$step" ] && printf '%s' "$step"
}
nm_gate_name() {
  local gate row
  gate=$(nm_gate_line_name)
  [ -n "$gate" ] && { printf '%s' "$gate"; return; }
  row=$(nm_gate_step_row)
  [ -n "$row" ] && printf '%s' "${row%%|*}"
}
nm_gate_findings_count() {
  local f row rest
  f=$(nm_findings_count)
  [ -n "$f" ] && { printf '%s' "$f"; return; }
  row=$(nm_gate_step_row)
  [ -n "$row" ] || return 0
  rest=${row#*|}
  rest=${rest#*|}
  rest=${rest%%|*}
  case "$rest" in ''|*[!0-9]*) return 0 ;; esac
  printf '%s' "$rest"
}
# 0 when the gate's own findings table holds at least one row whose `action`
# column is exactly `ask-user` - the pipeline's own record that this gate's
# answer is owed by a HUMAN, not by the crewmate (the gate's shape -
# awaiting_approval, fix_review, awaiting_agent - is reported parked in every
# case and does not by itself say who owes the answer; only a findings row whose
# `action` column is exactly `ask-user` does).
#
# Read POSITIONALLY, the way nm_gate_step_row above reads its row: locate the
# `findings[N]{...}` header, take the index of the `action` column from it, walk
# each of the N rows that follow to that index, and compare for EQUALITY. A
# substring search over the run payload cannot make this distinction - the
# trailing `description` column is free text that routinely quotes finding
# actions, and the payload also carries the branch name and step names, so a
# gate owed the crewmate's own answer would match just as readily as one owed a
# human. Column order is read from the header rather than assumed, so a table
# that grows a column keeps answering correctly. Both the header match and the
# row scan require the BRACE, so the count, the index and the rows all come from
# the same block: an earlier unbraced `findings[N]:` line from a resolved round
# must not supply the rows while the braced gate table supplies the index, which
# would read the wrong block's rows at the right block's offset
# (tests/fm-crew-state.test.sh's unbraced-precursor case pins it).
#
# Reading the index out of the header and then walking RAW COMMAS to it is only
# positional in name: the walk is sound only while every column before `action`
# is comma-free, and the producer does not quote commas inside `description`
# (tests/fm-crew-state.test.sh's own fixture proves it). A header ordering that
# puts free text before `action` would therefore let a row's description mint
# the marker - silently, with no error - which is the same class of hole the
# positional derivation exists to close, arriving by a different route. So the
# columns preceding `action` are checked against a WHITELIST of names this table
# is known to carry as short comma-free scalars, and anything else refuses:
# a whitelist rather than a blacklist of free-text names, because an unknown
# column must read as unsafe rather than as safe. When the table's shape is not
# provably safe the correct answer is the noisy one - a crewmate that went quiet
# before answering its own gate is the failure that must never be silenced.
# Residual bound, which no unquoted positional parse of this table escapes: a
# comma inside a whitelisted field's own value (a path with a comma in it, say)
# still shifts the walk.
nm_gate_awaits_human_decision() {
  local header count cols idx i name field rows row rest
  header=$(printf '%s\n' "$RUN_OUT" | grep -E '^[[:space:]]*findings\[[0-9]+\]\{[^}]*\}:' | head -1)
  [ -n "$header" ] || return 1
  count=$(printf '%s' "$header" | sed -n 's/^[[:space:]]*findings\[\([0-9][0-9]*\)\].*/\1/p')
  case "$count" in ''|*[!0-9]*) return 1 ;; esac
  [ "$count" -gt 0 ] || return 1
  cols=$(printf '%s' "$header" | sed -n 's/^[^{]*{\([^}]*\)}.*/\1/p')
  [ -n "$cols" ] || return 1
  idx=0
  i=0
  while [ -n "$cols" ]; do
    i=$((i + 1))
    name=$(strip_quotes "$(trim "${cols%%,*}")")
    if [ "$name" = action ]; then idx=$i; break; fi
    case "$name" in
      id|severity|file|line) ;;
      *) return 1 ;;
    esac
    case "$cols" in *,*) cols=${cols#*,} ;; *) cols='' ;; esac
  done
  [ "$idx" -gt 0 ] || return 1
  rows=$(printf '%s\n' "$RUN_OUT" \
    | awk -v n="$count" 'f { print; if (++c >= n) exit; next } /^[[:space:]]*findings\[[0-9]+\]\{/ { f = 1 }')
  while IFS= read -r row; do
    case "$row" in *,*) ;; *) continue ;; esac
    rest=$row
    i=1
    while [ "$i" -lt "$idx" ]; do
      case "$rest" in *,*) rest=${rest#*,} ;; *) rest=''; break ;; esac
      i=$((i + 1))
    done
    [ -n "$rest" ] || continue
    field=$(strip_quotes "$(trim "${rest%%,*}")")
    [ "$field" = ask-user ] && return 0
  done <<EOF
$rows
EOF
  return 1
}
log_reports_ci_ready() {
  [ "$LOG_VERB" = "done" ] || return 1
  fm_dod_note_reports_ci_ready "$(status_line_note "$LOG_LINE")"
}

# 0 when a status-log line reports positive daemon socket failure rather than a
# client-side timeout or generic unreachability.
log_reports_daemon_socket_down() {  # <line>
  local line
  line=$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')
  case "$line" in
    *daemon*|*no-mistakes*) ;;
    *) return 1 ;;
  esac
  case "$line" in
    *"connection refused"*|*"connections refused"*|*"socket refused connection"*|*"socket refuses connection"*|*"socket refusing connection"*|*"socket missing"*|*"socket is missing"*|*"missing socket"*) return 0 ;;
  esac
  return 1
}

# 0 when a status-log line blames the pipeline's transport rather than the work.
# None of these claims alone is evidence the daemon died: a drive call is only
# waiting for a read while the fix round runs in the background.
log_claims_pipeline_unreachable() {  # <line>
  case "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')" in
    *daemon*|*timeout*|*"timed out"*|*unreachab*) return 0 ;;
  esac
  return 1
}

# Rows of the `active_steps[N]{...}:` table in the captured run output
# ($RUN_OUT), which the pipeline emits only while a step is actually running or
# fixing. Column order is deliberately not assumed: the header's own indentation
# bounds the block, and callers below read the table as text.
nm_active_steps_rows() {
  printf '%s\n' "$RUN_OUT" | awk '
    /^[[:space:]]*active_steps\[[0-9]+\]\{/ { hdr = index($0, "active_steps"); inblock = 1; next }
    inblock {
      if ($0 ~ /^[[:space:]]*$/) { inblock = 0; next }
      match($0, /[^ \t]/)
      if (RSTART <= hdr) { inblock = 0; next }
      print
    }
  '
}

# Rows of the `steps[N]{step,status,findings,duration_ms}:` table in the
# captured run output ($RUN_OUT) - the full per-step ledger, present on
# terminal runs too, unlike active_steps[] which the pipeline emits only while
# a step is actually running or fixing. Column order is deliberately not
# assumed: the header's own indentation bounds the block, and callers below
# read the table as text.
nm_steps_rows() {
  printf '%s\n' "$RUN_OUT" | awk '
    /^[[:space:]]*steps\[[0-9]+\]\{/ { hdr = index($0, "steps"); inblock = 1; next }
    inblock {
      if ($0 ~ /^[[:space:]]*$/) { inblock = 0; next }
      match($0, /[^ \t]/)
      if (RSTART <= hdr) { inblock = 0; next }
      print
    }
  '
}

# 0 when the pipeline itself reports RECENT activity on an actively running or
# fixing step. The client prefixes a step's `last_activity` with `quiet` once no
# step log or native-agent lifecycle event has arrived for longer than its
# configured quiet warning, so its own recency verdict is the signal here rather
# than a second threshold invented in firstmate. Positive evidence is required:
# an absent table is not recency, so a run record that merely still says
# `running` while nothing executes it never reads as alive.
nm_run_activity_is_recent() {
  local rows
  rows=$(nm_active_steps_rows)
  [ -n "$rows" ] || return 1
  ! printf '%s\n' "$rows" | grep -q 'quiet'
}

# 0 when a terminal failed or cancelled run ended at the ci monitor and the
# ci log's last recognized marker reads checks green. Requires the exact
# shape, all on positive evidence: a steps[] table where every step completed
# except `ci` failed/cancelled and an optional skipped rebase (any other
# non-completed step disqualifies), plus nm_ci_checks_state=green (a genuinely red
# check, or an unreadable ci log, cannot prove delivery). This is the
# orphaned-CI-monitor gap (2026-09-05 jr-voice): a run held for a captain
# merge decision polls until the shared daemon restarts under it and marks
# the run failed, although GitHub's own check state - the actual shippability
# authority - is green and every substantive step completed.
nm_failed_run_is_green_held_ci() {
  local rows row rest step status saw_ci_failed
  rows=$(nm_steps_rows)
  [ -n "$rows" ] || return 1
  saw_ci_failed=0
  while IFS= read -r row; do
    row=$(trim "$row")
    step=$(trim "${row%%,*}")
    rest=${row#*,}
    status=$(strip_quotes "$(trim "${rest%%,*}")")
    case "$status" in
      completed) continue ;;
      skipped)
        [ "$step" = rebase ] || return 1
        continue
        ;;
      failed|cancelled)
        [ "$step" = ci ] || return 1
        saw_ci_failed=1
        continue
        ;;
      *) return 1 ;;
    esac
  done <<EOF
$rows
EOF
  [ "$saw_ci_failed" = 1 ] || return 1
  [ "$(nm_ci_checks_state)" = green ]
}

# Apply the header's terminal-delivery safeguard. The earlier green log cannot
# prove current PR disposition: a subsequent close can itself end the monitor.
nm_reclassify_failed_run_as_held_green() {
  nm_failed_run_is_green_held_ci || return 1
  local disposition pr_url
  disposition=$(passed_pr_detail)
  case "$disposition" in
    "run passed: PR open") RUN_DETAIL="checks green: PR held for merge (ci monitor ended)" ;;
    "run passed: PR merged") RUN_DETAIL="checks green: PR merged (ci monitor ended)" ;;
    *) return 1 ;;
  esac
  RUN_STATE="done"
  pr_url=$(strip_quotes "$(nm_field pr)")
  [ -n "$pr_url" ] && RUN_DETAIL="$RUN_DETAIL: $pr_url"
  return 0
}

# 0 when an explicit probe proves the shared daemon down: `no-mistakes daemon
# status` is the canonical down-probe (the same one fm-brief.sh hands crews
# before a blocked append) and exits non-zero when the daemon is not running.
# Bounded like every other CLI call; a probe that fails for any reason -
# refused socket, timeout, non-zero answer - means the daemon is not provably
# up, which is the only fact the coarse fallback needs.
nm_daemon_probe_down() {
  nm_daemon_probe
  [ "$NM_DAEMON_ANSWER" != up ]
}

# 0 only when the probe ANSWERED and that answer was "down". Suppressing a LIVE
# record needs this stricter question: `not provably up` above is fail-closed,
# which is safe when it degrades a terminal record to unknown, but on a live
# record it would drop a working crew back to a possibly-stale status log every
# time the probe merely ran slow - the crew would flap between working and
# failed on probe latency alone. 124 is the bounded call's own did-not-answer
# code (both the timeout and perl arms of fm_nm_run_bounded use it), and proves
# nothing about the daemon. The no-timeout-tool return of 1 cannot reach here:
# without a timeout tool the `axi status` read above is empty too, so this whole
# block is skipped.
nm_daemon_answered_down() {
  nm_daemon_probe
  [ "$NM_DAEMON_ANSWER" = down ]
}

# ONE bounded `daemon status` call per crew read, cached with the three answers
# its two readers need to stay distinguishable: `up`, `unanswered` (the bounded
# call's own 124), and `down`. Collapsing `up` and `unanswered` into a single
# not-down bucket is what would force a second subprocess, and on a wedged
# daemon each probe burns the full timeout inside the supervisor's per-crew
# polling loop.
nm_daemon_probe() {
  local rc=0
  [ -n "$NM_DAEMON_ANSWER" ] && return 0
  fm_nm_run_checked "$WT" "$NM_TIMEOUT" daemon status >/dev/null || rc=$?
  case "$rc" in
    0)   NM_DAEMON_ANSWER=up ;;
    124) NM_DAEMON_ANSWER=unanswered ;;
    *)   NM_DAEMON_ANSWER=down ;;
  esac
}

nm_ci_step_status() {
  local row rest
  row=$(printf '%s\n' "$RUN_OUT" | grep -E '^[[:space:]]*ci,[[:space:]]*"?(running|fixing)"?[[:space:]]*,' | head -1)
  [ -n "$row" ] || return 0
  row=$(trim "$row")
  rest=${row#*,}
  strip_quotes "$(trim "${rest%%,*}")"
}

nm_effective_ci_step_status() {
  local step_status
  if [ "${RUN_STATUS:-}" = fixing ]; then
    printf 'fixing'
    return 0
  fi
  step_status=$(nm_ci_step_status)
  if [ -n "$step_status" ]; then
    printf '%s' "$step_status"
    return 0
  fi
  if [ "${RUN_STATUS:-}" = ci ]; then
    printf 'running'
  fi
}

# Root cause of the PR #252 incident (2026-07): for a repo where merge is left
# to the captain, no-mistakes' ci step (and therefore top-level status/outcome)
# stays "running" for the ENTIRE CI-monitor phase, including long after GitHub
# reports every check green - it only reaches outcome=passed once the PR is
# actually merged (or failed/cancelled if closed). `axi status`'s steps[] table
# never distinguishes "still waiting on checks" from "checks green, waiting on
# merge": both read as plain `ci,running,...`. The only place that transition is
# recorded is the ci step's own log text, e.g. "all CI checks passed - still
# monitoring until merged or closed" or "no CI checks reported - still
# monitoring until merged or closed" (verified against 360+ real run logs under
# ~/.no-mistakes/logs/*/ci.log on the installed v1.32.2 binary, including the
# actual PR #252 run). Reads the ci step's log via `axi logs --full` and scans
# it for the MOST RECENT recognized marker (the log is append-only/chronological,
# so the last match is current): green with nothing red after it means CI is
# green right now, still only waiting on merge/close.
# "base branch advanced (..), re-arming CI monitor timeout" is deliberately NOT
# a marker: the monitor logs a checks state only when that state changes, and a
# base advance re-arms only its idle timeout without clearing readiness, so the
# green marker before it is still current (no-mistakes' own ci-log parser
# ignores the line the same way, v1.32.2 through v1.79.0). Reading it as
# not-ready held a green PR at working for as long as main kept advancing.
nm_ci_checks_state() {
  local run_id ci_log marker
  run_id=$(strip_quotes "$(nm_field id)")
  [ -n "$run_id" ] || { printf 'unknown'; return; }
  ci_log=$(nm_run axi logs --step ci --run "$run_id" --full) || true
  [ -n "$ci_log" ] || { printf 'unknown'; return; }
  marker=$(printf '%s\n' "$ci_log" \
    | grep -E 'CI checks passed|no CI checks reported - still monitoring|no CI checks reported yet|checks failed|issues detected|CI checks running' \
    | tail -1)
  case "$marker" in
    *"checks passed"*|*"no CI checks reported - still monitoring"*) printf 'green' ;;
    *"no CI checks reported yet"*|*"checks failed"*|*"issues detected"*|*"CI checks running"*) printf 'not-ready' ;;
    *) printf 'unknown' ;;
  esac
}
# Coarse fallback when the bare `axi status` answer is not this branch's own
# matching run: either it names another branch (routine once several crews
# validate the same underlying repo concurrently - a worktree with its own
# active run reliably gets that run answered, even under concurrent load), or
# it names this branch's run but the strict head rule rejected it - a run that
# is parked, terminal, or executing with the daemon answered down, since an
# executing run whose daemon still answers binds before this fallback is
# reached. The ledger resolves every answer STRICTLY: it never accepts a row on
# branch name alone, so a head-tied row can re-bind such a record as working. The real
# run-listing command is the top-level `no-mistakes runs` (the `axi` surface
# has no runs-listing subcommand; tests/fm-crew-state.test.sh owns the
# 2026-07-02 dead-code incident history this fallback replaced).
# fm_nm_runs_status_for_worktree in bin/fm-nm-run-lib.sh is the ONE owner of
# the ledger format, the newest-row-decides rule, and the anchored
# pipeline-continuation recognition
# (model-routing-benchmark-hardening: an active fix round whose head object the
# task copy never fetched used to be rejected here, letting the older failed row
# answer as current), so both attribution routes share one rule.
# The same reader checks for conflicting run records when the AXI overview
# cannot identify this branch's run.
nm_runs_list() {
  nm_run runs --limit "$FM_CREW_STATE_RUNS_LIMIT"
}

# CREW_BRANCH is empty at detached HEAD (a just-spawned crew, or a scout's
# scratch worktree); with no branch there is no run to attribute to this crew.
CREW_BRANCH=$(git -C "$WT" symbolic-ref --quiet --short HEAD 2>/dev/null || true)

# 0 if the active axi-status run's head field matches this worktree's code
# identity. Branch match is a precondition (caller). Rule owned by
# fm_nm_head_matches_worktree in bin/fm-nm-run-lib.sh.
nm_run_head_matches_worktree() {
  local run_head
  run_head=$(strip_quotes "$(nm_field head)")
  fm_nm_head_matches_worktree "$WT" "$run_head"
}

HAVE_RUN=0
# RUN_SOURCE distinguishes the two ways HAVE_RUN=1 can happen: "full" means
# $RUN_OUT is real `axi status` TOON with step/gate detail (including a
# same-branch run the strict head rule rejected but the ledger proved is this
# worktree's pipeline-owned continuation); "coarse" means only a bare status
# word came back from the runs-list fallback, so the run-step block below skips
# the TOON field parsing entirely for this crew.
RUN_SOURCE=full
NM_DAEMON_ANSWER=""
RUN_DEAD_DAEMON=""
COARSE_STATUS=""
SELECTED_RUN_ID=""
# Scouts and secondmates never drive a no-mistakes validation of their own
# worktree, so skip the lookup for them and read state from pane/log directly.
if [ "$KIND" = ship ] && [ -n "$CREW_BRANCH" ] && command -v no-mistakes >/dev/null 2>&1; then
  RUN_OUT=$(nm_run axi status)
  if [ "$(strip_quotes "$(printf '%s\n' "$RUN_OUT" | sed -n 's/^error: //p')")" = "repo not initialized (run 'no-mistakes init' first)" ]; then
    RUN_OUT=""
  fi
  if [ -n "$RUN_OUT" ]; then
    # The overview includes run ids and creation order, which the plain runs
    # listing omits. Keep the primary empty-call bound above: a nonresponding
    # CLI is not retried. Older CLI surfaces without the table retain the
    # coarse fallback below, but cannot turn a replacement into a vague live
    # verdict when its identity and gate cannot be read.
    overview_ok=1
    run_overview=$(fm_nm_run_checked "$WT" "$NM_TIMEOUT" axi) || overview_ok=0
    [ -n "$run_overview" ] || emit unknown run-step "run inventory unavailable; run id: $(strip_quotes "$(nm_field id)")"
    run_choice=$(fm_nm_select_run "$CREW_BRANCH" "$run_overview" "$WT" "$NM_TIMEOUT")
    [ "$overview_ok" = 1 ] || emit unknown run-step "run inventory unreadable; run ids: $(strip_quotes "$(nm_field id)"), ${run_choice##*|}"
    case "$run_choice" in
      unknown\|*)
        known_run_id=""
        if [ "$(strip_quotes "$(nm_field branch)")" = "$CREW_BRANCH" ]; then
          known_run_id=$(strip_quotes "$(nm_field id)")
        fi
        emit unknown run-step "${run_choice#*|}${known_run_id:+; last reported run id: $known_run_id}"
        ;;
      selected\|*)
        IFS='|' read -r _ selected_id selected_status candidate_ids <<< "$run_choice"
        RUN_OUT=$(fm_nm_run_checked "$WT" "$NM_TIMEOUT" axi status --run "$selected_id") \
          || emit unknown run-step "selected run unreadable; run ids: $candidate_ids"
        if [ "$(strip_quotes "$(nm_field id)")" != "$selected_id" ] \
          || [ "$(strip_quotes "$(nm_field branch)")" != "$CREW_BRANCH" ]; then
          emit unknown run-step "selected run unavailable or mismatched; run ids: $candidate_ids"
        fi
        case "$(strip_quotes "$(nm_field status)")" in
          pending|running|fixing|ci|awaiting_approval|fix_review|completed|failed|cancelled) ;;
          *) emit unknown run-step "selected run status unverified; run ids: $candidate_ids" ;;
        esac
        if fm_nm_run_is_active "$RUN_OUT"; then current_class=live; else current_class=terminal; fi
        if [ "$(fm_nm_run_status_class "$selected_status")" != "$current_class" ]; then
          emit unknown run-step "selected run status disagrees with inventory; run ids: $candidate_ids"
        fi
        if nm_run_head_matches_worktree || fm_nm_run_is_pipeline_owned_active "$RUN_OUT" \
          || { fm_nm_run_is_executing "$RUN_OUT" && ! nm_daemon_answered_down; }; then
          HAVE_RUN=1
        elif [ -z "$(fm_nm_resolve_commit "$WT" "$(strip_quotes "$(nm_field head)")")" ]; then
          if fm_nm_run_is_active "$RUN_OUT" \
            && [ "$(fm_nm_runs_status_for_worktree "$WT" "$CREW_BRANCH" "$(nm_runs_list)" "$(strip_quotes "$(nm_field head)")")" = running ]; then
            # The anchor PROVED code identity; only liveness can still fail, so
            # a dead daemon is reported as such rather than as an identity
            # failure, and a parked run keeps its gate and findings.
            HAVE_RUN=1
            if ! fm_nm_run_is_parked "$RUN_OUT" && nm_daemon_answered_down; then
              RUN_DEAD_DAEMON="no-mistakes daemon unreachable; last run record $(strip_quotes "$(nm_field status)") - unverified"
            fi
          else
            emit unknown run-step "selected run code identity unverified; run ids: $candidate_ids"
          fi
        fi
        SELECTED_RUN_ID=$selected_id
        ;;
    esac
    if [ "$HAVE_RUN" = 0 ] && [ -z "$SELECTED_RUN_ID" ]; then
      run_branch=$(strip_quotes "$(nm_field branch)")
      # Head equality, the pipeline-owned parked-run exemption, or executing
      # regardless of head: a live run on this branch is current even after a
      # rebase, and while the pipeline owns this branch a parked run binds
      # without the lane head being a git object here (fm_nm_run_is_executing
      # and fm_nm_run_is_pipeline_owned_active in bin/fm-nm-run-lib.sh). The
      # head-free route additionally needs the daemon not provably down, so a
      # record left saying `running` by a dead daemon stops answering once the
      # worktree moves off the run head.
      if [ -n "$run_branch" ] && [ "$run_branch" = "$CREW_BRANCH" ] \
        && { nm_run_head_matches_worktree || fm_nm_run_is_pipeline_owned_active "$RUN_OUT" \
          || { fm_nm_run_is_executing "$RUN_OUT" && ! nm_daemon_answered_down; }; }; then
        HAVE_RUN=1
        # Without run ids, contradictory liveness cannot prove precedence.
        # A live replacement also needs an id-addressed status read: a bare
        # "running" row cannot tell working from waiting at a gate.
        ledger_status=$(fm_nm_runs_status_for_worktree "$WT" "$CREW_BRANCH" "$(nm_runs_list)")
        if fm_nm_run_is_active "$RUN_OUT"; then
          if [ "$(fm_nm_run_status_class "$ledger_status")" = terminal ]; then
            emit unknown run-step "run records disagree; run ids: $(strip_quotes "$(nm_field id)"), competing identity unavailable"
          fi
        else
          if [ "$(fm_nm_run_status_class "$ledger_status")" = live ]; then
            emit unknown run-step "replacement run identity unavailable; run ids: $(strip_quotes "$(nm_field id)"), replacement unavailable"
          elif [ -n "$ledger_status" ] \
            && [ "$ledger_status" != "$(strip_quotes "$(nm_field status)")" ] \
            && [ "$ledger_status" != "$(strip_quotes "$(nm_field outcome)")" ]; then
            COARSE_STATUS=$ledger_status
            RUN_SOURCE=coarse
          fi
        fi
      else
        # The active-or-most-recent run is for another branch, or it names this
        # branch with a head this copy cannot verify (a pipeline-advanced fix
        # round, or a rewritten tip). Deliberately nested inside
        # `[ -n "$RUN_OUT" ]`: an empty/timed-out primary call means the CLI
        # itself did not respond, so retrying it immediately with a second
        # bounded call would just double the wait for no better answer.
        COARSE_STATUS=$(fm_nm_runs_status_for_worktree "$WT" "$CREW_BRANCH" "$(nm_runs_list)")
        if [ -n "$COARSE_STATUS" ]; then
          HAVE_RUN=1
          # A branch-matching answer the strict rule rejected is this branch's
          # own current run once the ledger proves the pipeline-owned
          # continuation, so its axi TOON is the authoritative run detail
          # (RUN_SOURCE stays full); only a foreign-branch answer leaves
          # coarse status-word detail.
          [ "$run_branch" = "$CREW_BRANCH" ] || RUN_SOURCE=coarse
        fi
      fi
    fi
  fi
fi

# --- run-step authoritative path -------------------------------------------

if [ "$HAVE_RUN" = 1 ]; then
  RUN_STATE=working
  RUN_DETAIL=""
  CI_STEP_STATUS=""
  CI_LOG_STATE=""
  RUN_STATUS=""
  if [ -n "$RUN_DEAD_DAEMON" ]; then
    # ONE dead-instrument verdict for every route that reaches one. It is set,
    # not emitted, so the status-log reconciliation below still runs: an
    # unverified record must not silence the crew's own open decision.
    RUN_STATE=unknown
    RUN_DETAIL=$RUN_DEAD_DAEMON
  elif [ "$RUN_SOURCE" = coarse ]; then
    # No step/gate detail is available from the plain runs list - only ever
    # working, done, failed, or unknown. Gate detail requires the identity-aware
    # read above. The status event span remains independently available to the
    # supervisor through fm-classify-lib.sh's status_span_first_actionable.
    case "$COARSE_STATUS" in
      running) RUN_STATE=working; RUN_DETAIL="validating (background run)" ;;
      completed) RUN_STATE="done";  RUN_DETAIL="run completed" ;;
      failed)
        # The ledger row is terminal but the coarse path has no steps table
        # and no ci log, so the orphaned-monitor shape cannot be recognized
        # here. With the daemon provably down, the row is unverified evidence
        # from a dead instrument and must not read as work failure.
        if nm_daemon_probe_down; then
          RUN_STATE=unknown
          RUN_DETAIL="no-mistakes daemon unreachable; last ledger record failed - unverified"
        else
          RUN_STATE=failed; RUN_DETAIL="run failed"
        fi ;;
      cancelled) RUN_STATE=unknown; RUN_DETAIL="run cancelled: no verdict" ;;
      *)         RUN_STATE=unknown; RUN_DETAIL="runs list status: $COARSE_STATUS" ;;
    esac
  else
    status=$(strip_quotes "$(nm_field status)")
    RUN_STATUS=$status
    outcome=$(strip_quotes "$(nm_field outcome)")
    awaiting=$(printf '%s\n' "$RUN_OUT" | grep -E "$FM_NM_AWAITING_AGENT_RE" | head -1 || true)
    gate_status=$(nm_gate_status)
    has_gate=0
    nm_has_gate && has_gate=1

    if [ -n "$outcome" ]; then
      case "$outcome" in
        passed|passed-with-override) RUN_STATE="done"; RUN_DETAIL=$(passed_pr_detail) ;;
        passed-with-skips) RUN_STATE="done"; RUN_DETAIL="$(passed_pr_detail) (publication/CI verification skipped)" ;;
        checks-passed) RUN_STATE="done"; RUN_DETAIL="checks green: PR ready for review" ;;
        failed)
          if nm_reclassify_failed_run_as_held_green; then :; else
            RUN_STATE=failed; RUN_DETAIL="run failed"
          fi ;;
        cancelled)
          if nm_reclassify_failed_run_as_held_green; then :; else
            RUN_STATE=unknown; RUN_DETAIL="run cancelled: no verdict"
          fi ;;
        *)             RUN_STATE=unknown; RUN_DETAIL="outcome: $outcome" ;;
      esac
    elif [ -n "$awaiting" ] || [ "$status" = awaiting_approval ] || [ "$status" = fix_review ] || [ -n "$gate_status" ] || [ "$has_gate" = 1 ]; then
      if [ "$has_gate" = 1 ]; then
        gate=$(nm_gate_line_name)
      else
        gate=$(nm_gate_name)
      fi
      [ -n "$gate" ] || gate=$status
      [ -n "$gate" ] || gate=gate
      RUN_STATE=parked
      RUN_DETAIL="parked at $gate"
      fcount=$(nm_gate_findings_count)
      [ -n "$fcount" ] && RUN_DETAIL="$RUN_DETAIL: $fcount finding(s)"
      # Its own ${SEP} component, not free text inside the detail: consumers
      # compare a whole component for equality, so nothing a gate name or a
      # later note happens to contain can mint it.
      if nm_gate_awaits_human_decision; then
        RUN_DETAIL="$RUN_DETAIL${SEP}$FM_GATE_HUMAN_DECISION"
      fi
    else
      case "$status" in
        ci)             RUN_STATE=working; RUN_DETAIL="ci running" ;;
        running|fixing) RUN_STATE=working; RUN_DETAIL="validating ($status)" ;;
        completed)      RUN_STATE="done"; RUN_DETAIL="run completed" ;;
        failed)
          if nm_reclassify_failed_run_as_held_green; then :; else
            RUN_STATE=failed; RUN_DETAIL="run failed"
          fi ;;
        cancelled)
          if nm_reclassify_failed_run_as_held_green; then :; else
            RUN_STATE=unknown; RUN_DETAIL="run cancelled: no verdict"
          fi ;;
        "")             RUN_STATE=working; RUN_DETAIL="run active" ;;
        *)              RUN_STATE=working; RUN_DETAIL="run active ($status)" ;;
      esac
      if [ "$RUN_STATE" = working ]; then
        CI_STEP_STATUS=$(nm_effective_ci_step_status)
        case "$CI_STEP_STATUS" in
          running)
            CI_LOG_STATE=$(nm_ci_checks_state)
            if [ "$CI_LOG_STATE" = green ]; then
              RUN_STATE="done"
              RUN_DETAIL="checks green: PR ready for review (still monitoring for merge/close)"
              # The run's own PR URL makes this reading actionable even when
              # the worker never reported it and no pr= was recorded.
              ci_pr_url=$(strip_quotes "$(nm_field pr)")
              [ -z "$ci_pr_url" ] || RUN_DETAIL="$RUN_DETAIL: $ci_pr_url"
            fi
            ;;
          fixing)
            CI_LOG_STATE=not-ready
            ;;
        esac
      fi
    fi
  fi

  if [ "$RUN_STATE" = working ] && log_reports_ci_ready; then
    if [ "$RUN_SOURCE" = coarse ]; then
      emit_ship_status_done "run still monitoring PR"
    fi
    [ -n "$CI_STEP_STATUS" ] || CI_STEP_STATUS=$(nm_effective_ci_step_status)
    if [ "$RUN_STATUS" = fixing ]; then
      CI_LOG_STATE=not-ready
    elif [ "$CI_STEP_STATUS" = running ] && [ -z "$CI_LOG_STATE" ]; then
      CI_LOG_STATE=$(nm_ci_checks_state)
    elif [ "$CI_STEP_STATUS" = fixing ]; then
      CI_LOG_STATE=not-ready
    fi
    if [ "$CI_LOG_STATE" != not-ready ]; then
      emit_ship_status_done "run still monitoring PR"
    fi
  fi

  # Reconcile the status log. A needs-decision/blocked log line that the run-step
  # has moved past (anything but a genuinely parked run) is deterministically
  # stale: the gate resolved and the run resumed or finished.
  #
  # A refused or missing daemon socket is positive daemon-down evidence and
  # outranks any attributed run record, including a terminal one left behind
  # after the daemon stopped, but only while that blocker is itself the log's
  # LATEST recognized event: a later event of any kind means the crew has moved
  # on, and the attributed run is the better witness again. The evidence is
  # therefore read off that latest event, not off the reconciled declaration -
  # the two are the same line while the blocker is current, and when they differ
  # the open blocker is by definition no longer the log's tip. Other blocked
  # claims caused by a timed-out drive call are contradicted only when the run
  # reports recent activity; the answer is then to steer the crew to reattach
  # without touching the shared daemon.
  case "$LOG_VERB" in
    needs-decision|blocked)
      LOG_LATEST=$(last_status_line "$LOG")
      if [ "$LOG_VERB" = blocked ] \
        && [ "$(status_line_verb "$LOG_LATEST")" = blocked ] \
        && log_reports_daemon_socket_down "$LOG_LATEST"; then
        emit blocked status-log "$(status_line_note "$LOG_LATEST")${SEP}daemon socket down despite attributed run record"
      fi
      # An UNVERIFIED record cannot close an open decision. The crew observed
      # its gate or its blocker first hand; a record the dead instrument left
      # behind is the weaker witness, so the log answers and the unverified
      # record is reported as the reason rather than replacing it.
      LOG_TIP_STATE=$(map_log_state "$LOG_LINE")
      if [ -n "$RUN_DEAD_DAEMON" ]; then
        emit "$LOG_TIP_STATE" status-log "$(status_line_note "$LOG_LINE")${SEP}${RUN_DEAD_DAEMON}${SELECTED_RUN_ID:+${SEP}run: $SELECTED_RUN_ID}"
      fi
      if [ "$RUN_STATE" != parked ]; then
        if [ "$RUN_STATE" = working ]; then
          if [ "$LOG_VERB" = blocked ] \
            && log_claims_pipeline_unreachable "$LOG_LINE" \
            && { [ "$RUN_STATUS" = running ] || [ "$RUN_STATUS" = fixing ]; } \
            && nm_run_activity_is_recent; then
            RUN_DETAIL="$RUN_DETAIL${SEP}status-log superseded: run alive, not a daemon failure (steer reattach)"
          else
            RUN_DETAIL="$RUN_DETAIL${SEP}status-log superseded by active run"
          fi
        else
          RUN_DETAIL="$RUN_DETAIL${SEP}status-log superseded (run $RUN_STATE)"
        fi
      fi
      ;;
  esac

  [ -z "$SELECTED_RUN_ID" ] || RUN_DETAIL="$RUN_DETAIL${SEP}run: $SELECTED_RUN_ID"
  emit "$RUN_STATE" run-step "$RUN_DETAIL"
fi

# --- fallback: no run attributed to this crew ------------------------------
# The run-step path above already handled any crew with a run, regardless of pane
# liveness, so a finished-but-pane-closed crew never reaches here. Down here there
# is no run to consult, so only positive evidence that the target is gone may
# read as death - a backend that failed to answer is unknown, never death, for
# both classifier-backed backends (tmux and herdr) - and every death-class
# verdict reports unknown rather than trusting a possibly-stale status log as
# the current state.
[ -n "$BACKEND_TARGET" ] || {
  NO_TARGET_DETAIL="no backend target recorded"
  [ -z "$MATE_QUIET_DETAIL" ] || NO_TARGET_DETAIL="$NO_TARGET_DETAIL${SEP}$MATE_QUIET_DETAIL"
  emit unknown none "$NO_TARGET_DETAIL"
}
if ! pane_readable "$BACKEND_TARGET"; then
  # A failed probe is not itself evidence the pane is gone: the herdr CLI can
  # error or stall under load, and tmux can fail to be executed at all (a
  # trimmed PATH) or answer non-definitively, while the pane is alive - a busy
  # box would otherwise score dozens of live claims dead. Both backends own a
  # recovery-grade classifier (fm_backend_agent_state), which separates the
  # outcomes:
  #   missing - the endpoint is authoritatively absent: herdr's pane get
  #             answered pane_not_found; tmux's successful window inventory
  #             omitted the exact recorded window, or tmux gave one of its
  #             definitive no-session/no-server/no-socket responses (which
  #             fm_backend_tmux_agent_state owns as death, since fm-bootstrap
  #             and fm-session-start depend on it to license a respawn after a
  #             genuine server death - a socket-connection failure is NOT
  #             covered by the unknown-never-death rule above).
  #   dead    - the endpoint exists but confidently has no agent (herdr's agent
  #             get answered agent_not_found, or its registration lingers over a
  #             pane whose processes are nothing but shells - issue #4115;
  #             tmux's readable foreground process group is nothing but
  #             shells), still positive death evidence.
  #   alive   - the endpoint and its agent answered and only the heavy
  #             scrollback read failed, so the live state is classified by the
  #             normal flow below instead of being discarded.
  #   anything else - the cheap probes themselves failed to answer or
  #             contradicted themselves, which is unknown, never death.
  # Backends with no classifier (orca, zellij, and cmux all report unverified)
  # keep their historical capture-failure-means-gone reading.
  #
  # An unreadable target is still not proof the crew is gone. A backend whose
  # endpoint identifiers are generated (Herdr pane ids) re-issues them when it
  # rebuilds its layout, and the recorded one then names nothing while the
  # crew keeps running under a new one. Ask whether a live endpoint still
  # carries this task's identity before reporting the crew as gone; the
  # recorded worktree is the same directory the endpoint was created in.
  if [ "$(fm_backend_agent_state "$TASK_BACKEND" "$BACKEND_TARGET" \
      "$EXPECTED_LABEL" "$WT" 2>/dev/null)" = drifted ]; then
    emit unknown pane "recorded endpoint identifier is stale; a live endpoint still carries this task's identity - correct the record with bin/fm-control.sh $ID rebind"
  fi
  case "$TASK_BACKEND" in
    tmux|herdr) AGENT_STATE=$(fm_backend_agent_state "$TASK_BACKEND" "$BACKEND_TARGET") ;;
    *) AGENT_STATE=none ;;
  esac
  case "$TASK_BACKEND:$AGENT_STATE" in
    tmux:alive|herdr:alive)
      ;;
    tmux:missing|herdr:missing)
      emit unknown none "backend target gone: $BACKEND_TARGET"
      ;;
    tmux:dead|herdr:dead)
      emit unknown none "backend target gone: $BACKEND_TARGET (agent gone, pane shell remains)"
      ;;
    tmux:*|herdr:*)
      emit unknown none "backend unreachable ($TASK_BACKEND endpoint state: $AGENT_STATE)"
      ;;
    *)
      emit unknown none "backend target gone: $BACKEND_TARGET"
      ;;
  esac
fi

# A stopped agent is checked before anything else here, and for every kind.
# It is the one endpoint fact that makes every OTHER source untrustworthy at
# once: the process that writes the semantic record is frozen, so the record
# says whatever it last said forever, and the status log stopped where the
# worker stopped. It is checked for a secondmate too, even though the busy
# state below is not - a secondmate's idle pane is healthy, but a secondmate
# frozen mid-turn is not idle, and the live incident that produced this check
# was a secondmate.
if fm_backend_endpoint_suspended "$TASK_BACKEND" "$BACKEND_TARGET" 2>/dev/null; then
  emit unknown pane "harness process is suspended, not gone; resume it in its own endpoint before trusting any recorded state"
fi

# An agent that EXITED is checked next, and for the same reason: a readable
# endpoint proves the SHELL is there, not the agent. pane_readable above only
# asked whether the target resolves, so a worker whose agent left behind a live
# prompt reached the busy verdict and the status log below with no one to
# contradict them - the semantic record kept the `idle` its own shutdown hook
# wrote, and the log kept the last line the worker appended BEFORE it left.
# That pre-exit line then became the reported CURRENT state, which is how four
# workers on 2026-08-20 each went on reporting `working` for up to an hour
# after their agent was gone.
#
# fm_backend_agent_state is the one source that separates the three ways a
# quiet endpoint looks identical from the outside, and it is deliberately the
# ONLY thing consulted here: `dead` means the endpoint's foreground process
# group is shells alone, `suspended` means the agent is present but frozen
# (handled just above), and `alive` means the agent is right there - between
# turns, or unable to accept input behind its own overlay. Only `dead` is
# consumed, so an agent that is merely unreachable is never reported as gone,
# and a live worker can never be relaunched onto its own worktree on this
# evidence.
#
# The status log still decides whether the exit was a REPORT or a departure.
# A worker that wrote done:/failed: (or a captain-relevant needs-decision: or
# blocked:) and then exited said what happened, and keeps its own verdict from
# the log below. This verdict answers only the case the log cannot: the
# terminal line was never written at all. That distinction is the whole
# defect - not the exit, which is normal, but the missing line, which is what
# leaves firstmate unable to tell a finished worker from a stopped one.
AGENT_STATE=$(fm_backend_agent_state "$TASK_BACKEND" "$BACKEND_TARGET" \
  "$EXPECTED_LABEL" "$WT" 2>/dev/null) || AGENT_STATE=unknown

# A worker FIRSTMATE ITSELF stopped is separated here, before `exited`, because
# the two are byte-identical from the outside and mean opposite things. An
# endpoint that is agent-free because bin/fm-control.sh exit emptied it on
# purpose is not a worker that needs attention; it is a worker that is
# intentionally not running, and until this verdict existed there was no way to
# say so. The declaration is only ever believed alongside a dead agent, and only
# while it still binds to the incarnation it was written for
# (bin/fm-stopped-lib.sh), so an agent that died on its own writes nothing and
# still reports `exited`, an agent that is alive is never called stopped, and a
# relaunch's replacement is supervised normally from its first read.
#
# It outranks the status log deliberately. Whatever the worker last appended, it
# was appended BEFORE firstmate stopped it, so it describes the run rather than
# the present; the stop is the newer fact and the one that explains the empty
# endpoint.
if [ "$AGENT_STATE" = dead ] && fm_stopped_declared "$STATE" "$ID"; then
  STOP_REASON=$(fm_stopped_field "$STATE" "$ID" reason)
  STOP_AGE=$(fm_stopped_age "$STATE" "$ID") || STOP_AGE=
  STOP_DETAIL="stopped by firstmate on purpose"
  [ -z "$STOP_AGE" ] || STOP_DETAIL="$STOP_DETAIL ${STOP_AGE}s ago"
  [ -z "$STOP_REASON" ] || STOP_DETAIL="$STOP_DETAIL: $STOP_REASON"
  emit stopped declared-stop "$STOP_DETAIL; its work is intact at $WT and nothing is running - resume it with bin/fm-control.sh $ID relaunch"
fi

if [ "$AGENT_STATE" = dead ] \
  && ! status_is_terminal_verb "$LOG_LINE"; then
  # ... unless the record is still inside its registration window, where the
  # same emptiness is equally consistent with a harness that has not started.
  # Reported as unknown rather than smoothed into working: unknown licenses
  # nothing, so a spawn that really did die on arrival is still surfaced by the
  # next read instead of being absorbed as progress.
  if within_spawn_grace; then
    emit unknown pane "endpoint holds no agent yet, but $SPAWN_GRACE_REASON; a harness that has not started leaves the same empty endpoint as one that left, so this is not proof either way - re-read it before acting, and do not relaunch on it"
  fi
  if [ -n "$LOG_LINE" ]; then
    EXIT_LAST="last event: $(status_line_note "$LOG_LINE")"
  else
    EXIT_LAST="no status events"
  fi
  emit exited pane "harness agent exited without a terminal status line ($EXIT_LAST); its work is intact at $WT - read the deliverable to tell finished from stopped mid-task, and do not assume either"
fi

# Secondmates idle on their own watcher (idle pane = healthy), so the busy
# state is not meaningful for them; read their state from the status log only.
# Only an exact busy verdict reports working here, and only an exact idle
# verdict permits the status-log fallback below. Missing, malformed, stale, or
# unverified semantic state remains unknown.
if [ "$KIND" != secondmate ]; then
  COMPOSER_STATE=$(fm_backend_composer_state "$TASK_BACKEND" "$BACKEND_TARGET" 2>/dev/null)
  if [ "$COMPOSER_STATE" = dialog ]; then
    emit blocked pane "modal dialog"
  fi
  BUSY_VERDICT=$(crew_busy_verdict "$BACKEND_TARGET")
  case "${BUSY_VERDICT%% *}" in
    busy)
      if CAP_DETAIL=$(fm_crew_opencode_cap_detail); then
        emit blocked pane "$CAP_DETAIL"
      fi
      emit working pane "harness busy (${BUSY_VERDICT#* })" ;;
    idle) ;;
    *) PANE_UNKNOWN_REASON="harness state unavailable ($BUSY_VERDICT)" ;;
  esac
fi

# Fall back to the resolved status declaration, but ONLY when its verb maps to a real
# run-state. A decision-closing event - resolved: (fm-classify-lib.sh's
# FM_CLASSIFY_RESOLVE_VERB), and any future decision-only sibling - is NOT a state:
# it exists solely to CLOSE a keyed decision in the durable fold, so a trailing
# resolved: must never become the current state or leak its resolution prose as the
# detail. Skipping it lets a just-resolved idle crew (typically a secondmate, which
# has no busy check above) fall through to the idle default instead of rendering
# `unknown` with the resolution note as `doing`. map_log_state is the single owner of
# the verb->state mapping (including the configurable paused verb), so reusing its
# `unknown` verdict as the "not a state" test needs no second verb list here.
if [ -n "$LOG_VERB" ]; then
  if [ "$LOG_VERB" = "done" ]; then
    emit_ship_status_done
  fi
  LOG_STATE=$(map_log_state "$LOG_LINE")
  if [ "$LOG_STATE" != unknown ]; then
    LOG_DETAIL="$(status_line_note "$LOG_LINE")"
    [ -z "$MATE_QUIET_DETAIL" ] || LOG_DETAIL="$LOG_DETAIL${SEP}$MATE_QUIET_DETAIL"
    emit "$LOG_STATE" status-log "$LOG_DETAIL"
  fi
fi

if [ -n "${PANE_UNKNOWN_REASON:-}" ]; then
  emit unknown pane "$PANE_UNKNOWN_REASON"
fi

FINAL_DETAIL="no current-state source available"
[ -z "$MATE_QUIET_DETAIL" ] || FINAL_DETAIL="$FINAL_DETAIL${SEP}$MATE_QUIET_DETAIL"
emit unknown none "$FINAL_DETAIL"
