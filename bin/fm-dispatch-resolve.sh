#!/usr/bin/env bash
# fm-dispatch-resolve.sh - resolve one concrete crewmate or scout dispatch
# profile from a task brief with exact project rules and optional Jev judgment.
#
# Usage:
#   fm-dispatch-resolve.sh <brief-file> [--project <name>] [--json]
#   fm-dispatch-resolve.sh --fallback [--json]
#
# Jev gate: TYPESAFE_API_KEY or AI_GATEWAY_API_KEY non-empty in this process
#   environment, else a matching KEY= line in $FM_HOME/.env read with
#   fmx_env_get, the same accessor as FMX_PAIRING_TOKEN (bin/fm-env-lib.sh).
#   The environment wins. Without keys, exact project rules and the
#   deterministic default still resolve; no Jev request is made. Each key
#   lives in one shell variable and reaches curl as a header read from a file
#   descriptor, never on argv; nothing logs or writes either key.
#
# Ladder: with both keys present the tool tries the captain's typesafe.ai key
#   first and falls back to the free Vercel AI Gateway rung once, in the
#   same invocation, when typesafe.ai answers 429 or 401/403. The fallback is
#   per request with no persisted rung record, so every call re-derives the
#   answer. With one key present the tool uses that rung only.
#
# What it does: project rules are matched by exact project name in code. Jev is
#   asked only to decide among rules declared with `match: "judgment"`. The
#   project name and Task section of the brief travel as state; the Choice
#   options are only those judgment rules plus one fixed generic none option.
#   When Jev is off, unreachable, or below the confidence floor, the resolver
#   selects and reports the deterministic default. Typesafe rung: model
#   jev-latest at https://api.typesafe.ai/v1/systemone, what the tool did
#   before the ladder. Gateway rung: model typesafe-ai/jev at
#   https://ai-gateway.vercel.sh/typesafe/v1/systemone. Both rungs accept the
#   same request and response shapes; only the base URL, model, and key
#   change. Typesafe.ai answers 429 when exhausted, which descends the ladder
#   with auth failures (401/403). Jev returns a matched judgment rule, a
#   probability per option, and confidence. Everything after that is jq: the
#   confidence
#   floor, the rule's declared `approval` and `floor`, each profile's declared
#   `provider` and `floor`, and the quota rows from ONE quota-axi --json
#   snapshot. Quota evidence vetoes provably exhausted or below-floor
#   candidates but never ranks them: the clear profile is the FIRST ELIGIBLE
#   profile in the rule's (or default's) declared rung order, and the
#   spawn-time ladder gates own which rung actually launches. The model never
#   sees quota, catalogs, approvals, `why`, `use`, or project-profile mappings.
#   `--fallback` resolves only the optional exhausted_ladder_fallback declaration.
#   docs/configuration.md "Crew dispatch profiles" owns the declared fields and
#   "Typed dispatch resolution" owns this tool's operator contract.
#
# Output (stdout, TOON-style block):
#   dispatch-resolve:
#     status: clear | default | escalate | error | missing
#     model/latency_ms/tokens, rung (gateway | typesafe), rule (when excerpt) and confidence, probabilities
#     reason: <why the status is not clear>
#     candidate: <harness>:<model> provider=.. scope=.. remaining=..% spendPriority=.. runway=.. -> eligible | eligible, unranked: <reason> | not eligible: <reason>
#     profile: --harness <h> [--model <m>] [--effort <e>]     (clear/default only)
#   clear/default -> deterministic profile selected from the project rule, Jev, or default
#   escalate  -> the rule requires captain approval or no candidate is eligible
#   error     -> quota-axi failure; Jev failures select the deterministic default
#   --json emits one machine-readable route object for fm-spawn.sh.
#   Every outcome exits 0 so an intake is never blocked by this tool.
#   Exit 2 only for a usage or configuration error (unreadable brief, an
#   existing unreadable rules file, malformed rules, or missing jq), which is
#   actionable, never selected around.
#
# Environment:
#   TYPESAFE_API_KEY and AI_GATEWAY_API_KEY are the resolver-specific
#   environment settings. Either one opts the tool in; both together arm the
#   typesafe-first ladder.
#
# Authority: this tool never replaces firstmate's judgment, quota-array-dispatch,
#   the captain-approval gate, or fm-spawn.sh validation; it publishes one
#   inspectable answer plus every candidate's evidence, in code.
set -u

TYPESAFE_API_KEY_PRIVATE=${TYPESAFE_API_KEY:-}
export -n TYPESAFE_API_KEY_PRIVATE 2>/dev/null || true
unset TYPESAFE_API_KEY
AI_GATEWAY_API_KEY_PRIVATE=${AI_GATEWAY_API_KEY:-}
export -n AI_GATEWAY_API_KEY_PRIVATE 2>/dev/null || true
unset AI_GATEWAY_API_KEY

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
FM_ROOT="${FM_ROOT_OVERRIDE:-$(cd "$SCRIPT_DIR/.." && pwd)}"
FM_HOME="${FM_HOME:-$FM_ROOT}"
CONFIG="${FM_CONFIG_OVERRIDE:-$FM_HOME/config}"
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

# shellcheck source=bin/fm-quota-axi-lib.sh
. "$SCRIPT_DIR/fm-quota-axi-lib.sh"
# shellcheck source=bin/fm-control-lib.sh
. "$SCRIPT_DIR/fm-control-lib.sh"
# shellcheck source=bin/fm-env-lib.sh
. "$SCRIPT_DIR/fm-env-lib.sh"
# shellcheck source=bin/fm-timing-lib.sh
. "$SCRIPT_DIR/fm-timing-lib.sh"

CONFIDENCE_FLOOR=0.6
TS_MODEL=jev-latest
TS_BASE=https://api.typesafe.ai
GW_MODEL=typesafe-ai/jev
GW_BASE=https://ai-gateway.vercel.sh/typesafe
TS_TIMEOUT=5
DEFAULT_WHEN="No listed rule applies to this task."

die() { printf 'error: %s\n' "$1" >&2; exit 2; }
usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

BRIEF='' PROJECT='' RULES_PATH="$CONFIG/crew-dispatch.json" RULES=''
STATIC_HARNESS_DEFAULT=0
JSON_MODE=0 FALLBACK_MODE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || die "--project needs a value"; PROJECT=$2; shift 2 ;;
    --json) JSON_MODE=1; shift ;;
    --fallback) FALLBACK_MODE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) [ -z "$BRIEF" ] || die "one brief file only"; BRIEF=$1; shift ;;
  esac
done

# ---- opt-in gate ---------------------------------------------------------------
if [ -z "$TYPESAFE_API_KEY_PRIVATE" ]; then
  TYPESAFE_API_KEY_PRIVATE=$(fmx_env_get TYPESAFE_API_KEY "$FM_HOME/.env")
fi
if [ -z "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
  AI_GATEWAY_API_KEY_PRIVATE=$(fmx_env_get AI_GATEWAY_API_KEY "$FM_HOME/.env")
fi
# ---- inputs --------------------------------------------------------------------
[ "$FALLBACK_MODE" -eq 1 ] || [ -n "$BRIEF" ] || die "brief file required (see --help)"
[ "$FALLBACK_MODE" -eq 1 ] || [ -r "$BRIEF" ] || die "brief file not readable: $BRIEF"
# Jev matches dispatch rules from the task, not the scaffold: only the Task
#   section travels, never the whole file, so constant boilerplate cannot
#   clutter the match signal. A brief with no Task section falls back to the
#   whole brief, so an unfamiliar shape never breaks the intake.
BRIEF_TEXT=''
if [ "$FALLBACK_MODE" -eq 0 ]; then
  BRIEF_TEXT=$(awk 'BEGIN{f=0} /^# Task([ \t]|$)/{f=1;print;next} f==1 && /^# /{exit} f==1{print}' "$BRIEF")
  [ -n "$BRIEF_TEXT" ] || BRIEF_TEXT=$(cat "$BRIEF")
fi
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
if ! jq -e 'has("default")' "$RULES" >/dev/null 2>&1; then
  DEFAULT_HARNESS=$(FM_HOME="$FM_HOME" "$SCRIPT_DIR/fm-harness.sh" crew 2>/dev/null) || die "could not resolve the static crew harness for the default route"
  [ -n "$DEFAULT_HARNESS" ] || die "static crew harness resolved empty"
  STATIC_HARNESS_DEFAULT=1
  DEFAULT_RULES="$RULES.default"
  jq --arg harness "$DEFAULT_HARNESS" '. + {default:{harness:$harness}}' "$RULES" > "$DEFAULT_RULES" \
    || die "could not add the static crew harness to the rules snapshot"
  mv "$DEFAULT_RULES" "$RULES" || die "could not install the default rules snapshot"
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
    elif $h == "codex" then ((["low","medium","high","xhigh"] | index($e)) != null or ($e == "max" and ($m == "gpt-5.6-luna" or $m == "gpt-6-luna")))
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
    ($items | map([.harness, (.model // null), (.effort // null)] | @json)) as $keys
    | ($keys | length) != ($keys | unique | length);
  if type != "object" then "top-level value must be an object"
  elif has("rules") and (.rules | type) != "array" then "rules must be an array"
  elif any((.rules // [])[]; type != "object") then "each rule must be an object"
  elif any((.rules // [])[]; (.when | type) != "string" or (.when | length) == 0) then "each rule needs non-empty when"
  elif any((.rules // [])[]; has("projects") and has("match")) then "each rule must declare projects or match: \"judgment\", not both"
  elif any((.rules // [])[]; has("match") and .match != "judgment") then "rule match must be \"judgment\" when present"
  elif any((.rules // [])[]; has("projects") and ((.projects | type) != "array" or (.projects | length) == 0 or any(.projects[]; if type != "string" then true else length == 0 or contains("\n") or contains("\r") end))) then "rule projects must be a non-empty array of single-line project names"
  elif ([.rules[]?.projects[]?] | length) != ([.rules[]?.projects[]?] | unique | length) then "a project name may appear in only one dispatch rule"
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
  elif has("default") and any(profiles(.default)[]; (verified(.harness) | not)) then "each default profile must name a verified harness"
  elif has("default") and any(profiles(.default)[]; (effort_ok(.harness; .model; .effort) | not)) then "each default profile effort must be supported by its harness and model"
  elif has("exhausted_ladder_fallback") and .exhausted_ladder_fallback != null and (.exhausted_ladder_fallback | type) != "object" then "exhausted_ladder_fallback must be an object or null"
  elif (.exhausted_ladder_fallback // null) != null then
    (.exhausted_ladder_fallback) as $fallback |
    if ($fallback.include_agy_ladder != null and ($fallback.include_agy_ladder | type) != "boolean") then "exhausted_ladder_fallback.include_agy_ladder must be boolean"
    elif ($fallback.agy_effort != null and (($fallback.agy_effort | type) != "string" or (effort_ok("agy"; "fallback"; $fallback.agy_effort) | not))) then "exhausted_ladder_fallback.agy_effort must be low, medium, or high"
    elif ($fallback.use != null and (($fallback.use | type) != "object" and ($fallback.use | type) != "array")) then "exhausted_ladder_fallback.use must be a profile object or non-empty profile array"
    elif ($fallback.use != null and (profiles($fallback.use) | length) == 0) then "exhausted_ladder_fallback.use must not be empty"
    elif ($fallback.include_agy_ladder != true and (profiles($fallback.use // null) | length) == 0) then "exhausted_ladder_fallback must include the agy ladder or at least one use profile"
    elif ($fallback.include_agy_ladder == true and ((.agy_ladder | type) != "array" or (.agy_ladder | length) == 0) and ([profiles(.default // null)[] | select(.harness == "agy" and (.model | type) == "string")] | length) == 0) then "exhausted_ladder_fallback includes agy_ladder but no agy ladder is configured"
    elif any(profiles($fallback.use // null)[]; profile_bad(.)) then "each exhausted_ladder_fallback.use profile must be well formed"
    elif any(profiles($fallback.use // null)[]; (verified(.harness) | not)) then "each exhausted_ladder_fallback.use profile must name a verified harness"
    elif any(profiles($fallback.use // null)[]; (effort_ok(.harness; .model; .effort) | not)) then "each exhausted_ladder_fallback.use profile effort must be supported by its harness and model"
    elif duplicate_profiles(profiles($fallback.use // null)) then "exhausted_ladder_fallback.use must not contain duplicate profiles"
    else empty end
  else empty end
' "$RULES" 2>/dev/null) || die "malformed rules file: $RULES_PATH (not JSON)"
[ -z "$rules_err" ] || die "malformed rules file: $RULES_PATH - $rules_err"

missing_provider=$(jq -r '
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  ((.rules // [])[] | profiles(.use)[] | select(has("provider") | not) | "use\t\(.harness)"),
  (profiles(.default // null)[] | select(has("provider") | not) | "default\t\(.harness)"),
  (profiles(.exhausted_ladder_fallback.use // null)[] | select(has("provider") | not) | "exhausted_ladder_fallback\t\(.harness)")
' "$RULES" | while IFS=$'\t' read -r location harness; do
  if [ "$location" = default ] && [ "$STATIC_HARNESS_DEFAULT" = 1 ]; then
    continue
  fi
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
   + profiles(.exhausted_ladder_fallback.use // null)
   + (if .exhausted_ladder_fallback.include_agy_ladder == true then [{harness:"agy"}] else [] end))
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
trap 'rm -f "$RULES" "$RESP_FILE" "$QUOTA"' EXIT
LAT_MS=null
RUNG=local
DEFAULT_REASON=''
ROUTE_MODE=normal
NEED_JEV=0
RESOLUTION_CHOICE=default

make_response() {  # <choice>
  jq -n --arg choice "$1" '{model:"deterministic",answers:{rule:{choice:$choice,confidence:1,probabilities:{($choice):1}}}}' > "$RESP_FILE" \
    || die "could not prepare deterministic dispatch result"
}

PROJECT_RULE_INDEX=$(jq -r --arg project "$PROJECT" '
  [.rules | to_entries[] | select((.value.projects // []) | index($project)) | (.key + 1)] | .[0] // empty
' "$RULES")
JUDGMENT_RULE_COUNT=$(jq -r '[.rules[]? | select(.match == "judgment" or ((has("match") | not) and (has("projects") | not)))] | length' "$RULES")
if [ "$FALLBACK_MODE" -eq 1 ]; then
  FALLBACK_DECLARED=$(jq -r '(.exhausted_ladder_fallback // null) != null' "$RULES")
  if [ "$FALLBACK_DECLARED" != true ]; then
    if [ "$JSON_MODE" -eq 1 ]; then
      jq -cn '{status:"missing",source:"exhausted_ladder_fallback",reason:"no exhausted_ladder_fallback is configured",profile:null}'
    else
      printf 'dispatch-resolve:\n  status: missing\n  source: exhausted_ladder_fallback\n  reason: no exhausted_ladder_fallback is configured\n'
    fi
    exit 0
  fi
  ROUTE_MODE=fallback
elif [ -n "$PROJECT_RULE_INDEX" ]; then
  RESOLUTION_CHOICE="rule_$PROJECT_RULE_INDEX"
  DEFAULT_REASON="project '$PROJECT' matched rule_$PROJECT_RULE_INDEX"
elif [ "$JUDGMENT_RULE_COUNT" -gt 0 ]; then
  if [ -n "$TYPESAFE_API_KEY_PRIVATE" ] || [ -n "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
    NEED_JEV=1
  else
    DEFAULT_REASON='Jev is off because no resolver key is configured; deterministic default selected'
  fi
else
  DEFAULT_REASON='no judgment rule applies; deterministic default selected'
fi
if [ "$NEED_JEV" -eq 0 ]; then
  make_response "$RESOLUTION_CHOICE"
  if [ -n "$DEFAULT_REASON" ] && [ "$FALLBACK_MODE" -eq 0 ] && [ -z "$PROJECT_RULE_INDEX" ]; then
    echo "dispatch-resolve: $DEFAULT_REASON" >&2
  fi
fi

post_rung() { # <base-url> <model> <key>: one Jev POST; sets HTTP and LAT_MS
  local base=$1 model=$2 key=$3
  REQUEST=$(jq -n --arg brief "$BRIEF_TEXT" --arg project "$PROJECT" --arg model "$model" \
    --arg none_criterion "$DEFAULT_WHEN" --slurpfile rules "$RULES" '
    ($rules[0]) as $cfg |
    ([$cfg.rules | to_entries[] | select(.value.match == "judgment" or ((.value | has("match") | not) and (.value | has("projects") | not)))
      | {key: ("rule_" + ((.key + 1) | tostring)), value: .value.when}] | from_entries) as $criteria |
    {
      model: $model,
      state: {task: {project: $project, brief: $brief}},
      questions: {
        rule: {
          type: "choice",
          instructions: "Which ONE judgment-required dispatch rule best fits `task`? Project rules are matched by code and are not options here. Pick `default` when no judgment rule applies.",
          criteria: ($criteria + {default: $none_criterion})
        }
      }
    }')
  T0=$(fm_timing_now_ms)
  HTTP=$(printf '%s' "$REQUEST" | curl -sS --max-time "$TS_TIMEOUT" -o "$RESP_FILE" -w '%{http_code}' \
    -X POST "$base/v1/systemone" -H 'Content-Type: application/json' \
    -H @/dev/fd/3 3< <(printf 'Authorization: Bearer %s\n' "$key") \
    --data-binary @- 2>/dev/null) || HTTP=000
  T1=$(fm_timing_now_ms)
  LAT_MS=$(( T1 - T0 ))
}
primary_declined() { # <http>: 429 or auth failure means "not available now"
  case "$1" in 429|401|403) return 0 ;; *) return 1 ;; esac
}
if [ "$NEED_JEV" -eq 1 ]; then
  if ! command -v curl >/dev/null 2>&1; then
    DEFAULT_REASON='Jev is unavailable because curl is not installed; deterministic default selected'
  elif [ -n "$TYPESAFE_API_KEY_PRIVATE" ]; then
    post_rung "$TS_BASE" "$TS_MODEL" "$TYPESAFE_API_KEY_PRIVATE"
    if [ "$HTTP" = 200 ]; then
      RUNG=typesafe
    elif primary_declined "$HTTP" && [ -n "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
      TS_HTTP=$HTTP
      post_rung "$GW_BASE" "$GW_MODEL" "$AI_GATEWAY_API_KEY_PRIVATE"
      if [ "$HTTP" = 200 ]; then
        RUNG=gateway
      else
        DEFAULT_REASON="Jev is unavailable after typesafe http $TS_HTTP and gateway http $HTTP; deterministic default selected"
      fi
    else
      DEFAULT_REASON="Jev is unavailable after typesafe http $HTTP; deterministic default selected"
    fi
  else
    post_rung "$GW_BASE" "$GW_MODEL" "$AI_GATEWAY_API_KEY_PRIVATE"
    if [ "$HTTP" = 200 ]; then RUNG=gateway
    else DEFAULT_REASON="Jev is unavailable after gateway http $HTTP; deterministic default selected"; fi
  fi
  if [ -n "$DEFAULT_REASON" ]; then
    echo "dispatch-resolve: $DEFAULT_REASON" >&2
    make_response default
  elif ! jq -e --slurpfile rules "$RULES" '
      (([$rules[0].rules | to_entries[] | select(.value.match == "judgment" or ((.value | has("match") | not) and (.value | has("projects") | not)))
        | "rule_" + ((.key + 1) | tostring)]) + ["default"] | sort) as $choices |
      (.answers.rule.choice) as $choice |
      ($choice | type) == "string" and
      ($choices | index($choice)) != null and
      (.answers.rule.confidence | type) == "number" and
      .answers.rule.confidence >= 0 and .answers.rule.confidence <= 1 and
      (.answers.rule.probabilities | type) == "object" and
      ((.answers.rule.probabilities | keys | sort) == $choices) and
      all(.answers.rule.probabilities[]; type == "number" and . >= 0 and . <= 1) and
      ((.answers.rule.probabilities | [.[]] | add) as $total | $total >= 0.99 and $total <= 1.01) and
      ((has("usage") | not) or
        ((.usage | type) == "object" and
         (.usage.input_tokens | type) == "number" and
         (.usage.output_tokens | type) == "number"))' \
    "$RESP_FILE" >/dev/null 2>&1; then
    DEFAULT_REASON='Jev returned an invalid answer; deterministic default selected'
    echo "dispatch-resolve: $DEFAULT_REASON" >&2
    make_response default
  else
    :
  fi
fi

# ---- quota evidence: one quota-axi --json snapshot -----------------------------
command -v quota-axi >/dev/null 2>&1 || emit_error "quota-axi not installed"
quota-axi --json > "$QUOTA" 2>/dev/null || emit_error "quota-axi --json failed"
fm_quota_json_valid < "$QUOTA" || emit_error "quota-axi --json returned an invalid snapshot"

# ---- resolution: declared gates + quota evidence + declared rung order, all in jq -
RESULT=$(jq -n --arg floor "$CONFIDENCE_FLOOR" --argjson lat "$LAT_MS" --arg rung "$RUNG" --arg none_criterion "$DEFAULT_WHEN" \
  --arg mode "$ROUTE_MODE" --arg default_reason "$DEFAULT_REASON" --argjson pmap "$PMAP" \
  --argjson static_default "$STATIC_HARNESS_DEFAULT" \
  --slurpfile resp "$RESP_FILE" --slurpfile rules "$RULES" --slurpfile quota "$QUOTA" '
  ($resp[0]) as $r | ($rules[0]) as $cfg | ($quota[0]) as $q | ($r.answers.rule) as $a |
  def profiles($v): if ($v | type) == "array" then $v elif ($v | type) == "object" then [$v] else [] end;
  def agy_ladder_models($config):
    if ($config.agy_ladder | type) == "array" then $config.agy_ladder
    else [profiles($config.default // null)[] | select(.harness == "agy" and (.model | type) == "string") | .model]
    end;
  def fallback_profiles($config):
    ($config.exhausted_ladder_fallback // {}) as $fallback |
    (if $fallback.include_agy_ladder == true then
       [(agy_ladder_models($config))[] | {harness:"agy",model:.,effort:($fallback.agy_effort // "high"),provider:"agy"}]
     else [] end)
    + profiles($fallback.use // null);
  def prov($p): ([$q.providers[] | select(.provider == $p)] | first) // null;
  def rows($p): (prov($p) | .quotaSemantics.effectiveAvailability // []);
  def bare($m): ($m | split("/") | last);
  def provider_of($c): ($c.provider // $pmap[$c.harness] // null);
  def measured($p):
    (prov($p) != null and (["known", "partial"] | index(prov($p).quotaSemantics.status)) != null);
  def applicable($p; $m):
    (bare($m)) as $bare |
    [rows($p)[] | select(
      .scope == "all_models" or .scope == "all_products" or
      ($m != "" and (.scope == ("model:" + $bare) or .scope == ("product:" + $bare)))
    )];
  def floor_state($f; $p):
    if $f == null then "none"
    elif prov($p) == null or (measured($p) | not) then "unknown"
    else [rows($p)[] | select(.scope == $f.scope)] as $matches
      | if ($matches | length) == 0 or any($matches[]; .status != "known") then "unknown"
        elif any($matches[]; .effectivePercentRemaining < $f.min_percent) then "below"
        else "ok"
        end
    end;
  def evidence($rows):
    $rows | map({scope, status, pct: (.effectivePercentRemaining // null), runway: (.runway.status // null), spendPriority: (.selection.spendPriority // null)});
  def evaluate($c; $allow_static_default):
    (provider_of($c)) as $p |
    if $p == null and $allow_static_default then
      {profile: $c, eligible: true, unranked: true, unknown: true, reason: "static crew harness has no declared provider or model for quota measurement"}
    elif $p == null then {profile: $c, eligible: false, reason: "no provider family for harness \($c.harness); declare provider on the profile"}
    elif prov($p) == null then {profile: $c, provider: $p, eligible: true, unranked: true, reason: "provider \($p) not in the quota snapshot"}
    else
      (applicable($p; ($c.model // ""))) as $rows |
      (evidence($rows)) as $bounds |
      (floor_state($c.floor; $p)) as $profile_floor_state |
      if any($rows[]; (.runway.status // "") == "exhausted_now") then
        ($rows | map(select((.runway.status // "") == "exhausted_now")) | first) as $bad |
        {profile: $c, provider: $p, bounds: $bounds, scope: $bad.scope, pct: ($bad.effectivePercentRemaining // null), runway: $bad.runway.status, eligible: false, reason: "runway exhausted_now at \($bad.scope)"}
      elif any($rows[]; .status == "known" and (.effectivePercentRemaining | type) == "number" and .effectivePercentRemaining <= 0) then
        ($rows | map(select(.status == "known" and (.effectivePercentRemaining | type) == "number" and .effectivePercentRemaining <= 0)) | first) as $bad |
        {profile: $c, provider: $p, bounds: $bounds, scope: $bad.scope, pct: $bad.effectivePercentRemaining, runway: $bad.runway.status, eligible: false, reason: "0% remaining at \($bad.scope)"}
      elif $profile_floor_state == "below" then
        ([rows($p)[] | select(
          .scope == $c.floor.scope and
          .effectivePercentRemaining < $c.floor.min_percent
        )] | first) as $floor_row |
        {profile: $c, provider: $p, bounds: $bounds, scope: ($floor_row.scope // $c.floor.scope), pct: ($floor_row.effectivePercentRemaining // null), runway: ($floor_row.runway.status // null), eligible: false, reason: "profile floor \($c.floor.scope) below \($c.floor.min_percent)%"}
      elif (measured($p) | not) then
        ($rows | first) as $row |
        {profile: $c, provider: $p, bounds: $bounds, scope: ($row.scope // null), pct: ($row.effectivePercentRemaining // null), runway: ($row.runway.status // null), eligible: true, unranked: true, unknown: true, reason: "provider \($p) unmeasured (\(prov($p).quotaSemantics.status))"}
      elif ($rows | length) == 0 then
        {profile: $c, provider: $p, bounds: $bounds, eligible: true, unranked: true, unknown: true, reason: "no applicable quota row for provider \($p)"}
      elif $profile_floor_state == "unknown" then
        ([rows($p)[] | select(.scope == $c.floor.scope)] | first) as $floor_row |
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
  (if $rule == null then "none" else floor_state($rule.floor; $rule.floor.provider) end) as $rule_floor_state |
  (if $mode == "fallback" then fallback_profiles($cfg)
   elif $choice != "default" and $rule == null then []
   elif $rule == null then profiles($cfg.default // null)
   else profiles($rule.use)
   end) as $answer_use |
  (if $mode == "fallback" then {source:"exhausted_ladder_fallback",use:fallback_profiles($cfg),note:"OpenCode default ladder exhausted; evaluating configured fallback"}
   elif $choice != "default" and $rule == null then {invalid: "rule \($choice) is not in the rules file"}
   elif $a.confidence < ($floor | tonumber) then {source:"default",use:profiles($cfg.default // null),note:"confidence \($a.confidence) below floor \($floor); deterministic default selected"}
   elif $rule == null then {source: "default", use: profiles($cfg.default // null), note: (if ($default_reason | length) == 0 then "no judgment rule matched; deterministic default selected" else $default_reason end)}
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
    $ev + {status: "escalate", reason: $sel.escalate, candidates: ($answer_use | map(evaluate(.; ($static_default == 1 and $sel.source == "default"))))}
  elif ($sel.use | length) == 0 then $ev + {status: "escalate", reason: "no profiles configured for \($sel.source)", note: $sel.note, candidates: []}
  else
     ($sel.use | map(evaluate(.; ($static_default == 1 and $sel.source == "default")))) as $cands |
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
      + (if .scope then "  scope=\(.scope | flat)  remaining=\(show(.pct))%  spendPriority=\(show(.spendPriority))  runway=\(show(.runway))" else "" end)
      + (if (.bounds // [] | length) > 1 then "  bounds=" + ([.bounds[] | "\(.scope | flat):\(show(.pct))%/\((.runway // .status) | flat)"] | join(",")) else "" end)
      + "  -> " + (if .unranked then "eligible, unranked: \(.reason | flat): disclosed uncertainty" elif .eligible then "eligible" else "not eligible: \(.reason | flat)" end)),
  (if .chosen then "  profile: --harness \(.chosen.profile.harness | shell_arg)"
      + (if .chosen.profile.model then " --model \(.chosen.profile.model | shell_arg)" else "" end)
      + (if .chosen.profile.effort then " --effort \(.chosen.profile.effort | shell_arg)" else "" end)
      + (if .source == "default" and (
            (.chosen.profile.harness == "codex" and .chosen.profile.model == "gpt-6-luna") or
            (.chosen.profile.harness == "opencode" and (.chosen.profile.model as $m | (["opencode/muse-spark-1.3-contributor-free", "opencode-go/muse-spark-1.3-contributor"] | index($m) != null)))
          ) then " --dispatch-ladder opencode" else "" end) else empty end)' <<<"$RESULT") || emit_error "output rendering failed"
if [ "$JSON_MODE" -eq 1 ]; then
  jq -c '
    def ladder_route:
      .source == "default" and (
        (.chosen.profile.harness == "codex" and .chosen.profile.model == "gpt-6-luna") or
        (.chosen.profile.harness == "opencode" and (.chosen.profile.model as $m | (["opencode/muse-spark-1.3-contributor-free", "opencode-go/muse-spark-1.3-contributor"] | index($m) != null)))
      );
    {
      status,
      source: (if (.source // "") | startswith("rule_") then (.source | sub("^rule_"; "rule:")) else .source end),
      reason: (.reason // .note // ""),
      profile: (if .chosen then {
        harness: .chosen.profile.harness,
        model: (.chosen.profile.model // null),
        effort: (.chosen.profile.effort // null),
        dispatch_ladder: (if ladder_route then "opencode" else null end)
      } else null end)
    }
  ' <<<"$RESULT" || emit_error "JSON output rendering failed"
  exit 0
fi
printf '%s\n' "$TEXT"
for rung in free plus go; do
  case "$rung" in free) label='OpenCode free' ;; plus) label='Codex Plus' ;; go) label='OpenCode Go' ;; esac
  cap=$("$SCRIPT_DIR/fm-opencode-retry.sh" check-cap "$STATE" "$rung" 2>/dev/null) || cap=
  case "$cap" in
    *'status=blocked'*)
      reset_at=$(printf '%s\n' "$cap" | sed -n 's/.*reset_at=//p')
      reset_iso=$(jq -nr --arg reset "$reset_at" 'try ($reset | tonumber | gmtime | strftime("%Y-%m-%dT%H:%M:%SZ")) catch "unknown"' 2>/dev/null)
      printf '  cap: %s capped until %s\n' "$label" "${reset_iso:-unknown}"
      ;;
  esac
done
exit 0
