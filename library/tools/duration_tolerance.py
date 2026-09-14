"""The one tolerance every duration invariant in the pipeline is judged by.

Two steps compare a measured source duration against a timeline
allocation - step 3.01 (`assign_a_roll`, warn) and step 3.03
(`review_rough_cut`, refuse) - and each carried its own
``DURATION_TOLERANCE = 0.15`` literal. Two literals with one value is a
disagreement waiting for an edit to land on one side: the day they
differ, a cut 3.01 warns about is a cut 3.03 passes, or the reverse,
and no test can see it because there is no single value to assert.

The authoritative value is **0.15 seconds**. Both code sites agreed on
it; the 0.1s in step 3.03's `handoff.md` (Checks table, "Duration
tolerance") is stale prose. The prompt text is owned by the prompt
lane, so this module cannot fix the sentence - it fixes the code side
by giving both steps one import instead of two literals.

Float rounding across segment summation is what the slack is for, not
editorial latitude: a full frame at 23.976 fps is 0.042s, and three
rounded segments can stack past a frame.
"""

# Seconds of slack between a source duration and the timeline duration
# it is measured against. Authoritative: step 3.01 and step 3.03 both
# import this rather than carrying the literal.
DURATION_TOLERANCE = 0.15
