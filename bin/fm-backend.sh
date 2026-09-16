#!/usr/bin/env bash
# fm-backend.sh - runtime-backend selection, meta helpers, selector resolution,
# and dispatch for firstmate's session-provider abstraction.
#
# Design: data/fm-backend-design-d7/report.md ("Backend Interface") and
# data/fm-backend-design-d7/herdr-addendum.md ("Events as the core
# abstraction"). This fork keeps exactly one runtime session backend, herdr;
# the tmux, zellij, orca, and cmux adapters were removed and survive only in
# git history. The adapter indirection below is deliberately kept so a future
# pull-back stays cheap.
# Codex App is intentionally not in the known set yet.
# docs/codex-app-backend.md owns that blocked backend contract.
#
# Compatibility contract: a task's meta may omit `backend=`; every reader here
# treats that as `herdr`. fm-spawn.sh does not write `backend=herdr` for a
# default-backend task, so existing and newly spawned default-path metas stay
# byte-identical. Only a task spawned on a non-default spawn-capable backend
# would carry an explicit `backend=` line; no such backend exists in this
# fork, so explicit values other than `herdr` are refused.
#
# Event-source framing (herdr-addendum "Events as the core abstraction"): a
# backend's supervision surface is conceptually an EVENT SOURCE - it produces
# task events (status-changed, went-stale, exited) that map onto firstmate's
# existing signal/stale/check/heartbeat wake vocabulary.
# The pull primitives also stay available
# on their own for on-demand reads (fm-peek.sh, fm-crew-state.sh).

FM_BACKEND_SCRIPT=${BASH_SOURCE[0]:-$0}
FM_BACKEND_LIB_DIR="$(cd "$(dirname "$FM_BACKEND_SCRIPT")" && pwd)"
unset FM_BACKEND_SCRIPT
FM_BACKEND_DEFAULT_ROOT="$(cd "$FM_BACKEND_LIB_DIR/.." && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-${FM_ROOT:-$FM_BACKEND_DEFAULT_ROOT}}"
FM_HOME="${FM_HOME:-${FM_ROOT_OVERRIDE:-$FM_ROOT}}"
FM_BACKEND_CONFIG_DIR="${FM_CONFIG_OVERRIDE:-$FM_HOME/config}"

# The shared process-name vocabulary for the endpoint-evidence probes below
# (suspension needs harness identity, not just a stopped state). Eager, not
# adapter-lazy, on purpose: the suspension probe answers in its CALLER's shell
# while the adapter it reads the tty through loads inside a command
# substitution whose definitions are discarded on return, so by probe time the
# adapter is present but a lazily loaded classifier would not be. Guarded on
# existence rather than assumed: isolated consumers (test fake-roots) source
# this file without the full bin directory, and there the probe degrades to
# unprovable rather than breaking the load.
# shellcheck source=bin/fm-agent-process-lib.sh
if [ -f "$FM_BACKEND_LIB_DIR/fm-agent-process-lib.sh" ]; then
  . "$FM_BACKEND_LIB_DIR/fm-agent-process-lib.sh"
fi

# The one runtime session backend this fork supports. The adapter seam is
# kept so a removed backend stays cheap to pull back from git history.
# codex-app remains deliberately absent; see docs/codex-app-backend.md.
FM_BACKEND_KNOWN="herdr"
FM_BACKEND_SPAWN="herdr"

# fm_backend_list_contains: whitespace-delimited membership without relying on
# shell word splitting. fm-backend.sh is normally sourced by bash scripts, but
# zsh diagnostics can source it too, so backend-name matching must stay portable.
fm_backend_list_contains() {  # <list> <name>
  local list=$1 name=$2
  case "$name" in
    *[[:space:]]*) return 1 ;;
  esac
  case " $list " in
    *" $name "*) return 0 ;;
  esac
  return 1
}

fm_backend_is_known() {  # <name>
  fm_backend_list_contains "$FM_BACKEND_KNOWN" "$1"
}

# fm_backend_detect: detect the runtime firstmate itself is CURRENTLY executing
# inside, from verified environment markers (mirrors bin/fm-harness.sh's
# env-marker detection layer for harnesses). Prints the detected backend name
# and returns 0, or returns 1 when nothing is detected. herdr injects
# HERDR_ENV=1 (plus HERDR_SOCKET_PATH/HERDR_PANE_ID) into every process it
# manages a pane for; HERDR_ENV=1 alone selects herdr. No other backend is
# detectable in this fork.
# Callers needing the detection result read FM_BACKEND_DETECTED after a
# direct (non-command-substitution) call.

fm_backend_detect() {
  FM_BACKEND_DETECTED=""
  if [ "${HERDR_ENV:-}" = "1" ]; then
    FM_BACKEND_DETECTED=herdr
    printf 'herdr'
    return 0
  fi
  return 1
}

# fm_backend_name: resolve the ACTIVE backend for a NEW spawn, absent an
# explicit per-task override. Precedence: FM_BACKEND env, then config/backend
# (a single word on its first non-empty line, mirroring config/crew-harness),
# then runtime auto-detection (fm_backend_detect), then default herdr. A
# per-task `--backend` flag is parsed by the caller (fm-spawn.sh) and takes
# precedence over this resolution entirely; it is not read here. Auto-detect
# fires only when nothing was explicitly configured, so an explicit setting
# always wins.
fm_backend_name() {
  local line v detected
  if [ -n "${FM_BACKEND:-}" ]; then
    printf '%s' "$FM_BACKEND"
    return 0
  fi
  if [ -f "$FM_BACKEND_CONFIG_DIR/backend" ]; then
    while IFS= read -r line || [ -n "$line" ]; do
      v=$(printf '%s' "$line" | tr -d '[:space:]')
      if [ -n "$v" ]; then
        printf '%s' "$v"
        return 0
      fi
    done < "$FM_BACKEND_CONFIG_DIR/backend"
  fi
  # Called directly (not in a command substitution) so the detect signal
  # globals survive into the notice below.
  if fm_backend_detect >/dev/null; then
    detected=$FM_BACKEND_DETECTED
    printf '%s' "$detected"
    return 0
  fi
  printf 'herdr'
}

# fm_backend_validate: refuse an unknown backend LOUDLY. Silent on success.
# The removed backends (tmux, zellij, orca, cmux) are refused here with a
# message naming herdr as the supported backend.
fm_backend_validate() {  # <name>
  local name=$1
  if ! fm_backend_is_known "$name"; then
    case "$name" in
      tmux|zellij|orca|cmux)
        echo "error: backend '$name' is not supported in this fork (supported: herdr)" >&2
        ;;
      *)
        echo "error: unknown backend '$name' (known: $FM_BACKEND_KNOWN)" >&2
        ;;
    esac
    return 1
  fi
  return 0
}

fm_backend_validate_spawn() {  # <name>
  local name=$1
  fm_backend_validate "$name" || return 1
  fm_backend_list_contains "$FM_BACKEND_SPAWN" "$name" && return 0
  echo "error: backend '$name' does not support task spawning yet (spawn-supported: $FM_BACKEND_SPAWN)" >&2
  return 1
}

# fm_backend_required_tools: the backend-SPECIFIC CLI tools a firstmate home on
# <backend> genuinely requires, beyond firstmate's universal toolchain (owned by
# docs/configuration.md "Toolchain" and bootstrap's COMMON list). This is the
# single owner of the per-backend dependency delta, so bootstrap follows the
# RESOLVED backend instead of demanding an inactive backend's tools.
# Prints a single space-separated line and returns 0 for a known backend; returns
# 1 and prints nothing for an unknown backend.
fm_backend_required_tools() {  # <backend>
  case "$1" in
    herdr)  printf '%s' 'herdr jq treehouse' ;;
    *) return 1 ;;
  esac
}

fm_backend_required_tool_available() {  # <backend> <tool>
  local backend=$1 tool=$2 required
  required=$(fm_backend_required_tools "$backend") || return 1
  fm_backend_list_contains "$required" "$tool" || return 1
  command -v "$tool" >/dev/null 2>&1
}

# fm_meta_get: the LAST value of `key=` in <meta-file>, or empty (never
# errors) if the file or key is absent. Mirrors the ad hoc `grep '^key=' |
# tail -1 | cut -d= -f2-` snippet every fm-*.sh script used to repeat inline.
fm_meta_get() {  # <meta-file> <key>
  local meta=$1 key=$2 line value=''
  [ -f "$meta" ] || return 0
  while IFS= read -r line || [ -n "$line" ]; do
    case "$line" in
      "$key="*) value=${line#*=} ;;
    esac
  done < "$meta" 2>/dev/null || true
  printf '%s' "$value"
}

# fm_backend_of_meta: the backend recorded in <meta-file>, defaulting to
# `herdr` when the field is absent.
fm_backend_of_meta() {  # <meta-file>
  local v
  v=$(fm_meta_get "$1" backend)
  printf '%s' "${v:-herdr}"
}

fm_backend_target_of_meta() {  # <meta-file>
  local meta=$1 window
  window=$(fm_meta_get "$meta" window)
  [ -n "$window" ] && printf '%s' "$window"
}

# fm_backend_validate_task_endpoint: validate a task cleanup record entirely
# from its durable metadata before any runtime command or cleanup mutation.
# The validation binds the exact task id, selected backend, target, project,
# and worktree. Records carry endpoint_task_id because the runtime pane ids
# do not encode the task label.
# On success, sets FM_BACKEND_VALIDATED_BACKEND and
# FM_BACKEND_VALIDATED_TARGET. On failure, prints one refusal and returns 1.
fm_backend_meta_exact_value() {  # <meta-file> <key>
  local meta=$1 key=$2 count value
  count=$(grep -c "^$key=" "$meta" 2>/dev/null || true)
  [ "$count" -eq 1 ] || return 1
  value=$(grep "^$key=" "$meta" | cut -d= -f2-)
  [ -n "$value" ] || return 1
  printf '%s' "$value"
}

fm_backend_endpoint_atom_valid() {  # <value>
  case "$1" in
    ''|*[!A-Za-z0-9._@%+-]*) return 1 ;;
  esac
}

fm_backend_validate_task_endpoint() {  # <meta-file> <task-id>
  local meta=$1 id=$2 backend_count backend window worktree project binding_count binding
  local recorded_session workspace tab pane
  FM_BACKEND_VALIDATED_BACKEND=
  FM_BACKEND_VALIDATED_TARGET=
  [ -f "$meta" ] && [ ! -L "$meta" ] || {
    echo "REFUSED: task $id has no regular endpoint metadata at $meta; preserving task state." >&2
    return 1
  }
  case "$id" in ''|*[!A-Za-z0-9._-]*)
    echo "REFUSED: task endpoint identity has an invalid task id; preserving task state." >&2
    return 1
  esac
  window=$(fm_backend_meta_exact_value "$meta" window) || {
    echo "REFUSED: task $id has a missing, empty, or ambiguous window endpoint; preserving task state." >&2
    return 1
  }
  worktree=$(fm_backend_meta_exact_value "$meta" worktree) || {
    echo "REFUSED: task $id has a missing, empty, or ambiguous worktree identity; preserving task state." >&2
    return 1
  }
  project=$(fm_backend_meta_exact_value "$meta" project) || {
    echo "REFUSED: task $id has a missing, empty, or ambiguous project identity; preserving task state." >&2
    return 1
  }
  case "$worktree$project$window" in *$'\n'*|*$'\r'*|*$'\t'*)
    echo "REFUSED: task $id has malformed endpoint metadata; preserving task state." >&2
    return 1
  esac
  backend_count=$(grep -c '^backend=' "$meta" 2>/dev/null || true)
  case "$backend_count" in
    0) backend=herdr ;;
    1) backend=$(fm_backend_meta_exact_value "$meta" backend) || backend= ;;
    *) backend= ;;
  esac
  if [ -z "$backend" ] || ! fm_backend_is_known "$backend"; then
    echo "REFUSED: task $id has a missing, ambiguous, or unknown backend identity; preserving task state." >&2
    return 1
  fi
  binding_count=$(grep -c '^endpoint_task_id=' "$meta" 2>/dev/null || true)
  case "$binding_count" in
    0) binding= ;;
    1)
      binding=$(fm_backend_meta_exact_value "$meta" endpoint_task_id) || {
        echo "REFUSED: task $id has an empty endpoint task binding; preserving task state." >&2
        return 1
      }
      ;;
    *)
      echo "REFUSED: task $id has an ambiguous endpoint task binding; preserving task state." >&2
      return 1
      ;;
  esac
  if [ -n "$binding" ] && [ "$binding" != "$id" ]; then
    echo "REFUSED: endpoint metadata belongs to task $binding, not $id; preserving task state." >&2
    return 1
  fi

  case "$backend" in
    herdr)
      [ "$binding" = "$id" ] || {
        echo "REFUSED: Herdr endpoint metadata for task $id lacks an exact task binding; preserving task state." >&2
        return 1
      }
      recorded_session=$(fm_backend_meta_exact_value "$meta" herdr_session) || recorded_session=
      workspace=$(fm_backend_meta_exact_value "$meta" herdr_workspace_id) || workspace=
      tab=$(fm_backend_meta_exact_value "$meta" herdr_tab_id) || tab=
      pane=$(fm_backend_meta_exact_value "$meta" herdr_pane_id) || pane=
      if [ -z "$recorded_session" ] || [ -z "$workspace" ] || [ -z "$tab" ] || [ -z "$pane" ] \
        || [ "$window" != "$recorded_session:$pane" ] \
        || ! fm_backend_endpoint_atom_valid "$recorded_session" \
        || ! fm_backend_endpoint_atom_valid "$workspace" \
        || ! fm_backend_endpoint_atom_valid "${tab//:/_}" \
        || ! fm_backend_endpoint_atom_valid "${pane//:/_}"; then
        echo "REFUSED: Herdr endpoint metadata for task $id is malformed or inconsistent; preserving task state." >&2
        return 1
      fi
      ;;
  esac
  # shellcheck disable=SC2034 # Output globals are consumed by sourcing callers.
  FM_BACKEND_VALIDATED_BACKEND=$backend
  # shellcheck disable=SC2034 # Output globals are consumed by sourcing callers.
  FM_BACKEND_VALIDATED_TARGET=$window
  return 0
}

fm_backend_meta_for_window() {  # <target> <state-dir>
  local target=$1 state=$2 meta window terminal
  for meta in "$state"/*.meta; do
    [ -e "$meta" ] || continue
    window=$(fm_meta_get "$meta" window)
    terminal=$(fm_meta_get "$meta" terminal)
    { [ -n "$window" ] && [ "$window" = "$target" ]; } || { [ -n "$terminal" ] && [ "$terminal" = "$target" ]; } || continue
    printf '%s' "$meta"
    return 0
  done
  return 1
}

fm_backend_task_id_for_selector() {  # <raw-target> <state-dir>
  local raw=$1 state=$2 id
  case "$raw" in
    *:*) return 1 ;;
  esac
  if [ -f "$state/$raw.meta" ]; then
    printf '%s' "$raw"
    return 0
  fi
  case "$raw" in
    fm-*)
      id=${raw#fm-}
      [ -f "$state/$id.meta" ] || return 1
      printf '%s' "$id"
      return 0
      ;;
  esac
  return 1
}

fm_backend_meta_for_selector() {  # <raw-target> <state-dir>
  local raw=$1 state=$2 id
  id=$(fm_backend_task_id_for_selector "$raw" "$state") || return 1
  printf '%s/%s.meta' "$state" "$id"
}

fm_backend_of_selector() {  # <raw-target> <resolved-target> <state-dir>
  local raw=$1 resolved=$2 state=$3 meta
  meta=$(fm_backend_meta_for_selector "$raw" "$state" 2>/dev/null || true)
  [ -n "$meta" ] && { fm_backend_of_meta "$meta"; return 0; }
  if [ -n "$resolved" ]; then
    meta=$(fm_backend_meta_for_window "$resolved" "$state" 2>/dev/null || true)
    [ -n "$meta" ] && { fm_backend_of_meta "$meta"; return 0; }
  fi
  printf 'herdr'
}

fm_backend_expected_label_of_selector() {  # <raw-target> <state-dir>
  local raw=$1 state=$2 id
  id=$(fm_backend_task_id_for_selector "$raw" "$state" 2>/dev/null || true)
  [ -n "$id" ] && printf 'fm-%s' "$id"
  return 0
}

# fm_backend_source: source the named backend's adapter file, once per shell.
# The adapter is an independently linted canonical root. The /dev/null source
# boundaries keep runtime dispatch from importing adapter ASTs into
# every dispatcher consumer while preserving the runtime source operations.
fm_backend_source() {  # <name>
  local name=$1
  fm_backend_validate "$name" || return 1
  case "$name" in
    herdr)
      if [ -z "${_FM_BACKEND_HERDR_SOURCED:-}" ]; then
        # shellcheck source=/dev/null
        . "$FM_BACKEND_LIB_DIR/backends/herdr.sh" || return 1
        _FM_BACKEND_HERDR_SOURCED=1
      fi
      ;;
  esac
}

# fm_backend_resolve_selector: resolve a raw fm-send.sh/fm-peek.sh style
# selector to a live session-provider target. Four forms, in order:
#   target with ":"   used as-is (the escape hatch for a window/pane outside
#                      this firstmate home) - backend-independent, a literal string.
#   exact task id      routed through <state-dir>/<id>.meta's backend target
#                      (`window=`) - backend-independent, a stored value, NOT
#                      re-verified against a live backend inventory (matches
#                      today's behavior).
#   "fm-<id>"          legacy task window label fallback routed through
#                      <state-dir>/<id>.meta when no exact
#                      <state-dir>/fm-<id>.meta exists.
#   anything else      first matched against recorded `window=`
#                      metadata, then refused: with one backend there is no
#                      live-inventory bare-name fallback.
fm_backend_resolve_selector() {  # <raw-target> <state-dir>
  local raw=$1 state=$2 meta window
  case "$raw" in
    *:*)
      printf '%s' "$raw"
      return 0
      ;;
  esac
  meta=$(fm_backend_meta_for_selector "$raw" "$state" 2>/dev/null || true)
  if [ -n "$meta" ]; then
    window=$(fm_backend_target_of_meta "$meta")
    [ -n "$window" ] || { echo "error: no backend target recorded in $meta" >&2; return 1; }
    printf '%s' "$window"
    return 0
  fi
  case "$raw" in
    fm-*)
      echo "error: no metadata for $raw in $state; pass session:pane to target a pane outside this firstmate home" >&2
      return 1
      ;;
    *)
      meta=$(fm_backend_meta_for_window "$raw" "$state" 2>/dev/null || true)
      if [ -n "$meta" ]; then
        window=$(fm_backend_target_of_meta "$meta")
        [ -n "$window" ] || { echo "error: no backend target recorded in $meta" >&2; return 1; }
        printf '%s' "$window"
        return 0
      fi
      echo "error: no metadata for $raw in $state; pass session:pane to target a pane outside this firstmate home" >&2
      return 1
      ;;
  esac
}

# --- generic per-op dispatch -------------------------------------------------
#
# Thin case-dispatch wrappers so a caller names an operation and a backend
# rather than hand-writing per-backend cases at every call site. Each
# backend arm is kept even with one backend so a future pull-back stays
# cheap, without changing call sites.

# fm_backend_capture: bounded plain-text session capture.
fm_backend_capture() {  # <backend> <target> <lines> [expected-label]
  local backend=$1
  shift
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_capture "$@" ;;
    *) echo "error: no capture implementation for backend '$backend'" >&2; return 1 ;;
  esac
}

# fm_backend_send_key: one backend-supported named special key.
fm_backend_send_key() {  # <backend> <target> <key> [expected-label]
  local backend=$1
  shift
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_send_key "$@" ;;
    *) echo "error: no send-key implementation for backend '$backend'" >&2; return 1 ;;
  esac
}

# fm_backend_send_text_submit: type text once, then submit and verify,
# retrying only the submission (never retyping). Echoes the backend's
# proof-carrying verdict; callers require exact empty for confirmed delivery.
fm_backend_send_text_submit() {  # <backend> <target> <text> <retries> <enter-sleep> <settle> [expected-label]
  local backend=$1
  shift
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_send_text_submit "$@" ;;
    *) echo "error: no send-text implementation for backend '$backend'" >&2; return 1 ;;
  esac
}

# fm_backend_kill: remove the task's session endpoint (best-effort; a
# nonexistent/already-gone target is not an error - callers already swallow
# failures here).
fm_backend_kill() {  # <backend> <target>
  local backend=$1
  shift
  [ -n "${1:-}" ] || { echo "error: refusing empty backend kill target" >&2; return 1; }
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_kill "$@" ;;
    *) echo "error: no kill implementation for backend '$backend'" >&2; return 1 ;;
  esac
}

fm_backend_remove_worktree() {  # <backend> <worktree-id>
  local backend=$1
  shift
  echo "error: backend '$backend' does not own task worktrees" >&2
  return 1
}

fm_backend_worktree_path() {  # <backend> <worktree-id>
  local backend=$1
  shift
  echo "error: backend '$backend' does not own task worktrees" >&2
  return 1
}

# fm_backend_busy_state: semantic busy/idle/unknown for backends that expose
# native agent-state (herdr-addendum "busy state" row). Callers own the
# fallback policy: fm-watch.sh uses unknown as the cue for harness-scoped
# pane-tail detection, while fm-crew-state.sh also corroborates native idle
# verdicts with the recorded harness's signature before treating a no-run
# crew as not busy.
fm_backend_busy_state() {  # <backend> <target>
  local backend=$1
  shift
  fm_backend_source "$backend" || { printf 'unknown'; return 0; }
  case "$backend" in
    herdr) fm_backend_herdr_busy_state "$@" ;;
    *) printf 'unknown' ;;
  esac
}

# fm_backend_agent_root_pids: the pids of <target>'s foreground processes, one
# per line, as the roots of the pane's process subtree. Prints nothing and
# returns 1 when this backend has no per-pane pid source or the read fails.
#
# Exists for supervision's positive progress measurement (bin/fm-progress-lib.sh),
# which needs a process to measure accumulated CPU on rather than a rendered tail
# to compare bytes of. It reports the FOREGROUND process specifically, so a
# harness-named process left running in the background of an otherwise idle
# pane cannot lend that pane its CPU; the progress library walks the ppid
# graph down from here to reach the tool calls the harness spawns.
#
# herdr is the backend with a per-pane pid source.
fm_backend_agent_root_pids() {  # <backend> <target>
  local backend=$1
  shift
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_foreground_pids "$@" ;;
    *) return 1 ;;
  esac
}

# fm_backend_composer_state: classify the composer/input area of <target> as
# empty|pending|pending-unproven|dialog|unknown for callers that need a
# pre-submit input guard, a submit acknowledgement, a launch-readiness check,
# or a modal-dialog detection. It is
# exposed so a caller other than the send path (the away-mode daemon's
# supervisor-pane pending-input guard in bin/fm-supervise-daemon.sh, and
# fm-spawn.sh's kimi readiness/delivery checks) can ask the same question
# without duplicating per-backend composer reading.
fm_backend_composer_state() {  # <backend> <target> [expected-label] -> empty|pending|pending-unproven|dialog|unknown
  local backend=$1
  shift
  fm_backend_source "$backend" || { printf 'unknown'; return 0; }
  case "$backend" in
    herdr) fm_backend_herdr_composer_state "$@" ;;
    *) printf 'unknown' ;;
  esac
}

# fm_backend_target_exists: cheap, READ-ONLY existence check - does the
# recorded TARGET endpoint still exist on BACKEND? Never starts a server or
# session: for herdr this deliberately queries the pane directly instead of
# going through fm_backend_herdr_target_ready (which auto-starts the herdr
# server as a side effect via fm_backend_herdr_server_ensure - fine for an
# operation that is about to use the pane, wrong for a passive liveness
# probe). An unqueryable herdr pane (server down, pane closed) simply fails,
# which IS "does not exist" for this purpose.
# Mirrors fm-crew-state.sh's pane_readable check; exists here as one shared
# primitive so callers that only need a fast alive/dead read (recovery
# digests, the session-start fleet digest) do not re-derive it inline.
fm_backend_target_exists() {  # <backend> <target>
  local backend=$1 target=$2 session pane
  case "$backend" in
    herdr)
      fm_backend_source herdr || return 1
      session=${target%%:*}
      pane=${target#*:}
      [ -n "$session" ] && [ -n "$pane" ] && [ "$pane" != "$target" ] || return 1
      # fm_backend_herdr_cli (not a raw HERDR_SESSION-only call): verified
      # empirically (docs/herdr-backend.md "Session targeting") that the bare
      # env var alone is NOT reliably honored once another herdr server is
      # already bound on the machine - it silently queries whatever server IS
      # running instead. fm_backend_herdr_cli appends the required --session
      # flag on top, so this check is correctly scoped even when the caller's
      # own ambient session (e.g. the primary firstmate's default session) is
      # a DIFFERENT one than the target's.
      fm_backend_herdr_cli "$session" pane get "$pane" >/dev/null 2>&1
      ;;
    *)
      return 1
      ;;
  esac
}

# --- endpoint evidence: identity and suspension --------------------------
#
# Both probes below exist for the same defect, found live on 2026-08-20: a
# liveness verdict was answered from firstmate's own BOOKKEEPING about an
# endpoint rather than from the endpoint itself, and answered confidently in
# the direction that authorises recovery.
#
#   - A Herdr server renumbered its panes. The recorded pane id stopped
#     resolving while the agent kept running under a new id in the same
#     session, and the recorded-endpoint probe reported `missing`, which is
#     one of the two verdicts that license a relaunch. Only an incidental
#     duplicate-tab-label refusal inside Herdr stopped a second agent from
#     being launched alongside the first.
#   - A worker was suspended with ctrl-z. Herdr deregisters the agent the
#     moment the shell reclaims the foreground, so `agent get` answered
#     agent_not_found and the endpoint read `dead` - which both licenses a
#     relaunch and makes the tab a close-and-replace husk candidate, so a
#     frozen-but-recoverable worker could be killed and replaced.
#
# The rule both probes follow: absence of the recorded IDENTIFIER is not
# absence of the AGENT, and a record about a process cannot be current while
# that process is stopped. Each probe can only WITHHOLD a recovery-licensing
# verdict, never create one, so a backend that cannot answer degrades to
# exactly today's behavior instead of to a new hazard.

# fm_backend_endpoint_tty: the controlling terminal device of <target>'s
# endpoint on <backend>, as `ps` names it (e.g. `ttys012`), or empty when the
# backend cannot answer. This is the one seam the suspension probe needs from
# an adapter; everything else about the probe is backend-independent.
fm_backend_endpoint_tty() {  # <backend> <target>
  local backend=$1 target=$2
  fm_backend_source "$backend" || return 0
  case "$backend" in
    herdr) fm_backend_herdr_endpoint_tty "$target" ;;
    *) : ;;
  esac
}

# fm_backend_tty_suspended_agent: 0 when <tty> hosts a STOPPED process that
# the shared name classifier recognizes as a verified harness agent.
#
# The signal is the kernel's own process state (`T`), not a rendered string,
# so it is immune to how a harness draws itself. Requiring BOTH the stopped
# state and agent identity is what keeps the probe narrow enough to sit
# outside the foreground process group without weakening the negative
# verdicts the liveness classifiers depend on: a harness-named process merely
# left running in the background of an idle pane is `S`, never `T`, so a
# genuinely agent-free endpoint still classifies as agent-free. The name
# classifier it consults is loaded eagerly at this file's top (guarded on
# existence), because this probe runs in its caller's shell while the adapter
# load it depends on for the tty happens inside a discarded subshell.
fm_backend_tty_suspended_agent() {  # <tty>
  local tty=$1 state pid comm args argv0
  [ -n "$tty" ] || return 1
  tty=${tty#/dev/}
  while read -r state pid comm; do
    [ -n "$pid" ] || continue
    case "$state" in T*) ;; *) continue ;; esac
    [ -n "$comm" ] && [ "$(fm_agent_process_classify_name "$comm")" = agent ] && return 0
    args=$(LC_ALL=C ps -p "$pid" -o args= 2>/dev/null) || continue
    args=${args#"${args%%[![:space:]]*}"}
    argv0=${args%%[[:space:]]*}
    [ -n "$argv0" ] || continue
    [ "$(fm_agent_process_classify_name '' "$argv0")" = agent ] && return 0
  done <<EOF
$(LC_ALL=C ps -t "$tty" -o state=,pid=,comm= 2>/dev/null)
EOF
  return 1
}

# fm_backend_endpoint_suspended: 0 when <target> on <backend> provably holds a
# stopped harness agent. Anything the backend cannot answer is 1 (not
# provable), never a claim in either direction.
fm_backend_endpoint_suspended() {  # <backend> <target>
  local tty
  tty=$(fm_backend_endpoint_tty "$1" "$2" 2>/dev/null) || return 1
  fm_backend_tty_suspended_agent "$tty"
}

# fm_backend_identity_claimants: every LIVE endpoint on <backend>, within the
# recorded <target>'s own session scope, that carries this task's identity -
# endpoint label exactly <expected-label> AND working directory exactly
# <expected-cwd>. One target per line; empty when none, when either hint is
# absent, or when the backend cannot answer.
#
# Both hints are required together because neither alone identifies a task:
# two homes can run a task of the same name, and many endpoints can share a
# directory. Together they are distinguishing, because no two firstmate homes
# share both a task id and that task's own worktree or home directory.
fm_backend_identity_claimants() {  # <backend> <target> <expected-label> <expected-cwd>
  local backend=$1 target=$2 label=$3 cwd=$4
  [ -n "$label" ] && [ -n "$cwd" ] || return 0
  fm_backend_source "$backend" || return 0
  case "$backend" in
    herdr) fm_backend_herdr_identity_claimants "$target" "$label" "$cwd" ;;
    *) : ;;
  esac
}

# fm_backend_rebind_meta_fields: print <meta>'s endpoint fields rewritten to
# bind <new-target>, one `key=value` per line, for the fields this backend
# records an endpoint identifier in. Prints nothing and returns non-zero when
# the backend has no drifting identifier to correct, or when the new target
# cannot be read.
#
# Every consumer of a task record reads the same endpoint through several
# fields at once, and fm_backend_validate_task_endpoint refuses a record whose
# fields disagree. So a rebind is only correct if it rewrites all of them from
# ONE observation of the new endpoint, which is why the per-backend field shape
# lives here beside that validator rather than in the caller.
fm_backend_rebind_meta_fields() {  # <backend> <meta> <new-target>
  local backend=$1 new_target=$3 info workspace tab pane session
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr)
      session=${new_target%%:*}
      pane=${new_target#*:}
      [ -n "$session" ] && [ -n "$pane" ] && [ "$pane" != "$new_target" ] || return 1
      info=$(fm_backend_herdr_cli "$session" pane get "$pane" 2>/dev/null) || return 1
      workspace=$(printf '%s' "$info" | jq -er --arg pane "$pane" '
        select(.result.pane.pane_id == $pane) | .result.pane.workspace_id
        | select(type == "string" and length > 0)' 2>/dev/null) || return 1
      tab=$(printf '%s' "$info" | jq -er --arg pane "$pane" '
        select(.result.pane.pane_id == $pane) | .result.pane.tab_id
        | select(type == "string" and length > 0)' 2>/dev/null) || return 1
      printf 'window=%s\n' "$session:$pane"
      printf 'herdr_workspace_id=%s\n' "$workspace"
      printf 'herdr_tab_id=%s\n' "$tab"
      printf 'herdr_pane_id=%s\n' "$pane"
      ;;
    *) return 1 ;;
  esac
}

# fm_backend_agent_state: the single recovery-grade agent/endpoint state
# contract. It is deliberately richer than fm_backend_target_exists's cheap
# pane-presence read and prints exactly one of:
#   alive      - a verified harness agent is running.
#   dead       - the endpoint exists but confidently has no agent.
#   missing    - the recorded endpoint is authoritatively absent.
#   drifted    - the recorded IDENTIFIER no longer resolves, but a live
#                endpoint in the same session still carries this task's
#                identity. The record is stale; the agent is not gone.
#   suspended  - the endpoint holds a stopped harness agent. The agent is
#                present but frozen, so no record about it is current and it
#                must not be treated as agent-free.
#   ambiguous  - the endpoint exists but its process cannot be attributed.
#   unreadable - a target or inventory read failed or contradicted itself.
#   unverified - this backend has no recovery classifier.
# Only `dead` and `missing` license recovery. Every `alive` is proven at
# process level through the shared classifier in bin/fm-agent-process-lib.sh,
# never from a registration or a rendered title alone. The Herdr adapter
# reuses its strict husk classifier - which verifies a registered agent
# against `pane process-info` and the real process table, so a registration
# Herdr kept over a shell-only pane reads `dead` here (issue #4115) - then
# maps a positively stopped session server to `missing` only in this
# recovery-grade view.
# The two refinements below are applied HERE rather than in each adapter, so
# there is one owner of when an adapter's raw verdict is not the whole answer.
# Each only ever downgrades a recovery-licensing verdict to a non-licensing
# one, so a backend that supplies no evidence keeps exactly the adapter's own
# behavior. <expected-label> and <expected-cwd> are this task's identity; a
# caller that omits them gets no drift refinement, and therefore today's
# `missing`.
fm_backend_agent_state() {  # <backend> <target> [expected-label] [expected-cwd]
  local backend=$1 target=$2 label=${3:-} cwd=${4:-} raw
  fm_backend_source "$backend" || { printf 'unverified'; return 0; }
  case "$backend" in
    herdr) raw=$(fm_backend_herdr_agent_state "$target") ;;
    *) printf 'unverified'; return 0 ;;
  esac
  case "$raw" in
    dead)
      if fm_backend_endpoint_suspended "$backend" "$target"; then
        printf 'suspended'
        return 0
      fi
      ;;
    missing)
      if [ -n "$(fm_backend_identity_claimants "$backend" "$target" "$label" "$cwd")" ]; then
        printf 'drifted'
        return 0
      fi
      ;;
  esac
  printf '%s' "$raw"
}

# Backward-compatible three-state view for existing callers. An
# authoritatively missing endpoint is confidently not a live agent, while every
# ambiguous, unreadable, unverified, drifted, or suspended result stays unknown -
# a drifted record and a frozen agent are both cases where firstmate does not
# know, and unknown is the answer that licenses nothing.
fm_backend_agent_alive() {  # <backend> <target> [expected-label] [expected-cwd]
  case "$(fm_backend_agent_state "$@")" in
    alive) printf 'alive' ;;
    dead|missing) printf 'dead' ;;
    *) printf 'unknown' ;;
  esac
}

# --- native event push (backend-extensible) ---------------------------------
#
# The watcher's event-wait splice (bin/fm-watch.sh) is backend-agnostic: it asks
# fm_backend_has_push whether a window's backend can push semantic state changes,
# and for those backends replaces its blind `sleep POLL` with a bounded wait on
# fm_backend_wait_transition. Every push-capable backend reuses the shared
# normalized-transition shape and policy table (bin/fm-transition-lib.sh); today
# only herdr implements the surface (docs/herdr-backend.md "Native
# pane.agent_status_changed push escalation"). A backend with no native push
# reports has-push false and returns 2 from the dispatchers below, so the
# watcher falls back to its poll loop - the permanent fail-closed backstop.

# fm_backend_has_push: 0 if <backend> exposes a native transition push stream.
fm_backend_has_push() {  # <backend>
  case "$1" in
    herdr) return 0 ;;
    *) return 1 ;;
  esac
}

# fm_backend_events_capable: 0 if <backend>'s push path is usable for <session>
# right now (version/schema/reader gate). Non-push backends are never capable.
# The watcher memoizes this per session so the potentially heavy capability
# probe is not repeated every poll.
fm_backend_events_capable() {  # <backend> <session>
  local backend=$1
  shift
  fm_backend_has_push "$backend" || return 1
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_events_capable "$@" ;;
    *) return 1 ;;
  esac
}

# fm_backend_wait_transition: bounded wait for a fresh actionable (blocked)
# transition on one of <pane_window...> in <session>, up to <timeout_secs>.
# Prints the normalized transition record and returns 0 on a fresh actionable
# edge; returns 1 on a clean timeout (the caller has effectively already slept);
# returns 2 when the event path is unusable (the caller sleeps the budget
# itself). Non-push backends always return 2.
fm_backend_wait_transition() {  # <backend> <session> <timeout_secs> <state_dir> <pane_window...>
  local backend=$1
  shift
  fm_backend_has_push "$backend" || return 2
  fm_backend_source "$backend" || return 2
  case "$backend" in
    herdr) fm_backend_herdr_wait_transition "$@" ;;
    *) return 2 ;;
  esac
}

fm_backend_commit_transition() {  # <backend> <state_dir> <session> <record>
  local backend=$1
  shift
  fm_backend_has_push "$backend" || return 1
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_commit_transition "$@" ;;
    *) return 1 ;;
  esac
}

fm_backend_clear_transition() {  # <backend> <state_dir> <window>
  local backend=$1
  shift
  fm_backend_has_push "$backend" || return 0
  fm_backend_source "$backend" || return 1
  case "$backend" in
    herdr) fm_backend_herdr_clear_transition "$@" ;;
    *) return 0 ;;
  esac
}
