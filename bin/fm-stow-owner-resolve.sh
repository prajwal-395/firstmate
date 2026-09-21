#!/usr/bin/env bash
# fm-stow-owner-resolve.sh - resolve one stow finding's owner with typesafe.ai's
# System One model (Jev), opt-in.
#
# Usage:
#   fm-stow-owner-resolve.sh <learning-file> [--secondmate-home]
#
# Opt-in gate: TYPESAFE_API_KEY or AI_GATEWAY_API_KEY non-empty in this process
#   environment, else a matching KEY= line in $FM_HOME/.env read with
#   fmx_env_get, the same accessor as FMX_PAIRING_TOKEN (bin/fm-env-lib.sh).
#   The environment wins. Absent in both places for both keys: one
#   "stow-owner-resolve: off" line on stderr, nothing on stdout, exit 0, no
#   network call, so the stow pass routes exactly as today. Each key lives in
#   one shell variable and reaches curl as a header read from a file
#   descriptor, never on argv; nothing logs or writes either key.
#
# Ladder: with both keys present the tool tries the free Vercel AI Gateway
#   rung first and falls back to the captain's typesafe.ai key once, in the
#   same invocation, when the gateway answers 429 or 401/403. The fallback is
#   per request with no persisted rung record, so every call re-derives the
#   answer. With one key present the tool uses that rung only.
#
# What it does when on: one POST asking the Jev question, answered by
#   whichever rung serves it. Gateway rung: model typesafe-ai/jev at
#   https://ai-gateway.vercel.sh/typesafe/v1/systemone. Typesafe rung: model
#   jev-latest at https://api.typesafe.ai/v1/systemone, what the sibling
#   dispatch tool did before the ladder. Both rungs accept the same request
#   and response shapes; only the base URL, model, and key change. The free
#   tier answers 429 when exhausted, which descends the ladder with auth
#   failures (401/403). The candidate learning text travels as state and ONE
#   Choice question whose options are the seven fixed knowledge owners from
#   AGENTS.md section 6 plus one fixed neutral none option. Jev returns the
#   matched owner, a probability per option, and a confidence. Everything
#   after that is jq: the confidence floor and the four code gates below.
#   The model never assigns tiers, pinning, or aging; those stay in the stow
#   skill where they are.
#
# Code gates, in order, after the answer:
#   (a) secondmate read-only reroute: --secondmate-home plus a
#       captain-shared-md answer stays clear but writes route-to-primary,
#       never an in-place edit of the primary-owned shared file.
#   (b) project-memory gate: a project-agents-md answer stays clear but
#       writes via-delivery-path, never a direct edit of a project's
#       AGENTS.md (Firstmate never writes a project's AGENTS.md directly).
#   (c) tier gate: file tier defaults, pinning, and aging apply after
#       routing; the model never assigns them.
#   (d) evidence gate: reinforcement still requires nameable session
#       evidence; routing confidence never renews an entry's lease.
#   docs/configuration.md "Stow owner resolution" owns this tool's operator
#   contract.
#
# Output (stdout, TOON-style block):
#   stow-owner-resolve:
#     status: clear | ambiguous | error
#     model/latency_ms/tokens, rung (gateway | typesafe), match and confidence, probabilities
#     reason: <why the status is not clear>
#     note: <write-path and tier/evidence reminders>     (status clear only)
#     owner: <slug>                                      (status clear only)
#     write: direct | route-to-primary | via-delivery-path   (status clear only)
#   clear     -> file the finding under owner: through the write path named
#                by the write: line, then apply tier defaults as today
#   ambiguous -> confidence below the floor; route manually as today
#   error     -> API, network, or response failure; route manually as today
#   Every outcome exits 0 so a stow pass is never blocked by this tool.
#   Exit 2 only for a usage or configuration error (missing or unreadable
#   learning file, or missing jq), which is actionable, never selected around.
#
# Environment:
#   TYPESAFE_API_KEY and AI_GATEWAY_API_KEY are the resolver-specific
#   environment settings. Either one opts the tool in; both together arm the
#   gateway-first ladder.
#
# Authority: this tool never replaces the stow pass, the tier clocks, or the
#   write boundaries; it publishes one inspectable answer, in code.
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

CONFIDENCE_FLOOR=0.6
TS_MODEL=jev-latest
TS_BASE=https://api.typesafe.ai
GW_MODEL=typesafe-ai/jev
GW_BASE=https://ai-gateway.vercel.sh/typesafe
TS_TIMEOUT=5
NONE_CRITERION="No listed owner applies; file elsewhere or drop."

die() { printf 'error: %s\n' "$1" >&2; exit 2; }
usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

LEARNING='' SECOND_MATE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --secondmate-home) SECOND_MATE=1; shift ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) [ -z "$LEARNING" ] || die "one learning file only"; LEARNING=$1; shift ;;
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
  echo "stow-owner-resolve: off (TYPESAFE_API_KEY absent from the environment and $FM_HOME/.env; AI_GATEWAY_API_KEY absent too)" >&2
  exit 0
fi

# ---- inputs --------------------------------------------------------------------
[ -n "$LEARNING" ] || die "learning file required (see --help)"
[ -r "$LEARNING" ] || die "learning file not readable: $LEARNING"
command -v jq >/dev/null 2>&1 || die "jq required"
# The whole file travels: one candidate learning per call, no scaffold to strip.
LEARNING_TEXT=$(cat "$LEARNING")
[ -n "$LEARNING_TEXT" ] || die "learning file is empty: $LEARNING"

RESP_FILE=$(mktemp) || die "mktemp failed"
trap 'rm -f "$RESP_FILE"' EXIT
LAT_MS=null
RUNG=''
command -v curl >/dev/null 2>&1 || {
  echo "stow-owner-resolve: error (curl not installed)" >&2
  printf 'stow-owner-resolve:\n  status: error\n  reason: curl not installed\n'
  exit 0
}
emit_error() {
  local reason=$1
  echo "stow-owner-resolve: error ($reason)" >&2
  printf 'stow-owner-resolve:\n  status: error\n  reason: %s\n' "$reason"
  exit 0
}
post_rung() { # <base-url> <model> <key>: one Jev POST; sets HTTP and LAT_MS
  local base=$1 model=$2 key=$3
  REQUEST=$(jq -n --arg learning "$LEARNING_TEXT" --arg model "$model" \
    --arg none_criterion "$NONE_CRITERION" '
    {
      model: $model,
      state: {task: $learning},
      questions: {
        owner: {
          type: "choice",
          instructions: "Which ONE owner does this learning belong to? Pick the most specific owner.",
          criteria: {
            "captain-md": "Home-domain captain preferences and working style.",
            "captain-shared-md": "Captain preferences shared across secondmate domains.",
            "learnings-md": "Fleet-local operational facts.",
            "backlog-note": "Notes scoped to one task; filed with the backlog item.",
            "scout-report": "Investigation findings; filed in the scout report.",
            "project-agents-md": "Knowledge useful to almost every contributor to one project.",
            "firstmate-tracked": "Knowledge general to every firstmate user.",
            "elsewhere-or-drop": $none_criterion
          }
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
jq -e '
    ["backlog-note", "captain-md", "captain-shared-md", "elsewhere-or-drop", "firstmate-tracked", "learnings-md", "project-agents-md", "scout-report"] as $choices |
    (.answers.owner.choice | type) == "string" and
    (.answers.owner.confidence | type) == "number" and
    .answers.owner.confidence >= 0 and .answers.owner.confidence <= 1 and
    (.answers.owner.probabilities | type) == "object" and
    ((.answers.owner.probabilities | keys | sort) == $choices) and
    all(.answers.owner.probabilities[]; type == "number" and . >= 0 and . <= 1) and
    ((.answers.owner.probabilities | [.[]] | add) as $total | $total >= 0.99 and $total <= 1.01) and
    ((has("usage") | not) or
      ((.usage | type) == "object" and
       (.usage.input_tokens | type) == "number" and
       (.usage.output_tokens | type) == "number"))' \
  "$RESP_FILE" >/dev/null 2>&1 || emit_error "response is not an owner Choice answer"

# ---- resolution: floor plus the four code gates, all in jq ---------------------
RESULT=$(jq -n --arg floor "$CONFIDENCE_FLOOR" --argjson lat "$LAT_MS" --arg rung "$RUNG" \
  --argjson second_mate "$SECOND_MATE" \
  --slurpfile resp "$RESP_FILE" '
  ($resp[0]) as $r | ($r.answers.owner) as $a |
  {
    model: $r.model, latency_ms: $lat, rung: $rung, tokens: ($r.usage // null),
    match: $a.choice,
    match_criterion: ({
      "captain-md": "Home-domain captain preferences and working style.",
      "captain-shared-md": "Captain preferences shared across secondmate domains.",
      "learnings-md": "Fleet-local operational facts.",
      "backlog-note": "Notes scoped to one task; filed with the backlog item.",
      "scout-report": "Investigation findings; filed in the scout report.",
      "project-agents-md": "Knowledge useful to almost every contributor to one project.",
      "firstmate-tracked": "Knowledge general to every firstmate user.",
      "elsewhere-or-drop": "No listed owner applies; file elsewhere or drop."
    }[$a.choice]),
    confidence: $a.confidence, probabilities: $a.probabilities
  } as $ev |
  if $ev.match_criterion == null then
    $ev + {status: "error", reason: "owner \($a.choice) is not a known owner"}
  elif $a.confidence < ($floor | tonumber) then
    $ev + {status: "ambiguous", reason: "confidence \($a.confidence) below floor \($floor)"}
  elif $second_mate == 1 and $a.choice == "captain-shared-md" then
    $ev + {status: "clear", owner: $a.choice, write: "route-to-primary",
      note: "secondmate home: captain-shared-md is primary-owned read-only; route the finding to the primary owner instead of editing in place. File tier defaults apply after routing; reinforcement still needs session evidence."}
  elif $a.choice == "project-agents-md" then
    $ev + {status: "clear", owner: $a.choice, write: "via-delivery-path",
      note: "project-agents-md is never written directly; route through a crewmate ship task on the project delivery path. File tier defaults apply after routing; reinforcement still needs session evidence."}
  else
    $ev + {status: "clear", owner: $a.choice, write: "direct",
      note: "File tier defaults apply after routing; reinforcement still needs session evidence."}
  end') || emit_error "resolution failed"

TEXT=$(jq -r '
  def flat: tostring | gsub("[\t\r\n]"; " ");
  def show($value): ($value // "-") | flat;
  "stow-owner-resolve:",
  "  status: \(.status | flat)",
  "  model: \(show(.model))   latency_ms: \(show(.latency_ms))   tokens: \(show(.tokens.input_tokens))/\(show(.tokens.output_tokens))",
  "  rung: \(.rung | flat)",
  "  match: \(.match | flat) (\(.match_criterion // "-" | flat))   confidence: \(.confidence | flat)",
  "  probabilities: \([.probabilities | to_entries[] | "\(.key | flat)=\(.value | flat)"] | join(" "))",
  (if .reason then "  reason: \(.reason | flat)" else empty end),
  (if .note then "  note: \(.note | flat)" else empty end),
  (if .owner then "  owner: \(.owner | flat)" else empty end),
  (if .write then "  write: \(.write | flat)" else empty end)' <<<"$RESULT") || emit_error "output rendering failed"
printf '%s\n' "$TEXT"
exit 0
