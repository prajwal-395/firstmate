# Reel conformance verifier

Incident history moved verbatim out of `tests/test_reel_conformance_verifier.py`.
The tests keep the invariant; this file keeps how each was found.


## `test_reel_conformance_verifier.py` - TestF2CaptionDuration.test_pairs_on_start_frame_not_list_position

The regression this exists to prevent, and it is the whole bug.

`check_caption_duration` used to read `actual_captions[i]` under a
comment saying it matched on start frame. That only agrees with
itself while both lists are the same length. Measured on the
captain's nineteen reels, 832 cards were planned and 763 placed,
so after each reel's first unplaced card every remaining pair
compared one card's plan against a DIFFERENT card's item - 701
findings and r(planned, placed) = 0.027, which reads as a
placement defect and is not one.

Here card 2 is planned and never placed. Index pairing would
compare card 2's 24 frames against card 3's item and card 3
against nothing, inventing a delta on a card that is correct.

## `test_reel_conformance_verifier.py` - TestF2SegmentGranularity

F2/F14 pair at the SEGMENT granularity the builder places.

The 2026-09-07 rebuild failure: reel 1 was rebuilt fresh (per-block
overlay segments, one V3 item per block) and verification failed it
with 34 errors - one per planned card. The check paired every CARD
against the block-spanning item, so each block's first card drew F2
(delta = the rest of the block) and every other card drew F14.
The builder was right - per-block segments are what `compile_manifest`
promises (`_assert_subtitle_overlay_matches_plan`) - and the check
was grading cards against their container.

## `test_reel_conformance_verifier.py` - TestF2AbuttingBlocks

F2 on blocks that abut EXACTLY in seconds - reel 07, 2026-09-08.

The rebuild failed deterministically (twice identically) with::

    caption segment 24 (block 23, 2 cards starting
    'number one on google because y') planned 34 frames, placed 33

Measured off the rendered props: block 22 spans 60.125-62.374s and
block 23 spans 62.374-63.809s - the boundary second is IDENTICAL, so
the plan holds no overlap.  Per-block integer placement
(``round(start)`` + ``round(duration)``) laid them as [1442,1496) and
[1495,1529): frame 1495 asked for twice.  Of all 30 placed caption
spans on the reel this is the ONLY overlap, and the single F2 is on
the later block - Resolve trimmed a frame where the request
overlapped, deterministically on both attempts.

The builder manufactured the overlap out of abutting inputs, so the
fix is on the encoding side: spans are rounded PER EDGE -
``[round(start), round(end))`` - the same arithmetic picture
``placements`` already uses, under which abutting blocks abut.
The check grades that same span, so the two agree by construction
and the gate stays exact (no tolerance widened).

## `test_reel_conformance_verifier.py` - TestClosingCallToAction.test_the_plan_can_be_derived_with_a_project_folder

The card branch, which no test reached until it broke.

`_derive_plan_from_master` only plans cards when it is given BOTH
a `moment` and a `project_folder`; every other test here passes
neither, so the `plan_cards` call was never executed. PR #1095
made `plan_cards` take a required `width`/`height` - the declared
delivery frame - and updated its three callers in `reel_build`
and `captain_edits` but not this one. The verifier then raised
`TypeError` the first time a build reached the gate, and
`verify_built_reels` turned that into "Reel conformance verifier
failed to run", which discarded five correctly staged reels.

The same shape as PR #524's required `fps` on `placements`, noted
at the top of this section. A branch nothing runs is a branch
that breaks silently, so this runs it.

## `test_reel_conformance_verifier.py` - TestClosingCallToAction.test_a_recorded_head_trim_survives_re_derivation_in_play_order

A reel cannot be built to one rule and checked against another.

The build trims keep ranges per recorded `span_retime` pins
(`captain_edits.retime_ranges`) before the ending reads the last
range. The verifier re-derived without them, so Reel 16's pinned
1802-frame timeline (2026-09-18) was graded against a 1926-frame
plan and failed F4 - plus a logo card planned 124 frames past
where the trimmed build placed it. The re-derivation applies the
same trims at the same seam, and the early-master CTA still
closes the reel.

## `test_reel_conformance_verifier.py` - TestNoReferenceRefusesRatherThanSkipping

The vacuous caption gate, and why it is a class of its own.

Measured 2026-09-05 on the captain's nineteen: the verifier reported
captions expected/actual as 0/28, 0/39 ... 0/762. It expected zero
caption cards, found seven hundred and sixty-two, and PASSED - so F2,
F5, F6 and F7 were all vacuous. The guard was
`if plan.captions and timeline.caption_items:` and `plan.captions`
was `()` on every run the verifier had ever made, because
`reel_subtitles.py` was a parallel module whose output never entered
the plan the verifier reads.

The plan side is now derived, but that is not what makes this safe -
a derivation can break again, and the captioner is moving into step
4.01. What makes it safe is that the EMPTINESS is now the finding.

## `test_reel_conformance_verifier.py` - TestNoReferenceRefusesRatherThanSkipping.test_could_not_be_asked_is_not_the_same_as_has_none

The branch that stops the gate going quiet when the producer moves.

`_derive_planned_captions` used to import `reel_subtitles` inside
`except ImportError: return ()`. That module is being deleted -
the captain ruled it should never have existed and its work
belongs in step 4.01 - and on the day it went the plan side would
have become permanently empty, the caption gate would have gone
back to expecting zero cards while finding hundreds, and every
test here would still have passed.

So "could not be asked" is now its own answer and it REFUSES,
while "has none" stays clean.

## `test_reel_conformance_verifier.py` - TestPlanMismatchRefusesF4

F4 reported a RE-DERIVED plan's disagreement with the timeline as
clips the builder had dropped.

`_derive_plan_from_master` recomputes the picture plan with today's
`reel_build`.  `MIN_TAKE_SECONDS` was removed from it in 890a61b at
22:58 on 2026-09-05; the captain's nineteen were built at 14:46 the
same day.  Today's cut rule finds retakes the build never cut, so the
derived plan lays down a different number of frames - and F4 read
that as "planned 6 picture items, found 4 on the timeline", ten
findings across five reels, none of them a build defect.

## `test_reel_conformance_verifier.py` - TestF6GradesPlacedCards

`verify_reel` must grade F6 against the placed items, not the plan.

F6 graded the plan's caption cards re-derived from today's code - the
same reference F2/F14 now refuse without a recorded baseline. On the
captain's nineteen that reference derived 39 cards where the build
placed 28, so F6 failed correct output when the planner drifted from
what was built, and missed real placed overlaps for the same reason.
These fixtures replay both directions: an overlapping plan beside
non-overlapping placed items, and a clean plan beside overlapping
placed items.

## `test_reel_conformance_verifier.py` - TestRecordedPinsAreReadBeforeThePlanIsGraded

The CLI must grade the plan the build placed, not the raw file.

Measured 2026-09-12 on the rebuilt field-test project. The build's
in-process sweep PASSED; the same sweep run from the command line -

    python3 -m library.tools.reel_conformance_verifier \
      --project 'Podcast (field test)' --master 'GEO Podcast - Synced' \
      --plan <project>/pipeline_output/review/reel_proposals_v2.json \
      --transcript <project>/.../transcript.json

- reported PLAN-MISMATCH as an ERROR on Reels 09, 26 and 28:
"+54 frames, +2.25s ... it is not the plan that built this reel".
Those are exactly the three built reels whose closer the captain's
`redraw_closer` pin moves, and 2.252s is exactly 54 frames at
23.976fps.

The cause was ORDERING, not logic. `_apply_recorded_pins` reads the
pins out of the project folder and returns the moments UNCHANGED
when it has none; the CLI can only DERIVE that folder from
`--plan`, and the derivation sat 70 lines below the call. So the
in-process caller, which passes the folder, applied the pins and
the CLI did not - a gate that fails correct output, which is no
more coverage than one that cannot fail (AGENTS.md 10.4).

## `test_reel_conformance_verifier.py` - TestF5CaptionCoverage.test_f5_counts_only_the_seconds_the_reel_plays

A row that starts before the reel still counts inside it. This is the case
F5 was built for and used to skip: it mapped the row's own start through
`reel_time`, got None because the row begins outside the reel, and
`continue`d. Every reel has two such rows by construction, at its two
boundaries. On the captain's nineteen approved reels that dropped 51 rows
carrying 364.7s of in-reel overlap and reported 29.2s of the 170.1s
uncaptioned.

A cut inside a row removes seconds; they are not speech. Mapping the two raw
endpoints was wrong even when it returned numbers - the row mapped to one
contiguous reel interval spanning the removed take, so seconds the builder
had cut out counted as speech that needed a caption.
