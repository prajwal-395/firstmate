#!/usr/bin/env bash
# fm-mate-route.sh - route one intake to at most one secondmate with
# typesafe.ai's System One model (Jev), opt-in.
#
# Usage:
#   fm-mate-route.sh <intake-file> [--project <name>] [--registry <path>]
#     [--redirect <mate-id|main>] [--blocked <id,...>] [--unreachable <id,...>]
#
# Opt-in gate: TYPESAFE_API_KEY or AI_GATEWAY_API_KEY non-empty in this process
#   environment, else a matching KEY= line in $FM_HOME/.env read with
#   fmx_env_get, the same accessor as FMX_PAIRING_TOKEN (bin/fm-env-lib.sh).
#   The environment wins. Absent in both places for both keys: one
#   "mate-route: off" line on stderr, nothing on stdout, exit 0, no
#   network call, so firstmate routes exactly as today. Each key lives in
#   one shell variable and reaches curl as a header read from a file
#   descriptor, never on argv; nothing logs or writes either key.
#
# Ladder: with both keys present the tool tries the free Vercel AI Gateway
#   rung first and falls back to the captain's typesafe.ai key once, in the
#   same invocation, when the gateway answers 429 or 401/403. The fallback is
#   per request with no persisted rung record, so every call re-derives the
#   answer. With one key present the tool uses that rung only.
#
# What it does when on: one POST asking the mate question, answered by
#   whichever rung serves it. Gateway rung: model typesafe-ai/jev at
#   https://ai-gateway.vercel.sh/typesafe/v1/systemone. Typesafe rung: model
#   jev-latest at https://api.typesafe.ai/v1/systemone, what the dispatch
#   resolver uses. Both rungs accept the same request and response shapes;
#   only the base URL, model, and key change. The free tier answers 429 when
#   exhausted, which descends the ladder with auth failures (401/403). The
#   intake text plus the resolved project travel as state and ONE Choice
#   question `mate` with one option per registry entry plus a fixed default.
#   Jev returns the matched mate, a probability per option, and a confidence.
#   Everything after that is shell and jq: the local-only gate, the
#   captain-redirect gate, the liveness gate, and the confidence floor.
#   docs/configuration.md "Typed secondmate routing" owns this tool's
#   operator contract.
#
# Option source: the `scope:` field of each entry in the secondmate registry
#   (default $FM_HOME/data/secondmates.md, overridden by --registry).
#   Format owner: the secondmate-provisioning skill (scope: is the
#   natural-language intake responsibility; projects: is the non-exclusive
#   clone list). The model never sees `projects:`, only `scope:`, so it
#   cannot learn the wrong key; that exclusion is gate (b) and it is
#   structural, in the request builder below.
#
# Code gates, in order. (a) Local-only: the resolved project (--project)
#   through bin/fm-project-mode.sh; a local-only project stays with the main
#   home no matter what the answer says, and no scope text overrides that.
#   (d) Captain-redirect: an explicit --redirect wins over the model answer;
#   --redirect main keeps the work, --redirect <id> names the mate.
#   (c) Liveness: the selected mate must be neither --blocked nor
#   --unreachable, else the work falls through to the main home. Firstmate
#   passes --blocked/--unreachable from its own current-state reconciliation
#   (bin/fm-crew-state.sh); this tool never guesses liveness itself, and
#   absent flags mean no known blockage. A redirected-then-unreachable target
#   escalates instead of routing main, because the captain's explicit target
#   cannot be satisfied. The confidence floor (0.6) applies to the model
#   answer only: below it the intake returns unrouted with its probabilities
#   recorded, never a guessed mate.
#
# Fallback path (current behavior, quoted): `Send in-scope work to the
#   fitting secondmate unless it is blocked or the captain explicitly
#   redirects it; do not read the secondmate's chat because marked routed
#   replies return through its status or referenced document.` (AGENTS.md)
#   plus `If no secondmate scope fits, use the main home or discuss creating
#   an appropriate persistent secondmate.` (AGENTS.md). Off, ambiguous, and
#   error outcomes all mean exactly this: firstmate reads the scope text by
#   hand as today.
#
# Output (stdout, TOON-style block):
#   mate-route:
#     status: clear | ambiguous | escalate | error
#     model/latency_ms/tokens, rung (gateway | typesafe), answer and confidence, probabilities
#     gate: <which code gate fired and why>
#     reason: <why the status is not clear>
#     mate: <id> | main     (status clear only)
#   clear     -> the mate line is the route; main means the main home keeps it
#   ambiguous -> confidence below the floor; the intake returns unrouted with
#                its lean recorded, and firstmate routes by hand as today
#   escalate  -> no routable scope (empty registry), or the captain's explicit
#                redirect target is blocked or unreachable; decide as today
#   error     -> API, network, or response failure; route as today
#   Every outcome exits 0 so an intake is never blocked by this tool.
#   Exit 2 only for a usage or configuration error (missing, unreadable, or
#   empty intake file, missing jq, an unreadable registry, or a --redirect,
#   --blocked, or --unreachable naming an unregistered id), which is
#   actionable, never selected around.
#
# Environment:
#   TYPESAFE_API_KEY and AI_GATEWAY_API_KEY are the router-specific
#   environment settings. Either one opts the tool in; both together arm the
#   gateway-first ladder.
#
# Authority: this tool never replaces firstmate's judgment, the secondmate
#   routing rules in AGENTS.md section 7, or fm-spawn.sh validation; it
#   publishes one inspectable answer plus the gate evidence, in code.
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

# shellcheck source=bin/fm-env-lib.sh
. "$SCRIPT_DIR/fm-env-lib.sh"
# shellcheck source=bin/fm-timing-lib.sh
. "$SCRIPT_DIR/fm-timing-lib.sh"
# shellcheck source=bin/fm-secondmate-registry-lib.sh
. "$SCRIPT_DIR/fm-secondmate-registry-lib.sh"

CONFIDENCE_FLOOR=0.6
TS_MODEL=jev-latest
TS_BASE=https://api.typesafe.ai
GW_MODEL=typesafe-ai/jev
GW_BASE=https://ai-gateway.vercel.sh/typesafe
TS_TIMEOUT=5

MATE_QUESTION_INSTRUCTIONS="Which ONE secondmate scope best fits this work? Pick an entry only when its scope text covers the work; pick default when no scope covers it."
MATE_CRITERION_DEFAULT="No listed scope applies; the main home keeps the work."

die() { printf 'error: %s\n' "$1" >&2; exit 2; }
usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

INTAKE='' PROJECT='' REGISTRY='' REDIRECT='' BLOCKED='' UNREACHABLE=''
while [ $# -gt 0 ]; do
  case "$1" in
    --project) [ $# -ge 2 ] || die "--project needs a value"; PROJECT=$2; shift 2 ;;
    --registry) [ $# -ge 2 ] || die "--registry needs a value"; REGISTRY=$2; shift 2 ;;
    --redirect) [ $# -ge 2 ] || die "--redirect needs a value"; REDIRECT=$2; shift 2 ;;
    --blocked) [ $# -ge 2 ] || die "--blocked needs a value"; BLOCKED=$2; shift 2 ;;
    --unreachable) [ $# -ge 2 ] || die "--unreachable needs a value"; UNREACHABLE=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) [ -z "$INTAKE" ] || die "one intake file only"; INTAKE=$1; shift ;;
  esac
done

# ---- opt-in gate ---------------------------------------------------------------
if [ -z "$TYPESAFE_API_KEY_PRIVATE" ]; then
  TYPESAFE_API_KEY_PRIVATE=$(fmx_env_get TYPESAFE_API_KEY "$FM_HOME/.env")
fi
if [ -z "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
  AI_GATEWAY_API_KEY_PRIVATE=$(fmx_env_get AI_GATEWAY_API_KEY "$FM_HOME/.env")
fi
if [ -z "$TYPESAFE_API_KEY_PRIVATE" ] && [ -z "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
  echo "mate-route: off (TYPESAFE_API_KEY absent from the environment and $FM_HOME/.env; AI_GATEWAY_API_KEY absent too)" >&2
  exit 0
fi

# ---- inputs --------------------------------------------------------------------
[ -n "$INTAKE" ] || die "intake file required (see --help)"
[ -r "$INTAKE" ] || die "intake file not readable: $INTAKE"
command -v jq >/dev/null 2>&1 || die "jq required"
INTAKE_TEXT=$(cat "$INTAKE")
[ -n "$INTAKE_TEXT" ] || die "intake file is empty: $INTAKE"
if [ -z "$REGISTRY" ]; then
  REGISTRY="$FM_HOME/data/secondmates.md"
fi

emit_error() {
  local reason=$1
  echo "mate-route: error ($reason)" >&2
  printf 'mate-route:\n  status: error\n  reason: %s\n' "$reason"
  exit 0
}

emit_escalate() {
  local reason=$1
  printf 'mate-route:\n  status: escalate\n  reason: %s\n' "$reason"
  exit 0
}

# ---- registry: ids and scope text only; projects: never travels ---------------
MATE_IDS=''
CRITERIA_JSON='{}'
if [ ! -e "$REGISTRY" ]; then
  emit_escalate "no secondmate scopes registered (no registry at $REGISTRY)"
fi
[ -r "$REGISTRY" ] || die "registry not readable: $REGISTRY"
while IFS= read -r line || [ -n "$line" ]; do
  case "$line" in ''|-*) ;; *) continue ;; esac
  case "$line" in '- '*) ;; *) continue ;; esac
  if ! secondmate_registry_parse_line "$line"; then
    echo "mate-route: warn: skipping malformed registry entry: $line" >&2
    continue
  fi
  id=$SECONDMATE_REGISTRY_ID
  scope=$SECONDMATE_REGISTRY_SCOPE
  case "$MATE_IDS" in
    *"|$id|"*) echo "mate-route: warn: skipping duplicate registry entry: $id" >&2; continue ;;
  esac
  MATE_IDS="$MATE_IDS|$id|"
  CRITERIA_JSON=$(jq -c --arg id "$id" --arg scope "$scope" '. + {($id): $scope}' <<<"$CRITERIA_JSON") \
    || emit_error "criteria rendering failed"
done < "$REGISTRY"
if [ -z "$MATE_IDS" ]; then
  emit_escalate "no secondmate scopes registered (no usable entry in $REGISTRY)"
fi

in_registry() { # <id>: 0 when the id names a registered mate
  case "$MATE_IDS" in *"|$1|"*) return 0 ;; *) return 1 ;; esac
}

if [ -n "$REDIRECT" ] && [ "$REDIRECT" != "main" ] && ! in_registry "$REDIRECT"; then
  die "--redirect names an unregistered secondmate: $REDIRECT"
fi
list_check() { # <flag> <csv>: every named id must be registered
  local flag=$1 rest=$2 name
  rest="$rest,"
  while [ -n "$rest" ]; do
    name=${rest%%,*}
    rest=${rest#*,}
    [ -n "$name" ] || die "--$flag names an empty secondmate id"
    in_registry "$name" || die "--$flag names an unregistered secondmate: $name"
  done
}
[ -z "$BLOCKED" ] || list_check blocked "$BLOCKED"
[ -z "$UNREACHABLE" ] || list_check unreachable "$UNREACHABLE"
is_listed() { # <csv> <id>: 0 when the id is in the comma list
  case ",$1," in *",$2,"*) return 0 ;; *) return 1 ;; esac
}

# ---- task state: the request text plus the resolved project --------------------
TASK_TEXT=$INTAKE_TEXT
if [ -n "$PROJECT" ]; then
  TASK_TEXT="Resolved project: $PROJECT

$INTAKE_TEXT"
fi

RESP_FILE=$(mktemp) || die "mktemp failed"
trap 'rm -f "$RESP_FILE"' EXIT
LAT_MS=null
RUNG=''
command -v curl >/dev/null 2>&1 || emit_error "curl not installed"
post_rung() { # <base-url> <model> <key>: one Jev POST; sets HTTP and LAT_MS
  local base=$1 model=$2 key=$3
  REQUEST=$(jq -n --arg task "$TASK_TEXT" --arg model "$model" \
    --arg instructions "$MATE_QUESTION_INSTRUCTIONS" \
    --arg default_criterion "$MATE_CRITERION_DEFAULT" --argjson scopes "$CRITERIA_JSON" '
    {
      model: $model,
      state: {task: $task},
      questions: {
        mate: {
          type: "choice",
          instructions: $instructions,
          criteria: ($scopes + {default: $default_criterion})
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
gateway_declined() { # <http>: 429 or auth failure means "not available now"
  case "$1" in 429|401|403) return 0 ;; *) return 1 ;; esac
}
if [ -n "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
  post_rung "$GW_BASE" "$GW_MODEL" "$AI_GATEWAY_API_KEY_PRIVATE"
  if [ "$HTTP" = 200 ]; then
    RUNG=gateway
  elif gateway_declined "$HTTP" && [ -n "$TYPESAFE_API_KEY_PRIVATE" ]; then
    GW_HTTP=$HTTP
    GW_LAT_MS=$LAT_MS
    GW_HEAD=$(head -c 200 "$RESP_FILE" 2>/dev/null | tr '\n' ' ')
    post_rung "$TS_BASE" "$TS_MODEL" "$TYPESAFE_API_KEY_PRIVATE"
    if [ "$HTTP" = 200 ]; then
      RUNG=typesafe
    else
      emit_error "gateway http $GW_HTTP after ${GW_LAT_MS} ms (${GW_HEAD}), then typesafe http $HTTP after ${LAT_MS} ms: $(head -c 200 "$RESP_FILE" 2>/dev/null | tr '\n' ' ')"
    fi
  else
    [ "$HTTP" = 200 ] || emit_error "http $HTTP after ${LAT_MS} ms: $(head -c 200 "$RESP_FILE" 2>/dev/null | tr '\n' ' ')"
    RUNG=gateway
  fi
else
  post_rung "$TS_BASE" "$TS_MODEL" "$TYPESAFE_API_KEY_PRIVATE"
  [ "$HTTP" = 200 ] || emit_error "http $HTTP after ${LAT_MS} ms: $(head -c 200 "$RESP_FILE" 2>/dev/null | tr '\n' ' ')"
  RUNG=typesafe
fi

# The choice set is registry-driven, so validation builds its expectation from
# the same criteria the request carried rather than a fixed word list.
EXPECTED_KEYS=$(jq -n -c --argjson scopes "$CRITERIA_JSON" '$scopes + {default: true} | keys | sort') \
  || emit_error "response validation setup failed"
jq -e --argjson expected "$EXPECTED_KEYS" '
    (.answers.mate.choice | type) == "string" and
    (.answers.mate.choice as $c | ([$expected[]] | index($c)) != null) and
    (.answers.mate.confidence | type) == "number" and
    .answers.mate.confidence >= 0 and .answers.mate.confidence <= 1 and
    (.answers.mate.probabilities | type) == "object" and
    ((.answers.mate.probabilities | keys | sort) == $expected) and
    all(.answers.mate.probabilities[]; type == "number" and . >= 0 and . <= 1) and
    ((.answers.mate.probabilities | [.[]] | add) as $total | $total >= 0.99 and $total <= 1.01) and
    ((has("usage") | not) or
      ((.usage | type) == "object" and
       (.usage.input_tokens | type) == "number" and
       (.usage.output_tokens | type) == "number"))' \
  "$RESP_FILE" >/dev/null 2>&1 || emit_error "response is not a mate Choice answer"

ANSWER=$(jq -r '.answers.mate.choice' "$RESP_FILE")
CONFIDENCE=$(jq -r '.answers.mate.confidence' "$RESP_FILE")

# ---- code gates after the answer, in report order ------------------------------
GATE='' MATE='' STATUS='' REASON='' NOTE=''
if [ -n "$PROJECT" ]; then
  MODE=$(FM_HOME="$FM_HOME" "$SCRIPT_DIR/fm-project-mode.sh" "$PROJECT" 2>/dev/null | awk '{print $1}') || MODE=''
  if [ "$MODE" = "local-only" ]; then
    # Gate (a): a safety boundary, not a preference. Local-only work stays
    # with the main home, and no scope text may override that.
    GATE="local-only: project $PROJECT is local-only, so the main home keeps the work regardless of the answer"
    MATE=main
    STATUS=clear
  fi
fi
if [ -z "$STATUS" ] && [ -n "$REDIRECT" ]; then
  # Gate (d): an explicit captain redirect wins over the model answer.
  if [ "$REDIRECT" = "main" ]; then
    GATE="captain-redirect: the captain kept this work with the main home"
    MATE=main
    STATUS=clear
  elif { [ -n "$BLOCKED" ] && is_listed "$BLOCKED" "$REDIRECT"; } \
    || { [ -n "$UNREACHABLE" ] && is_listed "$UNREACHABLE" "$REDIRECT"; }; then
    emit_escalate "the captain redirected this work to $REDIRECT, but that mate is blocked or unreachable"
  else
    GATE="captain-redirect: the captain routed this work to $REDIRECT"
    MATE=$REDIRECT
    STATUS=clear
  fi
fi
if [ -z "$STATUS" ] && [ "$ANSWER" != "default" ] \
  && { { [ -n "$BLOCKED" ] && is_listed "$BLOCKED" "$ANSWER"; } \
    || { [ -n "$UNREACHABLE" ] && is_listed "$UNREACHABLE" "$ANSWER"; }; }; then
  # Gate (c): the target is blocked or its endpoint is unreachable, so the
  # work falls through to the main home.
  GATE="liveness: mate $ANSWER is blocked or unreachable, so the main home keeps the work"
  MATE=main
  STATUS=clear
fi
if [ -z "$STATUS" ] && awk "BEGIN{exit !(($CONFIDENCE) >= ($CONFIDENCE_FLOOR))}"; then
  if [ "$ANSWER" = "default" ]; then
    NOTE="no listed scope covers this work; the main home keeps it"
  fi
  MATE=main
  [ "$ANSWER" = "default" ] || MATE=$ANSWER
  STATUS=clear
elif [ -z "$STATUS" ]; then
  # Below the floor the intake returns unrouted with its lean recorded. A
  # router that silently picks wrong is worse than the hand reading it
  # replaces, because the hand reading is visibly a judgement and a wrong
  # route is not.
  STATUS=ambiguous
  REASON="confidence $CONFIDENCE below floor $CONFIDENCE_FLOOR; unrouted, route by hand as today"
fi

TEXT=$(jq -n -r --arg status "$STATUS" --arg answer "$ANSWER" --arg confidence "$CONFIDENCE" \
  --arg mate "$MATE" --arg gate "$GATE" --arg reason "$REASON" --arg note "$NOTE" --arg rung "$RUNG" \
  --argjson lat "$LAT_MS" --slurpfile resp "$RESP_FILE" --argjson scopes "$CRITERIA_JSON" '
  def flat: tostring | gsub("[\t\r\n]"; " ");
  def show($value): ($value // "-") | flat;
  ($resp[0]) as $r |
  "mate-route:",
  "  status: \($status | flat)",
  "  model: \(show($r.model))   latency_ms: \(show($lat))   tokens: \(show($r.usage.input_tokens))/\(show($r.usage.output_tokens))",
  "  rung: \($rung | flat)",
  "  answer: \($answer | flat)   confidence: \($confidence | flat)",
  "  probabilities: " + ([$scopes | keys[] as $k | (($k | flat) + "=" + ($r.answers.mate.probabilities[$k] | tostring))] + ["default=" + ($r.answers.mate.probabilities.default | tostring)] | join(" ")),
  (if $gate != "" then "  gate: \($gate | flat)" else empty end),
  (if $reason != "" then "  reason: \($reason | flat)" else empty end),
  (if $note != "" then "  note: \($note | flat)" else empty end),
  (if $mate != "" then "  mate: \($mate | flat)" else empty end)') || emit_error "output rendering failed"
printf '%s\n' "$TEXT"
exit 0
