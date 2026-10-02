# Reel take cuts

History moved out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_take_cuts.py` (moved from its module docstring, 2026-10-02)

The judge above the take-cut heuristic.

`reel_build.redundant_takes` is a candidate generator: it counts shared
words at CUT_CONTAINMENT/CUT_JACCARD over the transcript and calls the
winners retakes. Three of the captain's recorded corrections come from
treating those winners as decisions (lc-0004, lc-0005, lc-0006), and the
before-inventory for this change found a fourth live defect in the same
mechanism: on Reel 15 a word-stream window straddling two SIMULTANEOUS
speakers' segments runs backwards in time (first word 1203.06, last word
1200.63), becomes a Cut with a NEGATIVE range, and `keep_ranges` lays the
reel's ranges over each other - the seconds 1200.63-1203.06 play TWICE.

`judge_take_cuts` is the deterministic judge above those candidates. It
withdraws only cuts that are structurally indefensible as "one telling
removed, a later one kept", and reports each with its reason. What it
deliberately does NOT do is re-judge close pairs: Reel 15's salon
retakes (containment 1.000/0.875), Reel 28's false start and Reel 31's
"Yeah." all survive the judge unchanged, because a word-overlap
threshold cannot tell a reworded retake from a refrain tail and the
captain approved those reels.

Synthetic under `tmp_path`-style fixtures (AGENTS.md 8); the field-test
numbers appear as stated inputs, never read from a real project.

## `tests/unit/reels/test_keep_ranges.py` (moved from its module docstring, 2026-10-02)

Reel 13's repetitive Craig line, removed in the keep ranges.

The captain, 2026-09-12, on frame 189 of
`Reel 13 - the-accounting-firm-ai-called-healthcare`: *"craig's line at
this point is a bit repetitive with the line he says just before this,
can you fix this?"*

The pair, off the master transcript (real words, real seconds):

    889.92-893.02  Craig   "and that's what happened with that company
                            why they came back as a healthcare company"
    893.46-895.12  Akshita "And not an accounting software company, yes."
    895.47-899.40  Craig   "and that's one of the reasons why that company
                            got called a healthcare company"

This is a cutter MISS by design, not by accident: the two readings are
paraphrase across an interjection, marginal on every bar the cut lane
holds (containment 0.667/1.0 against 0.75, Jaccard 0.286/0.50 against
0.55, durations 1.625s against 0.8s at ratio 2.03 over 2.0). Loosening
any of those bars to catch it would also catch deliberate restatement -
Reel 30's green marker records the cutter dropping a repeated closing
phrase that was never a retake, and the fix there was WITHDRAWING the
cut. So the cutter still refuses this pair, the suspect lane still
flags it for the captain, and the captain's verdict travels as a
recorded keep exclusion: Craig's SECOND line goes
(895.471-899.400, the one AT the marker), the first stays.

And the Reel 30 seam rule holds here: an exclusion edge through a word
plays half a word and then jumps (`exclusion_midword_edges` refuses the
build). Both edges of this one sit exactly on timed word edges, so the
seam carries no partial word and no clipped breath - the cut starts on
"and"'s first frame (grown back over the wordless lead-in to "yes."'s
last) and ends on "company"'s last.

## `tests/unit/reels/test_take_cuts.py` (moved from its module docstring, 2026-10-02)

A repeated take is removed WHOLE or not at all.

The defect, measured on the pipeline's rebuild of the captain's reel 03
(master 301.241-341.270s, approved 2026-09-05):

Akshita says one sentence three times.  The transcript segments the
first take as three consecutive lines.  Two of them paired with the
second take at containment 1.000 and Jaccard 1.000 and were CUT; the
third paired just as confidently and was refused, because 2.851s against
0.600s is a duration ratio of 4.75 and `DURATION_RATIO` is 2.0.  So two
thirds of a take were removed and its TAIL was left - and because the
take was at the head of the span, that orphaned tail became the reel's
first line.  The rebuild opened on "And whoever AI best understands,
gets the answer", the answer before the question, and the model's own
written hook did not arrive until 3.41 seconds in.  2.348s of repetition
really did go, and the reel was worse at the open than the timeline it
replaced.

The segments below are the captain's real ones, copied verbatim from
`pipeline_output/scratch/timeline_transcript/transcript.json` on the
field-test project.  No test here reads that project (AGENTS.md 8): the
measurement travels as data so the case can be run anywhere.

`library/tools/reel_build.py` - `redundant_runs`, `refused_take_groups`,
`assert_takes_are_whole`.

## `tests/unit/reels/test_take_cuts.py` (moved from its module docstring, 2026-10-02)

Wordless islands stranded BETWEEN take cuts are absorbed, not placed.

The census for vep-yeah-breaks-reels walked all 22 approved reels'
keep ranges and found one reel carrying the same strand-a-nub shape
as the two fixed deaths: Reel 15
(`the-3d-nail-art-salon-beats-the-chains`, master 1186.94-1251.21s)
keeps 1221.51-1221.73s - 0.22s of room tone between dropping "if
you're a salon that specializes in 3D nail art" (1219.06-1221.51) and
"and your content is built around that niche, ..." (1221.73-1227.05).
Placing that island is a 5-frame picture+audio item the F7 floor
refuses - the death Reel 13 died with its strike's tail (PR 1058).
The strike path absorbs its own edge-dust
(`absorb_wordless_remnants`); the take path never did, so the island
survived `reel_ranges` and waited for the gate.

Numbers below are the field test's own, copied verbatim from
`pipeline_output/scratch/timeline_transcript/transcript.json` and the
approved reel span. No test here reads that project (AGENTS.md 8):
the measurement travels as data so the case runs anywhere.

## `tests/unit/reels/test_take_cuts.py` (moved from its module docstring, 2026-10-02)

The six duplicate-take fixes hold through the build cascade.

The captain, 2026-09-19, marked repeated takes on six reels and called
it a pattern. Five became recorded keep exclusions (lc-0093..lc-0097);
the sixth (Reel 08) the bleed-aware judge now cuts mechanically. Each
test replays its fix through the build's exact cascade -
`exclusion_cuts_for_span` -> lead-in/tail growth -> `reel_ranges` with
judge and wholeness guard - and asserts the bad take is gone, the kept
telling plays whole, and nothing refuses.

Frozen specimens (AGENTS.md 8: no test reaches a real project). Segment
texts, boundaries and the word timings the verdict edges depend on are
the 2026-09-18 timeline transcript's own; interior words of long
tellings are evenly spread, which moves no edge these tests assert.

## `tests/unit/reels/test_take_cuts.py` (moved from its module docstring, 2026-10-02)

Retake and false-start candidates the cut lane misses.

The captain, 2026-09-19, marked six retake-shaped repetitions across
six reel timelines and called it a pattern. The cut lane
(`redundant_takes` + the judge) removes one of the six. This module
measures the other shapes - false starts, paraphrases and
cross-segment word-stream verbatims - and REPORTS them with both
texts, a basis and a recommended strike. It cuts nothing.

Specimens are this project's, frozen as stated data (AGENTS.md 8: no
test reaches a real project). Segment texts and boundaries are the
transcript's own; word timings are the transcript's own where the span
under test depends on them (Reel 21's tail-drop, Reel 24's restart),
evenly spread where only the wording matters. Each test says which.

## `tests/unit/reels/test_keep_ranges.py` (moved from its module docstring, 2026-10-02)

Seconds the captain says STAY IN withdraw the take cut that drops them.

Reel 01, frame 270, 2026-09-11: *"this cut here on craig is a little
jarring and does't actually make sense, it's better to just not cut out
those few words inbetween"*.

The cut was `reel_build.redundant_takes`'s.  Craig says *"we've got to
get into geo geo geo i get it"* - one sentence with a rhetorical triple
- and the word-stream duplicate scan matched the run before the
repetition against the repetition itself at 0.667 similarity, called
the first a retake, and removed "got to get into" from the middle of
the sentence.

A keep EXCLUSION could only have removed more.  This is its inverse, in
the same store, enforced at the build.

Synthetic under `tmp_path` (AGENTS.md 8); nothing reaches a real
project.

## `tests/unit/reels/test_keep_ranges.py` (moved from its module docstring, 2026-10-02)

A recorded exclusion reaches an approved moment's build.

The captain struck "so what do they" (lc-0002, 653.42-654.35s) on
APPROVED Reel 09 three times, and the build played it every time.
Selection enforces strikes on NEW proposals only
(`select_reels/post_bridge.py`), and `rebuild_reels_in_project` never
read the store - so the guard meant to stop the ENGINE re-deciding an
approved range also stopped the CAPTAIN's own strikes on exactly the
reels they annotated. These tests pin the fix:

- a strike cuts the approved build's ranges (`reel_ranges(extra_cuts)`):
  one reel in, one reel out, fewer seconds - never two;
- the selection-time trim-or-drop (`apply_keep_exclusions`) is UNCHANGED,
  so the no-split rule still holds where proposals are drawn;
- a strike the build cannot honour (whole body gone, edge through a
  word) is refused or dropped WITH its reason, never placed silently.

Fail-before: `reel_ranges` takes no `extra_cuts`, and
`transcript_corrections` has no `exclusion_cuts_for_span` - the first
two tests error on import/call, not on assertion.
