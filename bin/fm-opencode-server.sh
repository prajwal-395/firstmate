#!/usr/bin/env bash
# fm-opencode-server.sh - the shared OpenCode server an attached worker uses.
# Usage: fm-opencode-server.sh ensure <state-dir> <profile> <task-id> [<org> <config-dir>]
#        fm-opencode-server.sh session <state-dir> <profile> <directory> <title>
#        fm-opencode-server.sh prompt <state-dir> <profile> <session-id> <directory> <model> <variant> <text>
#        fm-opencode-server.sh dispose <state-dir> <profile> <directory>
#        fm-opencode-server.sh end-session <state-dir> <profile> <session-id> <directory>
#        fm-opencode-server.sh release <state-dir> <profile> <task-id> [<session-id> <directory>]
#
# THE EXPERIMENT. With config/opencode-shared-server set to `on`
# (docs/configuration.md owns the switch), bin/fm-spawn.sh launches an
# OpenCode worker as `opencode attach` against one long-lived `opencode serve`
# instead of a full standalone runtime per worker. The visible pane keeps the
# interactive client and each task keeps its own session; the server runs the
# sessions, so the per-worker cost is the thin client.
#
# ONE SERVER PER PROFILE. The server owns the session database and ignores a
# client's OPENCODE_DB, so workspace billing holds only with one server per
# database. <profile> is one of:
#   main       the main Go workspace pin, when a second workspace is configured
#   secondary  the second Go workspace pin
#   default    everything unpinned (free and non-Go models), on the machine's
#              own console database exactly as a standalone launch uses it
# A pinned server never opens the shared pin itself: it runs on its own private
# copy, state/opencode-server-<profile>.opencode.db, built and proven against
# its workspace by fm_opencode_workspace_db_for_task (bin/fm-opencode-second-lib.sh
# owns building, refreshing, and the workspace proof) before every server
# start. A launch-time pin refresh can therefore never rewrite or quarantine a
# database a live server holds, and the single server process is that copy's
# only OpenCode writer.
#
# ensure takes the profile lock, reuses the recorded server when it is alive and
# answers its health endpoint, and otherwise starts a fresh one; it then records
# the task's lease and prints `<url><TAB><password-file>`. The server binds
# 127.0.0.1 on a port the OS chooses, requires a per-server random password held
# in a mode-0600 file (clients read it from OPENCODE_SERVER_PASSWORD, never
# argv), and is detached into its own session so it outlives the spawn that
# started it. A server that cannot start exits non-zero, which the caller
# treats as "launch standalone instead".
#
# session creates one task session scoped to <directory> and prints its id.
# prompt submits text to a session asynchronously with an optional
# provider/model and variant. dispose drops the server's cached instance for a
# directory so a relaunch starts its new session on a fresh instance; the
# server caches plugin modules across that reload, which is why the attached
# plugin reads its busy generation at event time (bin/fm-spawn.sh). end-session
# aborts any running turn on a session and deletes it, leaving the lease alone.
#
# release aborts and deletes the task's session when one is named, drops the
# task's lease, and stops the server only when no lease remains. Every
# operation that changes leases or server lifetime runs under the profile lock,
# so a concurrent ensure can never attach to a server a release is stopping.
#
# Records (all under <state-dir>, mode 0600):
#   opencode-server-<profile>           pid=, port=, db= of the live server
#   opencode-server-<profile>.password  the server's basic-auth password
#   opencode-server-<profile>.log       server output
#   opencode-server-<profile>.leases/<task-id>  one empty file per user
#   .opencode-server-<profile>.lock     the profile lock
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

die() { echo "error: $*" >&2; exit 1; }

FM_OPENCODE_SERVER_USER=opencode
START_ATTEMPTS=${FM_OPENCODE_SERVER_START_POLLS:-120}

valid_profile() {
  case "${1:-}" in main|secondary|default) return 0 ;; esac
  return 1
}

valid_id() {
  case "${1:-}" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  return 0
}

profile_paths() {  # <state> <profile>
  RECORD="$1/opencode-server-$2"
  PASSWORD_FILE="$RECORD.password"
  LOG_FILE="$RECORD.log"
  LEASES="$RECORD.leases"
  LOCK="$1/.opencode-server-$2.lock"
}

record_field() {  # <field>
  [ -f "$RECORD" ] || return 1
  sed -n "s/^$1=//p" "$RECORD" | head -n 1
}

# curl with the server password supplied on stdin config, never argv.
server_curl() {  # <curl args...>
  local password
  password=$(cat "$PASSWORD_FILE" 2>/dev/null) || return 1
  [ -n "$password" ] || return 1
  printf 'user = "%s:%s"\n' "$FM_OPENCODE_SERVER_USER" "$password" \
    | curl --config - --silent --show-error --fail "$@"
}

server_url() {
  local port
  port=$(record_field port) || return 1
  case "$port" in ''|*[!0-9]*) return 1 ;; esac
  printf 'http://127.0.0.1:%s\n' "$port"
}

# The recorded pid is still an opencode serve process (pid reuse cannot pass)
# and the recorded port answers healthy for this server's own password.
server_alive() {
  local pid args url
  pid=$(record_field pid) || return 1
  case "$pid" in ''|*[!0-9]*) return 1 ;; esac
  kill -0 "$pid" 2>/dev/null || return 1
  args=$(ps -p "$pid" -o command= 2>/dev/null) || return 1
  case "$args" in *opencode*serve*) ;; *) return 1 ;; esac
  url=$(server_url) || return 1
  server_curl --max-time 3 "$url/global/health" 2>/dev/null \
    | grep -Eq '"healthy"[[:space:]]*:[[:space:]]*true'
}

with_profile_lock() {  # <op> <state> <profile> <args...>
  profile_paths "$2" "$3"
  if command -v flock >/dev/null 2>&1; then
    flock -w 120 "$LOCK" bash "$0" _locked "$@"
  elif command -v lockf >/dev/null 2>&1; then
    lockf -t 120 "$LOCK" bash "$0" _locked "$@"
  else
    die "the shared OpenCode server needs flock or lockf"
  fi
}

new_password() {
  local password
  password=$(od -An -N24 -tx1 /dev/urandom 2>/dev/null | tr -d ' \n') || return 1
  [ "${#password}" -ge 32 ] || return 1
  printf '%s\n' "$password"
}

# Start a detached server and wait for it to print its port and answer health.
start_server() {  # <state> <profile> <db-or-empty>
  local state=$1 profile=$2 db=$3 password tmp pid port n line
  password=$(new_password) || return 1
  tmp="$PASSWORD_FILE.tmp.$$"
  (umask 077 && printf '%s\n' "$password" > "$tmp") || { rm -f "$tmp"; return 1; }
  mv -f "$tmp" "$PASSWORD_FILE" || { rm -f "$tmp"; return 1; }
  : > "$LOG_FILE" && chmod 600 "$LOG_FILE" 2>/dev/null
  (
    cd "$state" || exit 1
    export OPENCODE_SERVER_PASSWORD="$password"
    export OPENCODE_SERVER_USERNAME="$FM_OPENCODE_SERVER_USER"
    export OPENCODE_CONFIG_CONTENT='{"permission":{"*":"allow"}}'
    export OPENCODE_DISABLE_AUTOUPDATE=1
    if [ -n "$db" ]; then export OPENCODE_DB="$db"; else unset OPENCODE_DB; fi
    # A new session detaches the server from the spawning shell's process
    # group and terminal, so the caller's exit cannot take it down.
    if command -v setsid >/dev/null 2>&1; then
      exec setsid opencode serve --hostname 127.0.0.1 --port 0 </dev/null >>"$LOG_FILE" 2>&1
    elif command -v perl >/dev/null 2>&1; then
      exec perl -MPOSIX -e 'POSIX::setsid(); exec @ARGV or exit 127' \
        opencode serve --hostname 127.0.0.1 --port 0 </dev/null >>"$LOG_FILE" 2>&1
    fi
    exec nohup opencode serve --hostname 127.0.0.1 --port 0 </dev/null >>"$LOG_FILE" 2>&1
  ) &
  pid=$!
  n=0
  port=
  while [ "$n" -lt "$START_ATTEMPTS" ]; do
    kill -0 "$pid" 2>/dev/null || return 1
    if [ -z "$port" ]; then
      line=$(grep -m1 -o 'listening on http://127\.0\.0\.1:[0-9][0-9]*' "$LOG_FILE" 2>/dev/null) || line=
      port=${line##*:}
      if [ -n "$port" ]; then
        tmp="$RECORD.tmp.$$"
        (umask 077 && printf 'pid=%s\nport=%s\ndb=%s\n' "$pid" "$port" "$db" > "$tmp") \
          || { rm -f "$tmp"; kill "$pid" 2>/dev/null; return 1; }
        mv -f "$tmp" "$RECORD" || { rm -f "$tmp"; kill "$pid" 2>/dev/null; return 1; }
      fi
    fi
    if [ -n "$port" ] && server_alive; then
      return 0
    fi
    n=$((n + 1))
    sleep 0.25
  done
  kill "$pid" 2>/dev/null || true
  rm -f "$RECORD"
  return 1
}

stop_server() {
  local pid args n
  pid=$(record_field pid) || pid=
  case "$pid" in ''|*[!0-9]*) rm -f "$RECORD"; return 0 ;; esac
  if kill -0 "$pid" 2>/dev/null; then
    args=$(ps -p "$pid" -o command= 2>/dev/null) || args=
    case "$args" in
      *opencode*serve*)
        kill "$pid" 2>/dev/null || true
        n=0
        while kill -0 "$pid" 2>/dev/null && [ "$n" -lt 40 ]; do
          n=$((n + 1))
          sleep 0.25
        done
        kill -0 "$pid" 2>/dev/null && return 1
        ;;
    esac
  fi
  rm -f "$RECORD"
}

locked_ensure() {  # <state> <profile> <task-id> [<org> <config-dir>]
  local state=$1 profile=$2 task=$3 org=${4:-} config_dir=${5:-} db='' url
  profile_paths "$state" "$profile"
  if ! server_alive; then
    if [ "$profile" != default ]; then
      [ -n "$org" ] && [ -n "$config_dir" ] || die "the $profile OpenCode server needs its workspace id and config directory"
      # shellcheck source=bin/fm-opencode-second-lib.sh
      . "$SCRIPT_DIR/fm-opencode-second-lib.sh"
      db=$(fm_opencode_workspace_db_for_task "$profile" "$org" "$config_dir" "opencode-server-$profile" "$state") \
        || die "the $profile OpenCode server database could not be proven against its workspace"
    fi
    stop_server || die "a stale $profile OpenCode server could not be stopped"
    start_server "$state" "$profile" "$db" || die "the $profile OpenCode server did not start (log: $LOG_FILE)"
  fi
  url=$(server_url) || die "the $profile OpenCode server record has no port"
  mkdir -p "$LEASES" && chmod 700 "$LEASES" 2>/dev/null
  : > "$LEASES/$task" || die "the $profile OpenCode server lease for $task could not be recorded"
  printf '%s\t%s\n' "$url" "$PASSWORD_FILE"
}

encode_directory() {
  jq -rn --arg value "$1" '$value|@uri'
}

# The first call for a directory boots the server's instance for it (config
# and plugins), which can take tens of seconds on a loaded machine.
session_call() {  # <max-seconds> <method> <path> <directory> [json-body]
  local max=$1 method=$2 path=$3 directory=$4 body=${5:-} url encoded
  url=$(server_url) || return 1
  encoded=$(encode_directory "$directory") || return 1
  if [ -n "$body" ]; then
    server_curl --max-time "$max" -X "$method" -H 'Content-Type: application/json' \
      --data "$body" "$url$path?directory=$encoded"
  else
    server_curl --max-time "$max" -X "$method" "$url$path?directory=$encoded"
  fi
}

# Abort a session's running turn, then delete it. Quiet when no server runs:
# a stopped server is already running nothing.
end_session() {  # <profile> <session-id> <directory>
  server_alive || return 0
  session_call 30 POST "/session/$2/abort" "$3" >/dev/null 2>&1 || true
  session_call 30 DELETE "/session/$2" "$3" >/dev/null 2>&1 && return 0
  echo "warning: the $1 OpenCode server did not delete session $2" >&2
  return 1
}

locked_release() {  # <state> <profile> <task-id> [<session-id> <directory>]
  local state=$1 profile=$2 task=$3 session=${4:-} directory=${5:-} rc=0 remaining
  profile_paths "$state" "$profile"
  [ -z "$session" ] || end_session "$profile" "$session" "${directory:-$state}" || true
  rm -f "$LEASES/$task"
  remaining=$(find "$LEASES" -type f 2>/dev/null | head -n 1)
  if [ -z "$remaining" ]; then
    stop_server || { echo "error: the idle $profile OpenCode server could not be stopped" >&2; rc=1; }
  fi
  return "$rc"
}

need_tools() {
  command -v curl >/dev/null 2>&1 || die "curl is not installed"
  command -v jq >/dev/null 2>&1 || die "jq is not installed"
}

op=${1:-}
[ "$#" -gt 0 ] && shift
case "$op" in
  _locked)
    sub=${1:-}
    shift
    case "$sub" in
      ensure) locked_ensure "$@"; exit $? ;;
      release) locked_release "$@"; exit $? ;;
      *) die "unknown locked operation '$sub'" ;;
    esac
    ;;
  ensure)
    [ "$#" -eq 3 ] || [ "$#" -eq 5 ] || die "usage: fm-opencode-server.sh ensure <state-dir> <profile> <task-id> [<org> <config-dir>]"
    [ -d "$1" ] || die "state directory does not exist: $1"
    valid_profile "$2" || die "unknown OpenCode server profile '$2'"
    valid_id "$3" || die "invalid task id '$3'"
    command -v opencode >/dev/null 2>&1 || die "opencode is not installed"
    need_tools
    with_profile_lock ensure "$@"
    ;;
  release)
    [ "$#" -eq 3 ] || [ "$#" -eq 5 ] || die "usage: fm-opencode-server.sh release <state-dir> <profile> <task-id> [<session-id> <directory>]"
    [ -d "$1" ] || die "state directory does not exist: $1"
    valid_profile "$2" || die "unknown OpenCode server profile '$2'"
    valid_id "$3" || die "invalid task id '$3'"
    [ -z "${4:-}" ] || valid_id "$4" || die "invalid session id '$4'"
    need_tools
    with_profile_lock release "$@"
    ;;
  session)
    [ "$#" -eq 4 ] || die "usage: fm-opencode-server.sh session <state-dir> <profile> <directory> <title>"
    valid_profile "$2" || die "unknown OpenCode server profile '$2'"
    [ -d "$3" ] || die "OpenCode session directory does not exist: $3"
    need_tools
    profile_paths "$1" "$2"
    body=$(jq -nc --arg title "$4" '{title:$title}') || die "could not encode the session title"
    out=$(session_call 120 POST /session "$3" "$body") || die "the $2 OpenCode server could not create a session"
    id=$(printf '%s' "$out" | jq -er '.id') || die "the $2 OpenCode server returned no session id"
    valid_id "$id" || die "the $2 OpenCode server returned an unusable session id"
    printf '%s\n' "$id"
    ;;
  prompt)
    [ "$#" -eq 7 ] || die "usage: fm-opencode-server.sh prompt <state-dir> <profile> <session-id> <directory> <model> <variant> <text>"
    valid_profile "$2" || die "unknown OpenCode server profile '$2'"
    valid_id "$3" || die "invalid session id '$3'"
    need_tools
    profile_paths "$1" "$2"
    body=$(jq -nc --arg text "$7" --arg model "$5" --arg variant "$6" '
      {parts:[{type:"text",text:$text}]}
      + (if ($model|test("^[^/]+/.+")) then
           {model:{providerID:($model|split("/")[0]),modelID:($model|sub("^[^/]+/";""))}}
         else {} end)
      + (if ($variant|length) > 0 then {variant:$variant} else {} end)') \
      || die "could not encode the task prompt"
    session_call 120 POST "/session/$3/prompt_async" "$4" "$body" >/dev/null \
      || die "the $2 OpenCode server did not accept the task prompt"
    ;;
  end-session)
    [ "$#" -eq 4 ] || die "usage: fm-opencode-server.sh end-session <state-dir> <profile> <session-id> <directory>"
    valid_profile "$2" || die "unknown OpenCode server profile '$2'"
    valid_id "$3" || die "invalid session id '$3'"
    need_tools
    profile_paths "$1" "$2"
    end_session "$2" "$3" "$4"
    ;;
  dispose)
    [ "$#" -eq 3 ] || die "usage: fm-opencode-server.sh dispose <state-dir> <profile> <directory>"
    valid_profile "$2" || die "unknown OpenCode server profile '$2'"
    need_tools
    profile_paths "$1" "$2"
    session_call 60 POST /instance/dispose "$3" >/dev/null || die "the $2 OpenCode server could not reload $3"
    ;;
  *)
    die "usage: fm-opencode-server.sh ensure|session|prompt|dispose|end-session|release ..."
    ;;
esac
