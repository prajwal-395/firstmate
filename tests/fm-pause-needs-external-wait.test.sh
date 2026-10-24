#!/usr/bin/env bash
# Regression for paused declarations that hide work the worker has not started.
set -u

# shellcheck source=tests/wake-helpers.sh
. "$(dirname "${BASH_SOURCE[0]}")/wake-helpers.sh"
# shellcheck source=/dev/null
. "$ROOT/bin/fm-classify-lib.sh"

invalid='paused [at=1792058904]: Resolve reference-edit qualification through Ren Qualification; resume when both qualification renders and verification finish; until 2026-10-16T00:00Z checkpoint-sha=abc'
until_epoch=$(( $(date +%s) + 3600 ))
until_iso=$(date -u -r "$until_epoch" '+%Y-%m-%dT%H:%MZ' 2>/dev/null \
  || date -u -d "@$until_epoch" '+%Y-%m-%dT%H:%MZ')
valid="paused [at=1792058904]: waits-for=task:vep-render-a awaiting the already running qualification render; until $until_iso"

status_is_paused "$invalid" && { echo 'FAIL: self-declared future work was absorbed as a wait' >&2; exit 1; }
status_is_captain_relevant "$invalid" || { echo 'FAIL: invalid pause did not surface for supervision' >&2; exit 1; }
status_is_paused "$valid" || { echo 'FAIL: named external wait stopped being absorbed' >&2; exit 1; }
status_is_captain_relevant "$valid" && { echo 'FAIL: valid external wait became captain-relevant' >&2; exit 1; }
[ "$(status_paused_until "$valid")" -gt "$(date +%s)" ] || { echo 'FAIL: valid wait lost its until recheck' >&2; exit 1; }
firstmate_resume='paused [at=1792058904]: waits-for=firstmate-resume; waiting for firstmate to resume this worker'
status_is_paused "$firstmate_resume" \
  || { echo 'FAIL: firstmate-resume wait stopped being recognized as an external pause' >&2; exit 1; }
status_file=$(mktemp)
trap 'rm -f "$status_file"' EXIT
printf '%s\n' "$invalid" > "$status_file"
status_span_has_actionable "$status_file" 0 \
  || { echo 'FAIL: invalid pause status log did not produce a supervisor action' >&2; exit 1; }
printf '%s\n' "$valid" > "$status_file"
status_span_has_actionable "$status_file" 0 \
  && { echo 'FAIL: valid external wait status log produced a supervisor action' >&2; exit 1; }
for external in \
  'task:vep-render-a' \
  'pr:https://github.com/example/project/pull/42' \
  'lease:task-17' \
  'lock:publish-slot' \
  'process:4242' \
  'decision:api-shape'; do
  status_is_paused "paused: waits-for=$external waiting for its owner" \
    || { echo "FAIL: named external reference was rejected: $external" >&2; exit 1; }
done
status_is_paused 'paused: waits-for=self:unfinished-work soon' \
  && { echo 'FAIL: unsupported self reference was absorbed' >&2; exit 1; }
echo 'PASS: unsubstantiated pauses surface; named external waits retain quiet until behavior'
