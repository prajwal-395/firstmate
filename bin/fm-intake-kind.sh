#!/usr/bin/env bash
# fm-intake-kind.sh - classify one intake as ship or scout with typesafe.ai's
# System One model (Jev), opt-in.
#
# Usage:
#   fm-intake-kind.sh <intake-file>
#
# Opt-in gate: TYPESAFE_API_KEY or AI_GATEWAY_API_KEY non-empty in this process
#   environment, else a matching KEY= line in $FM_HOME/.env read with
#   fmx_env_get, the same accessor as FMX_PAIRING_TOKEN (bin/fm-env-lib.sh).
#   The environment wins. Absent in both places for both keys: one
#   "intake-kind: off" line on stderr, nothing on stdout, exit 0, no
#   network call, so firstmate classifies exactly as today. Each key lives in
#   one shell variable and reaches curl as a header read from a file
#   descriptor, never on argv; nothing logs or writes either key.
#
# Ladder: with both keys present the tool tries the free Vercel AI Gateway
#   rung first and falls back to the captain's typesafe.ai key once, in the
#   same invocation, when the gateway answers 429 or 401/403. The fallback is
#   per request with no persisted rung record, so every call re-derives the
#   answer. With one key present the tool uses that rung only.
#
# What it does when on: one POST asking the kind question, answered by
#   whichever rung serves it. Gateway rung: model typesafe-ai/jev at
#   https://ai-gateway.vercel.sh/typesafe/v1/systemone. Typesafe rung: model
#   jev-latest at https://api.typesafe.ai/v1/systemone, what the dispatch
#   resolver uses. Both rungs accept the same request and response shapes;
#   only the base URL, model, and key change. The free tier answers 429 when
#   exhausted, which descends the ladder with auth failures (401/403). The
#   whole intake file (the captain request text plus any report, decision, or
#   PR text the caller concatenated into it) travels as state and ONE Choice
#   question `kind` with exactly two options. Jev returns the matched kind, a
#   probability per option, and a confidence. Everything after that is shell
#   and jq: the explicit-knowledge-request string gate, the diagnostic-evidence
#   gate, the confidence floor with its asymmetric low-confidence rule, and the
#   existing-evidence advisory. docs/configuration.md "Typed intake
#   classification" owns this tool's operator contract.
#
# Output (stdout, TOON-style block):
#   intake-kind:
#     status: clear | ambiguous | escalate | error
#     model/latency_ms/tokens, rung (gateway | typesafe), answer and confidence, probabilities
#     gate: <which code gate fired and why>
#     note: <low-confidence handling or absorb check>
#     kind: ship | scout     (status clear or ambiguous only)
#   clear     -> the kind is decided; ship work proceeds through the selected
#                delivery mode, scout work through the knowledge path
#   ambiguous -> an uncertain ship answer below the floor; ship only after the
#                existing human-readable checks (explicit project match,
#                delivery-mode resolution), exactly as today
#   escalate  -> a ship answer whose only basis is diagnostic evidence is held
#                for authorization; decide as today
#   error     -> API, network, or response failure; classify as today
#   Every outcome exits 0 so an intake is never blocked by this tool.
#   Exit 2 only for a usage or configuration error (missing or unreadable
#   intake file, an empty intake file, or missing jq), which is actionable,
#   never selected around.
#
# Environment:
#   TYPESAFE_API_KEY and AI_GATEWAY_API_KEY are the classifier-specific
#   environment settings. Either one opts the tool in; both together arm the
#   gateway-first ladder.
#
# Authority: this tool never replaces firstmate's judgment, the intake
#   classifier in AGENTS.md section 7, or fm-spawn.sh validation; it publishes
#   one inspectable answer plus the gate evidence, in code.
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

KIND_QUESTION_INSTRUCTIONS="Is the deliverable a project change or a knowledge deliverable?"
KIND_CRITERION_SHIP="Default: produces a project change through the selected delivery mode."
KIND_CRITERION_SCOUT="Produces knowledge, never a PR: the captain explicitly requests a separate knowledge or design deliverable, or unresolved uncertainty could materially change whether or what to build."

# Code-gate word lists. This header owns them; the gates below apply them as
# case-insensitive whole-word string matches, never model judgment. Gate (a)
# fires only on a request-shaped mention: a request verb plus a knowledge
# noun ("write up the report"), an imperative investigation lead ("audit the
# retry path"), or a look-into/find-out phrasing. Merely citing attached
# evidence ("the scout report is attached") is not a request; that shape
# belongs to gate (b). A ship answer that only cites diagnostic evidence
# without an implementation verb is held (gate b).
# Bare "plan" stays out of the knowledge nouns: "I plan to ..." is not a
# request for a deliverable, and the model criterion already covers the
# captain explicitly requesting a separate design deliverable.
KNOWLEDGE_NOUNS='report|reports|reported|reporting|investigation|investigations|diagnose|diagnoses|diagnosed|diagnosing|diagnosis|diagnostic|diagnostics|audit|audits|audited|auditing|proposal|proposals|finding|findings|recommendation|recommendations|recommend|recommends|recommended|research|researches|researched|scout|scouts|scouted|scouting|design doc|design docs'
REQUEST_VERBS='write|writes|writing|written|wrote|draft|drafts|drafted|drafting|prepare|prepares|prepared|preparing|produce|produces|produced|producing|create|creates|created|creating|give|gives|gave|given|giving|send|sends|sent|sending|share|shares|shared|sharing|publish|publishes|published|publishing|file|files|filed|filing|deliver|delivers|delivered|delivering|author|authors|authored|update|updates|updated|updating|refresh|refreshes|refreshed|revise|revises|revised|summarize|summarizes|summarized|summarizing|compile|compiles|compiled'
IMPERATIVE_LEAD='^[[:space:]]*(please[[:space:]]+)?(investigat|diagnos|audit|research)'
PHRASAL_REQUEST='look into|find out'
EVIDENCE_WORDS='report|reports|reported|finding|findings|diagnose|diagnoses|diagnosed|diagnosing|diagnosis|diagnostic|diagnostics|recommendation|recommendations|recommend|recommends|recommended|audit|audits|audited|investigation|investigations|review|reviews|analysis|analyses|assessment|assessments'
AUTHORIZE_VERBS='fix|fixes|fixed|fixing|implement|implements|implemented|implementing|change|changes|changed|changing|update|updates|updated|updating|add|adds|added|adding|create|creates|created|creating|build|builds|built|building|ship|ships|shipped|shipping|modify|modifies|modified|modifying|refactor|refactors|refactored|refactoring|patch|patches|patched|patching|migrate|migrates|migrated|migrating|remove|removes|removed|removing|delete|deletes|deleted|deleting|land|lands|landed|landing|merge|merges|merged|merging|apply|applies|applied|applying|correct|corrects|corrected|correcting|resolve|resolves|resolved|resolving|resolution'
INTERROGATIVE_LEAD='^[[:space:]]*(who|what|when|where|why|how|is|are|can|could|does|do|should|which)[^[:alnum:]]'

die() { printf 'error: %s\n' "$1" >&2; exit 2; }
usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

INTAKE=''
while [ $# -gt 0 ]; do
  case "$1" in
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
  echo "intake-kind: off (TYPESAFE_API_KEY absent from the environment and $FM_HOME/.env; AI_GATEWAY_API_KEY absent too)" >&2
  exit 0
fi

# ---- inputs --------------------------------------------------------------------
[ -n "$INTAKE" ] || die "intake file required (see --help)"
[ -r "$INTAKE" ] || die "intake file not readable: $INTAKE"
command -v jq >/dev/null 2>&1 || die "jq required"
INTAKE_TEXT=$(cat "$INTAKE")
[ -n "$INTAKE_TEXT" ] || die "intake file is empty: $INTAKE"

emit_error() {
  local reason=$1
  echo "intake-kind: error ($reason)" >&2
  printf 'intake-kind:\n  status: error\n  reason: %s\n' "$reason"
  exit 0
}

RESP_FILE=$(mktemp) || die "mktemp failed"
trap 'rm -f "$RESP_FILE"' EXIT
LAT_MS=null
RUNG=''
command -v curl >/dev/null 2>&1 || emit_error "curl not installed"
post_rung() { # <base-url> <model> <key>: one Jev POST; sets HTTP and LAT_MS
  local base=$1 model=$2 key=$3
  REQUEST=$(jq -n --arg task "$INTAKE_TEXT" --arg model "$model" \
    --arg instructions "$KIND_QUESTION_INSTRUCTIONS" \
    --arg ship "$KIND_CRITERION_SHIP" --arg scout "$KIND_CRITERION_SCOUT" '
    {
      model: $model,
      state: {task: $task},
      questions: {
        kind: {
          type: "choice",
          instructions: $instructions,
          criteria: {ship: $ship, scout: $scout}
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
    (.answers.kind.choice | type) == "string" and
    (.answers.kind.choice == "ship" or .answers.kind.choice == "scout") and
    (.answers.kind.confidence | type) == "number" and
    .answers.kind.confidence >= 0 and .answers.kind.confidence <= 1 and
    (.answers.kind.probabilities | type) == "object" and
    ((.answers.kind.probabilities | keys | sort) == ["scout", "ship"]) and
    all(.answers.kind.probabilities[]; type == "number" and . >= 0 and . <= 1) and
    ((.answers.kind.probabilities | [.[]] | add) as $total | $total >= 0.99 and $total <= 1.01) and
    ((has("usage") | not) or
      ((.usage | type) == "object" and
       (.usage.input_tokens | type) == "number" and
       (.usage.output_tokens | type) == "number"))' \
  "$RESP_FILE" >/dev/null 2>&1 || emit_error "response is not a kind Choice answer"

ANSWER=$(jq -r '.answers.kind.choice' "$RESP_FILE")
CONFIDENCE=$(jq -r '.answers.kind.confidence' "$RESP_FILE")

# ---- code gates: string predicates on the intake text, never model judgment ---
gate_a_request() { # request verb plus knowledge noun in the same intake
  printf '%s' "$INTAKE_TEXT" | grep -Eqi -w "$REQUEST_VERBS" &&
    printf '%s' "$INTAKE_TEXT" | grep -Eqi -w "$KNOWLEDGE_NOUNS"
}
gate_a_imperative() { # the intake leads with investigate/diagnose/audit/research
  printf '%s' "$INTAKE_TEXT" | grep -Eqi "$IMPERATIVE_LEAD"
}
gate_a_phrasal() { # look-into/find-out phrasing is inherently a request
  printf '%s' "$INTAKE_TEXT" | grep -Eqi -w "$PHRASAL_REQUEST"
}
GATE='' KIND='' STATUS='' NOTE=''
if gate_a_request || gate_a_imperative || gate_a_phrasal; then
  # Gate (a): the captain explicitly requested a knowledge deliverable, so the
  # kind is scout regardless of the model answer.
  GATE="explicit-knowledge-request: the intake names a knowledge deliverable, so the kind is scout regardless of the answer"
  KIND=scout
  STATUS=clear
elif [ "$ANSWER" = "ship" ] \
  && printf '%s' "$INTAKE_TEXT" | grep -Eqi -w "$EVIDENCE_WORDS" \
  && ! printf '%s' "$INTAKE_TEXT" | grep -Eqi -w "$AUTHORIZE_VERBS"; then
  # Gate (b): a diagnostic request, report, recommendation, or
  # implementation-ready finding is evidence, not authorization to change code.
  GATE="diagnostic-evidence: a ship answer whose only basis is diagnostic evidence is held for authorization"
  STATUS=escalate
elif awk "BEGIN{exit !(($CONFIDENCE) >= ($CONFIDENCE_FLOOR))}"; then
  STATUS=clear
  KIND=$ANSWER
  if [ "$ANSWER" = "scout" ] && { printf '%s' "$INTAKE_TEXT" | grep -Eqi "$INTERROGATIVE_LEAD" || printf '%s' "$INTAKE_TEXT" | grep -Eq '\?[[:space:]]*$'; }; then
    # Gate (c): established evidence may already answer an informational
    # question, so flag the absorb check without changing the kind.
    NOTE="existing-evidence check: established evidence may already answer this question; relay it without a scout when it does"
  fi
elif [ "$ANSWER" = "scout" ]; then
  # Asymmetric floor: an uncertain scout recommendation must not silently
  # become code changes, so it stays scout.
  STATUS=clear
  KIND=scout
  NOTE="confidence $CONFIDENCE below floor $CONFIDENCE_FLOOR; uncertain scout stays scout"
else
  # An uncertain ship still passes the existing human-readable checks, so it
  # returns to manual classification with its lean recorded.
  STATUS=ambiguous
  KIND=ship
  NOTE="confidence $CONFIDENCE below floor $CONFIDENCE_FLOOR; uncertain ship still passes the existing human-readable checks (explicit project match, delivery-mode resolution)"
fi

TEXT=$(jq -n -r --arg status "$STATUS" --arg answer "$ANSWER" --arg confidence "$CONFIDENCE" \
  --arg kind "$KIND" --arg gate "$GATE" --arg note "$NOTE" --arg rung "$RUNG" \
  --argjson lat "$LAT_MS" --slurpfile resp "$RESP_FILE" '
  def flat: tostring | gsub("[\t\r\n]"; " ");
  def show($value): ($value // "-") | flat;
  ($resp[0]) as $r |
  "intake-kind:",
  "  status: \($status | flat)",
  "  model: \(show($r.model))   latency_ms: \(show($lat))   tokens: \(show($r.usage.input_tokens))/\(show($r.usage.output_tokens))",
  "  rung: \($rung | flat)",
  "  answer: \($answer | flat)   confidence: \($confidence | flat)",
  "  probabilities: scout=\($r.answers.kind.probabilities.scout) ship=\($r.answers.kind.probabilities.ship)",
  (if $gate != "" then "  gate: \($gate | flat)" else empty end),
  (if $note != "" then "  note: \($note | flat)" else empty end),
  (if $kind != "" then "  kind: \($kind | flat)" else empty end)') || emit_error "output rendering failed"
printf '%s\n' "$TEXT"
exit 0
