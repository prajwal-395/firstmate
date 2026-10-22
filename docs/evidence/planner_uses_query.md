# VI-013b: reel planning asks the world narrowly, and the plan does not move

**Lane:** `p-vi013b-planner-uses-query` · **Date:** 2026-10-23 ·
**Corpus:** the 37 real reel candidates of the Ren-owned scratch project
`ren-scratch-l3wave`, with the reel builder's own speaker attribution as
ground truth · **Harness:**
`docs/benchmarks/vi013b_planner_uses_query.py` →
`docs/benchmarks/vi013b-planner-uses-query-2026-10-23.json`

PR 3139 (VI-013) built the world-model query surface and measured it at
404x less context per planning call, but no planning step consumed it:
the measurement showed what a planning call *would* carry. This lane is
the consumption.

## What changed

- **New:** `library/tools/reel_world_attribution.py` — one narrow
  speech question per candidate source span (`World.facts` over M1/M7
  at native timing), joined to the transcript's speakers by
  source-time overlap, filed as `review/reel_world_attribution.json`.
  It reports; it never decides: agreement is corroboration, disagreement
  is reported with both shares, and anything unanswerable is
  UNATTRIBUTED with the named reason.
- **Wired:** `select_reels` post_bridge `resolve` calls it after the
  selection is published. The return value is untouched — the only
  write is the review-area report.
- **DAG change:** `catalog -> select_reels` (`clip_catalog`), plus the
  optional manifest input and `reel.select` consumes. The input is
  bridge-only (absent from `context_fields`), so the model's prompt is
  byte-identical. Optional, so an uncatalogued run reports skipped
  rather than refusing.
- **Measured:** the harness above, before (whole per-second documents
  scanned per candidate) vs after (the wired query path), on the same
  37 candidates, same join, same ground truth.

## The measurement (denominators: 37 candidates)

| Measure | Before (whole view) | After (wired query) |
|---|---|---:|
| Candidates answered | 37/37 | 37/37 |
| Speaker set matches the plan | 36/37 | **37/37** |
| Speaker share within 0.10 of the plan | 31/37 | 32/37 |
| World-model context per planning call | 81,172,700 B · 28,609,326 o200k | **28,666 B · 9,739 o200k** |
| Candidates where wired is worse than the view | — | **0/37** |

2831x fewer bytes per planning call (2938x in o200k tokens), no
regression, one fix: candidate 4's Akshita turn, which the view's
cross-source scan misattributes to a Craig segment on another source
(source-seconds compared numerically across two different clocks). The
wired path joins within one source only, so the second time the same
numbers overlap they do not become the same moment.

The plan itself is untouched: `reel_candidates` and `reel_selection`
are byte-identical before and after (plan digest
`5c7eb59…2fd5` on both sides; `resolve()` returns no world keys —
`tests/unit/picture/test_reel_world_attribution.py`).

## What was left undone

- **No prompt view.** The INFRA-003 note envisioned a named context
  view over a routed input. Any prompt addition voids the output-identity
  proof, so the answers stay beside the prompt (review area) until a
  lane accepts re-measuring the model's decision with them in context.
- **The per-second view is still built per second**, and the whole-view
  route of this harness still reads it. The punchlist says to retain
  the view; this lane only stops planning from paying for it.
- **Share disagreement on 5/37** (both routes): the source-side and
  timeline-side diarizations split five candidates differently. Named
  per candidate in the report, not attributed.
- **No live Resolve build.** The hook runs in `resolve`, which runs in
  the pipeline; this lane proved the wiring read-only on scratch state
  plus the deterministic output identity. A live run will exercise the
  review-file write.
