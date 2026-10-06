#!/usr/bin/env bash
# fm-opencode-second-lib.sh - the second OpenCode Go workspace.
# Usage: . bin/fm-opencode-second-lib.sh
# Sourced by bin/fm-spawn.sh, bin/fm-opencode-ladder-lib.sh, and
# bin/fm-opencode-descent-lib.sh. Sourcing has no side effects beyond the
# constants and functions below.
#
# THE PROBLEM. One OpenCode account can hold two workspaces with separate Go
# subscriptions, but the active workspace is global to the machine: `opencode
# console switch` moves every worker's next launch at once, so it is never
# used here. The per-launch pin is OPENCODE_DB: opencode resolves its console
# login (bearer token plus active workspace id) from a sqlite database, and
# OPENCODE_DB points one launch at a different file. A pinned database holds
# only the migrated schema plus the account rows, with its own active
# workspace id, so the pinned launch fetches that workspace's console config
# and attributes its inference there. The model id is unchanged
# (`opencode-go/muse-spark-1.3-contributor` on both rungs); the workspace a
# lane runs on is recorded per task as `opencode_workspace=secondary` in its
# meta, absent meaning the main workspace exactly as today.
#
# Verified 2026-10-06 on opencode 1.18.34: a main-pinned launch resolves the
# main console config (`main / OpenCode`) while the machine-wide active
# workspace is secondary, a secondary-pinned launch resolves `secondary /
# OpenCode`, a real Go request on the secondary pin completes while the same
# request on the main pin parks in the vendor retry backoff (main Go
# exhausted), and OPENCODE_CONFIG_CONTENT provider overrides do NOT survive:
# the console fetch replaces every managed provider entry, so a separate
# provider id would be required and the model id would change - which is why
# the pin rides the database instead.
#
# PRIVACY. The workspace id lives in gitignored `config/opencode-second-org`
# (docs/configuration.md owns the schema); no token, key, or workspace id
# goes in tracked files, commit messages, or PR bodies. The pinned database
# carries the account's own tokens and lives beside it at
# `config/opencode-second.db`; both paths stay out of secondmate inheritance
# because that contract is allowlisted.
#
# FRESHNESS. The live login refreshes its token, so a pin copied once would
# go stale: after a rotation the pinned copy diverges and a secondary
# launch fails its console fetch or falls back to the auth.json key, which
# bills the main workspace. The account rows are therefore re-copied from
# the live database on every pinned launch, then re-pinned; a pin that
# will not refresh is discarded and rebuilt.
#
# PROOF. Before a launch rides the pin, OPENCODE_DB=<pin> opencode debug
# config must resolve the opencode provider name to the secondary
# workspace. Anything else refuses the launch, so a mis-pinned session
# can never bill the wrong workspace.
#
# ABSENCE IS TODAY. Every predicate below fails closed when the config file
# is absent: no marker is written, no OPENCODE_DB prefix is added, and the
# ladder keeps its three rungs byte-for-byte.
#
# QUOTA. quota-axi exposes one `opencode-go` row with no workspace dimension,
# so it cannot see the second workspace separately. The fourth rung therefore
# uses reactive vendor evidence only; predictive zero-availability rows keep
# feeding the main Go rung exactly as today.

_FM_OPENCODE_SECOND_LIB_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd 2>/dev/null)" || _FM_OPENCODE_SECOND_LIB_DIR="."
# fm_meta_get lives in the backend library; dispatch-path callers load it
# before this file, so the guarded source below fires only when this file
# stands alone.
# shellcheck source=/dev/null
if ! declare -f fm_meta_get >/dev/null 2>&1; then
  . "$_FM_OPENCODE_SECOND_LIB_DIR/fm-backend.sh"
fi

# The logical workspace names. `main` is the absence of a marker: task meta
# without opencode_workspace= runs exactly as today.
FM_OPENCODE_WORKSPACE_MAIN='main'
FM_OPENCODE_WORKSPACE_SECOND='secondary'

# fm_opencode_second_config_dir: the effective config directory.
fm_opencode_second_config_dir() {  # [config-dir]
  local config_dir=${1:-${FM_CONFIG_OVERRIDE:-}}
  if [ -z "$config_dir" ] && [ -n "${FM_HOME:-}" ]; then
    config_dir="$FM_HOME/config"
  fi
  [ -n "$config_dir" ] || return 1
  printf '%s\n' "$config_dir"
}

# fm_opencode_second_org_file [config-dir]: path of the workspace-id file.
fm_opencode_second_org_file() {  # [config-dir]
  local config_dir
  config_dir=$(fm_opencode_second_config_dir "${1:-}") || return 1
  printf '%s/opencode-second-org\n' "$config_dir"
}

# fm_opencode_second_org [config-dir]: print the second workspace id.
# Fails when the file is absent, unreadable, or malformed: absence means the
# home runs the three-rung ladder, and anything else is refused rather than
# guessed at.
fm_opencode_second_org() {  # [config-dir]
  local file org extra
  file=$(fm_opencode_second_org_file "${1:-}") || return 1
  [ -f "$file" ] && [ -r "$file" ] || return 1
  # Exactly one line: a second line is malformed rather than concatenated.
  exec 3< "$file" 2>/dev/null || return 1
  org=
  IFS= read -r org <&3 || [ -n "$org" ] || { exec 3<&-; return 1; }
  extra=
  # shellcheck disable=SC2034 # extra is existence-only: any second line refuses the file
  if IFS= read -r extra <&3; then
    exec 3<&-
    return 1
  fi
  exec 3<&-
  org=$(printf '%s' "$org" | tr -d '[:space:]' 2>/dev/null) || return 1
  case "$org" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
  [ "${#org}" -ge 8 ] && [ "${#org}" -le 128 ] || return 1
  printf '%s\n' "$org"
}

# fm_opencode_second_configured [config-dir]: 0 when a second workspace is
# configured and readable, 1 otherwise. Quiet: callers that must say why use
# fm_opencode_second_org.
fm_opencode_second_configured() {  # [config-dir]
  fm_opencode_second_org "${1:-}" >/dev/null 2>&1
}

# fm_opencode_second_db_file [config-dir]: path of the pinned database.
fm_opencode_second_db_file() {  # [config-dir]
  local config_dir
  config_dir=$(fm_opencode_second_config_dir "${1:-}") || return 1
  printf '%s/opencode-second.db\n' "$config_dir"
}

# fm_opencode_second_live_db: the machine's own console database opencode
# itself reads, resolved the way `opencode debug paths` reports it.
fm_opencode_second_live_db() {
  local data_dir line
  if command -v opencode >/dev/null 2>&1; then
    data_dir=$(opencode debug paths 2>/dev/null | sed -n 's/^data[[:space:]]\{1,\}//p' | head -n 1)
    if [ -n "$data_dir" ] && [ -f "$data_dir/opencode.db" ]; then
      printf '%s/opencode.db\n' "$data_dir"
      return 0
    fi
  fi
  line=${XDG_DATA_HOME:-$HOME/.local/share}/opencode/opencode.db
  [ -f "$line" ] || return 1
  printf '%s\n' "$line"
}

# fm_opencode_second_refresh <db> <live-db> <org>: re-copy the console
# login rows from the live database into the pinned database and re-pin
# the active workspace to <org>. Returns 0 with the pin and the copied
# token verified against the live database, 1 otherwise; the caller
# discards and rebuilds a pin that will not refresh. Plain INSERT would
# collide with the rows already there, so the dump is replayed as
# INSERT OR REPLACE.
fm_opencode_second_refresh() {  # <db> <live-db> <org>
  local db=$1 live=$2 org=$3
  [ -n "$db" ] && [ -n "$live" ] && [ -n "$org" ] || return 1
  [ -f "$db" ] && [ -f "$live" ] || return 1
  sqlite3 "$live" '.dump account account_state' 2>/dev/null \
    | grep '^INSERT' 2>/dev/null \
    | sed 's/^INSERT INTO /INSERT OR REPLACE INTO /' 2>/dev/null \
    | sqlite3 "$db" 2>/dev/null || return 1
  sqlite3 "$db" "UPDATE account_state SET active_org_id='$org' WHERE id=1;" 2>/dev/null || return 1
  [ "$(sqlite3 "$db" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null)" = "$org" ] || return 1
  [ "$(sqlite3 "$db" 'SELECT access_token FROM account LIMIT 1;' 2>/dev/null)" = "$(sqlite3 "$live" 'SELECT access_token FROM account LIMIT 1;' 2>/dev/null)" ] || return 1
  return 0
}

# fm_opencode_second_verify <db> [workspace-name]: prove the pinned
# database resolves the secondary workspace before a launch rides it.
# Returns 0 only when OPENCODE_DB=<db> opencode debug config resolves
# the opencode provider name to "<name> / OpenCode". Anything else - a
# drifted pin, a stale token falling back past the console config - is
# refused by the caller rather than launched.
fm_opencode_second_verify() {  # <db> [workspace-name]
  local db=$1 want=${2:-$FM_OPENCODE_WORKSPACE_SECOND} out
  [ -n "$db" ] && [ -f "$db" ] || return 1
  command -v opencode >/dev/null 2>&1 || return 1
  out=$(OPENCODE_DB="$db" opencode debug config 2>/dev/null) || return 1
  printf '%s' "$out" | grep -q "\"name\": \"$want / OpenCode\"" 2>/dev/null
}

# fm_opencode_second_db [config-dir]: print the pinned database path,
# building it first when needed. Building migrates a fresh database through
# opencode itself (an empty file is migrated on first load; a hand-seeded
# schema collides with the migrator), then copies only the account rows from
# the live login and pins the active workspace to the configured id. An
# existing pin is never trusted: its login rows are re-copied from the live
# database on every call and a pin that will not refresh is discarded and
# rebuilt, so the pin cannot carry a stale token. Before the path is
# published the pin must prove its workspace: a launch that cannot prove
# it never starts. Fails closed with the reason on stderr.
fm_opencode_second_db() {  # [config-dir]
  local config_dir=${1:-} org db live tmp old_umask got_name
  config_dir=$(fm_opencode_second_config_dir "${config_dir:-}") || {
    echo "error: second OpenCode workspace is not configured" >&2
    return 1
  }
  org=$(fm_opencode_second_org "$config_dir") || {
    echo "error: $config_dir/opencode-second-org is missing or malformed; it must hold the second workspace id alone" >&2
    return 1
  }
  db="$config_dir/opencode-second.db"
  command -v sqlite3 >/dev/null 2>&1 || {
    echo "error: second OpenCode workspace needs sqlite3 to pin its database" >&2
    return 1
  }
  [ -d "$config_dir" ] || {
    echo "error: config directory is missing: $config_dir" >&2
    return 1
  }
  # The CLI and the live login are needed on every pinned launch, not
  # only when building from scratch: the pin is refreshed and proven
  # below before any launch rides it.
  command -v opencode >/dev/null 2>&1 || {
    echo "error: second OpenCode workspace needs the opencode CLI on PATH" >&2
    return 1
  }
  live=$(fm_opencode_second_live_db) || {
    echo "error: second OpenCode workspace needs a console login on this machine; no readable opencode database found" >&2
    return 1
  }
  # A pin that will not refresh is discarded and rebuilt below.
  if [ -f "$db" ] && ! fm_opencode_second_refresh "$db" "$live" "$org"; then
    rm -f "$db"
  fi
  if [ ! -f "$db" ]; then
    old_umask=$(umask)
    umask 077
    tmp="$db.tmp.$$"
    rm -f "$tmp"
    if ! OPENCODE_DB="$tmp" opencode debug config >/dev/null 2>&1; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: second OpenCode workspace could not prepare its pinned database" >&2
      return 1
    fi
    if ! sqlite3 "$live" '.dump account account_state' 2>/dev/null | grep '^INSERT' | sqlite3 "$tmp" 2>/dev/null; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: second OpenCode workspace could not copy the console login into its pinned database" >&2
      return 1
    fi
    if ! sqlite3 "$tmp" "UPDATE account_state SET active_org_id='$org' WHERE id=1;" 2>/dev/null \
      || [ "$(sqlite3 "$tmp" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null)" != "$org" ]; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: second OpenCode workspace could not pin its database to the configured workspace" >&2
      return 1
    fi
    chmod 600 "$tmp" 2>/dev/null || true
    if ! mv -f "$tmp" "$db" 2>/dev/null; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: second OpenCode workspace could not publish its pinned database" >&2
      return 1
    fi
    umask "$old_umask"
  fi
  if ! fm_opencode_second_verify "$db"; then
    got_name=$(OPENCODE_DB="$db" opencode debug config 2>/dev/null | grep -m1 '/ OpenCode"' | sed 's/^ *//' 2>/dev/null)
    echo "error: second OpenCode workspace pin resolves ${got_name:-an unknown workspace}, not '$FM_OPENCODE_WORKSPACE_SECOND / OpenCode'; refusing the launch" >&2
    return 1
  fi
  printf '%s\n' "$db"
}

# fm_opencode_lane_workspace <state-dir> <id>: the logical workspace the
# lane's launch pinned, `secondary` only when its meta carries the marker.
# Anything else - absent meta, unreadable marker, any other value - is the
# main workspace, which is also what every lane recorded before markers
# existed reads as.
fm_opencode_lane_workspace() {  # <state-dir> <id>
  local marker
  if marker=$(fm_meta_get "$1/$2.meta" opencode_workspace 2>/dev/null) \
    && [ "$marker" = "$FM_OPENCODE_WORKSPACE_SECOND" ]; then
    printf '%s\n' "$FM_OPENCODE_WORKSPACE_SECOND"
  else
    printf '%s\n' "$FM_OPENCODE_WORKSPACE_MAIN"
  fi
}
