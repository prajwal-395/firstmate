#!/usr/bin/env bash
# fm-dispatch-policy-validate.sh - validate the version 2 dispatch policy.
# Usage: fm-dispatch-policy-validate.sh <policy.json>
set -u
file=${1:-}
[ -n "$file" ] && [ -r "$file" ] || { echo 'error: readable policy path required' >&2; exit 2; }
command -v jq >/dev/null 2>&1 || { echo 'error: jq required' >&2; exit 2; }
reason=$(jq -r '
  def valid_rung:
    type == "object" and (.id | type == "string" and length > 0)
    and (.harness | type == "string" and length > 0)
    and (.model | type == "string" and length > 0)
    and (.provider | type == "string" and test("^[a-z0-9]+(-[a-z0-9]+)*$"))
    and ((has("effort") | not) or (.effort | type == "string" and length > 0))
    and ((has("accounts") | not) or (.accounts | type == "array" and all(.[]; type == "string" and test("^[A-Za-z0-9][A-Za-z0-9_-]*$"))))
    and ((has("workspace") | not) or (.workspace | type == "string" and test("^[A-Za-z0-9][A-Za-z0-9_-]*$")))
    and ((has("account_strategy") | not) or (.account_strategy as $strategy | (["ordered","reset-soonest-yield-shorter-reset"] | index($strategy) != null)))
    and ((has("approval") | not) or .approval == "captain");
  def valid_rule:
    type == "object" and (.id | type == "string" and length > 0)
    and (.when | type == "string" and length > 0)
    and (.trigger | type == "object")
    and ((.trigger.kind == "declaration") or (.trigger.kind == "projects" and (.trigger.names | type == "array" and length > 0 and all(.[]; type == "string" and length > 0))))
    and (.ladder | type == "array" and length > 0 and all(.[]; valid_rung));
  if type != "object" then "top-level value must be an object"
  elif (keys - ["version", "rules", "default_ladder", "constraints", "providers"] | length) > 0 then "unknown top-level policy fields are not supported"
  elif .version != 2 then "version must be 2"
  elif (.rules | type) != "array" or (.default_ladder | type) != "array" or (.default_ladder | length) == 0 then "rules and non-empty default_ladder arrays are required"
  elif any([.default_ladder[]?, .rules[]?.ladder[]?][]; has("account_strategy") and (.account_strategy as $strategy | (["ordered","reset-soonest-yield-shorter-reset"] | index($strategy) == null))) then "account_strategy must be ordered or reset-soonest-yield-shorter-reset"
  elif any(.rules[]; valid_rule | not) then "each rule needs id, when, trigger, and a non-empty ladder of valid rungs"
  elif any(.default_ladder[]; valid_rung | not) then "each default_ladder rung needs id, harness, model, provider, and valid optional fields"
  elif ([.rules[].id] | length) != ([.rules[].id] | unique | length) then "rule ids must be unique"
  elif ([(.default_ladder + [.rules[].ladder[]])[]?.id] | length) != ([(.default_ladder + [.rules[].ladder[]])[]?.id] | unique | length) then "rung ids must be unique across the policy"
  elif ([.rules[] | select(.trigger.kind == "declaration")] | length) > 1 then "at most one declaration trigger is permitted"
  elif ([.rules[] | select(.trigger.kind == "projects") | .trigger.names[]] | length) != ([.rules[] | select(.trigger.kind == "projects") | .trigger.names[]] | unique | length) then "a project name may appear in only one rule"
  elif any(.constraints[]?; type != "object" or (.id | type != "string" or length == 0) or (.when | type != "object") or ((.when | has("provider") or has("home_role")) | not) or (.when | (has("scope") and .scope != "worker")) or (.when | (has("home_role") and .home_role != "research")) or (.when | (has("provider") and ((.provider | type) != "string" or (.provider | test("^[a-z0-9]+(-[a-z0-9]+)*$") | not)))) or (has("deny") == has("approval")) or (has("deny") and .deny != true) or (has("approval") and .approval != "captain")) then "constraints need id, a supported provider or home_role selector, and exactly one of deny:true or approval:captain"
  elif any(.providers[]?; type != "object" or (.id | type != "string" or length == 0) or (.refusal_patterns | type != "array")) then "providers need id and refusal_patterns array"
  elif ([.constraints[]?.id] | length) != ([.constraints[]?.id] | unique | length) then "constraint ids must be unique"
  elif ([.providers[]?.id] | length) != ([.providers[]?.id] | unique | length) then "provider ids must be unique"
  elif any(.providers[]?.refusal_patterns[]?; type != "object" or (.pattern | type != "string" or length == 0) or (.pattern as $pattern | ((try (" " | test($pattern)) catch "invalid") == "invalid")) or (.reset as $strategy | (["next-utc-day","retry-after-iso8601","retry-after-iso8601-or-next-utc-day","provider-reset-time-or-quota-axi"] | index($strategy) == null))) then "refusal patterns need a valid regex pattern and a supported reset strategy"
  else empty end
' "$file" 2>/dev/null) || { echo "error: malformed dispatch policy: $file" >&2; exit 1; }
[ -z "$reason" ] || { echo "error: malformed dispatch policy: $reason" >&2; exit 1; }
exit 0
