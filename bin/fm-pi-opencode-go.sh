#!/usr/bin/env bash
# fm-pi-opencode-go.sh - managed Pi configuration and OpenCode Go credentials.
# Usage: fm-pi-opencode-go.sh install <Pi-agent-dir>
#        fm-pi-opencode-go.sh token
# The installer merges the OpenCode Go provider into Pi's models.json. The
# request-time token command refreshes OpenCode's live console login through
# OpenCode itself when needed, then copies only its login rows to the task
# database. A safely fresh task token is served directly without the shared
# refresh lock or an OpenCode process.
set -u

SCRIPT_DIR=$(CDPATH='' cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P) || exit 1
# shellcheck source=bin/fm-opencode-second-lib.sh
. "$SCRIPT_DIR/fm-opencode-second-lib.sh"
# shellcheck source=bin/fm-timeout-lib.sh
. "$SCRIPT_DIR/fm-timeout-lib.sh"

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
  local agent_dir=${1:-} models tmp helper api_key
  models="$agent_dir/models.json"
  helper="$SCRIPT_DIR/fm-pi-opencode-go.sh"
  api_key="!bash $helper token"
  tmp="$models.tmp.$$"
  if [ -f "$models" ]; then
    jq -e 'type == "object" and ((.providers // {}) | type == "object")' "$models" >/dev/null 2>&1 || {
      echo "error: refusing malformed Pi models file at $models" >&2
      return 1
    }
    if jq --arg helper "$api_key" -e '.providers["opencode-go"] != null and (.providers["opencode-go"].baseUrl != "https://opencode.ai/inference/go/openai/v1" or .providers["opencode-go"].api != "openai-completions" or .providers["opencode-go"].apiKey != $helper)' "$models" >/dev/null 2>&1; then
      echo "error: Pi provider opencode-go is already configured differently in $models" >&2
      return 1
    fi
  else
    # shellcheck disable=SC2016
    printf '%s\n' '{"$schema":"https://pi.dev/schemas/models.schema.json","providers":{}}' > "$models" || return 1
  fi
  jq --arg api_key "$api_key" '
    .providers = (.providers // {}) |
    .providers["opencode-go"] = {
      "baseUrl":"https://opencode.ai/inference/go/openai/v1",
      "api":"openai-completions",
      "apiKey":$api_key,
      "headers":{
        "x-opencode-org-id":"${FM_OPENCODE_ORG_ID}",
        "x-opencode-session":"${FM_OPENCODE_SESSION}"
      },
      "models":[
        {"id":"minimax-m3.1-flash-free","name":"MiniMax M3.1 Flash Free","reasoning":true,"input":["text","image"],"contextWindow":1000000,"maxTokens":512000,"cost":{"input":0,"output":0,"cacheRead":0,"cacheWrite":0}},
        {"id":"muse-spark-1.3-contributor","name":"Muse Spark 1.3 Contributor","api":"openai-responses","reasoning":true,"input":["text","image"],"contextWindow":1048576,"maxTokens":131072,"cost":{"input":0.1,"output":0.2,"cacheRead":0.002,"cacheWrite":0}}
      ]
    }
  ' "$models" > "$tmp" || { echo "error: could not update Pi's OpenCode Go provider" >&2; return 1; }
  chmod 600 "$tmp" 2>/dev/null || true
  mv -f "$tmp" "$models" || return 1
}

token_for_task() {
  local db=${FM_OPENCODE_TASK_DB:-} org=${FM_OPENCODE_ORG_ID:-} config_dir=${FM_OPENCODE_CONFIG_DIR:-}
  local lock out rc=0
  [ -n "$db" ] && [ -n "$org" ] && [ -n "$config_dir" ] || return 1
  [ -f "$db" ] && [ -d "$config_dir" ] || return 1
  command -v sqlite3 >/dev/null 2>&1 && command -v opencode >/dev/null 2>&1 || return 1
  out=$(cached_token "$db" 300000) && { printf '%s' "$out"; return 0; }
  lock="$config_dir/opencode-pi-token.lock"
  if command -v lockf >/dev/null 2>&1; then
    lockf -t 2 "$lock" bash "$SCRIPT_DIR/fm-pi-opencode-go.sh" token-unlocked "$db" "$org" || rc=$?
  elif command -v flock >/dev/null 2>&1; then
    flock -w 2 "$lock" bash "$SCRIPT_DIR/fm-pi-opencode-go.sh" token-unlocked "$db" "$org" || rc=$?
  else
    return 1
  fi
  [ "$rc" -eq 0 ] && return 0
  # A contended refresh may outlast Pi's apiKey command budget. If the token
  # remains valid, return it rather than making a waiter fail behind the lock.
  out=$(cached_token "$db" 0) && { printf '%s' "$out"; return 0; }
  return "$rc"
}

cached_token() {  # <task-db> <minimum-remaining-ms>
  local db=${1:-} margin=${2:-} expiry now access
  [ -f "$db" ] || return 1
  expiry=$(sqlite3 "$db" 'SELECT token_expiry FROM account LIMIT 1;' 2>/dev/null) || return 1
  case "$expiry" in ''|*[!0-9]*) return 1 ;; esac
  now=$(date +%s) || return 1
  [ "$expiry" -gt "$((now * 1000 + margin))" ] || return 1
  access=$(sqlite3 "$db" 'SELECT access_token FROM account LIMIT 1;' 2>/dev/null) || return 1
  [ -n "$access" ] || return 1
  printf '%s' "$access"
}

token_unlocked() {
  local db=${1:-} org=${2:-} live access live_sql org_sql paths data_dir
  [ -f "$db" ] || return 1
  # The lock holder may have refreshed while this worker waited. Reuse that
  # result before contacting OpenCode.
  access=$(cached_token "$db" 30000) && { printf '%s' "$access"; return 0; }
  # Bound both OpenCode calls. The 1s + 3s ceilings leave room inside Pi's
  # 10s apiKey command timeout for lock acquisition and the sqlite copy.
  paths=$(fm_run_timed 1 opencode debug paths 2>/dev/null) || return 1
  data_dir=$(printf '%s\n' "$paths" | sed -n 's/^data[[:space:]]\{1,\}//p' | head -n 1)
  [ -n "$data_dir" ] && [ -f "$data_dir/opencode.db" ] || return 1
  live="$data_dir/opencode.db"
  # OpenCode owns console-token refresh. Run it against the canonical login
  # database so concurrent workers cannot race separate copies of refresh_token.
  OPENCODE_DB="$live" fm_run_timed 3 opencode debug config >/dev/null 2>&1 || return 1
  live_sql=${live//\'/\'\'}
  org_sql=${org//\'/\'\'}
  sqlite3 "$db" "ATTACH DATABASE '$live_sql' AS fm_live; BEGIN IMMEDIATE; INSERT OR REPLACE INTO main.account (id,email,url,access_token,refresh_token,token_expiry,time_created,time_updated) SELECT id,email,url,access_token,refresh_token,token_expiry,time_created,time_updated FROM fm_live.account; INSERT OR REPLACE INTO main.account_state (id,active_account_id,active_org_id) SELECT id,active_account_id,active_org_id FROM fm_live.account_state; UPDATE main.account_state SET active_org_id='$org_sql' WHERE id=1; COMMIT; DETACH DATABASE fm_live;" >/dev/null 2>&1 || return 1
  cached_token "$db" 30000
}

case "${1:-}" in
  install) [ "$#" -eq 2 ] && install_provider "$2" ;;
  install-unlocked) [ "$#" -eq 2 ] && install_unlocked "$2" ;;
  token) [ "$#" -eq 1 ] && token_for_task ;;
  token-unlocked) [ "$#" -eq 3 ] && token_unlocked "$2" "$3" ;;
  *) echo "usage: fm-pi-opencode-go.sh install <Pi-agent-dir> | token" >&2; exit 2 ;;
esac
