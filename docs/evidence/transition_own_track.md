# Transitions ride their own track (finding 16) and the finding-32 retry

Tests: `tests/unit/picture/test_transition_placement.py`.

Finding 16 + the finding-32 retry: transitions ride their own track.

Finding 16: a native transition "into" V2 b-roll landed where the V1
clip underneath ended, not at the b-roll edge it was planned into
(scout TR2.2 B2: the fallback slide sits at V1 854-860, where the
b-roll ENDS, not where it begins). Native transitions only went on V1:
`compile_manifest` seated every native row by `_v1_index_ending_at`
and the applicator indexed into the V1 items alone.

The fix seats a native row on V1 first (every existing placement is
unchanged) and then on the V2 pair abutting the planned cut - the
transition draws at the edge of the clip it was planned into, on that
clip's track. Where neither track carries the cut, the row still
downgrades to the recorded hard cut (finding 32), now stamped on the
per-item row itself.

The finding-32 retry (added after PR 1397, same shape as its finding-34
handling): a transition 4.02 can already see carries nowhere goes back
to the planner through `post_bridge_retry` on the first pass, so the
model can re-place it; only when the retry still cannot place it does
the hard-cut fallback ship, surfaced on the row.

## Finding 32: a misplaced transition downgrades instead of failing the compile

Tests: `tests/unit/picture/test_transition_planning.py`.

Finding 32: a transition 4.02 accepts can refuse the whole compile.

On the scout's B6 run (PA1.2) a slow cross dissolve at the cut out of a
transition slot (b-roll on V2, nothing on V1) passed 4.02 and then failed
`compile_manifest` with "Transition trans_002 at 15.0s does not sit at
the end of any V1 clip" - the whole run FAILED, rather than the one
entry being dropped with its reason (AGENTS.md 10.5's own rule for plan
entries: a plan entry that names no effect, no sound or no level is
DROPPED with the reason, never failed).

The fix: the compile ships the hard cut the boundary already is and
records the one entry in `transitions_downgraded` with its reason. The
run builds; the miss is said, not silent.
