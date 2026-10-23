#!/usr/bin/env bash
# fm-dispatch-resolve.sh - resolve one concrete crewmate or scout dispatch
# profile from a task brief with exact registered project triggers.
#
# Usage:
#   fm-dispatch-resolve.sh <brief-file> [--project <name>] [--declare <rule-id>] [--json]
#
# What it does: project rules are matched by exact registered project name in
#   code, with no network model judgment. A rule with a `trigger.kind` of
#   `declaration` fires only when the caller names it with --declare: an
#   existing declaration rule never applies by itself, so rationed profiles
#   stay off unless the task explicitly declares them. Otherwise the
#   deterministic default applies. Everything after that is jq: the rule's
#   declared `approval` and `floor`, each profile's declared `provider` and
#   `floor`, and the quota rows from ONE quota-axi --json snapshot. Quota
#   evidence vetoes provably exhausted or below-floor candidates but never
#   ranks them: the clear profile is the FIRST ELIGIBLE profile in the rule's
#   (or default's) declared rung order, and the spawn-time ladder gates own
#   which rung actually launches. A retired model id in the selected route is
#   refused with its recorded replacement before any worker launches.
#   docs/configuration.md "Crew dispatch profiles" owns the declared fields and
#   "Typed dispatch resolution" owns this tool's operator contract.
#
# Output (stdout, TOON-style block):
#   dispatch-resolve:
#     status: clear | default | escalate | error
#     model/latency_ms/tokens, rung (always deterministic), rule (when excerpt) and confidence, probabilities
#     reason: <why the status is not clear>
#     candidate: <harness>:<model> provider=.. scope=.. remaining=..% spendPriority=.. runway=.. -> eligible | eligible, unranked: <reason> | not eligible: <reason>
#     profile: --harness <h> [--model <m>] [--effort <e>]     (clear/default only)
#   clear/default -> deterministic profile selected from the project rule, an explicit declaration, or default
#   escalate  -> the rule requires captain approval or no candidate is eligible
#   error     -> quota-axi failure, an unusable model catalog, or a retired model in the selected route
#   --json emits one machine-readable route object for fm-spawn.sh.
#   Every outcome exits 0 so an intake is never blocked by this tool.
#   Exit 2 only for a usage or configuration error (unreadable brief, an
#   existing unreadable rules file, malformed rules, or missing jq), which is
#   actionable, never selected around.
#
# Authority: this tool never replaces firstmate's judgment, quota-array-dispatch,
#   the captain-approval gate, or fm-spawn.sh validation; it publishes one
#   inspectable answer plus every candidate's evidence, in code.
set -u

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-$FM_ROOT}"
CONFIG="${FM_CONFIG_OVERRIDE:-$FM_HOME/config}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

# shellcheck source=bin/fm-quota-axi-lib.sh
. "$SCRIPT_DIR/fm-quota-axi-lib.sh"
# shellcheck source=bin/fm-control-lib.sh
. "$SCRIPT_DIR/fm-control-lib.sh"
# shellcheck source=bin/fm-model-catalog-lib.sh
. "$SCRIPT_DIR/fm-model-catalog-lib.sh"

DEFAULT_WHEN="No listed rule applies to this task."

die() { printf 'error: %s\n' "$1" >&2; exit 2; }
usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

BRIEF='' PROJECT='' DECLARE='' RULES_PATH="$CONFIG/dispatch-policy.json" RULES=''
CAPTAIN_APPROVED=0
JSON_MODE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || die "--project needs a value"; PROJECT=$2; shift 2 ;;
    --declare) [ $# -ge 2 ] || die "--declare needs a rule id"; DECLARE=$2; shift 2 ;;
    --captain-approved) CAPTAIN_APPROVED=1; shift ;;
    --json) JSON_MODE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) [ -z "$BRIEF" ] || die "one brief file only"; BRIEF=$1; shift ;;
  esac
done

# ---- inputs --------------------------------------------------------------------
[ -n "$BRIEF" ] || die "brief file required (see --help)"
[ -r "$BRIEF" ] || die "brief file not readable: $BRIEF"
command -v jq >/dev/null 2>&1 || die "jq required"
RULES=$(mktemp) || die "mktemp failed"
trap 'rm -f "$RULES"' EXIT
if [ -e "$RULES_PATH" ] || [ -L "$RULES_PATH" ]; then
  [ -r "$RULES_PATH" ] || die "rules file not readable: $RULES_PATH"
  cp "$RULES_PATH" "$RULES" || die "could not snapshot rules file: $RULES_PATH"
else
  printf '{"rules":[]}\n' > "$RULES" || die "could not create empty rules snapshot"
fi
if ! jq -e . "$RULES" >/dev/null 2>&1; then
  die "malformed rules file: $RULES_PATH (not JSON)"
fi
if [ "$(jq -r '.version // empty' "$RULES" 2>/dev/null)" != 2 ]; then
  die "dispatch policy must use version 2; migrate this home with bin/fm-migrate-unified-dispatch.sh"
fi
if [ -e "$RULES_PATH" ] || [ -L "$RULES_PATH" ]; then
  "$SCRIPT_DIR/fm-dispatch-policy-validate.sh" "$RULES" >/dev/null 2>&1 || die "malformed version 2 dispatch policy: $RULES_PATH"
  home_role=worker
  if [ -r "$FM_HOME/data/charter.md" ] && grep -qi research "$FM_HOME/data/charter.md"; then
    home_role=research
  fi
  NORMALIZED="$RULES.v1"
  jq --arg home_role "$home_role" '
    def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
    def constrain($p; $constraints):
      ($p.provider // "") as $provider
      | ([$constraints[]? | select((.when.scope // "worker") == "worker" and (.when.provider // $provider) == $provider)
          | select((.when.home_role == null) or (.when.home_role == $home_role))]) as $matches
      | $p + {
          _dispatch_unified: true,
          _dispatch_denied: any($matches[]; .deny == true),
          _dispatch_approval: ($p.approval == "captain" or any($matches[]; .approval == "captain"))
        };
    def expand($items; $constraints):
      [($items[]?) as $r
        | (if ($r.accounts | type) == "array" and ($r.accounts | length) > 0
           then ($r.accounts[] | $r + {workspace:.} | del(.accounts))
           else $r end)
        | constrain(.; $constraints)];
    . as $cfg
    | .version = 1
    | .rules = [($cfg.rules // [])[] | . as $rule | . + {use: expand(profiles($rule.ladder); $cfg.constraints)} | del(.ladder)]
    | .default = expand(($cfg.default_ladder // []); $cfg.constraints)
    | del(.default_ladder, .constraints, .providers)
  ' "$RULES" > "$NORMALIZED" || die "version 2 policy normalization failed"
  mv "$NORMALIZED" "$RULES" || die "could not install normalized policy snapshot"
fi
chmod 400 "$RULES" || die "could not protect rules snapshot"
VERIFIED_HARNESSES=$(fm_control_harnesses | jq -Rsc 'split("\n") | map(select(length > 0))')

# The fields this tool consumes must be well formed; bootstrap owns the wider
# schema diagnostic, but an intake never selects around a malformed file.
rules_err=$(jq -r --argjson verified_harnesses "$VERIFIED_HARNESSES" --arg provider_re "$FM_QUOTA_PROVIDER_ID_RE" '
  def verified($h): $verified_harnesses | index($h);
  def provider_id($p): ($p | type) == "string" and ($p | test($provider_re));
  def effort_ok($h; $m; $e):
    if $e == null then true
    elif ($e | type) != "string" then false
    elif $e == "ultra" then (($h == "pi" or $h == "pi-signed") and (($m | type) == "string") and ($m | startswith("codex-native/")) and ($m | length) > 13)
    elif $h == "claude" then (["low","medium","high","xhigh","max"] | index($e)) != null
    elif $h == "codex" then (["low","medium","high","xhigh","max"] | index($e)) != null
    elif $h == "grok" or $h == "agy" then (["low","medium","high"] | index($e)) != null
    elif $h == "pi" or $h == "pi-signed" or $h == "omp" or $h == "muse" then (["low","medium","high","xhigh","max"] | index($e)) != null
    elif $h == "rovo" then (["low","medium","high","max"] | index($e)) != null
    elif $h == "opencode" or $h == "kimi" or $h == "cursor" then false
    else true end;
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  def floor_bad($f; $need_provider):
    ($f | type) != "object"
    or (($f.scope | type) != "string") or (($f.scope | length) == 0)
    or (($f.min_percent | type) != "number") or ($f.min_percent < 0) or ($f.min_percent > 100)
    or (if $need_provider
        then (provider_id($f.provider) | not)
        else ($f | has("provider"))
        end);
  def profile_bad($p):
    ($p | type) != "object"
    or (($p.harness | type) != "string") or (($p.harness | length) == 0)
    or ($p | has("model") and ((.model | type) != "string" or (.model | length) == 0))
    or ($p | has("effort") and ((.effort | type) != "string" or (.effort | length) == 0))
    or ($p | has("provider") and (provider_id(.provider) | not))
    or ($p | has("floor") and floor_bad(.floor; false));
  def duplicate_profiles($items):
    ($items | map([.harness, (.model // null), (.effort // null), (.workspace // null)] | @json)) as $keys
    | ($keys | length) != ($keys | unique | length);
  if type != "object" then "top-level value must be an object"
  elif has("version") and .version != 1 then "version must be 1"
  elif has("rules") and (.rules | type) != "array" then "rules must be an array"
  elif any((.rules // [])[]; type != "object") then "each rule must be an object"
  elif any((.rules // [])[]; (.when | type) != "string" or (.when | length) == 0) then "each rule needs non-empty when"
  elif any((.rules // [])[]; has("match") or has("projects")) then "legacy match/projects selectors are retired; use trigger"
  elif any((.rules // [])[]; (.id | type) != "string" or (.id | length) == 0) then "each rule needs a non-empty id"
  elif ([.rules[]?.id] | length) != ([.rules[]?.id] | unique | length) then "rule ids must be unique"
  elif any((.rules // [])[]; (.trigger | type) != "object" or ((.trigger.kind != "declaration") and (.trigger.kind != "projects"))) then "each rule needs trigger.kind declaration or projects"
  elif any((.rules // [])[]; .trigger.kind == "declaration" and (has("trigger") | not)) then "declaration rule needs a trigger"
  elif any((.rules // [])[]; .trigger.kind == "projects" and ((.trigger.names | type) != "array" or (.trigger.names | length) == 0 or any(.trigger.names[]; if type != "string" then true else length == 0 or contains("\n") or contains("\r") end))) then "trigger.names must be a non-empty array of single-line registered project names"
  elif ([.rules[]?.trigger.names[]?] | length) != ([.rules[]?.trigger.names[]?] | unique | length) then "a project name may appear in only one dispatch rule"
  elif ([.rules[]? | select(.trigger.kind == "declaration")] | length) > 1 then "only one declaration trigger may be configured"
  elif any((.rules // [])[]; (profiles(.use) | length) == 0) then "each rule needs at least one use profile"
  elif any((.rules // [])[]; has("approval") and .approval != "captain") then "approval must be \"captain\" when present"
  elif any((.rules // [])[]; has("select") and ((.select | type) != "string" or (.select | length) == 0)) then "select must be a non-empty string"
  elif any((.rules // [])[]; has("select") and .select != "quota-balanced") then
    "unknown select: " + ([.rules[] | select(has("select") and .select != "quota-balanced") | .select] | unique | join(", "))
  elif any((.rules // [])[]; has("floor") and floor_bad(.floor; true)) then "rule floor needs scope, min_percent 0..100, and provider matching ^[a-z0-9]+(-[a-z0-9]+)*\\z"
  elif any((.rules // [])[] | profiles(.use)[]; profile_bad(.)) then "each use profile needs harness; model, effort, and floor must be well formed, and provider must match ^[a-z0-9]+(-[a-z0-9]+)*\\z when present"
  elif any((.rules // [])[]; duplicate_profiles(profiles(.use))) then "each rule use must not contain duplicate harness, model, and effort profiles"
  elif any((.rules // [])[] | profiles(.use)[]; (verified(.harness) | not)) then "each use profile must name a verified harness"
  elif any((.rules // [])[] | profiles(.use)[]; (effort_ok(.harness; .model; .effort) | not)) then "each use profile effort must be supported by its harness and model"
  elif has("default") and (profiles(.default) | length) == 0 then "default must be a profile object or non-empty profile array"
  elif has("default") and any(profiles(.default)[]; profile_bad(.)) then "each default profile needs harness; model, effort, and floor must be well formed, and provider must match ^[a-z0-9]+(-[a-z0-9]+)*\\z when present"
  elif has("default") and duplicate_profiles(profiles(.default)) then "default must not contain duplicate harness, model, and effort profiles"
  elif has("default") and any(profiles(.default)[]; verified(.harness) | not) then "each default profile must name a verified harness"
  elif has("default") and any(profiles(.default)[]; (effort_ok(.harness; .model; .effort) | not)) then "each default profile effort must be supported by its harness and model"
  elif has("default_ladder") and ((.default_ladder | type) != "array" or (.default_ladder | length) == 0) then "default_ladder must be a non-empty array"
  elif has("default_ladder") and any(.default_ladder[]; (.id | type) != "string" or (.id | length) == 0 or profile_bad(.)) then "default_ladder rungs need non-empty ids and valid profiles"
  elif has("default_ladder") and ([.default_ladder[].id] | length) != ([.default_ladder[].id] | unique | length) then "default_ladder rung ids must be unique"
  elif has("default_ladder") and any(.default_ladder[]; (verified(.harness) | not) or (effort_ok(.harness; .model; .effort) | not)) then "default_ladder rungs must use verified harnesses and supported effort"
  else empty end
' "$RULES" 2>/dev/null) || die "malformed rules file: $RULES_PATH (not JSON)"
[ -z "$rules_err" ] || die "malformed rules file: $RULES_PATH - $rules_err"

missing_provider=$(jq -r '
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  ((.rules // [])[] | profiles(.use)[] | select(has("provider") | not) | "use\t\(.harness)"),
  (profiles(.default // null)[] | select(has("provider") | not) | "default\t\(.harness)"),
  ((.default_ladder // [])[] | select(has("provider") | not) | "default_ladder\t\(.harness)")
' "$RULES" | while IFS=$'\t' read -r location harness; do
  if ! fm_quota_single_provider_for_harness "$harness" >/dev/null; then
    printf '%s\t%s\n' "$location" "$harness"
    break
  fi
done)
if [ -n "$missing_provider" ]; then
  IFS=$'\t' read -r location harness <<< "$missing_provider"
  die "malformed rules file: $RULES_PATH - $location profiles whose harness lacks one authoritative provider family require provider: $harness"
fi

# ---- harness -> provider map, from the single owner in fm-quota-axi-lib.sh -----
PMAP='{}'
while IFS= read -r h; do
  [ -n "$h" ] || continue
  p=$(fm_quota_single_provider_for_harness "$h" 2>/dev/null) || p=''
  PMAP=$(jq -c --arg h "$h" --arg p "$p" '. + {($h): (if $p == "" then null else $p end)}' <<<"$PMAP")
done < <(jq -r '
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  ([((.rules // [])[]) | profiles(.use)[]] + profiles(.default // null)
   + ((.default_ladder // []) | map(select(type == "object"))))
  | map(.harness) | unique | .[]' "$RULES")

emit_error() {
  local reason=$1
  echo "dispatch-resolve: error ($reason)" >&2
  if [ "$JSON_MODE" -eq 1 ]; then
    jq -cn --arg reason "$reason" '{status:"error",source:null,reason:$reason,profile:null}'
  else
    printf 'dispatch-resolve:\n  status: error\n  reason: %s\n' "$reason"
  fi
  exit 0
}

RESP_FILE=$(mktemp) || die "mktemp failed"
QUOTA=$(mktemp) || { rm -f "$RESP_FILE"; die "mktemp failed"; }
RESET_ROWS=$(mktemp) || { rm -f "$RESP_FILE" "$QUOTA"; die "mktemp failed"; }
CATALOG_ROWS=$(mktemp) || { rm -f "$RESP_FILE" "$QUOTA" "$RESET_ROWS"; die "mktemp failed"; }
trap 'rm -f "$RULES" "$RESP_FILE" "$QUOTA" "$RESET_ROWS" "$CATALOG_ROWS"' EXIT
fm_model_catalog_check __validate__ "$CONFIG" >/dev/null 2>&1 || emit_error "malformed model catalog"
if [ -f "$CONFIG/model-catalog.json" ]; then
  cp "$CONFIG/model-catalog.json" "$CATALOG_ROWS" || die "could not snapshot model catalog"
else
  printf '{"models":[]}\n' > "$CATALOG_ROWS" || die "could not prepare empty model catalog"
fi
LAT_MS=null
RUNG=deterministic
DEFAULT_REASON=''
RESOLUTION_CHOICE=default

make_response() {  # <choice>
  jq -n --arg choice "$1" '{model:"deterministic",answers:{rule:{choice:$choice,confidence:1,probabilities:{($choice):1}}}}' > "$RESP_FILE" \
    || die "could not prepare deterministic dispatch result"
}

PROJECT_RULE_INDEX=$(jq -r --arg project "$PROJECT" '
  [((.rules // []) | to_entries[]) | select(.value.trigger.kind == "projects" and (.value.trigger.names // [] | index($project))) | (.key + 1)] | .[0] // empty
' "$RULES")
DECLARATION_RULE_INDEX=$(jq -r '[((.rules // []) | to_entries[]) | select(.value.trigger.kind == "declaration") | (.key + 1)] | .[0] // empty' "$RULES")
DECLARATION_RULE_ID=$(jq -r '[.rules[]? | select(.trigger.kind == "declaration") | .id] | .[0] // empty' "$RULES")
if [ -n "$DECLARE" ]; then
  [ -n "$DECLARATION_RULE_ID" ] || die "--declare $DECLARE names no rule: the policy declares no declaration trigger"
  [ "$DECLARE" = "$DECLARATION_RULE_ID" ] || die "--declare $DECLARE names no rule: the declaration trigger is '$DECLARATION_RULE_ID'"
fi
if [ -n "$PROJECT_RULE_INDEX" ]; then
  RESOLUTION_CHOICE="rule_$PROJECT_RULE_INDEX"
  DEFAULT_REASON="project '$PROJECT' matched rule_$PROJECT_RULE_INDEX"
elif [ -n "$DECLARE" ]; then
  RESOLUTION_CHOICE="rule_$DECLARATION_RULE_INDEX"
  DEFAULT_REASON="declared '$DECLARE' selected rule_$DECLARATION_RULE_INDEX"
else
  DEFAULT_REASON='no declared trigger applies; deterministic default selected'
fi
make_response "$RESOLUTION_CHOICE"
[ -z "$DEFAULT_REASON" ] || [ -n "$PROJECT_RULE_INDEX" ] || [ -n "$DECLARE" ] || echo "dispatch-resolve: $DEFAULT_REASON" >&2

# ---- quota evidence: one quota-axi --json snapshot -----------------------------
command -v quota-axi >/dev/null 2>&1 || emit_error "quota-axi not installed"
quota-axi --json > "$QUOTA" 2>/dev/null || emit_error "quota-axi --json failed"
fm_quota_json_valid < "$QUOTA" || emit_error "quota-axi --json returned an invalid snapshot"
if [ "$(jq -r '.version // 1' "$RULES_PATH" 2>/dev/null)" = 2 ]; then
  jq --slurpfile quota "$QUOTA" '
    def reset_at($p):
      [$quota[0].providers[]? | select(.provider == $p.provider and (.accountKey // "default") == ($p.workspace // "default"))
        | .quotaSemantics.effectiveAvailability[]? | .resetsAt // empty] | first // "9999";
    def order_accounts($items):
      [$items | to_entries[] | .value + {_dispatch_order:.key}] as $entries
      | [$entries | group_by(.id)[] | {
          order:(map(._dispatch_order) | min),
          items:(if .[0].account_strategy == "reset-soonest-yield-shorter-reset"
                 then sort_by(reset_at(.) , ._dispatch_order)
                 else sort_by(._dispatch_order) end)
        }]
      | sort_by(.order) | map(.items[]) | map(del(._dispatch_order));
    .default = order_accounts(.default)
    | .rules |= map(.use = order_accounts(.use))
  ' "$RULES" > "$RULES.ordered" || emit_error "could not apply configured account selection strategy"
  mv "$RULES.ordered" "$RULES" || emit_error "could not install account-ordered policy snapshot"
  "$SCRIPT_DIR/fm-dispatch-reset.sh" sync-quota "$STATE" "$RULES_PATH" "$QUOTA" >/dev/null 2>&1 \
    || emit_error "could not synchronize quota-axi exhaustion evidence"
fi
if [ -f "$STATE/dispatch-exhaustions.jsonl" ]; then
  jq -s 'if all(.[]; .version == 1 and (.provider|type)=="string" and (.model|type)=="string" and (.workspace|type)=="string" and (.reset_ms|type)=="number") then . else error("invalid reset record") end' \
    "$STATE/dispatch-exhaustions.jsonl" > "$RESET_ROWS" 2>/dev/null || emit_error "dispatch exhaustion store is malformed"
else
  printf '[]\n' > "$RESET_ROWS"
fi

# ---- resolution: declared gates + quota evidence + declared rung order, all in jq -
RESULT=$(jq -n --argjson lat "$LAT_MS" --arg rung "$RUNG" --arg none_criterion "$DEFAULT_WHEN" \
  --arg default_reason "$DEFAULT_REASON" --argjson pmap "$PMAP" --argjson captain_approved "$CAPTAIN_APPROVED" \
  --argjson now_ms "$(($(date +%s) * 1000))" \
  --slurpfile resp "$RESP_FILE" --slurpfile rules "$RULES" --slurpfile quota "$QUOTA" --slurpfile resets "$RESET_ROWS" --slurpfile catalog "$CATALOG_ROWS" '
  ($resp[0]) as $r | ($rules[0]) as $cfg | ($quota[0]) as $q | ($resets[0]) as $reset_rows | ($catalog[0]) as $catalog_data | ($r.answers.rule) as $a |
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  def prov($p; $c):
    ([$q.providers[] | select(.provider == $p)]) as $all
    | (if ($c.workspace | type) == "string" then ([$all[] | select(.accountKey == $c.workspace)] | first) else null end)
      // ([$all[] | select(.accountKey == "default")] | first)
      // ([$all[] | select(has("accountKey") | not)] | first)
      // ($all | first) // null;
  def rows($p; $c): if prov($p; $c) == null then [] else (prov($p; $c).quotaSemantics.effectiveAvailability // []) end;
  def bare($m): ($m | split("/") | last);
  def provider_of($c): ($c.provider // $pmap[$c.harness] // null);
  def reset_for($c; $p):
    ($c.workspace // "default") as $workspace
    | [$reset_rows[] | select(.provider == $p and .model == $c.model and .workspace == $workspace and .reset_ms > $now_ms)]
    | max_by(.observed_ms) // null;
  def catalog_item($model): [$catalog_data.models[]? | select(.id == $model)] | first // null;
  def measured($p; $c):
    if prov($p; $c) == null then false
    else (["known", "partial"] | index(prov($p; $c).quotaSemantics.status)) != null end;
  def applicable($p; $m; $c):
    (bare($m)) as $bare |
    [rows($p; $c)[] | select(
      .scope == "all_models" or .scope == "all_products" or
      ($m != "" and (.scope == ("model:" + $bare) or .scope == ("product:" + $bare)))
    )];
  def floor_state($f; $p; $c):
    if $f == null then "none"
    elif prov($p; $c) == null or (measured($p; $c) | not) then "unknown"
    else [rows($p; $c)[] | select(.scope == $f.scope)] as $matches
      | if ($matches | length) == 0 or any($matches[]; .status != "known") then "unknown"
        elif any($matches[]; .effectivePercentRemaining < $f.min_percent) then "below"
        else "ok"
        end
    end;
  def evidence($rows):
    $rows | map({scope, status, pct: (.effectivePercentRemaining // null), runway: (.runway.status // null), spendPriority: (.selection.spendPriority // null)});
  def evaluate($c):
    (provider_of($c)) as $p | (catalog_item($c.model // "")) as $catalog_item |
    if ($c._dispatch_denied // false) then
      {profile: $c, provider: $p, eligible: false, reason: "policy constraint denies this home and provider"}
    elif ($c._dispatch_approval // false) and $captain_approved != 1 then
      {profile: $c, provider: $p, eligible: false, reason: "captain approval required for this provider"}
    elif $catalog_item.status == "retired" then
      {profile: $c, provider: $p, eligible: false, reason: "model \($c.model) is retired" + (if ($catalog_item.replaced_by | type) == "string" and ($catalog_item.replaced_by | length) > 0 then "; replacement: \($catalog_item.replaced_by)" else "; no replacement listed" end)}
    elif ($catalog_item.harnesses | type) == "array" and ($catalog_item.harnesses | index($c.harness)) == null then
      {profile: $c, provider: $p, eligible: false, reason: "model \($c.model) is not verified for harness \($c.harness)"}
    elif $p != null and reset_for($c; $p) != null then
      (reset_for($c; $p)) as $reset |
      {profile: $c, provider: $p, bounds: [], scope: $c.workspace, reset_ms: $reset.reset_ms, eligible: false,
       reason: "provider/model/workspace is exhausted until ($reset.reset_ms)"}
    elif $p == null then {profile: $c, eligible: false, reason: "no provider family for harness \($c.harness); declare provider on the profile"}
    elif prov($p; $c) == null then {profile: $c, provider: $p, eligible: true, unranked: true, reason: "provider \($p) not in the quota snapshot"}
    else
      (applicable($p; ($c.model // ""); $c)) as $rows |
      (evidence($rows)) as $bounds |
      (floor_state($c.floor; $p; $c)) as $profile_floor_state |
      if any($rows[]; (.runway.status // "") == "exhausted_now") then
        ($rows | map(select((.runway.status // "") == "exhausted_now")) | first) as $bad |
        {profile: $c, provider: $p, bounds: $bounds, scope: $bad.scope, pct: ($bad.effectivePercentRemaining // null), runway: $bad.runway.status, eligible: false, reason: "runway exhausted_now at \($bad.scope)"}
      elif any($rows[]; .status == "known" and (.effectivePercentRemaining | type) == "number" and .effectivePercentRemaining <= 0) then
        ($rows | map(select(.status == "known" and (.effectivePercentRemaining | type) == "number" and .effectivePercentRemaining <= 0)) | first) as $bad |
        {profile: $c, provider: $p, bounds: $bounds, scope: $bad.scope, pct: $bad.effectivePercentRemaining, runway: $bad.runway.status, eligible: false, reason: "0% remaining at \($bad.scope)"}
      elif $profile_floor_state == "below" then
        ([rows($p; $c)[] | select(
          .scope == $c.floor.scope and
          .effectivePercentRemaining < $c.floor.min_percent
        )] | first) as $floor_row |
        {profile: $c, provider: $p, bounds: $bounds, scope: ($floor_row.scope // $c.floor.scope), pct: ($floor_row.effectivePercentRemaining // null), runway: ($floor_row.runway.status // null), eligible: false, reason: "profile floor \($c.floor.scope) below \($c.floor.min_percent)%"}
      elif (measured($p; $c) | not) then
        ($rows | first) as $row |
        {profile: $c, provider: $p, bounds: $bounds, scope: ($row.scope // null), pct: ($row.effectivePercentRemaining // null), runway: ($row.runway.status // null), eligible: true, unranked: true, unknown: true, reason: "provider \($p) unmeasured (\(prov($p; $c).quotaSemantics.status))"}
      elif ($rows | length) == 0 then
        {profile: $c, provider: $p, bounds: $bounds, eligible: true, unranked: true, unknown: true, reason: "no applicable quota row for provider \($p)"}
      elif $profile_floor_state == "unknown" then
        ([rows($p; $c)[] | select(.scope == $c.floor.scope)] | first) as $floor_row |
        {profile: $c, provider: $p, bounds: $bounds, scope: $c.floor.scope, pct: ($floor_row.effectivePercentRemaining // null), runway: ($floor_row.runway.status // null), eligible: true, unranked: true, unknown: true, reason: "profile floor \($c.floor.scope) is unverifiable: not rankable"}
      elif any($rows[]; .status != "known") then
        ($rows | map(select(.status != "known")) | first) as $bad |
        {profile: $c, provider: $p, bounds: $bounds, scope: $bad.scope, eligible: true, unranked: true, unknown: true, reason: "quota row \($bad.scope) unknown: not rankable"}
      elif any($rows[]; (.selection.spendPriority | type) != "number") then
        ($rows | map(select((.selection.spendPriority | type) != "number")) | first) as $bad |
        {profile: $c, provider: $p, bounds: $bounds, scope: $bad.scope, pct: $bad.effectivePercentRemaining, runway: $bad.runway.status, eligible: true, unranked: true, reason: "spendPriority missing or non-numeric at \($bad.scope): not rankable"}
      else
        ($rows | min_by(.selection.spendPriority)) as $limiting |
        {profile: $c, provider: $p, bounds: $bounds, scope: $limiting.scope, pct: $limiting.effectivePercentRemaining,
         spendPriority: $limiting.selection.spendPriority, runway: $limiting.runway.status, eligible: true, reason: "ok"}
      end
    end;
  ($a.choice) as $choice |
  (if ($choice | test("^rule_[1-9][0-9]*$"))
   then ($choice | ltrimstr("rule_") | tonumber)
   else null end) as $rule_number |
  (if $choice == "default" then null
   elif $rule_number != null and $rule_number <= (($cfg.rules // []) | length) then $cfg.rules[$rule_number - 1]
   else null end) as $rule |
  (if $rule == null then "none" else floor_state($rule.floor; $rule.floor.provider; {}) end) as $rule_floor_state |
  (if $choice != "default" and $rule == null then []
   elif $rule == null then profiles($cfg.default // null)
   else profiles($rule.use)
   end) as $answer_use |
  (if $choice != "default" and $rule == null then {invalid: "rule \($choice) is not in the rules file"}
   elif $rule == null then {source: "default", use: profiles($cfg.default // null), note: (if ($default_reason | length) == 0 then "no declared trigger matched; deterministic default selected" else $default_reason end)}
   elif ($rule.approval // "") == "captain" then {source: $choice, escalate: "rule requires the captain'"'"'s explicit approval before dispatch"}
   elif $rule_floor_state == "unknown" then {source: $choice, escalate: "rule \($choice) floor \($rule.floor.provider)/\($rule.floor.scope) is unverifiable"}
   elif $rule_floor_state == "below"
     then {source: "default", use: profiles($cfg.default // null), note: "rule \($choice) floor \($rule.floor.scope) below \($rule.floor.min_percent)%: fall through to default"}
   else {source: $choice, use: profiles($rule.use), note: "rule matched"} end) as $sel |
  {
    model: $r.model, latency_ms: $lat, rung: $rung, tokens: ($r.usage // null),
    rule: $choice, source: $sel.source,
    rule_when: (if $rule == null then $none_criterion else $rule.when end | .[0:60]),
    confidence: $a.confidence, probabilities: $a.probabilities
  } as $ev |
  if $sel.invalid then $ev + {status: "error", reason: $sel.invalid}
  elif $sel.escalate then
    $ev + {status: "escalate", reason: $sel.escalate, candidates: ($answer_use | map(evaluate(.)))}
  elif ($sel.use | length) == 0 then $ev + {status: "escalate", reason: "no profiles configured for \($sel.source)", note: $sel.note, candidates: []}
  else
     ($sel.use | map(evaluate(.))) as $cands |
     ([$cands[] | select(.eligible)]) as $usable |
     ([$cands[] | select(.unranked)]) as $unranked |
     if ($usable | length) == 0 then $ev + {status: "escalate", reason: "no eligible candidate", note: $sel.note, candidates: $cands}
     else $ev + {status: (if $sel.source == "default" then "default" else "clear" end), note: ($sel.note + "; declared rung order decides (first eligible profile)"), candidates: $cands, chosen: ($usable | first)}
       + (if ($unranked | length) > 0 then
            {unranked_note: "\($unranked | length) eligible candidate(s) unranked (\([$unranked[].provider] | unique | join(", ")))"}
          else {} end)
     end
  end') || emit_error "resolution failed"

TEXT=$(jq -r '
  def flat: tostring | gsub("[\t\r\n]"; " ");
  def show($value): ($value // "-") | flat;
  def shell_arg: flat | @sh;
  "dispatch-resolve:",
  "  status: \(.status | flat)",
  "  source: \(.source | flat)",
  "  model: \(show(.model))   latency_ms: \(show(.latency_ms))   tokens: \(show(.tokens.input_tokens))/\(show(.tokens.output_tokens))",
  "  rung: \(.rung | flat)",
  "  rule: \(.rule | flat) (\(.rule_when | flat))   confidence: \(.confidence | flat)",
  "  probabilities: \([.probabilities | to_entries[] | "\(.key | flat)=\(.value | flat)"] | join(" "))",
  (if .reason then "  reason: \(.reason | flat)" else empty end),
  (if .note then "  note: \(.note | flat)" else empty end),
  (if .unranked_note then "  note: \(.unranked_note | flat)" else empty end),
  (.candidates[]? | "  candidate: \(.profile.harness | flat):\(show(.profile.model))"
      + (if .provider then "  provider=\(.provider | flat)" else "" end)
      + (if .profile.workspace then "  workspace=\(.profile.workspace | flat)" else "" end)
      + (if .scope then "  scope=\(.scope | flat)  remaining=\(show(.pct))%  spendPriority=\(show(.spendPriority))  runway=\(show(.runway))" else "" end)
      + (if (.bounds // [] | length) > 1 then "  bounds=" + ([.bounds[] | "\(.scope | flat):\(show(.pct))%/\((.runway // .status) | flat)"] | join(",")) else "" end)
      + "  -> " + (if .unranked then "eligible, unranked: \(.reason | flat): disclosed uncertainty" elif .eligible then "eligible" else "not eligible: \(.reason | flat)" end)),
  (if .chosen then "  profile: --harness \(.chosen.profile.harness | shell_arg)"
      + (if .chosen.profile.model then " --model \(.chosen.profile.model | shell_arg)" else "" end)
      + (if .chosen.profile.effort then " --effort \(.chosen.profile.effort | shell_arg)" else "" end) else empty end)' <<<"$RESULT") || emit_error "output rendering failed"
if [ "$JSON_MODE" -eq 1 ]; then
  jq -c '
    {
      status,
      source: (if (.source // "") | startswith("rule_") then (.source | sub("^rule_"; "rule:")) else .source end),
      reason: (.reason // .note // ""),
      explanation: {
        status: .status,
        source: .source,
        reason: (.reason // .note // ""),
        confidence: .confidence,
        candidates: (.candidates // []),
        chosen: (.chosen // null)
      },
      profile: (if .chosen then {
        harness: .chosen.profile.harness,
        model: (.chosen.profile.model // null),
        effort: (.chosen.profile.effort // null),
        provider: (.chosen.provider // .chosen.profile.provider // null),
        workspace: (.chosen.profile.workspace // null),
        account_strategy: (.chosen.profile.account_strategy // null),
        dispatch_unified: (.chosen.profile._dispatch_unified // false),
      } else null end)
    }
  ' <<<"$RESULT" || emit_error "JSON output rendering failed"
  exit 0
fi
printf '%s\n' "$TEXT"
exit 0
