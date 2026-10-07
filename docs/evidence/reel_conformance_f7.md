# `reel_conformance_f7` - test history

Narrative moved verbatim out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_reel_conformance_verifier.py` module docstring (moved 2026-10-02)

```text
F7 measures PLACED item durations against the readability floor, with
no last-of-block exemption - and the same floor covers placed A/V items.

On 2026-09-08 the captain watched the fully-approved reels and reported
"subtitles misplaced". The investigation
(`data/vep-approved-reels-still-have-visible-defects/report.md`) found
caption cards on screen for THREE FRAMES - R02 card 0 "yeah." 3f, R03
card 3 "yeah" 3f, R19 card 32 "yeah." 2f at 23.976fps - each HELD by
`check_short_captions` as a warning because each placed item is
trivially the last card of its own block, the exemption's only clause.
A check whose exemption is always satisfied cannot refuse.

The exemption's intent was real: a block-final card cannot be lengthened
by regrouping (it leaves when the block does). But "nothing can lengthen
it" is a fact about the grouping, not a claim the card is readable - the
remedy belongs upstream (rejoin the fragment row to its sentence; do not
author sub-second keeps), and on the placed path the viewer sees a flash
either way. So F7 fails every placed item under the floor, whatever
block it ends with.

The floor is declared once at `manifest_validator.
MIN_CAPTION_DISPLAY_SECONDS` and read by the planner, subtitle QA, and F7.
It was 0.5s (12 frames at 23.976fps) when this evidence was first
recorded. On 2026-10-07 the captain lowered it to 0.4s (10 frames) so
measured subtitle passages could split into more cards while staying
within two lines. The original 2-3 frame flash cards remain well below
the current floor.
```
