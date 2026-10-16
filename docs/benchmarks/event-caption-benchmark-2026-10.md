# Event caption benchmark (SEM-015..025)

Status: protocol and evaluator defined; labels and model scorecards are pending. This benchmark measures technical event coverage and evidence grounding. It does not measure editorial quality.

## Scope and splits

Use only the rights-cleared podcast camera originals and Apple 001 material declared in [`2026-10-15-editorial-eval-corpus.json`](2026-10-15-editorial-eval-corpus.json). Read source media only. Labels are agent labels: two independent blind model-family labellers, with a third independent tie-breaker for disagreements. Keep all three source annotations and the adjudicated event ledger outside the repository; publish only the split-level aggregate and opaque evidence references. Do not expose held-out annotations during tuning. Tune on training, choose parameters on calibration, freeze prompts/configuration, then measure held-out once.

Sample at event-bearing and transition-rich intervals across both collections. Keep podcast synchronized cameras and Apple 001 capture-scene groups together. Use 10-second windows aligned with current Gemma action windows, with a fixed 2-second stride only for benchmark sampling where full-window overlap is needed. Preserve source time in seconds and half-open intervals `[start,end)`. Include actions, state changes, person/object relationships, and explicit no-event intervals; use `unknown` when the image does not support a label. Do not infer speech content from silent video or interpret emotions/intent.

## Record contract

Each reference and prediction is an individual event object, never a paragraph:

```json
{
  "event_id": "opaque stable id",
  "source_id": "corpus source id",
  "interval": {"start_seconds": 1.2, "end_seconds": 2.7},
  "action": "lifts cup",
  "entities": [{"entity_id": "person-1", "kind": "person"}, {"entity_id": "object-1", "kind": "cup"}],
  "relations": [{"subject": "person-1", "predicate": "holds", "object": "object-1"}],
  "evidence": [{"source_id": "corpus source id", "interval": {"start_seconds": 1.2, "end_seconds": 2.7}, "frame_times_seconds": [1.5, 2.0]}],
  "provenance": {"method": "agent-label|model", "model_id": "...", "revision": "...", "prompt_id": "..."}
}
```

Every generated claim must carry one or more evidence references resolving to the source and sampled frame times. The envelope also records the sampling plan and frames actually consumed; a source interval alone is not proof that the model saw it. Predictions without valid evidence remain in the output and count as unsupported claims.

## Metrics and matching

Normalize action text for case and punctuation only. Match each prediction to at most one reference within the same source. A match requires compatible action/entity labels and temporal intersection-over-union (IoU) of at least 0.3; ties maximize label compatibility, then IoU, then minimize midpoint error. Report exact matching implementation/version with every run.

Report each split and each collection separately:

- Event recall = matched references / references.
- Description precision = supported matched predictions / predictions.
- Unsupported-claim rate = unmatched or evidence-invalid predictions / predictions.
- Timestamp quality = matched-event mean absolute start and end error in seconds, plus interval IoU; unmatched events are not silently dropped from counts.
- Evidence coverage = predictions with resolvable sampled-frame evidence / predictions.
- Useful evidence per compute = matched, evidence-backed events per inference second and per peak GB-second; also report total event count to reveal verbosity.

Agreement is measured before adjudication: event-count agreement, action-label agreement on pairwise temporal matches, relation-label agreement, and start/end absolute disagreement. Report adjudicated and raw-label aggregates separately. Never collapse them into one scalar.

## Systems and scorecards

The incumbent is the current Gemma `analyze_windows` pass in `library/tools/analysis/vision_pipeline_v3.py`: one folded model call per 10-second window emits actions, scene, camera and assessment; action events are returned under per-window `actions` with start/end values in source time. It currently does not attach per-claim evidence references, so the benchmark adapter must attach the exact source window and actual sampled-frame timestamps from `sampling` before scoring. Preserve raw output and prompt/model revision.

Compare (1) current sequential Gemma windows, (2) parallel Gemma windows with equivalent sampling and input, and (3) one specialized full-window caption pass if supported. PadCaptioner must not be fetched or run until its checkpoint and base-model permissions are verified; its official release instructions show a CUDA/PyTorch setup and no clear checkpoint license in the model card. CapRL-Video-4B is explicitly research-only / CC BY-NC 4.0 per its publisher card, so limit it to personal research and do not represent it as Commercial-eligible. A candidate's source license does not establish its weight, base checkpoint, dataset, or conversion rights.

For each run freeze one `ren_evaluation_report/1` JSON per model/split with model revision, exact precision, license class, corpus/evidence checksums, adapter revision, sampling plan, quality metrics, cold/warm latency, peak memory, decode/inference/serialization split, and selection basis limited to training/calibration. Reuse `library/tools/evaluation_scorecard.py` to aggregate; build the per-split rows with `library/tools/event_caption_adapter.py::build_evaluation_report`, score frozen artifacts with `library/tools/event_caption_benchmark.py`, and compare sequential/parallel/one-pass modes with `event_caption_adapter.compare_modes`. Record decision per EVAL-032: adopt, retain baseline, research-only, conditional fallback, or reject. Adoption requires useful evidence per unit of compute to improve without material regression in event recall, precision, hallucination rate, timestamp quality, and memory/latency on the 24 GB Mac. Held-out results are report-only. Candidate license findings and current decisions: [`event-caption-candidates-2026-10.md`](event-caption-candidates-2026-10.md).

## Current implementation boundary

The canonical per-source M4 slot (`scenes.json`) stores scene observations and ffmpeg boundaries. Dense actions are produced in `vision_pipeline_v3.py` and later joined to semantic/project evidence. Existing action records preserve individual events, but do not provide a complete event-caption benchmark, per-claim frame evidence, or candidate scorecards. Therefore retain Gemma as the baseline and do not change capability defaults or canonical schemas in this lane. The benchmark adapter (`library/tools/event_caption_adapter.py`) sits beside the capability interface, converting frozen window outputs to scored events; it runs no models and changes no defaults, pending the shared model harness contract.
