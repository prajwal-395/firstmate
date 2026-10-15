# Agent-label calibration measurements (2026-10-15)

These are evaluator-only agent-label measurements, not human ground truth and
not frozen benchmark inputs. Search labels came from blind independent
labellers; action labels came from the two supplied agent annotation files.
The corpus sources and split assignments are documented in
[`2026-10-15-editorial-eval-corpus.md`](2026-10-15-editorial-eval-corpus.md).

## Blind visual search labels

Ingested all 30 pilot search windows to
[`evaluator-only/2026-10-15-agent-search-labels.json`](evaluator-only/2026-10-15-agent-search-labels.json).
The record preserves the three labeller models, 90% pairwise agreement, each
labeller's vote, and the third-labeller outcome. Final counts: 20 absent, 8
present, 2 unsure. Unsure rows are excluded from cutoff fitting. The labels
remain evaluator-only (`calibration_inputs: false`).

Scoring used the cached `openai/clip-vit-large-patch14` model, extracting one
336-pixel-short-edge frame per second from each 8-second window and taking the
maximum image-text cosine. The balanced-accuracy calibration selects a
`not found` cutoff of **0.14999** (below cutoff). On the 28 judged windows,
not-found precision is **14/16 = 0.875** and recall is **14/20 = 0.70**;
present retention is 0.75 and balanced accuracy is 0.725. The two unsure rows
were excluded. This is a measured candidate on agent labels, not human ground
truth; held-out media and labels were untouched. The per-window scores and
protocol are in
[`evaluator-only/2026-10-15-agent-search-calibration.json`](evaluator-only/2026-10-15-agent-search-calibration.json).

## Action and object interval labels

The consensus and third-labeller queue are
[`evaluator-only/2026-10-15-agent-action-consensus.json`](evaluator-only/2026-10-15-agent-action-consensus.json)
and
[`evaluator-only/2026-10-15-agent-action-disagreements.json`](evaluator-only/2026-10-15-agent-action-disagreements.json).
Matching is one-to-one within each take, requires a shared normalized verb or
object token and temporal IoU of at least 0.5, and chooses the highest-IoU
pair first. Matched boundaries are averaged. Unmatched labels and matched
labels with materially different wording or boundaries are left for a third
labeller; no event is added from inference by this task.

Across all 17 takes, 15 action pairs matched across a union of 89 action
labels (16.9% Jaccard agreement). Their mean absolute start-boundary
disagreement is 1.17 s and end-boundary disagreement is 0.62 s. On the nine
calibration takes, 9 pairs matched across a 46-label union (19.6%); mean
absolute start disagreement is 0.39 s and end disagreement is 0.96 s. The
agent annotation files sample frames at 1 fps, so these boundary differences
include roughly one-second visual timing granularity.

On the nine calibration takes, scratch-only M2/M3 produced 1 `hand_raise`, 2
`object_pickup` and 5 `nod` candidates. The two compatible agent-labeled
intervals are `stretch_arms_behind_head` -> `hand_raise` and `put_on_cap` ->
`object_pickup`. At temporal IoU >= 0.5, one matched: recall **1/2 = 0.50**.
For the matched interval, mean absolute boundary errors are **0.719 s** at
start and **2.454 s** at end. These small-sample agent-label measurements do
not establish performance on other actions or footage. M2 and Apple Vision M3
were computed from the rights-cleared originals and written under `/tmp`; the
Apple 001 project was not modified. Full candidate spans and match details are
in [`evaluator-only/2026-10-15-agent-action-candidate-measurement.json`](evaluator-only/2026-10-15-agent-action-candidate-measurement.json).
