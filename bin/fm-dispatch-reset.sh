#!/usr/bin/env bash
# fm-dispatch-reset.sh - the shared exhaustion store for dispatch candidates.
# Usage: fm-dispatch-reset.sh <record|observe|clear|check|prune|sync-quota> <state-dir> ...
# Keys are provider/model/workspace tuples; reset timestamps are epoch ms.
set -u

cmd=${1:-}; state_dir=${2:-}
shift 2 2>/dev/null || { echo 'usage: fm-dispatch-reset.sh <record|observe|observe-file|clear|check|prune|sync-quota> <state-dir> ...' >&2; exit 2; }
[ -n "$cmd" ] && [ -n "$state_dir" ] || { echo 'usage: fm-dispatch-reset.sh <record|observe|observe-file|clear|check|prune|sync-quota> <state-dir> ...' >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo 'error: jq required' >&2; exit 2; }
mkdir -p "$state_dir" || exit 1
# Local secondmate homes share quota exhaustion with their parent. Resolve the
# durable parent binding rather than trusting an inherited environment value.
home=${FM_HOME:-$(cd "$(dirname "$state_dir")" && pwd)}
if [ "$(cd "$state_dir" 2>/dev/null && pwd -P)" = "$(cd "$home/state" 2>/dev/null && pwd -P)" ] \
  && [ -f "$home/.fm-secondmate-parent" ] && [ ! -L "$home/.fm-secondmate-parent" ]; then
  # shellcheck source=bin/fm-secondmate-parent-lib.sh
  . "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/fm-secondmate-parent-lib.sh"
  if fm_secondmate_parent_record_parse "$home/.fm-secondmate-parent" \
    && [ "$FM_SECONDMATE_PARENT_ROUTE" = local ]; then
    state_dir="$FM_SECONDMATE_PARENT_HOME/state"
  fi
fi
mkdir -p "$state_dir" || exit 1
file="$state_dir/dispatch-exhaustions.jsonl"
lock="$state_dir/.dispatch-exhaustions.lock"
key_ok() { [[ ${1:-} =~ ^[A-Za-z0-9][A-Za-z0-9._:/+-]{0,254}$ ]]; }
require_key() {
  if ! key_ok "$1" || ! key_ok "$2" || ! key_ok "$3"; then
    echo 'error: invalid dispatch reset key' >&2
    exit 2
  fi
}
# OpenCode reports a refusal with the bare model id, while dispatch metadata
# stores the provider-qualified id. Keep one key form in the store and accept
# either spelling at every public key boundary.
normalize_model() {
  if [ "$1" = opencode ] && [[ ${2:-} == opencode/* && ${2#opencode/} != '' ]]; then
    printf '%s' "${2#opencode/}"
  else
    printf '%s' "${2:-}"
  fi
}
lock_acquire() {
  local i=0
  until mkdir "$lock" 2>/dev/null; do
    i=$((i + 1)); [ "$i" -lt 300 ] || { echo 'error: timed out acquiring dispatch exhaustion lock' >&2; return 1; }
    sleep 0.1
  done
  trap 'rmdir "$lock" 2>/dev/null || true' EXIT HUP INT TERM
}
now_ms() { printf '%s\n' "$(( $(date +%s) * 1000 ))"; }
valid_store() {
  [ ! -e "$file" ] || jq -e -s 'all(.[]; type == "object" and .version == 1 and (.provider | type == "string") and (.model | type == "string") and (.workspace | type == "string") and (.reset_ms | type == "number") and (.observed_ms | type == "number"))' "$file" >/dev/null 2>&1
}
case "$cmd" in
  observe|observe-file)
    [ "$#" -eq 6 ] || { echo 'usage: fm-dispatch-reset.sh observe <state-dir> <policy.json> <provider> <model> <workspace> <reset-ms|-> <error-text>; observe-file uses <error-file> in place of error-text' >&2; exit 2; }
    policy=$1 provider=$2 model=$3 workspace=$4 reset=$5 evidence=$6
    [ -r "$policy" ] || { echo 'error: dispatch policy must be readable' >&2; exit 2; }
    require_key "$provider" "$model" "$workspace"
    if [ "$cmd" = observe-file ]; then
      [ -r "$evidence" ] || { echo 'error: refusal evidence file must be readable' >&2; exit 2; }
      error_file=$evidence
      evidence='provider-refusal'
    else
      error_file=$(mktemp "${TMPDIR:-/tmp}/fm-dispatch-refusal.XXXXXX") || exit 1
      trap 'rm -f "$error_file"' EXIT
      printf '%s\n' "$evidence" > "$error_file" || exit 1
      evidence='provider-refusal'
    fi
    matched_reset=
    while IFS=$'\t' read -r pattern strategy; do
      [ -n "$pattern" ] || continue
      if grep -Eiq -- "$pattern" "$error_file"; then matched_reset=$strategy; break; fi
    done < <(jq -r --arg provider "$provider" '.providers[]? | select(.id == $provider) | .refusal_patterns[]? | [.pattern,.reset] | @tsv' "$policy")
    [ -n "$matched_reset" ] || { echo 'error: refusal does not match configured provider patterns' >&2; exit 1; }
    if [ "$reset" = - ]; then
      case "$matched_reset" in *next-utc-day*|*retry-after-iso8601*)
        reset= ;;
        *) echo 'error: configured refusal reset needs a provider reset timestamp or quota-axi evidence' >&2; exit 1 ;;
      esac
      if [[ "$matched_reset" == *retry-after-iso8601* ]]; then
        reset_at=$(grep -Eo '20[0-9]{2}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z' "$error_file" | tail -n 1)
        if [ -n "$reset_at" ]; then
          reset_seconds=$(jq -nr --arg value "$reset_at" 'try ($value | fromdateiso8601) catch empty' 2>/dev/null)
          [[ "$reset_seconds" =~ ^[0-9]+$ ]] && [ "$((reset_seconds * 1000))" -gt "$(now_ms)" ] \
            || { echo 'error: provider reset timestamp is invalid or already expired' >&2; exit 1; }
          reset=$((reset_seconds * 1000))
        fi
      fi
      if [ -z "$reset" ] && [[ "$matched_reset" == *next-utc-day* ]]; then
        now=$(date +%s)
        reset=$(( (now / 86400 + 1) * 86400 * 1000 ))
      fi
      [ -n "$reset" ] || { echo 'error: provider refusal did not include its configured reset timestamp' >&2; exit 1; }
    fi
    if [ "$cmd" = observe ]; then rm -f "$error_file"; fi
    "$0" record "$state_dir" "$provider" "$model" "$workspace" "$reset" "$evidence"
    ;;
  sync-quota)
    [ "$#" -eq 2 ] || { echo 'usage: fm-dispatch-reset.sh sync-quota <state-dir> <policy.json> <quota.json>' >&2; exit 2; }
    policy=$1 quota=$2
    [ -r "$policy" ] && [ -r "$quota" ] || { echo 'error: policy and quota snapshot must be readable' >&2; exit 2; }
    command -v jq >/dev/null 2>&1 || exit 2
    rows=$(jq -cr --slurpfile quota "$quota" '
      [(.default_ladder[]?, .rules[]?.ladder[]?) as $r
        | (($r.accounts // ["default"])[]) as $workspace
        | ($quota[0].providers[]? | select(.provider == $r.provider and ((.accountKey // "default") == $workspace))) as $provider
        | ($provider.quotaSemantics.effectiveAvailability[]? | select(
            (.runway.status // "") == "exhausted_now"
            or (.effectivePercentRemaining | type) == "number" and .effectivePercentRemaining <= 0
          )) as $availability
        | ($availability.resetsAt // ([.limitingWindowIds[]? as $id | $provider.windows[]? | select(.id == $id) | .resetsAt] | map(select(type == "string")) | first)) as $reset
        | (try ($reset | fromdateiso8601 * 1000) catch null) as $reset_ms
        | select(($reset_ms | type) == "number")
        | {provider:$r.provider,model:$r.model,workspace:$workspace,reset_ms:$reset_ms,evidence:"quota-axi"}]
      | unique_by([.provider,.model,.workspace])[]
      | [.provider,.model,.workspace,(.reset_ms|tostring),.evidence] | @tsv
    ' "$policy" 2>/dev/null) || { echo 'error: could not derive generic reset records from quota-axi' >&2; exit 1; }
    while IFS=$'\t' read -r provider model workspace reset_ms evidence; do
      [ -n "$provider" ] || continue
      "$0" record "$state_dir" "$provider" "$model" "$workspace" "$reset_ms" "$evidence" || exit 1
    done <<<"$rows"
    ;;
  record)
    [ "$#" -eq 5 ] || { echo 'usage: fm-dispatch-reset.sh record <state-dir> <provider> <model> <workspace> <reset-ms> <evidence>' >&2; exit 2; }
    provider=$1 model=$2 workspace=$3 reset=$4 evidence=$5
    require_key "$provider" "$model" "$workspace"
    model=$(normalize_model "$provider" "$model")
    [[ $reset =~ ^[0-9]+$ ]] || { echo 'error: reset-ms must be a non-negative integer' >&2; exit 2; }
    valid_store || { echo "error: malformed exhaustion store $file" >&2; exit 1; }
    lock_acquire || exit 1
    now=$(now_ms)
    tmp="$file.tmp.$$"
    if [ -e "$file" ]; then
      jq -c --arg provider "$provider" --arg model "$model" --arg workspace "$workspace" \
        'def model_key($p; $m): if $p == "opencode" then ($m | sub("^opencode/"; "")) else $m end;
         select(.provider != $provider or model_key(.provider; .model) != model_key($provider; $model) or .workspace != $workspace)' "$file" > "$tmp" || exit 1
    else
      : > "$tmp" || exit 1
    fi
    if ! jq -cn --arg provider "$provider" --arg model "$model" --arg workspace "$workspace" --argjson reset "$reset" --argjson now "$now" --arg evidence "$evidence" \
      '{version:1,provider:$provider,model:$model,workspace:$workspace,reset_ms:$reset,observed_ms:$now,evidence:$evidence}' >> "$tmp" \
      || ! chmod 600 "$tmp" || ! mv "$tmp" "$file"; then
      rm -f "$tmp"
      exit 1
    fi
    ;;
  clear)
    [ "$#" -eq 3 ] || { echo 'usage: fm-dispatch-reset.sh clear <state-dir> <provider> <model> <workspace>' >&2; exit 2; }
    provider=$1 model=$2 workspace=$3
    require_key "$provider" "$model" "$workspace"
    model=$(normalize_model "$provider" "$model")
    valid_store || { echo "error: malformed exhaustion store $file" >&2; exit 1; }
    lock_acquire || exit 1
    [ ! -e "$file" ] || jq -c --arg provider "$provider" --arg model "$model" --arg workspace "$workspace" '
      def model_key($p; $m): if $p == "opencode" then ($m | sub("^opencode/"; "")) else $m end;
      select(.provider != $provider or model_key(.provider; .model) != model_key($provider; $model) or .workspace != $workspace)
    ' "$file" > "$file.tmp.$$" || exit 1
    [ ! -e "$file" ] || mv "$file.tmp.$$" "$file"
    ;;
  check)
    [ "$#" -eq 3 ] || { echo 'usage: fm-dispatch-reset.sh check <state-dir> <provider> <model> <workspace>' >&2; exit 2; }
    provider=$1 model=$2 workspace=$3
    require_key "$provider" "$model" "$workspace"
    model=$(normalize_model "$provider" "$model")
    valid_store || { echo "error: malformed exhaustion store $file" >&2; exit 1; }
    [ -e "$file" ] || exit 1
    now=$(now_ms)
    jq -er -s --arg provider "$provider" --arg model "$model" --arg workspace "$workspace" --argjson now "$now" '
      def model_key($p; $m): if $p == "opencode" then ($m | sub("^opencode/"; "")) else $m end;
      [.[] | select(.provider == $provider and model_key(.provider; .model) == model_key($provider; $model) and .workspace == $workspace and .reset_ms > $now)]
      | if length == 0 then false else max_by(.observed_ms) | "reset_ms=\(.reset_ms) observed_ms=\(.observed_ms) evidence=\(.evidence // "unknown")" end' "$file"
    ;;
  prune)
    [ "$#" -eq 0 ] || { echo 'usage: fm-dispatch-reset.sh prune <state-dir>' >&2; exit 2; }
    valid_store || { echo "error: malformed exhaustion store $file" >&2; exit 1; }
    lock_acquire || exit 1
    [ ! -e "$file" ] || { now=$(now_ms); jq -c --argjson now "$now" 'select(.reset_ms > $now)' "$file" > "$file.tmp.$$" && mv "$file.tmp.$$" "$file"; }
    ;;
  *) echo 'usage: fm-dispatch-reset.sh <record|observe|clear|check|prune|sync-quota> <state-dir> ...' >&2; exit 2 ;;
esac
