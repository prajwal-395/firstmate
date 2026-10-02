# Promotion records and interruption

The post-promotion refusal matrix is in
`tests/test_build_refuses_when_the_record_would_lie.py`.
`library/tools/reel_build.py::promote_staged_reels` owns the sequence;
the test proves a record failure is raised after the staged reel lands.

## The ruling

The captain's 2026-09-18 rule is to refuse when a record would be wrong
and report when the outcome is only advisory. A reel can land correctly
while its promotion record does not. Downstream builds trust that
record, so failures in required bookkeeping must reach the run as
failures and identify the record that did not land.

The parameterized matrix covers seven failure points: backup
retirement, comparison retirement, declared sign-off supersession, a
sign-off disappearing during promotion, round stamping, carried
provenance signatures, and render-ledger timeline bindings. Every case
checks that staging has already taken the final name when the required
recording step fails. The declined-retirement row also checks that no
timeline was deleted.

## Unrecorded staging provenance

On 2026-10-02, promotion of a variant with no `built_reels` entry
renamed the approved and staged timelines, then raised while renaming
provenance. That left later promotion records unfinished. The final's
old provenance described the build just replaced, so the fix drops that
entry when the incoming staging has no build record and completes the
promotion. `tests/test_promote_replace_guard.py::test_a_staging_no_build_recorded_drops_the_replaced_provenance`
pins that case.

Media-pool filing refusal remains reportable on the promotion result;
an unreadable carried digest leaves its signature open so the next
build requires a rebuild. Those outcomes do not create a false record
and are not promoted to refusal cases.
