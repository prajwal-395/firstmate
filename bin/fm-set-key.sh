#!/usr/bin/env bash
# fm-set-key.sh - store one opt-in secret in $FM_HOME/.env without exposing it.
#
# Usage:
#   fm-set-key.sh                        pick a key, then paste its value at the prompt
#   fm-set-key.sh <KEY>                  prompt for that key's value
#   fm-set-key.sh <KEY> --remove         delete every assignment of that key
#   fm-set-key.sh --status               list known keys with a masked fingerprint
#   fm-set-key.sh <KEY> --no-verify      store without the live credential check
#
# The value is NEVER accepted as an argument. It is read from the terminal with
# echo off, or from stdin when this is not a terminal, so it never reaches argv,
# the process table, or shell history. It is held in one variable, written once,
# and printed only as a masked fingerprint (length plus last four characters).
#
# Keys this script will store, each the opt-in gate for one feature:
#   AI_GATEWAY_API_KEY  Vercel AI Gateway, the free first rung of the Jev ladder
#   TYPESAFE_API_KEY    typesafe.ai directly, the paid fallback rung
#   FMX_PAIRING_TOKEN   Relay public-mention integration (section 14)
# An unknown name is refused rather than written, because a typo in a key name
# is indistinguishable from an absent key at read time and fails silently.
#
# The file is created mode 0600 and rewritten atomically through a temporary
# file in the same directory, removed on every exit path so an interrupted write
# cannot leave a key-bearing file behind. $FM_HOME/.env and its siblings
# (.env.*) are gitignored, so neither the file nor a temporary can be committed.
#
# Verification (default on for the two dispatch keys) makes one live request to
# that key's own rung and reports what the service said. The key reaches curl as
# a header on a file descriptor, never on argv, the same way
# bin/fm-dispatch-resolve.sh sends it. Nothing about the response is stored.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-$FM_ROOT}"
ENV_FILE="$FM_HOME/.env"

# shellcheck source=bin/fm-env-lib.sh
. "$FM_ROOT/bin/fm-env-lib.sh"

VERIFY_TIMEOUT=${FM_SET_KEY_TIMEOUT:-25}

KNOWN_KEYS="AI_GATEWAY_API_KEY TYPESAFE_API_KEY FMX_PAIRING_TOKEN"

usage() {
  sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
}

die() { printf 'fm-set-key: %s\n' "$*" >&2; exit 2; }

key_is_known() { # <name>
  local k
  for k in $KNOWN_KEYS; do [ "$k" = "$1" ] && return 0; done
  return 1
}

key_label() { # <name>
  case "$1" in
    AI_GATEWAY_API_KEY) printf 'Vercel AI Gateway (free Jev rung)' ;;
    TYPESAFE_API_KEY) printf 'typesafe.ai (paid Jev fallback rung)' ;;
    FMX_PAIRING_TOKEN) printf 'Relay pairing token' ;;
    *) printf '%s' "$1" ;;
  esac
}

# mask <value>: a fingerprint that identifies the value without revealing it.
mask() {
  local v=$1 n=${#1}
  if [ "$n" -le 8 ]; then
    printf '%d chars, ending ****' "$n"
  else
    printf '%d chars, ending %s' "$n" "${v: -4}"
  fi
}

# env_write <key> <value>: replace every assignment of <key> with exactly one,
# atomically, leaving every other line byte-identical.
env_write() {
  local key=$1 value=$2 tmp quoted
  # fmx_env_get is a parser, not a shell: it strips ONE layer of matching outer
  # quotes and interprets no escapes. So backslash-escaping an inner quote does
  # not round-trip - it reads back literally. Pick a quote character the value
  # does not itself contain, and refuse rather than store something that would
  # read back altered. No API key or token contains a quote character.
  case "$value" in
    *\'*)
      case "$value" in
        *\"*) die 'the value contains both a single and a double quote, which this file format cannot represent; nothing written' ;;
        *) quoted="\"$value\"" ;;
      esac ;;
    *) quoted="'$value'" ;;
  esac
  ( umask 077
    tmp=$(mktemp "$ENV_FILE.XXXXXX") || exit 1
    # The temporary holds the secret until the rename. Remove it on ANY exit
    # path, so an interrupted write never leaves a key-bearing file behind.
    trap 'rm -f "$tmp"' EXIT HUP INT TERM
    if [ -f "$ENV_FILE" ]; then
      grep -vE "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
      # Keep a trailing newline so the appended line is never glued to the last.
      [ -s "$tmp" ] && [ "$(tail -c1 "$tmp" | wc -l)" -eq 0 ] && printf '\n' >> "$tmp"
    fi
    printf '%s=%s\n' "$key" "$quoted" >> "$tmp"
    chmod 600 "$tmp"
    mv -f "$tmp" "$ENV_FILE"
  ) || die "could not write $ENV_FILE"
  chmod 600 "$ENV_FILE" 2>/dev/null || true
}

env_remove() { # <key>
  local key=$1 tmp
  [ -f "$ENV_FILE" ] || { printf 'fm-set-key: %s holds no keys yet.\n' "$ENV_FILE"; return 0; }
  ( umask 077
    tmp=$(mktemp "$ENV_FILE.XXXXXX") || exit 1
    trap 'rm -f "$tmp"' EXIT HUP INT TERM
    grep -vE "^[[:space:]]*(export[[:space:]]+)?${key}=" "$ENV_FILE" > "$tmp" 2>/dev/null || true
    chmod 600 "$tmp"
    mv -f "$tmp" "$ENV_FILE"
  ) || die "could not write $ENV_FILE"
  printf 'fm-set-key: %s removed from %s.\n' "$key" "$ENV_FILE"
}

status() {
  local k v
  printf 'fm-set-key: %s\n' "$ENV_FILE"
  if [ ! -f "$ENV_FILE" ]; then
    printf '  (absent - no opt-in secrets stored in this home)\n'
    return 0
  fi
  printf '  permissions: %s\n' "$(stat -f '%Sp' "$ENV_FILE" 2>/dev/null || stat -c '%A' "$ENV_FILE" 2>/dev/null)"
  for k in $KNOWN_KEYS; do
    v=$(fmx_env_get "$k" "$ENV_FILE")
    if [ -n "$v" ]; then
      printf '  %-19s set    (%s)  %s\n' "$k" "$(mask "$v")" "$(key_label "$k")"
    else
      printf '  %-19s absent                       %s\n' "$k" "$(key_label "$k")"
    fi
  done
}

# verify <key-name> <value>: one live request to that key's own rung.
verify() {
  local key=$1 value=$2 base model http resp

  case "$key" in
    AI_GATEWAY_API_KEY) base=https://ai-gateway.vercel.sh/typesafe; model=typesafe-ai/jev ;;
    TYPESAFE_API_KEY) base=https://api.typesafe.ai; model=jev-latest ;;
    *) return 0 ;;
  esac

  command -v curl >/dev/null 2>&1 || { printf '  check skipped: curl is not installed.\n'; return 0; }

  resp=$(mktemp) || return 0
  http=$(
    cat <<EOF | curl -sS --max-time "$VERIFY_TIMEOUT" -o "$resp" -w '%{http_code}' \
      -X POST "$base/v1/systemone" -H 'Content-Type: application/json' \
      -H @/dev/fd/3 3< <(printf 'Authorization: Bearer %s\n' "$value") \
      --data-binary @- 2>/dev/null
{
  "model": "$model",
  "state": {"task": {"project": "firstmate", "brief": "Credential check. Answer with the option named ok."}},
  "questions": {
    "rule": {
      "type": "choice",
      "instructions": "Pick the option named ok.",
      "criteria": {"ok": "always pick this one", "other": "never pick this one"}
    }
  }
}
EOF
  ) || http=000

  case "$http" in
    200)
      printf '  checked: the key works. %s answered this request.\n' "$(key_label "$key")" ;;
    401|403)
      printf '  CHECKED AND REFUSED (HTTP %s): the service rejected this key.\n' "$http"
      printf '  The value is stored, but it will not authenticate. Re-run to replace it.\n' ;;
    429)
      printf '  checked: the key is valid and currently rate limited (HTTP 429).\n'
      printf '  That is the free quota answering; the ladder falls through to the paid rung.\n' ;;
    000)
      printf '  check inconclusive: no response within %ss (network, DNS, or the service).\n' "$VERIFY_TIMEOUT"
      printf '  The value is stored. Re-run with --status, or try again later.\n' ;;
    *)
      printf '  check inconclusive: the service answered HTTP %s.\n' "$http"
      printf '  The value is stored. Nothing about the response was kept.\n' ;;
  esac
  rm -f "$resp"
}

choose_key() {
  local i=1 k choice
  printf 'Which key are you setting?\n\n' >&2
  for k in $KNOWN_KEYS; do
    printf '  %d) %-19s %s\n' "$i" "$k" "$(key_label "$k")" >&2
    i=$((i + 1))
  done
  printf '\nNumber: ' >&2
  read -r choice </dev/tty || die 'no selection'
  case "$choice" in
    1) printf 'AI_GATEWAY_API_KEY' ;;
    2) printf 'TYPESAFE_API_KEY' ;;
    3) printf 'FMX_PAIRING_TOKEN' ;;
    *) die "not one of the offered numbers: $choice" ;;
  esac
}

main() {
  local key='' do_remove=0 do_verify=1 arg value

  for arg in "$@"; do
    case "$arg" in
      -h|--help) usage; exit 0 ;;
      --status) status; exit 0 ;;
      --remove) do_remove=1 ;;
      --no-verify) do_verify=0 ;;
      -*) die "unknown option: $arg" ;;
      *)
        [ -z "$key" ] || die 'pass at most one key name'
        # A value on argv would be visible in the process table and the shell
        # history, so a second bare argument is always a mistake worth refusing.
        key=$arg ;;
    esac
  done

  if [ -n "$key" ] && ! key_is_known "$key"; then
    printf 'fm-set-key: %s is not a key this script stores.\n' "$key" >&2
    printf 'Known keys: %s\n' "$KNOWN_KEYS" >&2
    exit 2
  fi

  if [ "$do_remove" -eq 1 ]; then
    [ -n "$key" ] || die '--remove needs a key name'
    env_remove "$key"
    exit 0
  fi

  [ -n "$key" ] || key=$(choose_key)

  printf '\nSetting %s - %s\n' "$key" "$(key_label "$key")" >&2
  printf 'Paste the value and press return. It will not be shown.\n' >&2
  printf 'Value: ' >&2

  if [ -t 0 ]; then
    read -rs value </dev/tty || die 'no value read'
    printf '\n' >&2
  else
    # Piped input: one line, still never on argv.
    IFS= read -r value || die 'no value on stdin'
  fi

  # Trim surrounding whitespace, which a paste commonly carries.
  value=${value#"${value%%[![:space:]]*}"}
  value=${value%"${value##*[![:space:]]}"}

  [ -n "$value" ] || die 'empty value; nothing written'

  case "$value" in
    *[![:print:]]*) die 'the value contains a control character; nothing written' ;;
  esac

  env_write "$key" "$value"
  printf '\nStored %s in %s (%s).\n' "$key" "$ENV_FILE" "$(mask "$value")"

  if [ "$do_verify" -eq 1 ]; then
    verify "$key" "$value"
  fi

  value=''
  printf '\nRun %s --status to see what this home holds.\n' "$(basename "$0")"
}

main "$@"
