# R06 - semantic verification is an explicit typed stage, 2026-10-22

The punch list's item **R06**: make semantic verification explicit -
distinguish structural validity from semantic validity, keep checking
spans against source boundaries, verify event/identity/action
assertions, require timestamp refinement for edit-critical events,
store the exact evidence, cache by candidate identity/model/task/
version, and return "uncertain" when verification does not establish
the claim. Acceptance: "Verified clip range" cannot be confused with
"visually verified action."

## What changed

* **New: `library/tools/retrieval/semantic_verify.py`** - the explicit
  stage. `verify_candidate` returns structural (the catalog's verdict,
  delegated to `search_in_planning.verify_span`, never reimplemented)
  and semantic (`verified` / `rejected` / `uncertain`, mapped from the
  judge's answer - only an explicit "no" rejects) side by side, with
  the top-level verdict always the semantic one. Edit-critical events
  (`task == "event"`) carry a `refined` span anchored in the evidence
  (the model's `frame_t` +- half the shown gap, else the shown span);
  a yes with no timestamp anywhere is `uncertain`, because the editor
  cannot cut on it. Every verdict stores the exact evidence (frame
  files/times, statement, model, prompt version, reply) and its cost.
  The cache lives in the source's OWN verdict store (`verdicts.json`,
  M8) under `semantic-verify/v1/` keys of (candidate identity, model,
  task, stage version) - the existing store extended, no new store.
  The module never loads a model: the default judge is the shipped
  interval verifier (shared shim, heavy-work lock), anything else is
  injected.
* **`library/tools/retrieval/temporal_rerank.py`** - the pass speaks
  the stage: unparseable answers become `uncertain` (counted apart
  from rejections, never presented), established events carry
  `refined_span` beside the unchanged presented window. The pass still
  selects and justifies; it does not re-shape the lane's geometry.
* **`library/tools/search_in_planning.py`** - planning verification
  with both halves: new `verify_claim` (structural + semantic), and
  every `slot_search` fill carries a `verification` block whose label
  is `verified clip range` unless a judge established the picture, in
  which case it is `visually verified action`. A judge's `rejected`
  span is skipped for the next candidate; `uncertain` still fills on
  its structural evidence, said aloud as a range. Default (no judge):
  placement behavior unchanged, now honestly labelled.
* **`library/tools/retrieval_eval.py`** - the temporal rollup counts
  `candidates_uncertain` (additive; existing keys untouched).

## Measurement: held-out set, before vs after

Corpus: the VI-002b bench (`ren-scratch-retrieval-bench`, rebuilt
frame index + recorded verdicts; no sealed label store, so the same
unlabelled entries stay unscored in both halves - comparable
before/after, not with the sealed report's absolutes). Denominators:
18 editorial queries, 6 ran the temporal pass, **59 judged
candidates**. Both halves: 0 live model calls (all 59 verdicts served
from recorded interval verdicts), recorded model time 3920.6 s in both.

| measure | before (`/tmp/r06-before.json`) | after (`/tmp/r06-after2.json`) |
|---|---|---|
| verdict accuracy (TP+TN / decided) | **0.4746** (13+15 / 59; FP 29, FN 2) | **0.4746** (identical) |
| uncertain verdicts | 0 (unparsed was silently a rejection) | 0 on this corpus; counted separately |
| visual macro recall (region-capped) | 0.0696 | 0.0696 |
| false-positive windows | 23 | 23 |
| per-query presented windows | - | byte-identical to before |
| event verdicts with refined timestamps | 0 | **6/6** (H03; 2/6 refined spans overlap a wanted moment) |
| planning fills labelled clip-range vs action | neither (one word: "filled") | every fill carries the `verification` block |

Verdict accuracy is defined in `/tmp/r06-accuracy.py`: a judged
candidate is ground-truth-positive iff its span overlaps a wanted
moment instance on its own asset (`retrieval_eval.overlaps`). The
accuracy is low in absolute terms because the candidates are already
the most similar frames and the judge over-verifies (FP 29) - that
over-verification is the VLM pass's known behavior, not this lane's
defect to fix; this lane's contract is that the number is now
measured under a typed verdict, with uncertainty counted apart.

Verification seconds: the stage cache was measured directly
(`/tmp/r06-cache-sweep.py`, same 59 candidates through
`verify_candidate` on the default judge): sweep 1 (populate,
interval-cache hits underneath) **12.21 s wall**, sweep 2 (stage
hits) **0.59 s wall** - ~20x on the cached path, 0 model calls
either way, verdicts stable across sweeps. The eval reruns likewise
spent 0 live calls.

## Known divergence (named, not fixed here)

12 of the 59 judged candidates are degenerate point windows
(start == end) the ranking lane proposes and the temporal pass puts
to the model (6 answered yes and presented). The stage refuses them
structurally (`verify_span`: the span is empty) and reports
`uncertain` without asking the judge. That is the stage being
stricter than the lane by design - a point is not a cuttable range -
and changing the lane's window geometry belongs to the
candidate-generation lane (VI-002b), whose tests pin today's shape.
Repro: `/tmp/r06-diag.py`.

## Reproducing

```sh
bin/vep -m library.tools.retrieval_eval \
  "$HOME/Documents/content_stuff/video_projects/ren-scratch-retrieval-bench" \
  --set library/tools/editorial_retrieval_heldout_set.json \
  --families editorial --out-json /tmp/r06-after2.json
bin/vep /tmp/r06-accuracy.py
bin/vep /tmp/r06-cache-sweep.py
bin/vep -m pytest tests/unit/picture/test_semantic_verify.py \
  tests/unit/picture/test_temporal_rerank.py \
  tests/unit/picture/test_search_in_planning.py -q
```

Unit: 16 new defect-naming tests (injected judges, no model); 149
neighboring + contract tests pass. No suite runs (lane rule).

## Left undone

* Wiring the stage into `FrameIndex.search_ranges` beyond the
  temporal pass's verdict mapping (the pass already routes through
  the stage vocabulary; `verify_ranges` provenance is unchanged).
* A semantic judge for planning slots by default (needs extracted
  frames per hit; the hook and the labels are in place).
* The 12 degenerate point windows (lane-geometry change, owned by
  candidate generation).
