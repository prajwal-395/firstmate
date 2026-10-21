#!/usr/bin/env bash
# fm-opencode-second-lib.sh - the OpenCode Go workspace pins.
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
# and attributes its inference there. The paid Go model is
# `opencode-go/muse-spark-1.3-contributor`; the free Minimax model also uses
# the `opencode-go` provider but does not bill or pin a Go workspace. The
# workspace a paid Go lane runs on is recorded per task as
# `opencode_workspace=secondary` in its meta, absent meaning main.
#
# BOTH WORKSPACES ARE PINNED. A main-workspace Go launch that rides no pin
# inherits the machine-wide active workspace, so the moment that active
# workspace is the secondary plan a `main` lane bills and caps the secondary
# plan while its record still reads main. Every Go launch in a home with a
# configured second workspace therefore rides its own pin, main and secondary
# alike, and a Go launch that cannot prove its pin is refused rather than run
# unpinned. The pin applies only to launches that actually bill a Go
# workspace (bin/fm-opencode-ladder-lib.sh fm_opencode_model_bills_go owns
# that test): the free model and every other non-Go model launch outside the
# workspace pin flow, so discovering or requiring a workspace never blocks
# them. The free Minimax model still requires console sign-in; its unpinned
# per-task database is seeded from the verified main pin or live console
# database. Other unpinned standalone launches may start with an empty task
# database. A home with no second workspace
# runs the main workspace without a workspace pin.
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
# PRIVACY. Each workspace id lives in its gitignored org file
# (`config/opencode-main-org`, `config/opencode-second-org`;
# docs/configuration.md owns the schema); no token, key, or workspace id
# goes in tracked files, commit messages, or PR bodies. The machine's own
# console database is found the way the OpenCode CLI reports it, unless
# FM_OPENCODE_LIVE_DB names it outright; a named database is the only one
# that database is read from, so a scratch home or a test never reaches the
# account of the person running the machine. Each org id is
# declared inherited local material, so secondmate homes converge it from
# the primary and run the Go tier too. Each pinned database carries the
# account's own tokens and lives beside it at `config/opencode-main.db` or
# `config/opencode-second.db`; neither is ever inherited - each home builds
# its own from its own machine's live console login on the first launch that
# needs it, then refreshes it on every later pinned launch. Builders for one
# config directory serialize on an advisory lock. OpenCode processes never use
# these shared pins directly: each standalone task receives a private writable
# database at `state/<task-id>.opencode.db`, seeded from the verified pin when
# workspace-pinned, or created empty when unpinned, and retired with the task
# by teardown. Each shared server (bin/fm-opencode-server.sh) gets one the same
# way.
#
# FRESHNESS. The live login refreshes its token, so a pin copied once would
# go stale: after a rotation the pinned copy diverges and a secondary
# launch fails its console fetch or falls back to the auth.json key, which
# bills the main workspace. The account rows are therefore re-copied from
# the live database inside one read-only read transaction on every pinned
# launch, then re-pinned; a pin that will not refresh is quarantined and
# rebuilt. The task copy refreshes only those login rows from the verified
# pin before relaunch, retaining its session data. Only the two login tables
# are read from the live database: it grows to tens of gigabytes, so a
# whole-file copy is never taken.
#
# PROOF. Before a launch rides a pin, OPENCODE_DB=<pin> opencode debug
# config must resolve the opencode provider name to that launch's own
# workspace. A pin that proves another workspace refuses the launch, so a
# mis-pinned session can never bill the wrong workspace. A pin whose
# workspace cannot be read after a bounded retry refuses with a plain
# could-not-read report instead of a wrong-workspace refusal.
#
# THE PIN IS A COPY, NEVER THE LOGIN. Refresh and rebuild write only the pin
# database; a pin path that names the live console database is refused before
# anything reads or writes either file, so the machine-wide active workspace
# cannot be rewritten behind the fleet.
#
# ABSENCE IS TODAY. Every secondary predicate below fails closed when its
# config file is absent: no workspace marker or workspace pin is added, and
# the ladder keeps its three rungs byte-for-byte. The main
# workspace id is discovered from the console login and recorded on first
# use when no file holds it, so pinning main needs no hand setup; a login
# that names no main workspace refuses the launch instead.
#
# QUOTA. quota-axi exposes one `opencode-go` row with no workspace dimension,
# so it cannot see the second workspace separately. The second workspace
# therefore uses reactive vendor evidence only, contributes no window timing
# to the tier pick, and reads as unknown while uncapped; predictive
# zero-availability rows keep feeding the main Go rung exactly as today.
# bin/fm-opencode-ladder-lib.sh owns the tier comparison.

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

# fm_opencode_pi_go_enabled [config-dir]: 0 for the opt-in, 1 when absent or
# off, and 2 for malformed content. Spawn and descent share this one reader.
fm_opencode_pi_go_enabled() {  # [config-dir]
  local config_dir=${1:-} file value=
  config_dir=$(fm_opencode_second_config_dir "$config_dir") || return 1
  file="$config_dir/pi-opencode-go"
  [ -f "$file" ] || [ -L "$file" ] || return 1
  [ "$(awk 'END { print NR }' "$file" 2>/dev/null)" = 1 ] \
    && IFS= read -r value < "$file" || return 2
  case "$value" in on) return 0 ;; off) return 1 ;; *) return 2 ;; esac
}

# Workspace file infixes. The secondary workspace keeps its historic `second`
# infix (config/opencode-second-org); the main workspace uses `main`.
fm_opencode_workspace_infix() {  # <workspace>
  case "${1:-}" in
    "$FM_OPENCODE_WORKSPACE_SECOND") printf 'second\n' ;;
    "$FM_OPENCODE_WORKSPACE_MAIN") printf 'main\n' ;;
    *) return 1 ;;
  esac
}

# Workspace labels for operator-facing reasons. Names the plan, never an id.
fm_opencode_workspace_label() {  # <workspace>
  case "${1:-}" in
    "$FM_OPENCODE_WORKSPACE_SECOND") printf 'second\n' ;;
    "$FM_OPENCODE_WORKSPACE_MAIN") printf 'main\n' ;;
    *) return 1 ;;
  esac
}

# fm_opencode_workspace_id_valid <id>: 0 when <id> is a usable workspace id.
# The single owner of the format: the org-file readers and the console login
# discovery below both enforce it, so a second copy cannot drift.
fm_opencode_workspace_id_valid() {  # <id>
  local id=${1:-}
  case "$id" in ''|*[!A-Za-z0-9_-]*) return 1 ;; esac
  [ "${#id}" -ge 8 ] && [ "${#id}" -le 128 ]
}

# fm_opencode_workspace_org_file <workspace> [config-dir]: path of the id file.
fm_opencode_workspace_org_file() {  # <workspace> [config-dir]
  local workspace=${1:-} infix config_dir
  infix=$(fm_opencode_workspace_infix "$workspace") || return 1
  config_dir=$(fm_opencode_second_config_dir "${2:-}") || return 1
  printf '%s/opencode-%s-org\n' "$config_dir" "$infix"
}

# fm_opencode_workspace_org <workspace> [config-dir]: print the workspace id.
# Fails when the file is absent, unreadable, or malformed: absence means the
# workspace is unconfigured, and anything else is refused rather than guessed.
fm_opencode_workspace_org() {  # <workspace> [config-dir]
  local workspace=${1:-} file org extra
  file=$(fm_opencode_workspace_org_file "$workspace" "${2:-}") || return 1
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
  fm_opencode_workspace_id_valid "$org" || return 1
  printf '%s\n' "$org"
}

# fm_opencode_workspace_configured <workspace> [config-dir]: 0 when the
# workspace id is configured and readable, 1 otherwise. Quiet: callers that
# must say why use fm_opencode_workspace_org.
fm_opencode_workspace_configured() {  # <workspace> [config-dir]
  fm_opencode_workspace_org "$1" "${2:-}" >/dev/null 2>&1
}

# The historic second-workspace names.
# fm_opencode_second_org_file [config-dir]: path of the workspace-id file.
fm_opencode_second_org_file() {  # [config-dir]
  fm_opencode_workspace_org_file "$FM_OPENCODE_WORKSPACE_SECOND" "${1:-}"
}

# fm_opencode_second_org [config-dir]: print the second workspace id.
# Fails when the file is absent, unreadable, or malformed: absence means the
# home runs the three-rung ladder, and anything else is refused rather than
# guessed at.
fm_opencode_second_org() {  # [config-dir]
  fm_opencode_workspace_org "$FM_OPENCODE_WORKSPACE_SECOND" "${1:-}"
}

# fm_opencode_second_configured [config-dir]: 0 when a second workspace is
# configured and readable, 1 otherwise. Quiet: callers that must say why use
# fm_opencode_second_org.
fm_opencode_second_configured() {  # [config-dir]
  fm_opencode_workspace_configured "$FM_OPENCODE_WORKSPACE_SECOND" "${1:-}"
}

# fm_opencode_main_org_file [config-dir]: path of the main workspace-id file.
fm_opencode_main_org_file() {  # [config-dir]
  fm_opencode_workspace_org_file "$FM_OPENCODE_WORKSPACE_MAIN" "${1:-}"
}

# fm_opencode_main_org [config-dir]: print the main workspace id from its file.
fm_opencode_main_org() {  # [config-dir]
  fm_opencode_workspace_org "$FM_OPENCODE_WORKSPACE_MAIN" "${1:-}"
}

# fm_opencode_main_configured [config-dir]: 0 when a main workspace id file
# is present and readable, 1 otherwise.
fm_opencode_main_configured() {  # [config-dir]
  fm_opencode_workspace_configured "$FM_OPENCODE_WORKSPACE_MAIN" "${1:-}"
}

# fm_opencode_main_org_discover: print the main workspace id read from the
# machine's own console login (`opencode console orgs`), nothing when it
# cannot be determined. The row whose workspace name is `main` wins; zero or
# ambiguous rows refuse rather than guess. The list arrives on stderr on
# opencode 1.18.35 (stdout on older versions), so both streams are read;
# row parsing below only matches valid workspace rows, so a failing
# command's own error text can never parse as an id. A failing list command
# retries with a bounded backoff; a list that parses but names no main row
# is definitive and refuses without retry. Reasons go to stderr,
# and no id is ever printed there.
fm_opencode_main_org_discover() {
  local out stripped candidates id found count attempt attempts listed
  command -v opencode >/dev/null 2>&1 || {
    echo "error: main OpenCode workspace discovery needs the opencode CLI on PATH" >&2
    return 1
  }
  attempts=$(fm_opencode_attempt_count "${FM_OPENCODE_VERIFY_ATTEMPTS:-}" 3)
  out=; listed=0; attempt=1
  while [ "$attempt" -le "$attempts" ]; do
    if out=$(opencode console orgs 2>&1); then
      listed=1
      break
    fi
    out=
    if [ "$attempt" -lt "$attempts" ]; then
      fm_opencode_retry_sleep "$attempt"
    fi
    attempt=$((attempt + 1))
  done
  [ "$listed" -eq 1 ] || {
    echo "error: main OpenCode workspace discovery could not list the console workspaces" >&2
    return 1
  }
  stripped=$(printf '%s' "$out" \
    | sed "s/$(printf '\033')\[[0-9;]*[A-Za-z]//g" 2>/dev/null \
    | tr -d '●' 2>/dev/null) || {
    echo "error: main OpenCode workspace discovery could not read the console workspace list" >&2
    return 1
  }
  candidates=$(printf '%s\n' "$stripped" \
    | awk -v want="$FM_OPENCODE_WORKSPACE_MAIN" '$1 == want { print $NF }' 2>/dev/null) || candidates=
  found=; count=0
  while IFS= read -r id || [ -n "$id" ]; do
    [ -n "$id" ] || continue
    fm_opencode_workspace_id_valid "$id" || continue
    if [ "$count" -eq 0 ]; then
      found=$id
    elif [ "$found" != "$id" ]; then
      echo "error: main OpenCode workspace discovery found ambiguous workspace rows; refusing to guess" >&2
      return 1
    fi
    count=$((count + 1))
  done <<EOF
$candidates
EOF
  [ "$count" -gt 0 ] || {
    echo "error: main OpenCode workspace discovery found no workspace named 'main'; refusing to guess" >&2
    return 1
  }
  printf '%s\n' "$found"
}

# fm_opencode_main_org_ensure [config-dir]: print the main workspace id,
# discovering it from the console login on first use. A valid id file wins;
# otherwise the id is read from `opencode console orgs` and recorded alone in
# the file for later launches, so discovery runs once, not on every launch.
# Anything undiscoverable is refused rather than guessed at.
fm_opencode_main_org_ensure() {  # [config-dir]
  local config_dir=${1:-} file id tmp
  config_dir=$(fm_opencode_second_config_dir "$config_dir") || return 1
  if id=$(fm_opencode_workspace_org "$FM_OPENCODE_WORKSPACE_MAIN" "$config_dir" 2>/dev/null); then
    printf '%s\n' "$id"
    return 0
  fi
  id=$(fm_opencode_main_org_discover) || return 1
  file="$config_dir/opencode-main-org"
  [ -d "$config_dir" ] || {
    echo "error: config directory is missing: $config_dir" >&2
    return 1
  }
  tmp="$file.tmp.$$"
  rm -f "$tmp" 2>/dev/null
  if ! printf '%s' "$id" > "$tmp" 2>/dev/null; then
    rm -f "$tmp" 2>/dev/null
    echo "error: main OpenCode workspace id could not be recorded" >&2
    return 1
  fi
  if ! mv -f "$tmp" "$file" 2>/dev/null; then
    rm -f "$tmp" 2>/dev/null
    echo "error: main OpenCode workspace id could not be recorded" >&2
    return 1
  fi
  printf '%s\n' "$id"
}

# fm_opencode_workspace_db_file <workspace> [config-dir]: path of the pin db.
fm_opencode_workspace_db_file() {  # <workspace> [config-dir]
  local workspace=${1:-} infix config_dir
  infix=$(fm_opencode_workspace_infix "$workspace") || return 1
  config_dir=$(fm_opencode_second_config_dir "${2:-}") || return 1
  printf '%s/opencode-%s.db\n' "$config_dir" "$infix"
}

# fm_opencode_second_db_file [config-dir]: path of the pinned database.
fm_opencode_second_db_file() {  # [config-dir]
  fm_opencode_workspace_db_file "$FM_OPENCODE_WORKSPACE_SECOND" "${1:-}"
}

# fm_opencode_main_db_file [config-dir]: path of the main pinned database.
fm_opencode_main_db_file() {  # [config-dir]
  fm_opencode_workspace_db_file "$FM_OPENCODE_WORKSPACE_MAIN" "${1:-}"
}

# fm_opencode_same_file <a> <b>: 0 when both paths name the same file.
# String equality covers a pin that does not exist yet; -ef follows symlinks
# once both exist. Anything unresolvable reads as different, so a missing pin
# never blocks on this guard.
fm_opencode_same_file() {  # <path-a> <path-b>
  [ -n "${1:-}" ] && [ -n "${2:-}" ] || return 1
  [ "$1" = "$2" ] && return 0
  [ -e "$1" ] && [ -e "$2" ] || return 1
  [ "$1" -ef "$2" ] 2>/dev/null
}

# fm_opencode_second_live_db: the machine's own console database opencode
# itself reads, resolved the way `opencode debug paths` reports it.
# FM_OPENCODE_LIVE_DB names that database outright. When it is set, the
# lookup never falls back to the machine's own data directory: a caller that
# names a database gets that database or nothing, so a test or a scratch
# home can never reach the account of the person running the machine.
fm_opencode_second_live_db() {
  local data_dir line
  if [ -n "${FM_OPENCODE_LIVE_DB:-}" ]; then
    [ -f "$FM_OPENCODE_LIVE_DB" ] || return 1
    printf '%s\n' "$FM_OPENCODE_LIVE_DB"
    return 0
  fi
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

# fm_opencode_db_backup <source-db> <destination-db>: take a consistent
# SQLite online backup, including WAL contents, without copying a live file.
fm_opencode_db_backup() {  # <source-db> <destination-db>
  local source=${1:-} destination=${2:-} escaped
  [ -n "$source" ] && [ -n "$destination" ] && [ -f "$source" ] || return 1
  command -v sqlite3 >/dev/null 2>&1 || return 1
  escaped=$(printf '%s' "$destination" | sed "s/'/''/g") || return 1
  sqlite3 "$source" ".backup '$escaped'" >/dev/null 2>&1
}

# fm_opencode_unpinned_seed <destination-db> [config-dir]: seed a standalone
# task database from a verified main pin, or from a consistent copy of the
# machine database. A missing login source leaves initialization to OpenCode.
fm_opencode_unpinned_seed() {  # <destination-db> [config-dir]
  local destination=${1:-} config_dir=${2:-} main_db live source tmp
  [ -n "$destination" ] || return 1
  config_dir=$(fm_opencode_second_config_dir "${config_dir:-}") || return 1
  main_db=$(fm_opencode_main_db_file "$config_dir") || return 1
  if [ -f "$main_db" ] && fm_opencode_main_verify "$main_db"; then
    source=$main_db
  else
    live=$(fm_opencode_second_live_db 2>/dev/null) || {
      : > "$destination"
      return $?
    }
    source=$live
  fi
  tmp="$destination.tmp.$$"
  rm -f "$tmp" || return 1
  if ! fm_opencode_db_backup "$source" "$tmp" || ! mv "$tmp" "$destination"; then
    rm -f "$tmp"
    return 1
  fi
  return 0
}

# fm_opencode_db_has_console_login <db>: 0 when the database contains an
# OpenCode console access token, as required by the free Minimax provider.
fm_opencode_db_has_console_login() {  # <db>
  local count
  [ -f "${1:-}" ] && command -v sqlite3 >/dev/null 2>&1 || return 1
  count=$(sqlite3 "$1" 'SELECT COUNT(*) FROM account WHERE access_token <> "";' 2>/dev/null) || return 1
  [ "${count:-0}" -gt 0 ] 2>/dev/null
}

# fm_opencode_attempt_count [override] [default]: the bounded retry count.
# Non-numeric and empty overrides read as the default, so a bad env value
# cannot disable the bound or spin.
fm_opencode_attempt_count() {  # [override] [default]
  local raw=${1:-} def=${2:-3}
  case "$def" in ''|*[!0-9]*) def=3 ;; esac
  case "$raw" in ''|*[!0-9]*) printf '%s\n' "$def"; return 0 ;; esac
  [ "$raw" -ge 1 ] 2>/dev/null || { printf '%s\n' "$def"; return 0; }
  printf '%s\n' "$raw"
}

# fm_opencode_retry_sleep <attempt-index>: back off between retries.
# FM_OPENCODE_VERIFY_SLEEP=0 (also no/off/skip) skips sleeping for tests.
fm_opencode_retry_sleep() {  # <attempt-index>
  local n=${1:-1}
  case "${FM_OPENCODE_VERIFY_SLEEP:-}" in 0|no|off|skip) return 0 ;; esac
  case "$n" in ''|*[!0-9]*) return 0 ;; esac
  [ "$n" -ge 1 ] 2>/dev/null || return 0
  sleep "$n" 2>/dev/null || true
}

# fm_opencode_config_name_once <db>: print the console provider name once,
# the one entry named "<workspace> / OpenCode". Other providers (a local model,
# for example) can be listed first and are never the workspace verdict.
# Fails when the file is absent, the CLI is missing, the command fails, or
# the output carries no console provider name: all of those are unreadable,
# never a workspace verdict.
fm_opencode_config_name_once() {  # <db>
  local db=${1:-} out name
  [ -n "$db" ] && [ -f "$db" ] || return 1
  command -v opencode >/dev/null 2>&1 || return 1
  out=$(OPENCODE_DB="$db" opencode debug config 2>/dev/null) || return 1
  [ -n "$out" ] || return 1
  name=$(printf '%s' "$out" | grep -m1 '"name": "[^"]* / OpenCode"' 2>/dev/null \
    | sed 's/.*"name": "\([^"]*\)".*/\1/' 2>/dev/null) || return 1
  [ -n "$name" ] || return 1
  printf '%s\n' "$name"
}

# fm_opencode_config_name <db>: print the provider name with bounded retry.
# A transient console fetch that fails under load reads here as unreadable
# and is retried; only a name that arrives is a verdict.
fm_opencode_config_name() {  # <db>
  local db=${1:-} attempts attempt name
  attempts=$(fm_opencode_attempt_count "${FM_OPENCODE_VERIFY_ATTEMPTS:-}" 3)
  attempt=1
  while [ "$attempt" -le "$attempts" ]; do
    if name=$(fm_opencode_config_name_once "$db" 2>/dev/null); then
      printf '%s\n' "$name"
      return 0
    fi
    if [ "$attempt" -lt "$attempts" ]; then
      fm_opencode_retry_sleep "$attempt"
    fi
    attempt=$((attempt + 1))
  done
  return 1
}

# fm_opencode_login_copy <live-db> <dest-db>: copy the console login rows
# from the live database into <dest-db>.
# Only the account and account_state tables are read, read-only and under
# the single read transaction `.dump` holds, so a live database mid-write in
# WAL mode cannot yield a half-copied pin and the rest of the live file (it
# reaches tens of gigabytes) is never copied. The rows are replayed as
# INSERT OR REPLACE. An empty dump (a transient lock that yielded nothing)
# fails rather than publishing a login-less pin. Returns 0 with the rows
# replayed, 1 otherwise; prints nothing, including no id.
fm_opencode_login_copy() {  # <live-db> <dest-db>
  local live=${1:-} dest=${2:-} dump attempts attempt copied
  [ -n "$live" ] && [ -n "$dest" ] || return 1
  [ -f "$live" ] || return 1
  command -v sqlite3 >/dev/null 2>&1 || return 1
  dump=$(mktemp "${TMPDIR:-/tmp}/fm-opencode-dump.XXXXXX" 2>/dev/null) || return 1
  attempts=$(fm_opencode_attempt_count "${FM_OPENCODE_COPY_ATTEMPTS:-${FM_OPENCODE_VERIFY_ATTEMPTS:-}}" 3)
  attempt=1; copied=0
  while [ "$attempt" -le "$attempts" ]; do
    if sqlite3 -readonly "$live" '.dump account account_state' 2>/dev/null \
      | grep '^INSERT' > "$dump" 2>/dev/null; then
      if [ -s "$dump" ] \
        && sed 's/^INSERT INTO /INSERT OR REPLACE INTO /' "$dump" 2>/dev/null \
          | sqlite3 "$dest" 2>/dev/null; then
        copied=1
        break
      fi
    fi
    if [ "$attempt" -lt "$attempts" ]; then
      fm_opencode_retry_sleep "$attempt"
    fi
    attempt=$((attempt + 1))
  done
  rm -f "$dump" 2>/dev/null
  [ "$copied" -eq 1 ]
}

# fm_opencode_prepare_db <db>: migrate a fresh pin database through opencode
# itself with bounded retry. An empty file is migrated on first load; a
# transient console fetch during migration retries rather than failing the
# build on one bad read.
fm_opencode_prepare_db() {  # <db>
  local db=${1:-} attempts attempt
  [ -n "$db" ] || return 1
  command -v opencode >/dev/null 2>&1 || return 1
  attempts=$(fm_opencode_attempt_count "${FM_OPENCODE_VERIFY_ATTEMPTS:-}" 3)
  attempt=1
  while [ "$attempt" -le "$attempts" ]; do
    if OPENCODE_DB="$db" opencode debug config >/dev/null 2>&1; then
      return 0
    fi
    if [ "$attempt" -lt "$attempts" ]; then
      fm_opencode_retry_sleep "$attempt"
    fi
    attempt=$((attempt + 1))
  done
  return 1
}

# fm_opencode_second_refresh <db> <live-db> <org>: re-copy the console
# login rows are re-copied from the live database's two login tables, read
# under one read-only transaction, on every pinned launch and a pin that will
# not refresh is discarded and rebuilt, so the pin cannot carry a stale
# token. An empty dump fails rather than publishing a login-less pin. Plain INSERT would
# collide with the rows already there, so the dump is replayed as
# INSERT OR REPLACE.
fm_opencode_second_refresh() {  # <db> <live-db> <org>
  local db=$1 live=$2 org=$3
  [ -n "$db" ] && [ -n "$live" ] && [ -n "$org" ] || return 1
  [ -f "$db" ] && [ -f "$live" ] || return 1
  # The pin is a copy: refreshing onto the live database would rewrite the
  # machine-wide active workspace behind every lane, so it is refused quiet
  # here while the database builder below refuses it loudly.
  fm_opencode_same_file "$db" "$live" && return 1
  fm_opencode_login_copy "$live" "$db" || return 1
  sqlite3 "$db" "UPDATE account_state SET active_org_id='$org' WHERE id=1;" 2>/dev/null || return 1
  [ "$(sqlite3 "$db" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null)" = "$org" ] || return 1
  [ "$(sqlite3 "$db" 'SELECT access_token FROM account LIMIT 1;' 2>/dev/null)" = "$(sqlite3 "$live" 'SELECT access_token FROM account LIMIT 1;' 2>/dev/null)" ] || return 1
  return 0
}

# fm_opencode_workspace_verify <db> <workspace>: prove the pinned database
# resolves <workspace> before a launch rides it. Returns 0 only when
# OPENCODE_DB=<db> opencode debug config resolves the opencode provider name
# to "<workspace> / OpenCode" across a bounded retry, 1 when it proves
# another workspace, and 2 when the workspace cannot be read at all: a
# drifted pin, a stale token falling back past the console config, or a
# console fetch that keeps failing under load. Callers refuse on 1 and 2
# alike but report them differently: proved-wrong names the workspace, while
# could-not-read never claims one.
fm_opencode_workspace_verify() {  # <db> <workspace>
  local db=$1 workspace=${2:-} name expected
  [ -n "$db" ] && [ -f "$db" ] || return 2
  case "$workspace" in
    "$FM_OPENCODE_WORKSPACE_MAIN"|"$FM_OPENCODE_WORKSPACE_SECOND") ;;
    *) return 2 ;;
  esac
  expected="$workspace / OpenCode"
  name=$(fm_opencode_config_name "$db" 2>/dev/null) || return 2
  [ "$name" = "$expected" ] || return 1
  return 0
}

# fm_opencode_second_verify <db> [workspace-name]: prove the pinned
# database resolves the secondary workspace before a launch rides it.
fm_opencode_second_verify() {  # <db> [workspace-name]
  if [ -n "${2:-}" ]; then
    fm_opencode_workspace_verify "$1" "$2"
  else
    fm_opencode_workspace_verify "$1" "$FM_OPENCODE_WORKSPACE_SECOND"
  fi
}

# fm_opencode_main_verify <db>: prove the pinned database resolves the main
# workspace before a launch rides it.
fm_opencode_main_verify() {  # <db>
  fm_opencode_workspace_verify "${1:-}" "$FM_OPENCODE_WORKSPACE_MAIN"
}

# fm_opencode_workspace_db_build <workspace> <org> [config-dir]: print the
# pinned database path, building it first when needed. Building migrates a
# fresh database through opencode itself (an empty file is migrated on first
# load; a hand-seeded schema collides with the migrator), then copies only
# the account rows from the live login, read-only, and pins the
# active workspace to the given id. An existing pin is never trusted: its
# login rows are re-copied from the live login on every call and a pin that
# will not refresh is discarded and rebuilt, so the pin cannot carry a stale
# token. Before the path is published the pin must prove its workspace: a
# launch that cannot prove it never starts, with a proved-wrong workspace
# refused as one and an unreadable workspace reported plainly as the other.
# Fails closed with the reason on stderr, never printing a workspace id.
fm_opencode_workspace_db_build_unlocked() {  # <workspace> <org> [config-dir]
  local workspace=${1:-} org=${2:-} config_dir=${3:-}
  local db live tmp old_umask got_name label infix verify_rc
  label=$(fm_opencode_workspace_label "$workspace") || return 1
  infix=$(fm_opencode_workspace_infix "$workspace") || return 1
  [ -n "$org" ] || {
    echo "error: $label OpenCode workspace is not configured" >&2
    return 1
  }
  [ -n "$config_dir" ] && [ -d "$config_dir" ] || {
    echo "error: config directory is missing: ${config_dir:-unknown}" >&2
    return 1
  }
  db="$config_dir/opencode-$infix.db"
  command -v sqlite3 >/dev/null 2>&1 || {
    echo "error: $label OpenCode workspace needs sqlite3 to pin its database" >&2
    return 1
  }
  # The CLI and the live login are needed on every pinned launch, not
  # only when building from scratch: the pin is refreshed and proven
  # below before any launch rides it.
  command -v opencode >/dev/null 2>&1 || {
    echo "error: $label OpenCode workspace needs the opencode CLI on PATH" >&2
    return 1
  }
  live=$(fm_opencode_second_live_db) || {
    echo "error: $label OpenCode workspace needs a console login on this machine; no readable opencode database found" >&2
    return 1
  }
  # The pin is a copy of the login, never the login itself: a pin path that
  # names the live database would rewrite the machine-wide active workspace
  # behind every lane (and a failed refresh would delete the login), so it is
  # refused before anything reads or writes either file.
  if fm_opencode_same_file "$db" "$live"; then
    echo "error: $label OpenCode workspace pin names the live console database; refusing to touch it" >&2
    return 1
  fi
  # A pin that will not refresh is quarantined and rebuilt below. Never
  # truncate the old inode: a reader may still have it open.
  if [ -f "$db" ] && ! fm_opencode_second_refresh "$db" "$live" "$org"; then
    mv "$db" "$db.corrupt.$(date +%s).$$" || {
      echo "error: $label OpenCode workspace could not quarantine its invalid pin" >&2
      return 1
    }
  fi
  if [ ! -f "$db" ]; then
    old_umask=$(umask)
    umask 077
    tmp="$db.tmp.$$"
    rm -f "$tmp"
    if ! fm_opencode_prepare_db "$tmp"; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: $label OpenCode workspace could not prepare its pinned database" >&2
      return 1
    fi
    if ! fm_opencode_login_copy "$live" "$tmp"; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: $label OpenCode workspace could not copy the console login into its pinned database" >&2
      return 1
    fi
    if ! sqlite3 "$tmp" "UPDATE account_state SET active_org_id='$org' WHERE id=1;" 2>/dev/null \
      || [ "$(sqlite3 "$tmp" 'SELECT active_org_id FROM account_state WHERE id=1;' 2>/dev/null)" != "$org" ]; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: $label OpenCode workspace could not pin its database to the configured workspace" >&2
      return 1
    fi
    chmod 600 "$tmp" 2>/dev/null || true
    if ! mv -f "$tmp" "$db" 2>/dev/null; then
      rm -f "$tmp"
      umask "$old_umask"
      echo "error: $label OpenCode workspace could not publish its pinned database" >&2
      return 1
    fi
    umask "$old_umask"
  fi
  fm_opencode_workspace_verify "$db" "$workspace"
  verify_rc=$?
  if [ "$verify_rc" -eq 0 ]; then
    printf '%s\n' "$db"
    return 0
  fi
  if [ "$verify_rc" -eq 1 ]; then
    got_name=$(fm_opencode_config_name_once "$db" 2>/dev/null \
      | sed 's/^ *//' 2>/dev/null)
    echo "error: $label OpenCode workspace pin resolves ${got_name:-an unknown workspace}, not '$workspace / OpenCode'; refusing the launch" >&2
    return 1
  fi
  echo "error: $label OpenCode workspace pin could not be verified: the workspace could not be read; refusing the launch" >&2
  return 1
}

# Run a workspace operation under a per-pin advisory lock. lockf is available
# on macOS and BSD; flock is the Linux equivalent. Fail closed without either.
fm_opencode_workspace_locked() {  # <workspace> <config-dir> <function> <args...>
  local workspace=$1 config_dir=$2 fn=$3 infix lock lib
  shift 3
  infix=$(fm_opencode_workspace_infix "$workspace") || return 1
  [ -d "$config_dir" ] || return 1
  lock="$config_dir/opencode-$infix.db.lock"
  lib="$_FM_OPENCODE_SECOND_LIB_DIR/$(basename "${BASH_SOURCE[0]}")"
  if command -v lockf >/dev/null 2>&1; then
    lockf -t 120 "$lock" bash -c '. "$1"; shift; "$@"' _ "$lib" "$fn" "$workspace" "$@"
  elif command -v flock >/dev/null 2>&1; then
    flock -w 120 "$lock" bash -c '. "$1"; shift; "$@"' _ "$lib" "$fn" "$workspace" "$@"
  else
    echo "error: OpenCode workspace pin requires lockf or flock for concurrency safety" >&2
    return 1
  fi
}

# fm_opencode_workspace_db_build <workspace> <org> [config-dir]: serialized
# public entry point. Builders for the same workspace/config directory share
# one lock and therefore cannot refresh, quarantine, or publish over one
# another.
fm_opencode_workspace_db_build() {  # <workspace> <org> [config-dir]
  local config_dir=${3:-}
  [ -n "$config_dir" ] || config_dir=$(fm_opencode_second_config_dir) || return 1
  fm_opencode_workspace_locked "$1" "$config_dir" fm_opencode_workspace_db_build_unlocked "$2" "$config_dir"
}

# Build and verify the shared pin, then give one task a private writable copy.
# The private path lives in state and survives relaunches of the same task.
fm_opencode_workspace_db_for_task_unlocked() {  # <workspace> <org> <config-dir> <task-id> <state-dir>
  local workspace=$1 org=$2 config_dir=$3 task_id=$4 state_dir=$5 pin private
  case "$task_id" in ''|*[!A-Za-z0-9._-]*) return 1 ;; esac
  [ -n "$state_dir" ] && [ -d "$state_dir" ] || return 1
  pin=$(fm_opencode_workspace_db_build_unlocked "$workspace" "$org" "$config_dir") || return 1
  private="$state_dir/$task_id.opencode.db"
  if [ ! -e "$private" ]; then
    sqlite3 "$pin" 'PRAGMA wal_checkpoint(TRUNCATE);' >/dev/null 2>&1 || return 1
    cp "$pin" "$private" || return 1
    chmod 600 "$private" 2>/dev/null || true
  else
    # Preserve this task's session tables while refreshing just the login
    # rows from the freshly verified shared pin before a relaunch.
    fm_opencode_login_copy "$pin" "$private" || return 1
    sqlite3 "$private" "UPDATE account_state SET active_org_id='$org' WHERE id=1;" \
      >/dev/null 2>&1 || return 1
  fi
  fm_opencode_workspace_verify "$private" "$workspace" || return 1
  printf '%s\n' "$private"
}

fm_opencode_workspace_db_for_task() {  # <workspace> <org> <config-dir> <task-id> <state-dir>
  local workspace=$1 org=$2 config_dir=$3 task_id=$4 state_dir=$5
  fm_opencode_workspace_locked "$workspace" "$config_dir" fm_opencode_workspace_db_for_task_unlocked \
    "$org" "$config_dir" "$task_id" "$state_dir"
}

# fm_opencode_second_db [config-dir]: print the pinned database path,
# building it first when needed. Before the path is published the pin must
# prove its workspace: a launch that cannot prove it never starts. Fails
# closed with the reason on stderr.
fm_opencode_second_db() {  # [config-dir]
  local config_dir=${1:-} org
  config_dir=$(fm_opencode_second_config_dir "${config_dir:-}") || {
    echo "error: second OpenCode workspace is not configured" >&2
    return 1
  }
  org=$(fm_opencode_workspace_org "$FM_OPENCODE_WORKSPACE_SECOND" "$config_dir") || {
    echo "error: $config_dir/opencode-second-org is missing or malformed; it must hold the second workspace id alone" >&2
    return 1
  }
  fm_opencode_workspace_db_build "$FM_OPENCODE_WORKSPACE_SECOND" "$org" "$config_dir"
}

# fm_opencode_main_db [config-dir]: print the main pinned database path,
# building it first when needed. The main id is discovered from the console
# login on first use when no file holds it. Before the path is published the
# pin must prove it resolves the main workspace: a launch that cannot prove
# it never starts. Fails closed with the reason on stderr.
fm_opencode_main_db() {  # [config-dir]
  local config_dir=${1:-} org
  config_dir=$(fm_opencode_second_config_dir "${config_dir:-}") || {
    echo "error: main OpenCode workspace is not configured" >&2
    return 1
  }
  org=$(fm_opencode_main_org_ensure "$config_dir") || return 1
  fm_opencode_workspace_db_build "$FM_OPENCODE_WORKSPACE_MAIN" "$org" "$config_dir"
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
