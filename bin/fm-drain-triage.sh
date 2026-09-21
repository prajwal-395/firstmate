#!/usr/bin/env bash
# fm-drain-triage.sh - classify one drain's queued wake rows as suppress-or-wake
# with typesafe.ai's System One model (Jev), opt-in, observe-only.
#
# Usage:
#   fm-drain-triage.sh [--state <dir>] [--rows-file <path>] [--actor <name>]
#
# A wake batch for records already closed elsewhere still costs a full
# supervisor turn that ends in "acknowledged, nothing to do".
# One Jev call per drain classifies each queued row before that turn starts.
# Iron rule: fail-open - errors and low-confidence answers wake exactly as
# today, because one suppressed real wake costs more than a hundred wasted
# turns.
#
# Opt-in gate: TYPESAFE_API_KEY or AI_GATEWAY_API_KEY non-empty in this process
#   environment, else a matching KEY= line in $FM_HOME/.env read with
#   fmx_env_get, the same accessor as FMX_PAIRING_TOKEN (bin/fm-env-lib.sh).
#   The environment wins. Absent in both places for both keys: one
#   "drain-triage: off" line on stderr, nothing on stdout, exit 0, no
#   network call, so the drain wakes exactly as today. Each key lives in
#   one shell variable and reaches curl as a header read from a file
#   descriptor, never on argv; nothing logs or writes either key.
#
# Ladder: with both keys present the tool tries the free Vercel AI Gateway
#   rung first and falls back to the captain's typesafe.ai key once, in the
#   same invocation, when the gateway answers 429 or 401/403. The fallback is
#   per request with no persisted rung record, so every call re-derives the
#   answer. With one key present the tool uses that rung only.
#   Gateway rung: model typesafe-ai/jev at
#   https://ai-gateway.vercel.sh/typesafe/v1/systemone. Typesafe rung: model
#   jev-latest at https://api.typesafe.ai/v1/systemone. Both rungs accept the
#   same request and response shapes; only the base URL, model, and key change.
#
# What it does when on: one POST carrying the presented rows plus the closing
#   markers already on disk as state, and one Choice question per classifiable
#   row with the fixed options `suppress` and `wake`. Suppress means the row's
#   event is already closed elsewhere (the referenced status file already
#   records its terminal outcome, or its decision key already resolved);
#   wake means handle it as today. When in doubt the model must pick wake.
#   Heartbeat rows never reach the model: heartbeat absorb, churn absorb,
#   merge-poll dedup, and inbox handling are out of scope, so those rows are
#   decided in code as wake. Everything after the answer runs in code: the
#   fixed 0.6 confidence floor per row, the heartbeat verdict, and the counts.
#
# Output (stdout, TOON-style block):
#   drain-triage:
#     status: clear | ambiguous | error
#     model/latency_ms/tokens, rung (gateway | typesafe)
#     row: <seq> <kind> <key> -> suppress | wake confidence=<c> [note]
#     counts: suppressed=X actionable=Y rows=N
#     reason: <why the status is not clear>
#   clear     -> every asked row answered at or above the floor
#   ambiguous -> at least one row answered below the floor; those rows wake,
#                the rest keep their verdict; the drain still wakes as today
#   error     -> API, network, or response failure; the drain wakes as today
#   Every outcome exits 0 so a drain is never blocked by this tool.
#   Exit 2 only for a usage or configuration error (unreadable rows file or
#   state directory, or missing jq), which is actionable, never selected around.
#
# Observe-only: this tool prints verdicts and counts but consumes nothing.
#   The drain records the counts line and still presents every row; no row is
#   suppressed anywhere until a later widening the week of counts justifies.
#
# Environment:
#   TYPESAFE_API_KEY and AI_GATEWAY_API_KEY are the triage-specific
#   environment settings. Either one opts the tool in; both together arm the
#   gateway-first ladder.
#   FM_DRAIN_TRIAGE_MAX_ROWS caps how many rows one call asks about
#   (default 32); rows past the cap wake in code as unasked.
#
# Authority: this tool never replaces the drain, the acknowledgement contract,
#   or firstmate's judgment; it publishes one inspectable answer per row, in code.
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
STATE="${FM_STATE_OVERRIDE:-$FM_HOME/state}"

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
MAX_ROWS=${FM_DRAIN_TRIAGE_MAX_ROWS:-32}
case "$MAX_ROWS" in ''|*[!0-9]*|0) MAX_ROWS=32 ;; esac
SUPPRESS_WHEN="This wake's event is already closed elsewhere: the referenced status file already records its terminal outcome, or its decision key already resolved."
WAKE_WHEN="Handle this wake as today: the event is still open, unread, or uncertain. When in doubt, pick wake."

die() { printf 'error: %s\n' "$1" >&2; exit 2; }
usage() {
  awk '
    NR == 1 { next }
    /^#/ { sub(/^# ?/, ""); print; next }
    { exit }
  ' "$0"
}

ROWS_FILE='' ACTOR="${FM_SUPERVISION_ACTOR:-main}"
while [ $# -gt 0 ]; do
  case "$1" in
    --state) [ $# -ge 2 ] || die "--state needs a value"; STATE=$2; shift 2 ;;
    --rows-file) [ $# -ge 2 ] || die "--rows-file needs a value"; ROWS_FILE=$2; shift 2 ;;
    --actor) [ $# -ge 2 ] || die "--actor needs a value"; ACTOR=$2; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    -*) die "unknown flag $1" ;;
    *) die "unexpected argument $1" ;;
  esac
done
case "$ACTOR" in main|branch) ;; *) die "actor must be main or branch" ;; esac
[ -d "$STATE" ] || die "state directory not readable: $STATE"
[ -z "$ROWS_FILE" ] && ROWS_FILE="$STATE/.wake-queue"
[ -r "$ROWS_FILE" ] || die "rows file not readable: $ROWS_FILE"

# ---- opt-in gate ---------------------------------------------------------------
if [ -z "$TYPESAFE_API_KEY_PRIVATE" ]; then
  TYPESAFE_API_KEY_PRIVATE=$(fmx_env_get TYPESAFE_API_KEY "$FM_HOME/.env")
fi
if [ -z "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
  AI_GATEWAY_API_KEY_PRIVATE=$(fmx_env_get AI_GATEWAY_API_KEY "$FM_HOME/.env")
fi
if [ -z "$TYPESAFE_API_KEY_PRIVATE" ] && [ -z "$AI_GATEWAY_API_KEY_PRIVATE" ]; then
  echo "drain-triage: off (TYPESAFE_API_KEY absent from the environment and $FM_HOME/.env; AI_GATEWAY_API_KEY absent too)" >&2
  exit 0
fi

# ---- inputs --------------------------------------------------------------------
command -v jq >/dev/null 2>&1 || die "jq required"
command -v curl >/dev/null 2>&1 || {
  printf 'drain-triage:\n  status: error\n  reason: curl not installed\n  counts: suppressed=0 actionable=0 rows=0\n'
  exit 0
}

emit_error() {
  local reason=$1 rows=${2:-0}
  echo "drain-triage: error ($reason)" >&2
  printf 'drain-triage:\n  status: error\n  reason: %s\n  counts: suppressed=0 actionable=%s rows=%s\n' "$reason" "$rows" "$rows"
  exit 0
}

# Valid rows only: five tab-separated fields with a numeric sequence, the same
# structural rule the drain's own unconsumable-row repair uses. Anything else
# can never be presented, so it is never classified.
VALID=$(mktemp) || die "mktemp failed"
RESP_FILE=''
ASKED=''
SNAPSHOT=''
trap 'rm -f "$VALID" "$RESP_FILE" "$ASKED" "$SNAPSHOT"' EXIT
awk -F '\t' 'NF >= 5 && $2 ~ /^[0-9]+$/ { print }' "$ROWS_FILE" > "$VALID" 2>/dev/null \
  || die "could not read rows file: $ROWS_FILE"
ROW_COUNT=$(awk 'END { print NR + 0 }' "$VALID")
if [ "$ROW_COUNT" -eq 0 ]; then
  printf 'drain-triage:\n  status: clear\n  reason: no classifiable rows\n  counts: suppressed=0 actionable=0 rows=0\n'
  exit 0
fi

# Closing markers already on disk, gathered in code and bounded: for a signal
# row the referenced status file's last lines are the marker that already
# closed the event elsewhere; for a check row the registered script's presence;
# heartbeat and stale rows carry no closable marker, and heartbeat rows never
# reach the model at all.
evidence_for() { # <kind> <key> -> one bounded evidence line on stdout
  local kind=$1 key=$2 task f line
  case "$kind" in
    heartbeat)
      printf 'decided in code: heartbeat rows always wake; heartbeat absorb is out of scope'
      return 0
      ;;
    signal)
      case "$key" in
        *.status) task=${key%.status} ;;
        *.turn-ended) task=${key%.turn-ended} ;;
        *) printf 'no status marker: key names no status file'; return 0 ;;
      esac
      case "$task" in ''|*[!A-Za-z0-9._-]*) printf 'no status marker: key names no status file'; return 0 ;; esac
      f="$STATE/$task.status"
      [ -f "$f" ] && [ ! -L "$f" ] && [ -r "$f" ] || { printf 'status file absent: %s' "$task.status"; return 0; }
      line=$(grep -v '^[[:space:]]*$' "$f" 2>/dev/null | tail -n 3 | LC_ALL=C tr '\t\r\n' '   ' | cut -c1-600)
      [ -n "$line" ] || { printf 'status file empty: %s' "$task.status"; return 0; }
      printf 'status %s last lines: %s' "$task.status" "$line"
      return 0
      ;;
    check)
      if [ -f "$STATE/$key" ] && [ ! -L "$STATE/$key" ]; then
        printf 'registered check script present: %s' "$key"
      else
        printf 'no check marker: %s not registered in state' "$key"
      fi
      return 0
      ;;
    stale)
      printf 'no closable marker: stale rows carry only their payload'
      return 0
      ;;
    *)
      printf 'unknown kind: wake'
      return 0
      ;;
  esac
}

# Split rows: heartbeat rows wake in code; the first MAX_ROWS of the rest are
# asked; any overflow wakes in code as unasked. Order follows the queue.
ASKED=$(mktemp) || die "mktemp failed"
SNAPSHOT=$(mktemp) || die "mktemp failed"
asked_count=0
while IFS=$(printf '\t') read -r _epoch seq kind key payload; do
  case "$kind" in signal|stale|check|heartbeat) ;; *) continue ;; esac
  case "$seq" in ''|*[!0-9]*) continue ;; esac
  if [ "$kind" = heartbeat ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$seq" "$kind" "$key" "wake" "-" "decided in code: heartbeat always wakes" >> "$SNAPSHOT"
    continue
  fi
  if [ "$asked_count" -ge "$MAX_ROWS" ]; then
    printf '%s\t%s\t%s\t%s\t%s\t%s\n' "$seq" "$kind" "$key" "wake" "-" "decided in code: over row cap $MAX_ROWS, unasked" >> "$SNAPSHOT"
    continue
  fi
  asked_count=$((asked_count + 1))
  ev=$(evidence_for "$kind" "$key")
  jq -n --arg seq "$seq" --arg kind "$kind" --arg key "$key" --arg payload "$payload" --arg evidence "$ev" \
    '{seq: $seq, kind: $kind, key: $key, payload: $payload, evidence: $evidence}' >> "$ASKED"
done < "$VALID"
[ "$asked_count" -gt 0 ] || {
  TEXT=$(awk -F '\t' '{
    printf "  row: %s %s %s -> %s confidence=%s", $1, $2, $3, $4, $5
    if ($6 != "") printf " (%s)", $6
    printf "\n"
  }' "$SNAPSHOT")
  ACTIONABLE=$(awk 'END { print NR + 0 }' "$SNAPSHOT")
  {
    printf 'drain-triage:\n  status: clear\n'
    printf '  reason: no rows to ask the model; all decided in code\n'
    [ -n "$TEXT" ] && printf '%s\n' "$TEXT"
    printf '  counts: suppressed=0 actionable=%s rows=%s\n' "$ACTIONABLE" "$ACTIONABLE"
  }
  exit 0
}

RESP_FILE=$(mktemp) || die "mktemp failed"
LAT_MS=null
RUNG=''
post_rung() { # <base-url> <model> <key>: one Jev POST; sets HTTP and LAT_MS
  local base=$1 model=$2 key=$3
  REQUEST=$(jq -n --arg actor "$ACTOR" --arg model "$model" \
    --arg suppress_when "$SUPPRESS_WHEN" --arg wake_when "$WAKE_WHEN" \
    --slurpfile rows "$ASKED" '
    ($rows) as $rows |
    {
      model: $model,
      state: {drain: {actor: $actor, rows: $rows}},
      questions: ($rows | map({
        key: ("row_" + .seq),
        value: {
          type: "choice",
          instructions: ("Wake row \(.seq) (\(.kind) \(.key)): \(.payload). Closing evidence already on disk: \(.evidence). Suppress ONLY when the evidence proves this wake'"'"'s event is already closed elsewhere; otherwise wake. When in doubt, wake."),
          criteria: {suppress: $suppress_when, wake: $wake_when}
        }
      }) | from_entries)
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
      emit_error "gateway http $GW_HTTP after ${GW_LAT_MS} ms (${GW_HEAD}), then typesafe http $HTTP after ${LAT_MS} ms: $(head -c 200 "$RESP_FILE" 2>/dev/null | tr '\n' ' ')" "$ROW_COUNT"
    fi
  else
    [ "$HTTP" = 200 ] || emit_error "http $HTTP after ${LAT_MS} ms: $(head -c 200 "$RESP_FILE" 2>/dev/null | tr '\n' ' ')" "$ROW_COUNT"
    RUNG=gateway
  fi
else
  post_rung "$TS_BASE" "$TS_MODEL" "$TYPESAFE_API_KEY_PRIVATE"
  [ "$HTTP" = 200 ] || emit_error "http $HTTP after ${LAT_MS} ms: $(head -c 200 "$RESP_FILE" 2>/dev/null | tr '\n' ' ')" "$ROW_COUNT"
  RUNG=typesafe
fi

# One typed Choice answer per asked row: answers carry exactly the asked row
# ids, each choice is suppress or wake, confidence is 0..1, and probabilities
# cover exactly both options and sum to ~1.
jq -e --slurpfile rows "$ASKED" '
  ($rows | map("row_" + .seq)) as $choices |
  ((.answers | keys | sort) == ($choices | sort)) and
  ([($choices[]) as $q |
    (.answers[$q].choice | type) == "string" and
    ((.answers[$q].choice == "suppress") or (.answers[$q].choice == "wake")) and
    (.answers[$q].confidence | type) == "number" and
    .answers[$q].confidence >= 0 and .answers[$q].confidence <= 1 and
    (.answers[$q].probabilities | type) == "object" and
    ((.answers[$q].probabilities | keys | sort) == ["suppress", "wake"]) and
    all(.answers[$q].probabilities[]; type == "number" and . >= 0 and . <= 1) and
    ((.answers[$q].probabilities | [.[]] | add) as $total | $total >= 0.99 and $total <= 1.01)
  ] | all)' \
  "$RESP_FILE" >/dev/null 2>&1 || emit_error "response is not a per-row Choice answer" "$ROW_COUNT"

# ---- resolution: fixed floor per row, everything else in code ------------------
RESULT=$(jq -n --arg floor "$CONFIDENCE_FLOOR" --argjson lat "$LAT_MS" --arg rung "$RUNG" \
  --slurpfile resp "$RESP_FILE" --slurpfile rows "$ASKED" '
  ($resp[0]) as $r | ($rows) as $rows |
  {
    model: $r.model, latency_ms: $lat, rung: $rung, tokens: ($r.usage // null),
    verdicts: [$rows[] | ("row_" + .seq) as $q | ($r.answers[$q]) as $a |
      {seq: .seq, kind: .kind, key: .key,
       verdict: (if $a.confidence < ($floor | tonumber) then "wake" else $a.choice end),
       confidence: $a.confidence,
       note: (if $a.confidence < ($floor | tonumber)
              then "confidence \($a.confidence) below floor \($floor): wake as today"
              else "" end)}]
  }') || emit_error "resolution failed" "$ROW_COUNT"

TEXT=$(jq -r --rawfile code "$SNAPSHOT" '
  def flat: tostring | gsub("[\t\r\n]"; " ");
  def show($value): ($value // "-") | flat;
  def shortkey: flat | .[0:160];
  ($code | split("\n") | map(select(. != ""))) as $codelines |
  ([.verdicts[] | select(.note != "")] | length) as $below |
  ([.verdicts[] | select(.verdict == "suppress")] | length) as $sup |
  (($codelines | length) + ([.verdicts[] | select(.verdict == "wake")] | length)) as $act |
  ((.verdicts | length) + ($codelines | length)) as $total |
  "drain-triage:",
  "  status: \(if $below == 0 then "clear" else "ambiguous" end)",
  "  model: \(show(.model))   latency_ms: \(show(.latency_ms))   tokens: \(show(.tokens.input_tokens))/\(show(.tokens.output_tokens))",
  "  rung: \(.rung | flat)",
  (.verdicts[] | "  row: \(.seq | flat) \(.kind | flat) \(.key | shortkey) -> \(.verdict | flat) confidence=\(.confidence | flat)" + (if .note != "" then " (\(.note | flat))" else "" end)),
  ($codelines[] | split("\t") | "  row: \(.[0] | flat) \(.[1] | flat) \(.[2] | shortkey) -> \(.[3] | flat) confidence=\(.[4] | flat) (\(.[5] | flat))"),
  "  counts: suppressed=\($sup) actionable=\($act) rows=\($total)",
  (if $below == 0 then empty else "  reason: \($below) row(s) below the confidence floor \($floor): wake as today" end)' \
  --arg floor "$CONFIDENCE_FLOOR" <<<"$RESULT") || emit_error "output rendering failed" "$ROW_COUNT"
printf '%s\n' "$TEXT"
exit 0
