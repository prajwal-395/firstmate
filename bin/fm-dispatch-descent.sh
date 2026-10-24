#!/usr/bin/env bash
# fm-dispatch-descent.sh - move an exhausted unified-dispatch worker to the
# first currently eligible rung in its recorded policy route.
# Usage: FM_HOME=<home> fm-dispatch-descent.sh [state-dir]
set -u

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
FM_HOME=${FM_HOME:-$(cd "$SCRIPT_DIR/.." && pwd)}
STATE=${1:-${FM_STATE_OVERRIDE:-$FM_HOME/state}}
DATA=${FM_DATA_OVERRIDE:-$FM_HOME/data}
CONFIG=${FM_CONFIG_OVERRIDE:-$FM_HOME/config}
POLICY="$CONFIG/dispatch-policy.json"
RESET="$SCRIPT_DIR/fm-dispatch-reset.sh"
RESOLVE="$SCRIPT_DIR/fm-dispatch-resolve.sh"
CONTROL=${FM_DISPATCH_DESCENT_CONTROL_BIN:-$SCRIPT_DIR/fm-control.sh}
STATE_READER=${FM_DISPATCH_DESCENT_STATE_BIN:-$SCRIPT_DIR/fm-crew-state.sh}
ONLY_ID=${2:-}

[ -d "$STATE" ] && [ -r "$POLICY" ] || exit 0
command -v jq >/dev/null 2>&1 || exit 0

# Refresh the shared reset store from the one quota-axi snapshot before any
# worker-specific decision. Missing or stale quota data remains a soft miss.
if command -v quota-axi >/dev/null 2>&1; then
  quota_snapshot=$(mktemp "${TMPDIR:-/tmp}/fm-dispatch-descent-quota.XXXXXX") || quota_snapshot=
  if [ -n "$quota_snapshot" ]; then
    if quota-axi --json > "$quota_snapshot" 2>/dev/null; then
      "$RESET" sync-quota "$STATE" "$POLICY" "$quota_snapshot" >/dev/null 2>&1 || true
    fi
    rm -f "$quota_snapshot"
  fi
fi

for meta in "$STATE"/*.meta; do
  [ -f "$meta" ] || continue
  [ "$(sed -n 's/^dispatch_unified=//p' "$meta" | tail -n 1)" = 1 ] || continue
  id=${meta##*/}; id=${id%.meta}
  case "$id" in ''|*[!A-Za-z0-9._-]*) continue ;; esac
  [ -z "$ONLY_ID" ] || [ "$id" = "$ONLY_ID" ] || continue
  [ "$(sed -n 's/^kind=//p' "$meta" | tail -n 1)" = secondmate ] && continue
  stopped="$STATE/$id.stopped"
  [ ! -e "$stopped" ] || continue
  state_line=$(FM_HOME="$FM_HOME" FM_STATE_OVERRIDE="$STATE" "$STATE_READER" "$id" 2>/dev/null) || continue
  case "$state_line" in
    'state: done '*|'state: failed '*|'state: paused '*|'state: stopped '*) continue ;;
    'state: exited '*)
      # OpenCode may already have been interrupted to its pane shell while
      # resolving an exhausted rung. relaunch verifies that the endpoint is
      # agent-free before starting the replacement; other harnesses retain
      # their terminal-state exclusion.
      [ "$(sed -n 's/^harness=//p' "$meta" | tail -n 1)" = opencode ] || continue
      ;;
  esac

  model=$(sed -n 's/^model=//p' "$meta" | tail -n 1)
  harness=$(sed -n 's/^harness=//p' "$meta" | tail -n 1)
  workspace=$(sed -n 's/^opencode_workspace=//p' "$meta" | tail -n 1)
  [ -n "$workspace" ] || workspace=$(sed -n 's/^dispatch_route_workspace=//p' "$meta" | tail -n 1)
  if [ -z "$workspace" ]; then
    workspace=$(jq -r --arg model "$model" '
      ([.default_ladder[]?, .rules[]?.ladder[]? | select(.model == $model)] | first) as $r
      | if ($r.workspace | type) == "string" then $r.workspace
        elif ($r.accounts | type) == "array" and ($r.accounts | length) == 1 then $r.accounts[0]
        else "default" end
    ' "$POLICY" 2>/dev/null) || workspace=
  fi
  [ -n "$workspace" ] || workspace=default
  provider=$(jq -r --arg model "$model" '
    ([.default_ladder[]?, .rules[]?.ladder[]? | select(.model == $model) | .provider] | unique | if length == 1 then .[0] else empty end)
  ' "$POLICY" 2>/dev/null) || provider=
  [ -n "$provider" ] || provider=$(sed -n 's/^dispatch_route_provider=//p' "$meta" | tail -n 1)
  [ -n "$provider" ] && [ -n "$model" ] || continue
  reset=$($RESET check "$STATE" "$provider" "$model" "$workspace" 2>/dev/null) || continue
  reset_ms=$(printf '%s\n' "$reset" | sed -n 's/.*reset_ms=\([0-9][0-9]*\).*/\1/p')
  [ -n "$reset_ms" ] || continue
  marker="$STATE/.dispatch-descent-$id"

  brief="$DATA/$id/brief.md"
  [ -r "$brief" ] || { printf 'refused %s has an exhausted dispatch rung but no recorded brief\n' "$id"; continue; }
  project=$(sed -n 's/^dispatch_project=//p' "$meta" | tail -n 1)
  declaration=$(sed -n 's/^dispatch_declaration=//p' "$meta" | tail -n 1)
  approved=$(sed -n 's/^dispatch_approved=//p' "$meta" | tail -n 1)
  args=(--json "$brief")
  [ -z "$project" ] || args+=(--project "$project")
  [ -z "$declaration" ] || args+=(--declare "$declaration")
  [ "$approved" != 1 ] || args+=(--captain-approved)
  route=$(FM_HOME="$FM_HOME" FM_CONFIG_OVERRIDE="$CONFIG" FM_STATE_OVERRIDE="$STATE" "$RESOLVE" "${args[@]}" 2>/dev/null) || route=
  chosen=$(jq -c '.profile // empty' <<<"$route" 2>/dev/null) || chosen=
  target_model=$(jq -r '.model // ""' <<<"$chosen" 2>/dev/null) || target_model=
  target_harness=$(jq -r '.harness // ""' <<<"$chosen" 2>/dev/null) || target_harness=
  target_effort=$(jq -r '.effort // ""' <<<"$chosen" 2>/dev/null) || target_effort=
  target_workspace=$(jq -r '.workspace // ""' <<<"$chosen" 2>/dev/null) || target_workspace=
  target_provider=$(jq -r '.provider // ""' <<<"$chosen" 2>/dev/null) || target_provider=
  route_signature="$reset_ms|${target_harness:-none}|${target_model:-none}|${target_workspace:-none}"
  [ "$(cat "$marker" 2>/dev/null || true)" = "$route_signature" ] && continue
  if [ -z "$target_model" ] || [ -z "$target_harness" ]; then
    printf '%s\n' "$route_signature" > "$marker" 2>/dev/null || true
    printf 'refused %s exhausted %s/%s/%s and no later policy rung is eligible\n' "$id" "$provider" "$model" "$workspace"
    continue
  fi
  if [ "$target_model" = "$model" ] && [ "$target_harness" = "$harness" ] \
    && [ "$target_workspace" = "$workspace" ]; then
    continue
  fi
  explanation=$(jq -r '.explanation.reason // .reason // "the recorded provider/model/workspace reset is active"' <<<"$route" 2>/dev/null)
  note="dispatch descent: $provider/$model on $workspace is exhausted until reset epoch-ms $reset_ms. The policy selected $target_harness/$target_model${target_workspace:+ on $target_workspace} because $explanation. Continue from this note, the brief, and committed work."
  relaunch=("$id" relaunch --harness "$target_harness" --model "$target_model" --note "$note")
  [ -z "$target_effort" ] || relaunch+=(--effort "$target_effort")
  [ -z "$target_workspace" ] || relaunch+=(--opencode-workspace "$target_workspace")
  if ctl_out=$(FM_HOME="$FM_HOME" FM_STATE_OVERRIDE="$STATE" "$CONTROL" "${relaunch[@]}" 2>&1); then
    printf '%s\n' "$route_signature" > "$marker" 2>/dev/null || true
    printf 'relaunched %s %s -> %s/%s%s\n' "$id" "$model" "$target_harness" "$target_model" "${target_workspace:+ on $target_workspace}"
  else
    printf '%s\n' "$route_signature" > "$marker" 2>/dev/null || true
    reason=${ctl_out%%$'\n'*}
    [ -n "$reason" ] || reason='control plane refused the exhausted-rung transition'
    printf 'refused %s could not move from %s/%s: %s\n' "$id" "$harness" "$model" "$reason"
  fi
done
