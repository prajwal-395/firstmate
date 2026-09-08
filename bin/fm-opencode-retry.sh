#!/usr/bin/env bash
# fm-opencode-retry.sh - the ONE owner of OpenCode retry-backoff evidence.
#
# Why this exists: a usage-capped opencode lane does NOT fail. OpenCode holds
# the session in `session.status` type `retry` and re-schedules forever (no
# max attempt; verified against the vendor schema and the 2026-09-07 twelve-
# lane stall), while firstmate's own plugin still reports busy and supervision
# reads `working` for the whole ~22h backoff. Nothing in any status log says
# otherwise, so the cap is invisible to supervision.
#
# The detector is structural, never banner text. The vendor's retry Info is a
# closed shape `{type:"retry", attempt:int, message:str, next:epoch-ms}` (see
# docs/verification/supervision.md for the dated schema evidence). `next` is
# the vendor's own scheduled-retry timestamp, so `next - now` IS the backoff
# horizon with no vendor string involved: transient rate-limit backoff is
# seconds (the vendor caps its own schedule at 30s absent a Retry-After
# header), while a quota/usage cap parks the lane for hours. A horizon longer
# than FM_OPENCODE_RETRY_BLOCK_SECS (default 600) therefore reads as a
# quota-scale block, and anything shorter keeps the existing working verdict.
#
# Sidecar: state/<id>.opencode-retry - exactly one line, atomically replaced
# by the spawn-installed plugin on every retry status event:
#
#   v1 attempt=<uint> next=<epoch-ms> ts=<epoch-s> [model=<id>] [session=<sid>]
#
# `ts` is when the plugin observed the event; `next` bounds the sidecar's own
# validity (see check). The record is written only for the latched worker
# session and cleared on idle, so a previous turn's backoff can never brand a
# resumed turn. As a second guard, bin/fm-crew-state.sh only consults this
# file while the busy record's own event is still `session-retry`: a genuinely
# resumed turn writes `session-busy` first, which drops the blocked verdict
# even if a stale sidecar survives.
#
# Subcommands:
#
#   record <state-dir> <id> <attempt> <next-ms> [model] [session]
#       Validate and atomically store one retry observation. Non-numeric
#       attempt/next, or a model/session outside the token charset, is refused
#       (exit 1): on vendor shape drift this degrades to the old behavior
#       (no sidecar, lane still reads working) rather than a false blocked.
#       A model or session of `-` or empty is stored as absent.
#
#   clear <state-dir> <id>
#       Remove the sidecar. Best-effort hygiene called on idle; always exits 0
#       so a missing file never breaks the plugin's lifecycle.
#
#   check <state-dir> <id>
#       Classify the stored observation. Prints one line:
#         status=<blocked|waiting> attempt=<n> horizon_s=<s> [model=<id>]
#       Exit 0 when the sidecar is present, well-formed, and unexpired;
#       exit 1 when it is absent, malformed, or expired (the scheduled retry
#       time plus FM_OPENCODE_RETRY_STALE_SECS grace has passed, so the file
#       no longer describes the present). `blocked` iff horizon_s exceeds
#       FM_OPENCODE_RETRY_BLOCK_SECS. Nothing here reads rendered text.
#
# Environment:
#   FM_OPENCODE_RETRY_BLOCK_SECS   horizon above which a retry reads as a
#                                  quota-scale block (default 600)
#   FM_OPENCODE_RETRY_STALE_SECS   grace after the scheduled retry time during
#                                  which the sidecar still describes the
#                                  present (default 600)
#
# Exit codes: 0 classified/recorded/cleared; 1 refused or nothing to classify;
# 2 usage. All plugin invocations swallow failures so supervision evidence can
# never break the harness's own lifecycle.
set -u

usage() {
  cat >&2 <<'EOF'
usage:
  fm-opencode-retry.sh record <state-dir> <id> <attempt> <next-ms> [model] [session]
  fm-opencode-retry.sh clear <state-dir> <id>
  fm-opencode-retry.sh check <state-dir> <id>
See the header comment for the full contract.
EOF
  exit 2
}

BLOCK_SECS=${FM_OPENCODE_RETRY_BLOCK_SECS:-600}
case "$BLOCK_SECS" in ''|*[!0-9]*) BLOCK_SECS=600 ;; esac
STALE_SECS=${FM_OPENCODE_RETRY_STALE_SECS:-600}
case "$STALE_SECS" in ''|*[!0-9]*) STALE_SECS=600 ;; esac

CMD=${1:-}
case "$CMD" in
  record|clear|check) shift ;;
  *) usage ;;
esac

STATE=${1:-}
ID=${2:-}
[ -n "$STATE" ] && [ -n "$ID" ] || usage
case "$ID" in *[!A-Za-z0-9._-]*) echo "error: invalid task id" >&2; exit 1 ;; esac
[ -d "$STATE" ] || { echo "error: state dir not found: $STATE" >&2; exit 1; }

REC="$STATE/$ID.opencode-retry"

# Model ids carry a provider prefix with a slash (e.g.
# opencode-go/muse-spark-1.3-contributor); session ids are plain tokens.
# Anything else (spaces, quotes, shell metacharacters) is refused.
token_valid() {  # <value>
  case "${1:-}" in
    ''|*[!A-Za-z0-9._/:-]*) return 1 ;;
  esac
  return 0
}

if [ "$CMD" = clear ]; then
  rm -f "$REC"
  exit 0
fi

if [ "$CMD" = record ]; then
  ATTEMPT=${3:-}
  NEXT_MS=${4:-}
  MODEL=${5:-}
  SESSION=${6:-}
  case "$ATTEMPT" in ''|*[!0-9]*) echo "error: invalid attempt: $ATTEMPT" >&2; exit 1 ;; esac
  case "$NEXT_MS" in ''|*[!0-9]*) echo "error: invalid next: $NEXT_MS" >&2; exit 1 ;; esac
  case "$MODEL" in ''|-) MODEL= ;; *) token_valid "$MODEL" || { echo "error: invalid model" >&2; exit 1; } ;; esac
  case "$SESSION" in ''|-) SESSION= ;; *) token_valid "$SESSION" || { echo "error: invalid session" >&2; exit 1; } ;; esac
  old_umask=$(umask)
  umask 077
  tmp="$REC.tmp.$$"
  {
    printf 'v1 attempt=%s next=%s ts=%s' "$ATTEMPT" "$NEXT_MS" "$(date +%s)"
    [ -n "$MODEL" ] && printf ' model=%s' "$MODEL"
    [ -n "$SESSION" ] && printf ' session=%s' "$SESSION"
    printf '\n'
  } > "$tmp" || { rm -f "$tmp"; umask "$old_umask"; echo "error: record write failed for $ID" >&2; exit 1; }
  mv -f "$tmp" "$REC" || { rm -f "$tmp"; umask "$old_umask"; echo "error: record write failed for $ID" >&2; exit 1; }
  umask "$old_umask"
  exit 0
fi

# check
[ -f "$REC" ] || exit 1
# Exactly one line; a second line (or an unreadable file) is malformed.
# shellcheck disable=SC2034 # extra exists only to prove the record is one line
{ IFS= read -r line && ! IFS= read -r extra; } < "$REC" 2>/dev/null || exit 1
[ -n "$line" ] || exit 1
# `read -a` never glob-expands a field and never touches positional params.
# shellcheck disable=SC3045
IFS=' ' read -r -a fields <<< "$line" 2>/dev/null || exit 1
ver=${fields[0]:-}
[ "$ver" = v1 ] || exit 1
attempt= next= model= session=
f=
for f in "${fields[@]:1}"; do
  case "$f" in
    attempt=*) attempt=${f#attempt=} ;;
    next=*) next=${f#next=} ;;
    model=*) model=${f#model=} ;;
    session=*) session=${f#session=} ;;
    ts=*) ;;
    *) exit 1 ;;
  esac
done
case "$attempt" in ''|*[!0-9]*) exit 1 ;; esac
case "$next" in ''|*[!0-9]*) exit 1 ;; esac
if [ -n "$model" ]; then token_valid "$model" || exit 1; fi
if [ -n "$session" ]; then token_valid "$session" || exit 1; fi
now_ms=$(($(date +%s) * 1000))
# Expired: the vendor's own scheduled retry time plus grace has passed, so
# this file no longer describes the present (the retry fired, resolved, or
# the lane moved on without an idle clear reaching us).
if [ "$now_ms" -gt $((next + STALE_SECS * 1000)) ]; then
  exit 1
fi
horizon_s=$(((next - now_ms) / 1000))
[ "$horizon_s" -ge 0 ] || horizon_s=0
if [ "$horizon_s" -gt "$BLOCK_SECS" ]; then
  status=blocked
else
  status=waiting
fi
out="status=$status attempt=$attempt horizon_s=$horizon_s"
[ -n "$model" ] && out="$out model=$model"
[ -n "$session" ] && out="$out session=$session"
printf '%s\n' "$out"
exit 0
