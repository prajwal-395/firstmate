# Held-out visual retrieval benchmark

This benchmark extends FI-01's visual retrieval slice with frozen, query-level
uncertainty, a seeded uniform-frame baseline, category failures, negative-query
behavior, latency, peak process RSS, and latency per useful returned interval.
It evaluates the shipped `FrameIndex.search_ranges` path and does not modify its
ranking policy.

## Held-out labels and protocol

The frozen set is
[`visual_retrieval_heldout_set.json`](../../library/tools/visual_retrieval_heldout_set.json).
It evaluates the held-out sources `LC4930` and `LCATL0011`, which belong to one
synchronized-camera leakage group. The set has four present queries covering
objects, person framing, and scene composition; three absent queries; and one
action query expected to be refused. The laptop and blue-screen labels, three
negative queries, and refusal query were inherited from the previously
registered FI-01 set. The laptop labels were supplemented on `LC4930`, and two
further descriptions and their sampled frame labels were annotated by visual
inspection of every cached 10-second frame from each held-out source. The
labels are evaluation-only. No held-out
query or label was used to tune a model, score floor, or retrieval policy.

The estimate and percentile confidence intervals bootstrap queries, not the
many correlated sampled frames. With only four present queries from one
recording group, the intervals describe this pilot set and should not be read as
uncertainty over genres or recording sessions.

The current visual index has no score floor. The report therefore gives the
measured negative-query return/abstention rate and expected-action refusal
accuracy, and explicitly marks floor-based abstention as unsupported; it does not imply that returning nothing is a
behavior the current retriever can choose. The random baseline uniformly ranks
the same frame index, then passes its sampled frames through the same range
merging and interval scorer. Its fixed seed and sample count make the comparison
reproducible.

## Pinned result (2026-10-15)

Measured on current HEAD with the reproduce command below; the run's
`code_revision` is `58cfa6e5a24adf8477309c2141812b9958642a6a`
(`ran_at` 2026-10-15T03:52:04Z). Quality figures are deterministic given
the frozen set, fixed seeds, and the pinned index; latency and RSS are
observed on the measuring machine and will vary.

- Macro recall 0.1473, 95% query-bootstrap CI [0.0, 0.2945] (n=4);
  macro precision 0.2, CI [0.0, 0.4]. Held-out-only: same recall CI;
  precision 0.6667, CI [0.0, 1.0] (n=3; V07 returned nothing).
- Per-query held-out recall: V02 0.2558 (11/43 spans, precision 1.0),
  V03 0.0, V06 0.3333 (7/21 spans, precision 1.0), V07 0.0 (abstained:
  nothing returned). V05 action query correctly refused.
- Negatives: 0/3 abstained all-source (false-return rate 1.0); 2/3
  abstained on held-out assets only (VX1, VX3 abstained; VX2 returned
  one range). No score floor exists, so floor-based abstention stays
  explicitly `unsupported` rather than a measured policy.
- Cost: 4 useful intervals for 6.455 query-seconds, i.e. 1.61 s per
  useful interval; hosted API cost $0.00 (local inference only).
  Mean latency 0.81 s, p95 6.02 s (p95 is the first query's model-load
  cost; steady-state queries answer in ~0.05 s). Peak process RSS
  1061.86 MB.
- Baseline: seeded uniform-frame ranking through the same scorer reaches
  macro recall 0.0053 / precision 0.0243 (500 samples, seed 20261016),
  far below the shipped retriever; it also never refuses (0/500).
- Index provenance: 1092 frames, `openai/clip-vit-large-patch14`,
  index sha256 `ba056a21…15bce5b`, embeddings sha256 `9402c431…841075ef585`.

## Reproduce

The project, source memory, and source media are read-only. The command writes
only the report path; it does not rebuild the index or download a model.
Run it under the heavy-work lock because text encoding and frame scoring use
the model runtime:

```sh
bin/vep -m library.tools.heavy_work_lock run --owner vep-eval02 --profile semantics.analyse:inference -- \
  bin/vep -m library.tools.retrieval_eval \
  "$HOME/Documents/content_stuff/video_projects/lucie/geo-podcast" \
  --families visual \
  --set library/tools/visual_retrieval_heldout_set.json \
  --out-json /tmp/visual-retrieval-heldout.json
```

The report's `visual_benchmark.held_out` section contains each held-out query's
interval recall and precision, macro query-bootstrap intervals, and negative
abstention counts. The `baseline`, `latency_seconds`, `peak_process_rss_mb`,
`useful_interval_cost`, and `category_failures` fields carry the comparison and
operational measurements. The retained FI-01 visual result is the before
measurement: macro recall 0.155 and macro precision 0.3625 over four present
queries. It is a narrower, all-source score and is not directly comparable to
the held-out-only figures.
