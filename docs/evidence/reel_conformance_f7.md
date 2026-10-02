# `reel_conformance_f7` - test history

Narrative moved verbatim out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_reel_f7_placed_floor.py` module docstring (moved 2026-10-02)

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

The floor is not chosen: it is `manifest_validator.
MIN_CAPTION_DISPLAY_SECONDS` (0.5s) - the pipeline's own hard floor
(AGENTS.md 10.4; `render_qa`'s `subtitle_too_short`; the manifest P6
check). At 23.976fps that is 12 frames; the 2-3 frame flash cards sit
an order of magnitude below it, so no correct reel is near the line.
```
