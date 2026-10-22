# F02 - task-dependent editorial precision (mm-f02-precision)

## What was built

Editorial precision is now an explicit query parameter instead of an
accidental by-product of which tool was called:

- `library/tools/edit_precision.py` (new): the four precisions
  (`browse`, `concept`, `transient`, `cut`), what each allows, which
  precision a result row carries (`frame_exact` only for a measured
  source-clock boundary - matching words with their own timings, a
  frame-exact refinement, or an exact measured boundary; everything
  else is `frame_approximate`), and the refusal (`partition_for_cut`,
  `check_boundary_for_cut`). A refusal is REPORTED, never silent.
- `FootageIndex.search` / `search_report`: new `precision` parameter
  (default `browse`, which answers exactly as before). Every result
  carries a `precision` block; at `cut` the approximate rows are
  refused into `precision_refused` and only frame-exact rows come
  back. `ren search --precision cut|transient|concept|browse`
  (text lane) and `search_json(..., precision=...)` reach it.
- `edit_candidates.candidates_from_retrieval(..., precision=...)`:
  at `cut` a frame-approximate observation is dropped with the reason,
  so the edit path refuses it before it reaches the timeline.
- Every `--json` hit row carries `temporal_precision` /
  `cut_allowed` on all lanes (text, sound, ocr, visual, chunks,
  unified): a sound span, OCR appearance, CLIP range or 8 s chunk is
  WHERE to look, never an exact event boundary.

## The named measurement: cuts from approximate observations, before vs after

Recorded-plan shape (one exact speech row among approximate scene /
action rows), through `candidates_from_retrieval`:

| run | placed | of which approximate | refused (named) |
|---|---|---|---|
| before (`browse`) | 3/3 | 2/3 | 0 |
| after (`cut`) | 1/3 | 0/1 | 2, each with reason |

So cuts whose boundary came from an approximate observation went
**2 -> 0** (denominators: 3 rows in, 2 approximate). No quality
regression: `browse` places everything exactly as before (default
unchanged), and the refused rows are reported in `dropped` /
`precision_refused`, never silently lost.

Pinned by `tests/unit/tools/test_edit_precision.py`
(`test_the_named_measurement_before_vs_after`,
`test_cut_refuses_the_approximate_and_reports_each_refusal`) and the
search-level equivalents (`test_search_at_cut_keeps_only_the_word_timed_row`,
`test_search_at_cut_with_nothing_exact_abstains_honestly`).

## What was left undone

- `transient` annotates (`needs_verification` + `verify_with`) but
  does not itself trigger a `frame_exact.refine_boundary` run: the
  tighter verification is named, not yet automatic. That run loads
  the Vision helper and decodes media, which this lane must not do
  (memory constraint).
- Sound/OCR/visual/chunk lanes answer at `browse` only: their rows
  are marked approximate in the JSON shape, but those lanes take no
  `precision` parameter yet.
- No live-Resolve measurement: per the brief, no model loads and no
  render/encode on this lane.
