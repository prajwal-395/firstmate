#!/usr/bin/env bash
# fm-model-catalog-refresh.sh - refresh the main home's available model catalog.
# Usage: FM_HOME=<main-home> bin/fm-model-catalog-refresh.sh
#
# Reads `opencode models` from the main-home operator environment, marks models
# no longer listed as retired, and preserves any existing replaced_by links.
# The resulting local config/model-catalog.json is inherited by secondmates.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_HOME=${FM_HOME:-}
[ -n "$FM_HOME" ] || { echo 'error: FM_HOME must name the main Firstmate home' >&2; exit 2; }
CONFIG=${FM_CONFIG_OVERRIDE:-$FM_HOME/config}
command -v jq >/dev/null 2>&1 || { echo 'error: jq is required' >&2; exit 2; }
command -v opencode >/dev/null 2>&1 || { echo 'error: opencode is not installed' >&2; exit 2; }
command -v codex >/dev/null 2>&1 || { echo 'error: codex is not installed' >&2; exit 2; }
mkdir -p "$CONFIG" || { echo "error: cannot create config directory $CONFIG" >&2; exit 1; }
target="$CONFIG/model-catalog.json"
old=$(mktemp "${TMPDIR:-/tmp}/fm-model-catalog-old.XXXXXX") || exit 1
listing=$(mktemp "${TMPDIR:-/tmp}/fm-model-catalog-list.XXXXXX") || { rm -f "$old"; exit 1; }
next=$(mktemp "$CONFIG/.model-catalog.XXXXXX") || { rm -f "$old" "$listing"; exit 1; }
codex_models=$(mktemp "${TMPDIR:-/tmp}/fm-model-catalog-codex.XXXXXX") || { rm -f "$old" "$listing" "$next"; exit 1; }
ids_file=$(mktemp "${TMPDIR:-/tmp}/fm-model-catalog-ids.XXXXXX") || { rm -f "$old" "$listing" "$next" "$codex_models"; exit 1; }
trap 'rm -f "$old" "$listing" "$next" "$codex_models" "$ids_file"' EXIT
if [ -e "$target" ]; then
  [ -f "$target" ] && [ ! -L "$target" ] || { echo "error: refusing non-regular catalog $target" >&2; exit 1; }
  jq -e '.models | type == "array"' "$target" >/dev/null 2>&1 || { echo "error: malformed catalog $target" >&2; exit 1; }
  cp "$target" "$old" || exit 1
else
  printf '{"models":[]}\n' > "$old"
fi
if ! FM_HOME="$FM_HOME" opencode models > "$listing"; then
  echo 'error: opencode models failed; catalog was not changed' >&2
  exit 1
fi
if ! codex debug models > "$codex_models"; then
  echo 'error: codex debug models failed; catalog was not changed' >&2
  exit 1
fi
jq -e '.models | type == "array" and all(.[]; (.slug | type) == "string")' "$codex_models" >/dev/null 2>&1 || {
  echo 'error: codex debug models returned an invalid catalog; catalog was not changed' >&2
  exit 1
}
jq -n --rawfile listing "$listing" --slurpfile codex "$codex_models" '
  (($listing | split("\n") | map(gsub("^[[:space:]]+|[[:space:]]+$"; "")) | map(select(test("^[a-z0-9][a-z0-9-]*/[^[:space:]]+$"))) | map({id:.,source:"opencode models"}))
   + ($codex[0].models | map({id:.slug,source:"codex debug models"})))
  | unique_by(.id) | sort_by(.id)
' > "$ids_file" || exit 1
if [ "$(jq 'length' "$ids_file")" -eq 0 ]; then
  echo 'error: opencode models returned no parseable provider/model ids; catalog was not changed' >&2
  exit 1
fi
jq --slurpfile old "$old" --slurpfile ids "$ids_file" --arg date "$(date +%F)" '
  ($ids[0]) as $listed |
  ($listed | map(.id)) as $available |
  ($old[0].models // []) as $previous |
  (([$listed[] | {id:.id,status:"active",bills:(if (.id|startswith("opencode-go/")) then "go" else "none" end),last_verified:$date,source:.source}])
   + [$previous[] | select(.id as $id | ($available | index($id)) == null) |
      .status="retired" | .last_verified=$date])
  | group_by(.id) | map(.[-1]) | sort_by(.id) | {models:.}
' "$old" > "$next" || exit 1
chmod 600 "$next" || exit 1
mv "$next" "$target" || exit 1
echo "updated $target"
