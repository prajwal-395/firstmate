# Shared reel closers and the caption-segment identity

Moved verbatim on 2026-10-02 from the module docstring of
`tests/test_reel_segment_collision.py` when the test file kept only its
invariant. The narrower tests that file also carried (different words never
share a filename; one filename behind two content keys is refused; the master
timeline needs no discriminator) duplicated
`tests/test_subtitle_segment_id.py` and were removed.

```text
Two reels that share a closer SHARE a file; two that differ must not.

The captain's format closes every reel on a spoken call to action taken
from anywhere in the episode, so several reels legitimately carry the
SAME `source_clip_id` and the same source span. Measured on the field
test: seven of nineteen reels close on one identical sentence and five
more on another.

Under the old timeline-carrying identity those twelve closers were a
collision: reel 09 re-rendered over reel 03's file, reel 03's manifest
still pointed at that path, and
`compile_manifest._assert_subtitle_overlay_matches_plan` PASSED - it
compares block position and time span and reads no content. The captain
saw the wrong words on screen with every check green.

Under the provenance-plus-content identity the same twelve closers are
the SHARING: same source span plus same pixels means one filename, one
render, twelve placements. What is refused now is one filename behind
TWO content keys - a drawing input that escaped the digest - because
that is the overwrite nothing downstream would catch.

The reel names below are the captain's real ones, and all sixteen live
in `tests/fixtures/field_test_16_reel_names.json`, copied verbatim from
the field test's own report. They are IN THE REPOSITORY on purpose: this
test first read that report out of firstmate's private data directory,
which exists on the captain's machine and nowhere else, so it skipped
silently everywhere else while reporting green. A condition that depends
on a path outside the repository is not an environment, and the fixture
is what makes the check run on every machine.
The shared CTA span is constructed: the plan carrying `call_to_action`
ranges is not on this machine, and the span's exact numbers are not what
is under test - that two reels sharing ANY span share the file is.
```

## `tests/test_closer_redraw.py` (moved from its module docstring, 2026-10-02)

The shared CTA opens on a sentence start, by a pin that survives rebuilds.

The captain, 2026-09-10: the shared closer [321.61, 328.23] opens
mid-sentence ("we're calling the lucy visibility system ..."), and the
redraw starts it from "it's exactly why we've been building this
platform we're calling ...". Firstmate measured `"it's"` at 319.358,
after a 0.478s pause following `"answer"` (ends 318.875) - so the new
start is 319.358, the end stays 328.231, and each of the four reels
sharing the span (2, 9, 20, 26) grows by exactly 2.252s.

PR 880 established that keep exclusions can only REMOVE seconds, so no
existing mechanism can extend a closer backwards. The pin lives in
`captain_edits` as a `redraw_closer` delta - word-anchored like every
other edit there, refusing timecode pins by construction - naming both
ends in words: `anchor_phrase` (what the closer must open on) and
`from_phrase` (what it opens on now, identifying WHICH closer moves so
no other reel's closer is touched).

Fail-before: `validate_edits` knows no `redraw_closer` kind and
`captain_edits` has no `apply_closer_redraws` - every test here errors
on the kind or the attribute, not on an assertion.
