#!/usr/bin/env bash
# Worker permission-posture drift detection and same-session repair support.
#
# This library owns the configured posture sources, bare-resume classification,
# foreground-process observation, idle and background-process gates, worktree
# verification, and same-session resume commands. bin/fm-control.sh owns the
# lifecycle transaction; bin/fm-posture-sweep.sh owns periodic application.
# The repair deliberately supports only verified same-session resume shapes:
# Claude, Codex, OpenCode, Grok, Gemini, Muse, and Rovo. Adapters whose
# references require deterministic relaunch are not eligible for this repair.
#
# The permission posture comes from the same launch contract as bin/fm-spawn.sh:
# Claude reads config/claude-permission-mode. The other supported adapters
# carry the same fixed permission posture their spawn templates use.
#
# A blank composer is not enough to prove that /exit is safe: Claude can open a
# modal when a background shell is running. Before any repair, the backend tty
# must positively show only the pane shell process group and the foreground
# agent process group. Any other process group or an unreadable probe defers the
# repair, leaving that pane and its background work untouched.

# fm_posture_harness_supported: 0 when this harness has a verified same-session
# restore command whose posture Firstmate can reconstruct.
fm_posture_harness_supported() {  # <harness>
  case "${1-}" in claude|codex|opencode|grok|gemini|muse|rovo) return 0 ;; esac
  return 1
}

# fm_posture_flag: print the CLI posture this home's launch template selects.
fm_posture_flag() {  # <harness> <config-dir>
  local harness=$1 config=$2 mode token
  case "$harness" in
    claude)
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
      ;;
    codex) printf '%s\n' '--dangerously-bypass-approvals-and-sandbox' ;;
    opencode) printf '%s\n' 'OPENCODE_CONFIG_CONTENT={"permission":{"*":"allow"}}' ;;
    grok) printf '%s\n' '--always-approve' ;;
    gemini) printf '%s\n' '-y' ;;
    muse|rovo) printf '%s\n' '--yolo' ;;
    *) return 1 ;;
  esac
}

# fm_posture_parse_cmdline: classify one foreground process command line.
# Prints clean|drifted <session-id>|skip <reason> and always succeeds.
fm_posture_parse_cmdline() {  # <harness> <command-line>
  local harness=$1 line=$2 tok first=1 env_prefix=0 skip_env_value=0
  local session='' resume_seen=0 posture_seen=0 settings_seen=0 extra=0 mode=clean_default value skip_next=0 skip_notify_tail=0 skip_rest=0
  local noglob=0
  case $- in *f*) noglob=1 ;; esac
  set -f
  for tok in $line; do
    [ "$skip_rest" -eq 0 ] || break
    if [ "$skip_notify_tail" -eq 1 ]; then
      case "$tok" in *']'*) skip_notify_tail=0 ;; esac
      continue
    fi
    if [ "$skip_next" -eq 1 ]; then
      if [ "$harness" = codex ]; then
        case "$tok" in
          *notify=*'['*']'*) : ;;
          *notify=*'['*) skip_notify_tail=1 ;;
        esac
      fi
      skip_next=0
      continue
    fi
    if [ "$first" -eq 1 ]; then
      if [ "$skip_env_value" -eq 1 ]; then skip_env_value=0; continue; fi
      case "$tok" in *=*) continue ;; esac
      if [ "$tok" = env ]; then env_prefix=1; continue; fi
      if [ "$env_prefix" -eq 1 ]; then
        case "$tok" in
          -u|--unset) skip_env_value=1; continue ;;
          -*) continue ;;
        esac
      fi
      case "${tok##*/}" in
        claude|codex|opencode|grok|gemini|muse|rovo) first=0 ;;
        node|bun|deno) continue ;;
        *) break ;;
      esac
      continue
    fi
    case "$harness:$tok" in
      claude:--dangerously-skip-permissions)
        mode=bypass
        ;;
      claude:--resume)
        resume_seen=1
        ;;
      claude:--resume=*)
        resume_seen=1
        session=${tok#--resume=}
        ;;
      claude:--permission-mode)
        mode=permission-next
        ;;
      claude:--permission-mode=*)
        value=${tok#--permission-mode=}
        case "$value" in
          bypassPermissions) mode=bypass ;;
          auto) mode=auto ;;
          default|manual) mode=clean_default ;;
          *) mode=other ;;
        esac
        ;;
      codex:--dangerously-bypass-approvals-and-sandbox)
        posture_seen=1
        ;;
      grok:--always-approve|gemini:-y|gemini:--yolo|muse:--yolo|rovo:--yolo|rovo:--disable-permission-checks)
        posture_seen=1
        ;;
      claude:--settings)
        settings_seen=1
        skip_next=1
        ;;
      codex:-c)
        skip_next=1
        ;;
      rovo:--config-override)
        skip_rest=1
        ;;
      claude:--model|claude:--effort|codex:--config|codex:-m|codex:--model|opencode:-m|opencode:--model|grok:--model|grok:--reasoning-effort|grok:--effort|gemini:-m|gemini:--model|muse:--model|muse:--reasoning-effort|rovo:--model)
        skip_next=1
        ;;
      codex:resume|muse:resume|opencode:--session)
        resume_seen=1
        ;;
      grok:--resume|gemini:--resume|rovo:--restore)
        resume_seen=1
        ;;
      opencode:--session=*)
        resume_seen=1
        session=${tok#--session=}
        ;;
      *)
        if [ "$harness" = claude ] && [ "$mode" = permission-next ]; then
          case "$tok" in
            bypassPermissions) mode=bypass ;;
            auto) mode=auto ;;
            default|manual) mode=clean_default ;;
            *) mode=other ;;
          esac
          continue
        fi
        if [ "$resume_seen" = 1 ] && [ -z "$session" ]; then
          session=$tok
        elif [ "$tok" = --session ] && [ "$harness" = opencode ]; then
          :
        else
          case "$harness:$tok" in
          codex:-C|codex:--cd|codex:-m|codex:--model|codex:-c|codex:--config)
            extra=1
            ;;
            *) extra=1 ;;
          esac
        fi
        ;;
    esac
  done
  [ "$noglob" -eq 1 ] || set +f
  [ "$first" -eq 0 ] || { printf 'skip not-%s\n' "$harness"; return 0; }
  [ "$resume_seen" -eq 1 ] || { printf 'skip no-resume\n'; return 0; }
  [ -n "$session" ] || { printf 'skip resume-picker\n'; return 0; }
  case "$session" in *[!A-Za-z0-9._-]*) printf 'skip bad-session-id\n'; return 0 ;; esac
  if [ "$extra" -ne 0 ]; then
    printf 'skip extra-args\n'
  elif [ "$harness" = claude ]; then
    case "$mode" in
      bypass|auto) printf 'clean %s %s\n' "$mode" "$session"; return 0 ;;
      *)
        if [ "$settings_seen" -eq 1 ]; then printf 'skip extra-args\n'
        else printf 'drifted %s\n' "$session"
        fi
        return 0
        ;;
    esac
  elif [ "$harness" = codex ] && [ "$posture_seen" -eq 1 ]; then
    printf 'clean %s\n' "$session"
  elif [ "$harness" = opencode ] || [ "$harness" = grok ] || [ "$harness" = gemini ] \
       || [ "$harness" = muse ] || [ "$harness" = rovo ]; then
    if [ "$posture_seen" -eq 1 ]; then
      printf 'clean %s\n' "$session"
    else
      printf 'drifted %s\n' "$session"
    fi
  else
    printf 'drifted %s\n' "$session"
  fi
}

# fm_posture_observe: print pid<TAB>command-line for the foreground processes
# at one recorded backend endpoint.
# FM_POSTURE_TEST_ARGS_FILE substitutes one command line per line in tests.
fm_posture_observe() {  # <backend> <target>
  local backend=$1 target=$2 pid args
  if [ -n "${FM_POSTURE_TEST_ARGS_FILE:-}" ]; then
    [ -r "$FM_POSTURE_TEST_ARGS_FILE" ] || return 0
    while IFS= read -r args; do
      case "$args" in
        *$'\t'*) printf '%s\n' "$args" ;;
        *) printf '\t%s\n' "$args" ;;
      esac
    done < "$FM_POSTURE_TEST_ARGS_FILE"
    return 0
  fi
  for pid in $(fm_backend_agent_root_pids "$backend" "$target" 2>/dev/null); do
    [ -n "$pid" ] || continue
    args=$(LC_ALL=C ps -p "$pid" -o args= 2>/dev/null) || continue
    [ -n "$args" ] && printf '%s\t%s\n' "$pid" "$args"
  done
}

# fm_posture_observe_env: print one process environment value when it can be
# read without exposing the rest of the environment.
# FM_POSTURE_TEST_ENV_FILE supplies the value in portable behavior tests.
fm_posture_observe_env() {  # <pid> <name>
  local pid=$1 name=$2 value token
  if [ -n "${FM_POSTURE_TEST_ENV_FILE:-}" ]; then
    [ -r "$FM_POSTURE_TEST_ENV_FILE" ] && cat "$FM_POSTURE_TEST_ENV_FILE"
    return 0
  fi
  if [ -r "/proc/$pid/environ" ]; then
    tr '\000' '\n' < "/proc/$pid/environ" | while IFS= read -r value; do
      case "$value" in "$name"=*) printf '%s' "${value#*=}"; return 0 ;; esac
    done
    return 0
  fi
  value=$(LC_ALL=C ps eww -p "$pid" -o command= 2>/dev/null) || return 0
  for token in $value; do
    case "$token" in "$name"=*) printf '%s' "${token#*=}"; return 0 ;; esac
  done
}

fm_posture_opencode_env_ok() {  # <pid>
  local value
  value=$(fm_posture_observe_env "$1" OPENCODE_CONFIG_CONTENT)
  [ -n "$value" ] || return 1
  printf '%s' "$value" | jq -e '.permission["*"] == "allow"' >/dev/null 2>&1
}

# fm_posture_drift: print drifted <session> <posture>, clean <posture> <session>,
# or skip <reason> for one task record.
fm_posture_drift() {  # <meta> <state-dir> <config-dir>
  local meta=$1 state=$2 config=$3 id backend target worktree harness family agent_state
  local entry pid line verdict session flag expected_mode observed_mode
  id=$(basename "$meta" .meta)
  harness=$(fm_meta_get "$meta" harness)
  family=$(fm_control_harness_family "$harness") || family=
  fm_posture_harness_supported "$family" || { printf 'skip unsupported-harness\n'; return 0; }
  backend=$(fm_backend_of_meta "$meta")
  case "$backend" in tmux|herdr) ;; *) printf 'skip unverified-backend\n'; return 0 ;; esac
  target=$(fm_backend_target_of_meta "$meta")
  worktree=$(fm_meta_get "$meta" worktree)
  agent_state=$(fm_backend_agent_state "$backend" "$target" "fm-$id" "$worktree" 2>/dev/null || true)
  [ "$agent_state" = alive ] || { printf 'skip not-alive-%s\n' "${agent_state:-unknown}"; return 0; }
  flag=$(fm_posture_flag "$family" "$config") || { printf 'skip bad-posture-config\n'; return 0; }
  case "$family:$flag" in
    claude:'--permission-mode auto') expected_mode=auto ;;
    claude:*) expected_mode=bypass ;;
    *) expected_mode=$flag ;;
  esac
  while IFS= read -r entry; do
    [ -n "$entry" ] || continue
    pid=${entry%%$'\t'*}
    line=${entry#*$'\t'}
    verdict=$(fm_posture_parse_cmdline "$family" "$line")
    case "$verdict" in
      drifted\ *)
        session=${verdict#drifted }
        if [ "$family" = opencode ] && [ -n "$pid" ] && fm_posture_opencode_env_ok "$pid"; then
          printf 'clean %s %s\n' "$expected_mode" "$session"
          return 0
        fi
        printf 'drifted %s %s\n' "$session" "$flag"
        return 0
        ;;
      clean\ *)
        session=${verdict#clean }
        case "$family" in
          claude)
            observed_mode=${session%% *}
            session=${session#* }
            if [ "$observed_mode" = "$expected_mode" ]; then
              printf 'clean %s %s\n' "$expected_mode" "$session"
              return 0
            fi
            printf 'drifted %s %s\n' "$session" "$flag"
            return 0
            ;;
          codex)
            printf 'clean %s %s\n' "$expected_mode" "$session"
            return 0
            ;;
          opencode)
            if [ -n "$pid" ] && fm_posture_opencode_env_ok "$pid"; then
              printf 'clean %s %s\n' "$expected_mode" "$session"
              return 0
            fi
            printf 'drifted %s %s\n' "$session" "$flag"
            return 0
            ;;
          *)
            printf 'clean %s %s\n' "$expected_mode" "$session"
            return 0
            ;;
        esac
        ;;
    esac
  done <<EOF
$(fm_posture_observe "$backend" "$target")
EOF
  printf 'skip no-restored-process\n'
}

fm_posture_epoch_seconds() {  # <ps etime: [[dd-]hh:]mm:ss>
  local etime=$1 days=0 rest hours=0 mins secs
  case "$etime" in *-*) days=${etime%%-*}; rest=${etime#*-} ;; *) rest=$etime ;; esac
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
  printf '%s\n' $((days * 86400 + hours * 3600 + mins * 60 + secs))
}

fm_posture_mtime() {  # <path>
  local out
  out=$(stat -f %m "$1" 2>/dev/null) && { printf '%s\n' "$out"; return 0; }
  out=$(stat -c %Y "$1" 2>/dev/null) && { printf '%s\n' "$out"; return 0; }
  return 1
}

# fm_posture_idle_ready: positive idle and no-background-process proof. A busy
# record older than the restored worker is stale after a machine restart.
fm_posture_idle_ready() {  # <meta> <state-dir>
  local meta=$1 state=$2 id backend target verdict record mtime now pid etime start
  id=$(basename "$meta" .meta)
  backend=$(fm_backend_of_meta "$meta")
  target=$(fm_backend_target_of_meta "$meta")
  verdict=$(fm_busy_classify_meta "$meta" "$id" "$state")
  case "${verdict%% *}" in
    busy)
      record="$state/$id.busy-state"
      mtime=$(fm_posture_mtime "$record" 2>/dev/null) || mtime=
      now=$(date +%s)
      for pid in $(fm_backend_agent_root_pids "$backend" "$target" 2>/dev/null); do
        [ -n "$pid" ] || continue
        etime=$(LC_ALL=C ps -p "$pid" -o etime= 2>/dev/null | tr -d ' ') || continue
        [ -n "$etime" ] || continue
        start=$((now - $(fm_posture_epoch_seconds "$etime")))
        if [ -n "$mtime" ] && [ "$mtime" -gt "$start" ]; then
          printf 'busy-live\n'
          return 1
        fi
      done
      ;;
  esac
  case "$(fm_backend_composer_state "$backend" "$target" 2>/dev/null)" in
    empty) ;;
    *) printf 'composer-not-empty\n'; return 1 ;;
  esac
  fm_backend_tty_background_process_group "$backend" "$target" || {
    local reason=$?
    if [ "$reason" -eq 2 ]; then printf 'background-process\n'; else printf 'background-probe-unverified\n'; fi
    return 1
  }
  printf 'ready\n'
}

fm_posture_resolve_binary() {  # <name> <fallback-path>
  local name=$1 fallback=$2 candidate dir
  candidate=$(command -v "$name" 2>/dev/null || true)
  if [ -z "$candidate" ] && [ -n "$fallback" ] && [ -x "$fallback" ]; then
    candidate=$fallback
  fi
  [ -n "$candidate" ] && [ -x "$candidate" ] || return 1
  case "$candidate" in
    /*) printf '%s\n' "$candidate" ;;
    *)
      dir=$(CDPATH='' cd -- "$(dirname "$candidate")" 2>/dev/null && pwd -P) || return 1
      printf '%s/%s\n' "$dir" "$(basename "$candidate")"
      ;;
  esac
}

# fm_posture_resume_command: reconstruct the same-session launch with the
# configured posture and the explicit recorded task worktree.
fm_posture_resume_command() {  # <harness> <session-id> <posture> <meta> <state-dir>
  local harness=$1 session=$2 posture=$3 meta=$4 state=$5
  local model effort kind settings model_args effort_args notify turnend id
  local config_json data_root data_real state_real rovo_bin muse_bin muse_config muse_data sessions_root
  id=$(basename "$meta" .meta)
  model=$(fm_meta_get "$meta" model)
  effort=$(fm_meta_get "$meta" effort)
  kind=$(fm_meta_get "$meta" kind)
  [ -n "$model" ] || model=default
  [ -n "$effort" ] || effort=default
  model_args=
  effort_args=
  if [ "$model" != default ]; then
    model_args="--model $(fm_posture_shell_quote "$model")"
  fi
  case "$harness:$effort" in
    claude:low|claude:medium|claude:high|claude:xhigh|claude:max)
      effort_args="--effort $(fm_posture_shell_quote "$effort")"
      ;;
    codex:low|codex:medium|codex:high|codex:xhigh|codex:max)
      effort_args="-c $(fm_posture_shell_quote "model_reasoning_effort=\"$effort\"")"
      ;;
    grok:low|grok:medium|grok:high)
      effort_args="--reasoning-effort $(fm_posture_shell_quote "$effort")"
      ;;
    muse:low|muse:medium|muse:high|muse:xhigh)
      effort_args="--reasoning-effort $(fm_posture_shell_quote "$effort")"
      ;;
    muse:max) effort_args='--reasoning-effort ultra' ;;
  esac
  case "$harness" in
    claude)
      case "$posture" in
        '--permission-mode auto') settings='{"permissions":{"defaultMode":"auto"},"feedbackDrafts":"off","attribution":{"commit":"","pr":"","sessionUrl":false}}' ;;
        '--dangerously-skip-permissions') settings='{"permissions":{"defaultMode":"bypassPermissions"},"feedbackDrafts":"off","attribution":{"commit":"","pr":"","sessionUrl":false}}' ;;
        *) return 1 ;;
      esac
      printf '%s' 'env -u CURSOR_AGENT -u CURSOR_INVOKED_AS -u GEMINI_CLI CLAUDE_CODE_ENABLE_PROMPT_SUGGESTION=false CLAUDE_CODE_SEND_FEEDBACK=0 '
      if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
        printf 'CLAUDE_CONFIG_DIR=%s ' "$(fm_posture_shell_quote "$CLAUDE_CONFIG_DIR")"
      fi
      printf 'claude --resume %s %s --settings %s' \
        "$session" "$posture" "$(fm_posture_shell_quote "$settings")"
      [ -z "$model_args" ] || printf ' %s' "$model_args"
      [ -z "$effort_args" ] || printf ' %s' "$effort_args"
      printf '\n'
      ;;
    codex)
      [ "$posture" = '--dangerously-bypass-approvals-and-sandbox' ] || return 1
      printf 'env -u CURSOR_AGENT -u CURSOR_INVOKED_AS -u GEMINI_CLI codex resume %s %s' "$session" "$posture"
      [ -z "$model_args" ] || printf ' %s' "$model_args"
      [ -z "$effort_args" ] || printf ' %s' "$effort_args"
      if [ "$kind" != secondmate ]; then
        turnend="$state/$(basename "$meta" .meta).turn-ended"
        turnend=${turnend//\\/\\\\}
        turnend=${turnend//\"/\\\"}
        notify="notify=[\"bash\",\"-c\",\"touch $turnend\"]"
        printf ' -c %s' "$(fm_posture_shell_quote "$notify")"
      fi
      printf '\n'
      ;;
    opencode)
      case "$posture" in
        'OPENCODE_CONFIG_CONTENT={"permission":{"*":"allow"}}')
          printf "env -u CURSOR_AGENT -u CURSOR_INVOKED_AS -u GEMINI_CLI OPENCODE_CONFIG_CONTENT='%s' opencode" "${posture#OPENCODE_CONFIG_CONTENT=}"
          [ -z "$model_args" ] || printf ' %s' "$model_args"
          printf ' --session %s\n' "$session"
          ;;
        *) return 1 ;;
      esac
      ;;
    grok)
      [ "$posture" = '--always-approve' ] || return 1
      printf 'env -u CURSOR_AGENT -u CURSOR_INVOKED_AS -u GEMINI_CLI grok --resume %s %s' "$session" "$posture"
      [ -z "$model_args" ] || printf ' %s' "$model_args"
      [ -z "$effort_args" ] || printf ' %s' "$effort_args"
      printf '\n'
      ;;
    gemini)
      [ "$posture" = '-y' ] || return 1
      [ -f "$state/$id.gemini-settings.json" ] && [ -r "$state/$id.gemini-settings.json" ] || return 1
      printf 'env -u CURSOR_AGENT -u CURSOR_INVOKED_AS -u GEMINI_CLI -u CLAUDECODE -u PI_CODING_AGENT -u GROK_AGENT -u FM_PI_HARNESS GEMINI_CLI_TRUST_WORKSPACE=true GEMINI_CLI_SYSTEM_SETTINGS_PATH=%s gemini --resume %s -y' \
        "$(fm_posture_shell_quote "$state/$id.gemini-settings.json")" "$session"
      [ -z "$model_args" ] || printf ' %s' "$model_args"
      printf '\n'
      ;;
    muse)
      [ "$posture" = '--yolo' ] || return 1
      [ -r "$state/$id.muse-session" ] || return 1
      muse_config=$(awk -F= '$1 == "config_home" { value = substr($0, index($0, "=") + 1) } END { print value }' "$state/$id.muse-session")
      sessions_root=$(awk -F= '$1 == "sessions_root" { value = substr($0, index($0, "=") + 1) } END { print value }' "$state/$id.muse-session")
      case "$sessions_root" in */muse/sessions) muse_data=${sessions_root%/muse/sessions} ;; *) return 1 ;; esac
      [ -d "$muse_config" ] && [ -d "$muse_data" ] || return 1
      muse_bin=$(fm_posture_resolve_binary muse "${HOME:-}/.local/bin/muse") || return 1
      printf 'env -u CURSOR_AGENT -u CURSOR_INVOKED_AS -u GEMINI_CLI -u CLAUDECODE -u PI_CODING_AGENT -u GROK_AGENT -u FM_PI_HARNESS XDG_CONFIG_HOME=%s XDG_DATA_HOME=%s MUSE_EXPERIMENTAL_FOREIGN_PERSONAL_CONTEXT_KILL=on %s --yolo' \
        "$(fm_posture_shell_quote "$muse_config")" "$(fm_posture_shell_quote "$muse_data")" \
        "$(fm_posture_shell_quote "$muse_bin")"
      [ -z "$model_args" ] || printf ' %s' "$model_args"
      [ -z "$effort_args" ] || printf ' %s' "$effort_args"
      printf ' resume %s\n' "$session"
      ;;
    rovo)
      [ "$posture" = '--yolo' ] || return 1
      data_root=${FM_DATA_OVERRIDE:-${FM_HOME:-}/data}
      data_real=$(CDPATH='' cd -- "$data_root/$id" 2>/dev/null && pwd -P) || return 1
      state_real=$(CDPATH='' cd -- "$state" 2>/dev/null && pwd -P) || return 1
      rovo_bin=$(fm_posture_resolve_binary rovo "${HOME:-}/.local/bin/rovo") || return 1
      config_json=$(jq -cn --arg data "$data_real" --arg inbox "$state_real/$id.inbox" \
        --arg status "$state_real/$id.status" --arg effort "$effort" '
          {toolPermissions:{allowedExternalPaths:[$data,$inbox,$status]}}
          + (if ($effort == "low" or $effort == "medium" or $effort == "high" or $effort == "max")
             then {agent:{efficiencyLevel:$effort}} else {} end)
        ') || return 1
      printf 'env -u CURSOR_AGENT -u CURSOR_INVOKED_AS -u GEMINI_CLI -u CLAUDECODE -u PI_CODING_AGENT -u GROK_AGENT -u FM_PI_HARNESS %s --restore %s --yolo' \
        "$(fm_posture_shell_quote "$rovo_bin")" "$session"
      [ -z "$model_args" ] || printf ' %s' "$model_args"
      printf ' --config-override %s\n' "$(fm_posture_shell_quote "$config_json")"
      ;;
    *) return 1 ;;
  esac
}

fm_posture_shell_quote() {  # <string>
  local value=$1
  value=${value//\'/\'\\\'\'}
  printf "'%s'" "$value"
}

fm_posture_path_matches() {  # <actual-path> <expected-directory>
  local actual expected
  [ -d "$1" ] && [ -d "$2" ] || return 1
  actual=$(CDPATH='' cd -- "$1" 2>/dev/null && pwd -P) || return 1
  expected=$(CDPATH='' cd -- "$2" 2>/dev/null && pwd -P) || return 1
  [ "$actual" = "$expected" ]
}

fm_posture_current_path_matches() {  # <backend> <target> <worktree>
  local path
  path=$(fm_backend_current_path "$1" "$2" 2>/dev/null) || return 1
  fm_posture_path_matches "$path" "$3"
}
