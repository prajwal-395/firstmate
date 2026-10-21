#!/usr/bin/env bash
# fm-pi-opencode-go.sh - managed Pi configuration and OpenCode Go credentials.
# Usage: fm-pi-opencode-go.sh install <Pi-agent-dir>
#        fm-pi-opencode-go.sh token
# The installer merges the OpenCode Go provider into Pi's models.json. The
# request-time token command answers from the task database first, then
# refreshes OpenCode's live console login through OpenCode itself when
# needed, then copies only its login rows to the task database.
#
# THE REQUEST PATH IS A DEADLINE, NOT A SCRIPT. Pi runs the provider's apiKey
# command with a hard 10s budget, throws that command's stderr away when it
# fails, and gives up the whole turn on failure. A valid, unexpired token must
# therefore be served by the cheapest possible read: one sqlite3 query against
# the task database, with a busy timeout, before any library is sourced, any
# lock is taken, or any OpenCode process is started. Everything after that
# read - the shared refresh lock, the OpenCode refresh, the valid-token
# fallback - is bounded by the same deadline, so a slow or contended machine
# ends with a token instead of with silence.
#
# EVERY FAILURE LEAVES A CAUSE. Because the caller cannot see stderr, a
# non-zero exit appends one line to a small log beside the task's state:
# timestamp, task database, the step that failed, the exit status, and the
# elapsed milliseconds. The token itself is never written there.
set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1

# The request-time margins. The fast path serves a token that is at least
# this far from expiry; the budget is the self-imposed deadline that keeps
# the slow path inside Pi's hard limit; the database timeout is how long a
# contended task database is waited for rather than failed.
FM_PI_TOKEN_FAST_MARGIN_MS=${FM_PI_TOKEN_FAST_MARGIN_MS:-300000}
FM_PI_TOKEN_BUDGET_SECONDS=${FM_PI_TOKEN_BUDGET_SECONDS:-9}
FM_PI_TOKEN_DB_TIMEOUT_MS=${FM_PI_TOKEN_DB_TIMEOUT_MS:-3000}
FM_PI_TOKEN_FALLBACK_DB_TIMEOUT_MS=${FM_PI_TOKEN_FALLBACK_DB_TIMEOUT_MS:-1000}
FM_PI_TOKEN_REFRESH_MAX_SECONDS=${FM_PI_TOKEN_REFRESH_MAX_SECONDS:-6}
FM_PI_TOKEN_LIVE_DB_SECONDS=${FM_PI_TOKEN_LIVE_DB_SECONDS:-4}
FM_PI_TOKEN_COPY_TIMEOUT_MS=${FM_PI_TOKEN_COPY_TIMEOUT_MS:-1000}
FM_PI_TOKEN_LOG_LINES=${FM_PI_TOKEN_LOG_LINES:-200}

# The request-path state. The step names the last thing that was tried, so a
# logged failure says where it stopped.
FM_PI_TOKEN_DB=${FM_OPENCODE_TASK_DB:-}
FM_PI_TOKEN_STEP=start
FM_PI_TOKEN_START_MS=
FM_PI_TOKEN_LIBS_LOADED=0

# fm_epoch_ms: the current time in milliseconds, from bash's own clock. It
# costs no process, and the shells that lack it fall back to the seconds
# counter, so no clock process ever gates the answer itself.
fm_epoch_ms() {
  local value sec frac
  [ -n "${EPOCHREALTIME:-}" ] || return 1
  value=${EPOCHREALTIME/,/.}
  sec=${value%%.*}
  frac=${value#*.}
  frac="${frac}000000"
  frac=${frac:0:3}
  case "$sec" in
    ''|*[!0-9]*) return 1 ;;
  esac
  case "$frac" in ''|*[!0-9]*) frac=000 ;; esac
  printf '%s\n' "$(( sec * 1000 + 10#$frac ))"
}

# fm_token_load_libs: load the shared libraries the slow path needs. The fast
# path never reaches this, which is what keeps a token answer free of library
# sourcing, lock helpers, and OpenCode processes.
fm_token_load_libs() {
  [ "$FM_PI_TOKEN_LIBS_LOADED" = 1 ] && return 0
  # shellcheck source=bin/fm-opencode-second-lib.sh
  . "$SCRIPT_DIR/fm-opencode-second-lib.sh" || return 1
  # shellcheck source=bin/fm-timeout-lib.sh
  . "$SCRIPT_DIR/fm-timeout-lib.sh" || return 1
  FM_PI_TOKEN_LIBS_LOADED=1
  return 0
}

# fm_token_remaining_seconds: the seconds the request may still spend. The
# parent measures its own budget; the refresh holder inherits the allowance
# the parent gave it, so the two processes share one deadline without a
# clock.
fm_token_remaining_seconds() {
  local budget=${FM_PI_TOKEN_ALLOWANCE_S:-} remaining
  case "$budget" in ''|*[!0-9]*) budget=$FM_PI_TOKEN_BUDGET_SECONDS ;; esac
  remaining=$(( budget - SECONDS ))
  [ "$remaining" -gt 0 ] || remaining=0
  printf '%s\n' "$remaining"
}

# fm_token_read_timeout_ms <cap>: how long a database read may wait for a
# contended task database. It never waits past the request's own deadline,
# and it always waits at least a second, so a reader can still recover a
# task database left with a hot journal.
fm_token_read_timeout_ms() {
  local cap=${1:-} remaining timeout
  case "$cap" in ''|*[!0-9]*) cap=0 ;; esac
  remaining=$(fm_token_remaining_seconds) || remaining=0
  [ "$remaining" -lt 1 ] && remaining=1
  timeout=$(( remaining * 1000 ))
  [ "$timeout" -gt "$cap" ] && timeout=$cap
  printf '%s\n' "$timeout"
}

# fm_token_fast <task-db> [margin-ms] [busy-timeout-ms]
# Print the task's token when it is at least <margin-ms> from expiry. One
# sqlite3 call, one busy timeout, no lock, no OpenCode, no library. The
# freshness test runs inside sqlite, so the answer costs a single process.
fm_token_fast() {
  local db=${1:-} margin=${2:-} timeout_ms=${3:-} row
  case "$margin" in ''|*[!0-9]*) margin=$FM_PI_TOKEN_FAST_MARGIN_MS ;; esac
  case "$timeout_ms" in ''|*[!0-9]*) timeout_ms=$FM_PI_TOKEN_DB_TIMEOUT_MS ;; esac
  timeout_ms=$(fm_token_read_timeout_ms "$timeout_ms") || timeout_ms=0
  [ -n "$db" ] && [ -f "$db" ] || return 1
  command -v sqlite3 >/dev/null 2>&1 || return 1
  row=$(sqlite3 -cmd ".timeout $timeout_ms" "$db" \
    "SELECT access_token FROM account WHERE access_token <> '' AND CAST(token_expiry AS INTEGER) > (CAST(strftime('%s','now') AS INTEGER) * 1000 + $margin) LIMIT 1;" \
    2>/dev/null) \
    || row=$(sqlite3 "$db" \
      "SELECT access_token FROM account WHERE access_token <> '' AND CAST(token_expiry AS INTEGER) > (CAST(strftime('%s','now') AS INTEGER) * 1000 + $margin) LIMIT 1;" \
      2>/dev/null) \
    || return 1
  [ -n "$row" ] || return 1
  printf '%s' "${row%$'\n'}"
}

# fm_token_log_path: the diagnostic log beside the task's state.
fm_token_log_path() {
  local db=${FM_PI_TOKEN_DB:-} dir base
  if [ -n "${FM_PI_TOKEN_LOG:-}" ]; then
    printf '%s\n' "$FM_PI_TOKEN_LOG"
    return 0
  fi
  [ -n "$db" ] || return 1
  dir=${db%/*}
  base=${db##*/}
  [ "$dir" = "$db" ] && dir=.
  printf '%s/%s.token-errors.log\n' "$dir" "$base"
  return 0
}

# fm_token_elapsed_ms: the milliseconds since the request started. The
# sub-second reading needs bash's own clock; without it the seconds counter
# answers, which is the same measurement at second resolution.
fm_token_elapsed_ms() {
  local now elapsed
  if [ -n "$FM_PI_TOKEN_START_MS" ] && now=$(fm_epoch_ms); then
    elapsed=$(( now - FM_PI_TOKEN_START_MS ))
    [ "$elapsed" -ge 0 ] || elapsed=0
    printf '%s\n' "$elapsed"
    return 0
  fi
  printf '%s\n' "$(( SECONDS * 1000 ))"
}

# fm_token_log_trim: keep the diagnostic log small. It is a trail for the next
# occurrence, not an archive.
fm_token_log_trim() {
  local log=${1:-} lines tmp
  [ -n "$log" ] && [ -f "$log" ] || return 0
  lines=$(wc -l < "$log" 2>/dev/null) || return 0
  case "$lines" in ''|*[!0-9]*) return 0 ;; esac
  [ "$lines" -gt "$FM_PI_TOKEN_LOG_LINES" ] || return 0
  tmp="$log.trim.$$"
  tail -n "$FM_PI_TOKEN_LOG_LINES" "$log" > "$tmp" 2>/dev/null \
    || { rm -f "$tmp" 2>/dev/null; return 0; }
  mv -f "$tmp" "$log" 2>/dev/null || rm -f "$tmp" 2>/dev/null
  return 0
}

# fm_token_log_failure <rc> <step>: one diagnostic line, never the token.
fm_token_log_failure() {
  local rc=${1:-1} step=${2:-unknown} log line stamp
  log=$(fm_token_log_path) || return 0
  [ -n "$log" ] || return 0
  stamp=$(date -u +%Y-%m-%dT%H:%M:%SZ 2>/dev/null) || stamp=unknown-time
  line="$stamp rc=$rc step=$step db=${FM_PI_TOKEN_DB:-none} elapsed_ms=$(fm_token_elapsed_ms)"
  printf '%s\n' "$line" >> "$log" 2>/dev/null || return 0
  fm_token_log_trim "$log"
  return 0
}

# fm_token_on_exit <rc>: the failure record for a non-zero exit.
fm_token_on_exit() {
  local rc=${1:-1}
  [ "$rc" -eq 0 ] && return 0
  fm_token_log_failure "$rc" "$FM_PI_TOKEN_STEP"
  return 0
}

# fm_token_refresh_seconds: how many seconds the refresh attempt may take.
# The fallback read keeps a second in reserve, so a slow refresh still leaves
# room to answer with a token that is already valid.
fm_token_refresh_seconds() {
  local remaining
  remaining=$(fm_token_remaining_seconds) || remaining=0
  remaining=$(( remaining - 1 ))
  [ "$remaining" -gt 0 ] || { printf '0\n'; return 0; }
  [ "$remaining" -gt "$FM_PI_TOKEN_REFRESH_MAX_SECONDS" ] && remaining=$FM_PI_TOKEN_REFRESH_MAX_SECONDS
  printf '%s\n' "$remaining"
}

# fm_token_live_db <seconds>
# The machine's own console database, resolved the way the shared library
# resolves it and bounded, so a slow OpenCode CLI cannot eat the request's
# whole budget. The watchdog is a plain background subshell: a timed runner
# that needs its own interpreter costs a second on a saturated machine,
# which is exactly when this path runs.
fm_token_live_db() {
  local seconds=${1:-4} pid deadline
  case "$seconds" in ''|*[!0-9]*) seconds=4 ;; esac
  deadline=$(( SECONDS + seconds ))
  ( fm_opencode_second_live_db ) &
  pid=$!
  while kill -0 "$pid" 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      kill -TERM "$pid" 2>/dev/null
      sleep 0.2
      kill -KILL "$pid" 2>/dev/null
      break
    fi
    sleep 0.1
  done
  wait "$pid" 2>/dev/null
}

# token_refresh <task-db> <org> <config-dir> <live-db> <allowance>
# The single-flight refresh. The console database is resolved before the lock
# is taken, so the lock is never held while the OpenCode CLI starts; one
# holder at a time then refreshes through OpenCode, and every other caller
# waits for the holder and reuses its result. The whole attempt is bounded
# by the caller's allowance, because a refresh that outlasts Pi's budget
# must not take the request down with it.
token_refresh() {
  local db=${1:-} org=${2:-} config_dir=${3:-} live=${4:-} allowance=${5:-}
  local lock wait_seconds
  FM_PI_TOKEN_DB=$db
  [ -f "$db" ] || { FM_PI_TOKEN_STEP=missing-task-db; return 1; }
  [ -n "$live" ] && [ -f "$live" ] || { FM_PI_TOKEN_STEP=live-db-unavailable; return 1; }
  case "$allowance" in ''|*[!0-9]*) allowance=1 ;; esac
  lock="$config_dir/opencode-pi-token.lock"
  # The wait covers the holder's own refresh, so a caller that arrives while
  # another caller is refreshing waits for that result instead of racing it.
  wait_seconds=$allowance
  if command -v lockf >/dev/null 2>&1; then
    FM_PI_TOKEN_ALLOWANCE_S=$allowance \
      fm_run_timed "$allowance" lockf -t "$wait_seconds" "$lock" \
        bash "$SCRIPT_DIR/fm-pi-opencode-go.sh" token-unlocked "$db" "$org" "$live"
    return $?
  fi
  if command -v flock >/dev/null 2>&1; then
    FM_PI_TOKEN_ALLOWANCE_S=$allowance \
      fm_run_timed "$allowance" flock -w "$wait_seconds" "$lock" \
        bash "$SCRIPT_DIR/fm-pi-opencode-go.sh" token-unlocked "$db" "$org" "$live"
    return $?
  fi
  return 1
}

install_provider() {
  local agent_dir=${1:-} lock
  [ -n "$agent_dir" ] || { echo "error: missing Pi agent directory" >&2; return 1; }
  command -v jq >/dev/null 2>&1 || { echo "error: jq is required to configure Pi's OpenCode Go provider" >&2; return 1; }
  mkdir -p "$agent_dir" || return 1
  models="$agent_dir/models.json"
  lock="$agent_dir/.fm-opencode-go.lock"
  if command -v lockf >/dev/null 2>&1; then
    lockf -t 30 "$lock" bash "$SCRIPT_DIR/fm-pi-opencode-go.sh" install-unlocked "$agent_dir"
  elif command -v flock >/dev/null 2>&1; then
    flock -w 30 "$lock" bash "$SCRIPT_DIR/fm-pi-opencode-go.sh" install-unlocked "$agent_dir"
  else
    echo "error: installing Pi's OpenCode Go provider requires lockf or flock" >&2
    return 1
  fi
}

install_unlocked() {
  local agent_dir=${1:-} models tmp helper_hash api_key
  models="$agent_dir/models.json"
  tmp="$models.tmp.$$"
  if [ -f "$models" ]; then
    jq -e 'type == "object" and ((.providers // {}) | type == "object")' "$models" >/dev/null 2>&1 || {
      echo "error: refusing malformed Pi models file at $models" >&2
      return 1
    }
    if jq -e '
      .providers["opencode-go"] as $p |
      $p != null and (
        $p.baseUrl != "https://opencode.ai/inference/go/openai/v1"
        or $p.api != "openai-completions"
        or ($p.apiKey != "!bash \"$FM_PI_OPENCODE_GO_HELPER\" token"
          and ($p.apiKey | test("^!bash .*fm-pi-opencode-go\\.sh.* token$") | not))
      )
    ' "$models" >/dev/null 2>&1; then
      echo "error: Pi provider opencode-go is already configured differently in $models" >&2
      return 1
    fi
  else
    # shellcheck disable=SC2016
    printf '%s\n' '{"$schema":"https://pi.dev/schemas/models.schema.json","providers":{}}' > "$models" || return 1
  fi
  agent_dir=$(CDPATH='' cd -- "$agent_dir" && pwd -P) || return 1
  models="$agent_dir/models.json"
  helper_hash=$(install_token_helper "$agent_dir") || return 1
  # Pi's provider file may be shared across multiple Firstmate homes and code
  # roots. Keep the request helper beside that provider, version it by content,
  # and select the correct agent directory at request time. Older helper
  # versions remain installed so workers that already loaded an older command
  # can continue to use it after a later install.
  api_key="!bash \"\${PI_CODING_AGENT_DIR:-\$HOME/.pi/agent}/fm-opencode-go/$helper_hash/fm-pi-opencode-go.sh\" token"
  jq --arg api_key "$api_key" '
    .providers as $existing_providers |
    .providers = (.providers // {}) |
    .providers["opencode-go"] = {
      "baseUrl":"https://opencode.ai/inference/go/openai/v1",
      "api":"openai-completions",
      "apiKey":$api_key,
      "headers":{
        "x-opencode-org-id":"${FM_OPENCODE_ORG_ID}",
        "x-opencode-session":"${FM_OPENCODE_SESSION}"
      },
      "models": (($existing_providers["opencode-go"].models // []
        | map(select(.id != "minimax-m3.1-flash-free" and .id != "muse-spark-1.3-contributor"))) + [
        {"id":"minimax-m3.1-flash-free","name":"MiniMax M3.1 Flash Free","reasoning":true,"input":["text","image"],"contextWindow":1000000,"maxTokens":512000,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0}},
        {"id":"muse-spark-1.3-contributor","name":"Muse Spark 1.3 Contributor","api":"openai-responses","reasoning":true,"input":["text","image"],"contextWindow":1048576,"maxTokens":131072,"cost":{"input":0.1,"output":0.2,"cacheRead":0,"cacheWrite":0}}
      ])
    }
  ' "$models" > "$tmp" || { echo "error: could not update Pi's OpenCode Go provider" >&2; return 1; }
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$models" || return 1
}

install_token_helper() {  # <Pi-agent-dir> -> prints the helper content hash
  local agent_dir=$1 helper_root version_dir tmp name hash_input hash
  local -a helper_files=(fm-pi-opencode-go.sh fm-opencode-second-lib.sh fm-timeout-lib.sh fm-backend.sh)
  helper_root="$agent_dir/fm-opencode-go"
  mkdir -p "$helper_root" || return 1
  command -v shasum >/dev/null 2>&1 || {
    echo "error: shasum is required to install Pi's versioned OpenCode Go helper" >&2
    return 1
  }
  hash_input=$(for name in "${helper_files[@]}"; do
    [ -f "$SCRIPT_DIR/$name" ] || return 1
    shasum -a 256 "$SCRIPT_DIR/$name" | awk '{print $1}'
  done) || return 1
  hash=$(printf '%s\n' "$hash_input" | shasum -a 256 | awk '{print $1}') || return 1
  case "$hash" in
    [0-9a-f][0-9a-f]*) [ "${#hash}" -eq 64 ] || return 1 ;;
    *) return 1 ;;
  esac
  version_dir="$helper_root/$hash"
  if [ -d "$version_dir" ]; then
    for name in "${helper_files[@]}"; do
      cmp -s "$SCRIPT_DIR/$name" "$version_dir/$name" || {
        echo "error: installed Pi helper hash collision at $version_dir" >&2
        return 1
      }
    done
  else
    tmp=$(mktemp -d "$helper_root/.install.XXXXXX") || return 1
    for name in "${helper_files[@]}"; do
      cp "$SCRIPT_DIR/$name" "$tmp/$name" || { rm -rf "$tmp"; return 1; }
      chmod 700 "$tmp/$name" 2>/dev/null || true
    done
    chmod 700 "$tmp" 2>/dev/null || true
    mv "$tmp" "$version_dir" || { rm -rf "$tmp"; return 1; }
  fi
  printf '%s\n' "$hash"
}

token_for_task() {
  local db=${FM_OPENCODE_TASK_DB:-} org=${FM_OPENCODE_ORG_ID:-} config_dir=${FM_OPENCODE_CONFIG_DIR:-}
  local out live refresh_seconds
  FM_PI_TOKEN_DB=$db
  [ -n "$db" ] || { FM_PI_TOKEN_STEP=missing-task-db; return 1; }
  # The fast path: one sqlite3 read of the task database, before any library
  # is sourced, any lock is taken, or any OpenCode process is started.
  out=$(fm_token_fast "$db" "$FM_PI_TOKEN_FAST_MARGIN_MS" "$FM_PI_TOKEN_DB_TIMEOUT_MS") && {
    FM_PI_TOKEN_STEP=fast-path-served
    printf '%s' "$out"
    return 0
  }
  FM_PI_TOKEN_STEP=fast-path-miss
  # Past the fast path the shared libraries and the OpenCode CLI are needed.
  fm_token_load_libs || { FM_PI_TOKEN_STEP=library-load-failed; return 1; }
  [ -n "$org" ] && [ -n "$config_dir" ] || { FM_PI_TOKEN_STEP=missing-config; return 1; }
  [ -d "$config_dir" ] || { FM_PI_TOKEN_STEP=missing-config-dir; return 1; }
  command -v opencode >/dev/null 2>&1 || { FM_PI_TOKEN_STEP=missing-opencode-cli; return 1; }
  # The refresh is the expensive path, so it only runs while the deadline
  # still has room for it. The console database is resolved first, and it is
  # resolved under a bound, so a slow OpenCode CLI cannot take the request
  # down with it.
  live=$(fm_token_live_db "$FM_PI_TOKEN_LIVE_DB_SECONDS") || live=
  if [ -z "$live" ]; then
    FM_PI_TOKEN_STEP=live-db-unavailable
  else
    refresh_seconds=$(fm_token_refresh_seconds)
    if [ "$refresh_seconds" -ge 1 ]; then
      FM_PI_TOKEN_STEP=refresh-lock
      out=$(token_refresh "$db" "$org" "$config_dir" "$live" "$refresh_seconds") && {
        FM_PI_TOKEN_STEP=refresh-served
        # The pinned workspace databases carry the same console login, so a
        # re-login reaches them too: a pin that keeps the old token reports
        # a rejected provider to everything that reads it. The copy runs
        # outward only, because the live database stays the single holder of
        # the console refresh token, and it runs after the console refresh
        # lock is released so it never delays another worker's request.
        (
          export FM_OPENCODE_LOCK_TIMEOUT=2
          fm_opencode_pins_refresh "$config_dir"
        ) >/dev/null 2>&1 || true
        printf '%s' "$out"
        return 0
      }
      FM_PI_TOKEN_STEP=refresh-failed
    else
      FM_PI_TOKEN_STEP=refresh-skipped-no-budget
    fi
  fi
  # A token that is still valid beats failing the request behind a refresh
  # that could not finish in time.
  out=$(fm_token_fast "$db" 0 "$FM_PI_TOKEN_FALLBACK_DB_TIMEOUT_MS") && {
    FM_PI_TOKEN_STEP=fallback-served
    printf '%s' "$out"
    return 0
  }
  return 1
}

token_unlocked() {  # <task-db> <org> <live-db>
  local db=${1:-} org=${2:-} live=${3:-} access live_sql org_sql seconds
  FM_PI_TOKEN_DB=$db
  [ -f "$db" ] || { FM_PI_TOKEN_STEP=missing-task-db; return 1; }
  fm_token_load_libs || { FM_PI_TOKEN_STEP=library-load-failed; return 1; }
  # The lock holder may have refreshed while this worker waited. Reuse that
  # result before contacting OpenCode.
  access=$(fm_token_fast "$db" 30000 "$FM_PI_TOKEN_FALLBACK_DB_TIMEOUT_MS") && {
    FM_PI_TOKEN_STEP=lock-holder-refreshed
    printf '%s' "$access"
    return 0
  }
  [ -n "$live" ] && [ -f "$live" ] || { FM_PI_TOKEN_STEP=live-db-unavailable; return 1; }
  seconds=$(fm_token_remaining_seconds)
  [ "$seconds" -ge 1 ] || { FM_PI_TOKEN_STEP=refresh-no-budget; return 1; }
  [ "$seconds" -gt "$FM_PI_TOKEN_REFRESH_MAX_SECONDS" ] && seconds=$FM_PI_TOKEN_REFRESH_MAX_SECONDS
  # OpenCode owns console-token refresh. Run it against the canonical login
  # database so concurrent workers cannot race separate copies of refresh_token.
  OPENCODE_DB="$live" fm_run_timed "$seconds" opencode debug config >/dev/null 2>&1 || {
    FM_PI_TOKEN_STEP=opencode-refresh-failed
    return 1
  }
  live_sql=${live//\'/\'\'}
  org_sql=${org//\'/\'\'}
  sqlite3 -cmd ".timeout $FM_PI_TOKEN_COPY_TIMEOUT_MS" "$db" \
    "ATTACH DATABASE '$live_sql' AS fm_live; BEGIN IMMEDIATE; INSERT OR REPLACE INTO main.account (id,email,url,access_token,refresh_token,token_expiry,time_created,time_updated) SELECT id,email,url,access_token,refresh_token,token_expiry,time_created,time_updated FROM fm_live.account; INSERT OR REPLACE INTO main.account_state (id,active_account_id,active_org_id) SELECT id,active_account_id,active_org_id FROM fm_live.account_state; UPDATE main.account_state SET active_org_id='$org_sql' WHERE id=1; COMMIT; DETACH DATABASE fm_live;" \
    >/dev/null 2>&1 || { FM_PI_TOKEN_STEP=task-db-copy-failed; return 1; }
  access=$(fm_token_fast "$db" 30000 "$FM_PI_TOKEN_FALLBACK_DB_TIMEOUT_MS") || {
    FM_PI_TOKEN_STEP=refreshed-token-unreadable
    return 1
  }
  FM_PI_TOKEN_STEP=refreshed
  printf '%s' "$access"
}

fm_token_usage() {
  echo "usage: fm-pi-opencode-go.sh install <Pi-agent-dir> | token" >&2
}

case "${1:-}" in
  install) [ "$#" -eq 2 ] && install_provider "$2" ;;
  install-unlocked) [ "$#" -eq 2 ] && install_unlocked "$2" ;;
  token)
    [ "$#" -eq 1 ] || { fm_token_usage; exit 2; }
    FM_PI_TOKEN_START_MS=$(fm_epoch_ms) || FM_PI_TOKEN_START_MS=
    export FM_PI_TOKEN_START_MS
    trap 'fm_token_on_exit $?' EXIT
    token_for_task
    exit $?
    ;;
  token-unlocked)
    [ "$#" -eq 4 ] || { fm_token_usage; exit 2; }
    FM_PI_TOKEN_START_MS=${FM_PI_TOKEN_START_MS:-}
    export FM_PI_TOKEN_START_MS
    trap 'fm_token_on_exit $?' EXIT
    token_unlocked "$2" "$3" "$4"
    exit $?
    ;;
  *) fm_token_usage; exit 2 ;;
esac
