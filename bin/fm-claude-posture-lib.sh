#!/usr/bin/env bash
# Claude permission-posture drift detection and same-session repair support.
#
# Background: Claude Code 2.1.257+ ignores permissions.defaultMode
# bypassPermissions/auto from project and local settings files, and a bare
# `claude --resume <session>` (what a terminal multiplexer re-issues after a
# machine restart) does not re-apply CLI flags. A restored worker therefore
# silently runs without its configured posture, and no settings file can fix
# it. Detection reads the live worker process command line instead: it is
# cheap (one ps read per candidate), version-proof (no transcript schema), and
# directly observes the launch contract. A correctly launched worker always
# carries exactly one posture flag, so it never reads as drifted.
#
# This library owns the configured-posture flag resolution for repair (it
# mirrors bin/fm-spawn.sh's config/claude-permission-mode parsing; the two
# must agree), the restore-shape command-line classifier, the idle gate, and
# the repair command builder. bin/fm-control.sh's repair-posture verb owns the
# lifecycle transaction (exit, resume, verify). bin/fm-claude-posture-sweep.sh
# owns periodic application. Requires bin/fm-backend.sh (process and composer
# reads) and bin/fm-busy-lib.sh (busy verdict) sourced by the caller, except
# for the pure parsers, which need nothing.
#
# Claude-only by construction: Claude Code's `claude --resume <id>` is the one
# verified pane-resume contract that keeps the conversation (proved live
# against claude 2.1.286); every other verified adapter needs a fresh agent
# (bin/fm-control-lib.sh). The primary firstmate session is out of scope: it
# is the captain's own conversation, never repaired automatically.

# fm_claude_posture_flag: print the permission flag this home's Claude workers
# are configured to carry. Mirrors bin/fm-spawn.sh's parsing exactly:
# absent/unreadable-shape rules are identical so launch and repair agree.
fm_claude_posture_flag() {  # <config-dir>
  local config=$1 mode token
  if [ ! -e "$config/claude-permission-mode" ] && [ ! -L "$config/claude-permission-mode" ]; then
    printf '%s\n' '--dangerously-skip-permissions'
    return 0
  fi
  if [ ! -f "$config/claude-permission-mode" ] || [ ! -r "$config/claude-permission-mode" ]; then
    echo "error: config/claude-permission-mode must be a readable regular file holding one of: bypass, auto" >&2
    return 1
  fi
  token=$(tr -d '[:space:]' < "$config/claude-permission-mode" || true)
  case "$token" in
    bypass) printf '%s\n' '--dangerously-skip-permissions' ;;
    auto) printf '%s\n' '--permission-mode auto' ;;
    *)
      echo "error: config/claude-permission-mode holds '$token'; accepted values are: bypass (--dangerously-skip-permissions, the default when the file is absent), auto (--permission-mode auto)" >&2
      return 1
      ;;
  esac
}

# fm_claude_posture_parse_cmdline: classify one observed command line. Prints
# one verdict line; always succeeds.
#   clean <mode>      a posture flag is present (mode: bypass|auto|other)
#   drifted <session> bare `claude --resume <id>`: the Herdr-restore shape with
#                     no posture flag and nothing else
#   skip <reason>     not-claude | no-resume | resume-picker | extra-args |
#                     bad-session-id. Anything a repair must not touch: a raw
#                     custom command (extra args), a session picker with no id,
#                     or a non-claude process.
fm_claude_posture_parse_cmdline() {  # <command-line>
  local line=$1 tok first=1 resume_seen=0 session='' mode=clean_default extra=0 val verdict=''
  local env_prefix=0 skip_env_value=0
  local noglob=0
  case $- in *f*) noglob=1 ;; esac
  set -f
  for tok in $line; do
    if [ "$first" -eq 1 ]; then
      if [ "$skip_env_value" -eq 1 ]; then
        skip_env_value=0
        continue
      fi
      case "$tok" in
        *=*) continue ;; # leading VAR=value environment assignment
      esac
      if [ "$tok" = env ]; then env_prefix=1; continue; fi
      if [ "$env_prefix" -eq 1 ]; then
        case "$tok" in
          -u|--unset) skip_env_value=1; continue ;;
          -*) continue ;;
        esac
      fi
      case "${tok##*/}" in
        claude) first=0 ;;
        *) verdict='skip not-claude'; break ;;
      esac
      continue
    fi
    case "$tok" in
      --dangerously-skip-permissions) mode=bypass ;;
      --permission-mode) mode=perm_next ;;
      --permission-mode=*)
        val=${tok#--permission-mode=}
        case "$val" in
          bypassPermissions) mode=bypass ;;
          auto) mode=auto ;;
          default|manual) mode=clean_default ;;
          *) mode=other ;;
        esac
        ;;
      *)
        if [ "$mode" = perm_next ]; then
          case "$tok" in
            bypassPermissions) mode=bypass ;;
            auto) mode=auto ;;
            default|manual) mode=clean_default ;;
            *) mode=other ;;
          esac
          continue
        fi
        case "$tok" in
          --resume) resume_seen=1 ;;
          --resume=*)
            resume_seen=1
            session=${tok#--resume=}
            ;;
          *)
            if [ "$resume_seen" = 1 ] && [ -z "$session" ]; then
              session=$tok
            else
              extra=1
            fi
            ;;
        esac
        ;;
    esac
  done
  [ "$noglob" -eq 1 ] || set +f
  if [ -n "$verdict" ]; then printf '%s\n' "$verdict"; return 0; fi
  [ "$first" -eq 0 ] || { printf 'skip not-claude\n'; return 0; }
  if [ "$mode" = bypass ] || [ "$mode" = auto ] || [ "$mode" = other ]; then
    if [ "$resume_seen" = 1 ] && [ -n "$session" ] && [ "$extra" -eq 0 ]; then
      printf 'clean %s %s\n' "$mode" "$session"
    else
      printf 'clean %s\n' "$mode"
    fi
    return 0
  fi
  [ "$resume_seen" = 1 ] || { printf 'skip no-resume\n'; return 0; }
  [ -n "$session" ] || { printf 'skip resume-picker\n'; return 0; }
  case "$session" in
    *[!A-Za-z0-9._-]*) printf 'skip bad-session-id\n'; return 0 ;;
  esac
  [ "$extra" -eq 0 ] || { printf 'skip extra-args\n'; return 0; }
  printf 'drifted %s\n' "$session"
}

# fm_claude_posture_observe: print the full command line of every claude
# foreground process on <backend> <target>, one per line. Prints nothing when
# no claude process is foreground. Needs bin/fm-backend.sh sourced.
# FM_CLAUDE_POSTURE_TEST_ARGS, when set, replaces the process read with its
# value (one command line per line); it is a test seam only, never set in
# production.
fm_claude_posture_observe() {  # <backend> <target>
  local backend=$1 target=$2 pid args
  if [ -n "${FM_CLAUDE_POSTURE_TEST_ARGS_FILE:-}" ]; then
    [ -r "$FM_CLAUDE_POSTURE_TEST_ARGS_FILE" ] && cat "$FM_CLAUDE_POSTURE_TEST_ARGS_FILE"
    return 0
  fi
  if [ -n "${FM_CLAUDE_POSTURE_TEST_ARGS:-}" ]; then
    printf '%s\n' "$FM_CLAUDE_POSTURE_TEST_ARGS"
    return 0
  fi
  for pid in $(fm_backend_agent_root_pids "$backend" "$target" 2>/dev/null); do
    [ -n "$pid" ] || continue
    args=$(LC_ALL=C ps -p "$pid" -o args= 2>/dev/null) || continue
    [ -n "$args" ] && printf '%s\n' "$args"
  done
}

# fm_claude_posture_drift: end-to-end detection for one task record. Prints
#   drifted <session> <flag>   live bare-restore claude, with the configured flag
#   clean <mode>               posture flag present
#   skip <reason>              not-claude-harness | unverified-backend |
#                              not-alive-<state> | no-claude-process |
#                              <parse verdict>
# Needs bin/fm-backend.sh, bin/fm-busy-lib.sh (for nothing here - kept out),
# and bin/fm-control-lib.sh (harness family) sourced by the caller.
fm_claude_posture_drift() {  # <meta> <state-dir> <config-dir>
  local meta=$1 state=$2 config=$3 id backend target worktree harness family
  local agent_state line verdict rest flag local_mode expected_mode observed_mode observed_session
  id=$(basename "$meta" .meta)
  harness=$(fm_meta_get "$meta" harness)
  family=$(fm_control_harness_family "$harness") || family=
  [ "$family" = claude ] || { printf 'skip not-claude-harness\n'; return 0; }
  backend=$(fm_backend_of_meta "$meta")
  case "$backend" in
    tmux|herdr) ;;
    *) printf 'skip unverified-backend\n'; return 0 ;;
  esac
  target=$(fm_backend_target_of_meta "$meta")
  worktree=$(fm_meta_get "$meta" worktree)
  agent_state=$(fm_backend_agent_state "$backend" "$target" "fm-$id" "$worktree" 2>/dev/null || true)
  [ "$agent_state" = alive ] || { printf 'skip not-alive-%s\n' "${agent_state:-unknown}"; return 0; }
  verdict=skip; rest=no-claude-process
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    verdict=$(fm_claude_posture_parse_cmdline "$line")
    case "$verdict" in
      drifted*)
        rest=${verdict#drifted }
        flag=$(fm_claude_posture_flag "$config") || { printf 'skip bad-posture-config\n'; return 0; }
        case "$flag" in
          '--permission-mode auto') flag=--permission-mode=auto ;;
        esac
        printf 'drifted %s %s\n' "$rest" "$flag"
        return 0
        ;;
      clean*)
        local_mode=${verdict#clean }
        observed_mode=${local_mode%% *}
        observed_session=
        [ "$local_mode" = "$observed_mode" ] || observed_session=${local_mode#* }
        flag=$(fm_claude_posture_flag "$config") || { printf 'skip bad-posture-config\n'; return 0; }
        expected_mode=bypass
        [ "$flag" = '--permission-mode auto' ] && expected_mode=auto
        if [ "$observed_mode" = "$expected_mode" ]; then
          if [ -n "$observed_session" ]; then
            printf 'clean %s %s\n' "$observed_mode" "$observed_session"
          else
            printf 'clean %s\n' "$observed_mode"
          fi
          return 0
        fi
        if [ -n "$observed_session" ]; then
          case "$flag" in
            '--permission-mode auto') flag=--permission-mode=auto ;;
          esac
          printf 'drifted %s %s\n' "$observed_session" "$flag"
          return 0
        fi
        printf 'skip configured-posture-mismatch\n'
        return 0
        ;;
      skip*) rest=${verdict#skip } ;;
    esac
  done <<EOF
$(fm_claude_posture_observe "$backend" "$target")
EOF
  printf 'skip %s\n' "$rest"
}

# etime_to_seconds: portable ps etime ([[dd-]hh:]mm:ss) to seconds.
fm_claude_posture_etime_seconds() {  # <etime>
  local etime=$1 days=0 rest hours=0 mins secs
  case "$etime" in
    *-*) days=${etime%%-*}; rest=${etime#*-} ;;
    *) rest=$etime ;;
  esac
  hours=0; mins=0; secs=0
  case "$rest" in
    *:*:*) hours=${rest%%:*}; rest=${rest#*:}; mins=${rest%%:*}; secs=${rest#*:} ;;
    *:*) mins=${rest%%:*}; secs=${rest#*:} ;;
    *) secs=$rest ;;
  esac
  days=$(printf '%s' "$days" | tr -cd '0-9'); [ -n "$days" ] || days=0
  hours=$(printf '%s' "$hours" | tr -cd '0-9'); [ -n "$hours" ] || hours=0
  mins=$(printf '%s' "$mins" | tr -cd '0-9'); [ -n "$mins" ] || mins=0
  secs=$(printf '%s' "$secs" | tr -cd '0-9'); [ -n "$secs" ] || secs=0
  printf '%s\n' $(( days * 86400 + hours * 3600 + mins * 60 + secs ))
}

# file_mtime_epoch: portable mtime.
fm_claude_posture_mtime() {  # <path>
  local path=$1 out
  out=$(stat -f %m "$path" 2>/dev/null) && { printf '%s\n' "$out"; return 0; }
  out=$(stat -c %Y "$path" 2>/dev/null) && { printf '%s\n' "$out"; return 0; }
  return 1
}

# fm_claude_posture_idle_ready: the repair idle gate. Prints ready, or the
# reason not, and returns 0 only for ready. A busy verdict from a record the
# live worker predates is stale (the writer died, e.g. across a restart) and
# does not block; a record newer than the worker proves a live turn.
# Needs bin/fm-backend.sh and bin/fm-busy-lib.sh sourced.
fm_claude_posture_idle_ready() {  # <meta> <state-dir>
  local meta=$1 state=$2 id backend target verdict record mtime now
  local pid etime start
  id=$(basename "$meta" .meta)
  backend=$(fm_backend_of_meta "$meta")
  target=$(fm_backend_target_of_meta "$meta")
  verdict=$(fm_busy_classify_meta "$meta" "$id" "$state")
  case "${verdict%% *}" in
    busy)
      record="$state/$id.busy-state"
      mtime=$(fm_claude_posture_mtime "$record" 2>/dev/null) || mtime=
      now=$(date +%s)
      for pid in $(fm_backend_agent_root_pids "$backend" "$target" 2>/dev/null); do
        [ -n "$pid" ] || continue
        etime=$(LC_ALL=C ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ') || continue
        [ -n "$etime" ] || continue
        start=$(( now - $(fm_claude_posture_etime_seconds "$etime") ))
        if [ -n "$mtime" ] && [ "$mtime" -gt "$start" ]; then
          printf 'busy-live\n'
          return 1
        fi
      done
      ;;
  esac
  case "$(fm_backend_composer_state "$backend" "$target" 2>/dev/null)" in
    empty) printf 'ready\n'; return 0 ;;
    *) printf 'composer-not-empty\n'; return 1 ;;
  esac
}

# fm_claude_posture_repair_command: the same-session resume command.
fm_claude_posture_repair_command() {  # <session-id> <flag>
  case "$2" in
    --permission-mode=auto) printf 'claude --resume %s --permission-mode auto\n' "$1" ;;
    --dangerously-skip-permissions) printf 'claude --resume %s %s\n' "$1" "$2" ;;
    *) return 1 ;;
  esac
}
