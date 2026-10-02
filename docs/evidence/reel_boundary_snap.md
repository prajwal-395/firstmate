# Reel boundary snap

History moved out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_keep_ranges.py` (moved from its module docstring, 2026-10-02)

A kept range that stops before its thought finishes.

Two field-test defects, one predicate family. Both are the same cause
at two sizes - a kept range stops before the thought it carries has
finished - and the existing gates cannot see either, for opposite
reasons:

- Reel 09 (one word): the keep edge lands 0.02s BEFORE the final
  word's start, so `midword_keep_edges` (which refuses an edge
  INSIDE a word) passes it clean and the last word is simply gone.
  The live timeline narrowed the Akshita keep bound 36510 down to
  36490; the staging records carry 36510 and that is the correct
  bound.
- Reel 04 (one sentence): the body ends on "why." while the sentence
  that states its point ("...is a decision engine.") starts five
  frames later. The boundary-repair machinery recorded that it knew
  the sentence continued and stopped, because no pin kind extends a
  body.

The fix under test: `stranded_tail_keep_edges` reports interior
edges that strand whole kept words (refused at build, like midword),
and `repair_moment_tail` extends or holds the stored moment end the
snap owns - extending to the sentence end where whole words stand
unplayed inside the speaker's pace, holding where the approved bound
already covers all but breath of the final word. Every threshold is
derived from the kept range's own segment word timings (median word
duration); the transcript-wide fallback says so loudly and never
extends.

Field-test numbers below are copied verbatim from
`pipeline_output/scratch/timeline_transcript/transcript.json` and the
approved reel spans. No test here reads that project (AGENTS.md 8):
the measurement travels as data so the case runs anywhere.

## `tests/unit/reels/test_reel_proposal.py` (moved from its module docstring, 2026-10-02)

Stored proposals predate the boundary snap; the build repairs them.

PR #671 made the boundary drawer snap cut boundaries OUT of word
interiors when a proposal is GENERATED.  But the stored
`reel_proposals_v2.json` was written weeks earlier and is read AS-IS at
build time, so the snap never touched it: reel 5 rebuilt on fully fixed
code and still gate-FAILED on F8, its END at 413.85s cutting through
the word 'about' (412.77-414.03s).

The fix under test is option (b): `snap_moment_to_speech` applies the
SAME snap at BUILD time to whatever proposal is read - body window and
closing CTA alike - without re-running selection, so WHICH moments the
captain approved is untouched and only where each one opens and closes
moves.  The build (`rebuild_reels_in_project`) and the gate
(`run_verification`) both consume the repaired moments, so a reel
cannot be built to one span and graded against another.

## `tests/unit/reels/test_reel_proposal.py` (moved from its module docstring, 2026-10-02)

The snap preview: cascades become a line of output beforehand.

D2: `snap_to_speech` widens outward to whole segments in a fixed-point
loop, and transcript segments overlap by ASR jitter - so a snap walks
from one segment into the next and keeps going.  The canary found a
9.1s closer move by looking at a finished 61s timeline.  This suite
pins the preview that reports, per moment, how far each boundary would
move and what words that pulls in or drops, flagging moves over about
two seconds as needing a decision BEFORE the build.

The snap itself is untouched: repair is still outward, still silent
where small, still idempotent (`test_reel_proposal_build_time_snap`).
A pin, when one is decided, goes through the captain's declaration
store (`captain_edits.record_edit`) with its provenance - the loop
the last test walks end to end.
