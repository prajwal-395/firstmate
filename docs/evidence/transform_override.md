# `transform_override` - the incidents behind the contract

This is the narrative moved out of `tests/unit/picture/test_transform_override.py`
(plan item 7: incident archaeology belongs here, not in executable
tests). The contract lives in `library/tools/captain_edits.py`
(`validate_edits`, `match_transform_overrides`, `record_edit`) and the
write-side applier `library/tools/reel_build.py`
(`apply_transform_overrides`). Where this file and the code disagree,
the code wins.

## 2026-09-10 - the hand move that would not persist

The captain moved Akshita's clip by hand in Resolve to Pan -35 from
the pipeline's 14 and asked whether any such change actually persists:
*"i want you to really investigate if any of these changes actually
persist and are saved"*. Still standing from earlier: *"if i ask to
remove a piece of the video and replace it with something else, and
then ask you to rebuild the timeline, those changes should persist"*.

The store, the reader and the CTA redraw already existed
(`library/tools/captain_edits.py`, PR #857); the missing piece was the
WRITE side and a placement-shaped entry. A `transform_override` is that
entry, in the SAME store - the anchor is the same stable thing every
other kind anchors to (the spoken words), only the payload differs (a
number held, not a range redrawn). The "x" the captain moved is the API
property `Pan` (the Inspector's Position X; `PositionX` is not an API
property - measured live on Reel 09).

## 2026-09-12 - stale has two opposite readings (`geo-podcast`)

Ten recorded overrides, every one matched against every reel, so a
Reel 09 build printed EIGHT stale lines for decisions belonging to
Reels 01, 13, 26 and 28 that are working perfectly. A captain's value
whose words were reworded away printed the ninth, in the same
sentence. The two are opposite: one is routine, the other is the
hand-set value gone for every future build of every reel. The match
separates them: `scope == "reel"` ("not on this reel", routine) versus
`scope == "transcript"` ("LOST", the value is gone). The rebuild says
the LOST line loudly and keeps the routine case off the lost channel,
or the new line is eight-ninths noise and gets ignored like the old
one.

## Gain-4 capture rebased at a gain-1 build

A capture taken while the timeline read Pan/Tilt at draw gain 4 is
rebased when the next build runs at gain 1
(`rebase_override_value` over the shared forward/inverse law in
`library/tools/resolve_transform.py`), so the override and the picture
geometry cannot invent separate laws. A refused `SetProperty` refuses
the build rather than building past the captain's number.

## Per-reel scope - one reel's Pan on a shared shot

A shot four reels share speaks one anchor on all four; the captain's
Pan for ONE of them is the same words with a `reel` scope (matched by
prefix, the `reel_ending` convention). The failing input the scope
exists to end: the same anchor with no `reel` matched on both reels'
builds, which is why one reel's Pan was inexpressible before the
scope. A scoped narrowing and the general decision coexist: on the
scoped reel only the narrowing holds that property on that span; on
every other reel the general one still does. Different scopes are
different decisions (both stay in force); a same-reel re-ruling
supersedes in place.

## 2026-09-17 - the freeze that jumped (Reels 30 and 31)

The live speaker moved to Pan -26 while the freeze held the engine aim
(-12.00, -2.24): the held frame jumped against the live picture in
front of it. A freeze speaks nothing, so its own master span is empty
and no word anchor could name it; the value arrived only through the
build-time copy from whatever played before it. The freeze placement
now carries the tail span it was held from (`held_master`, via
`reel_ending.plan_freeze`), and the match reads the anchor against
that - the declaration names the hold directly, on top of the copy
that already runs.
