# Promotion replacement guard

The incident coverage lives in
`tests/unit/reels/test_promote_replace_guard.py`.
`library/tools/reel_replace_guard.py` owns the row diff;
`library/tools/reel_build.py::promote_staged_reels` runs it before
renaming or deleting timelines.

## The incident

The replacement path used to rename a captain-visible timeline without
checking what the staged version had lost. On a cutaway-bearing reel,
the staged V1 row dropped from three items to two and lost a 24-frame
cover at record frame 574. A failed semantic overlay render could also
leave the complete `Semantic` row out of staging. Either case could
replace approved work with an incomplete timeline.

## Preserved outcomes

The tests cover undeclared cut and row losses, a reduction named by the
captain, joins and frame growth that preserve the played picture,
unreadable retiring rows, and marker and snapshot carry. Refusals are
checked before the old timeline is renamed or removed.

On 2026-10-02, a promoted variant had no build provenance entry. The
promotion used to rename both timelines and then fail while renaming
the provenance sidecar. The build now drops the replaced final's stale
provenance in that case. The regression is
`test_a_staging_no_build_recorded_drops_the_replaced_provenance`.
