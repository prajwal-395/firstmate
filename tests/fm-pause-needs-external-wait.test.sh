#!/usr/bin/env bash
# Regression for paused declarations that hide work the worker has not started.
set -u

# shellcheck source=tests/wake-helpers.sh
. "$(dirname "${BASH_SOURCE[0]}")/wake-helpers.sh"
# shellcheck source=/dev/null
. "$ROOT/bin/fm-classify-lib.sh"

invalid='paused [at=1792058904]: Resolve reference-edit qualification through Ren Qualification; resume when both qualification renders and verification finish; until 2026-10-16T00:00Z checkpoint-sha=abc'
valid='paused [at=1792058904]: waits-for=task:vep-render-a awaiting the already running qualification render; until 2026-10-16T00:00Z'

status_is_paused "$invalid" && { echo 'FAIL: self-declared future work was absorbed as a wait' >&2; exit 1; }
status_is_captain_relevant "$invalid" || { echo 'FAIL: invalid pause did not surface for supervision' >&2; exit 1; }
status_is_paused "$valid" || { echo 'FAIL: named external wait stopped being absorbed' >&2; exit 1; }
status_is_captain_relevant "$valid" && { echo 'FAIL: valid external wait became captain-relevant' >&2; exit 1; }
[ "$(status_paused_until "$valid")" -gt "$(date +%s)" ] || { echo 'FAIL: valid wait lost its until recheck' >&2; exit 1; }
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
