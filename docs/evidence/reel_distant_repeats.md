# `reel_distant_repeats` - test history

Narrative moved verbatim out of test module docstrings; the tests keep the invariant.

## `tests/test_reel_distant_repeats.py` module docstring (moved 2026-10-02)

```text
Distant repeats are reported, never cut - and the CTA is scanned at all.

The brief's measured case (report filed outside this tree, reconstructed
here from its description and the repo's own verbatim fixtures): reel 03
plays its tagline THREE times - twice in the body 40-150s apart with
another speaker between them, and again as the closing CTA from elsewhere
in the episode. Reel 07 says its line twice, twelve seconds apart, and
survives on a text bar rather than the window.

What today's code does with that shape (reproduced before the fix):

- `redundant_takes` over the body: [] - the pair is past CUT_WINDOW.
- `suspected_takes` over the body: [] - the loose scan breaks at the
  same window, so the report lane is blind past it too.
- the CTA range: never scanned - `reel_ranges` places it whole, so a
  closer echoing the body plays the line twice with nothing said.
- only `duplicate_takes` (word-stream, selection-time, report-only)
  sees the body pair.

The distinction this file pins, by rule versus by model:

- BY RULE, CUT: nothing new. Distance alone cannot tell a callback from
  a retake, so CUT_WINDOW_SECONDS still gates every cut. A near-identical
  same-speaker pair 45s apart is NOT cut here.
- BY RULE, REPORT: a distant pair that meets the CUT text bars with
  agreeing durations becomes a suspect (the markers lane), and a closer
  that echoes the body or repeats itself is named by `closer_repeats`.
  Both say why they were kept: distance / a deliberate choice.
- LEFT TO THE MODEL: whether a reported repeat is a retake to redraw
  past or a deliberate callback to keep.

`library/tools/reel_build.py` - `suspected_takes`, `closer_repeats`;
`library/tools/reel_proposal.py` - `enrich`.
```
