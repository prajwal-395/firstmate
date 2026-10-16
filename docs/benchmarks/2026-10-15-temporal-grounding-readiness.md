# Temporal grounding lane readiness (2026-10-15)

## Existing Ren behavior

The incumbent is present and remains the default: M7 derives geometric event
candidate spans from M3/M3b in `library/tools/event_spans.py`; M8 judges chosen
spans on demand in `library/tools/span_verification.py`. The separate
Project Model candidate rules live in
`library/tools/temporal_action_candidates.py`. Candidate intervals stay
hypotheses until verified.

The deterministic evaluator in
`library/tools/analysis/temporal_event_benchmark.py` already adapts frozen
M7/M8 outputs, does one-to-one source/class matching, and reports proposal
and verifier scores. Its earlier report only had boundary error averaged
together and no explicit repeated-event or boundary-refinement score. This
lane adds separate onset and offset errors, explicit repeated-event recall,
short-gesture and directional event classes, and a refinement stage linked to
its detection proposal.

The M7/M8 proxy audit already exists in
[`2026-10-15-action-candidate-precision.md`](2026-10-15-action-candidate-precision.md).
On one held-out LC4930 source, its calibrated candidate filter reduced 26
proposals to 6 and retained 2/2 stored M8 `yes` decisions. Stored verifier
answers are not independent labels: 0.333 is proxy agreement precision, not
human or agent-labeled precision. Independent event recall, repeated-event
recall, false proposal rate, and true temporal IoU are still unmeasured.

The source boundary is the existing rights-cleared GEO podcast and Apple 001
corpus in
[`2026-10-15-editorial-eval-corpus.json`](2026-10-15-editorial-eval-corpus.json).
Calibration labels must determine any settings. Held-out measurements are a
single report-only pass after prompts and thresholds are frozen. No footage
outside those collections is in scope.

## Candidate readiness

TimeLens2 publishes a 4B checkpoint and reports 47.7 average mIoU across its
own benchmark settings. That figure is not a Ren result and does not predict
performance on the allowed podcast and Apple 001 clips. The official code
repository says Apache-2.0 and links the checkpoint; this lane keeps its
weights research-only under the captain's restriction. The task will start
with 4B if the approved academic evaluation proceeds. The publisher revision
is frozen and verified (MCG-NJU/TimeLens2-4B HEAD
`f5dbceb56fb3060c16bfb24bb9d84bf8902dc0ac`, BF16, ~9.7 GB publisher storage,
checked 2026-10-16); checkpoint terms, a compatible compressed precision,
and the macOS execution path still need to be frozen before a model pass.

TimePLE's official implementation describes interval-native prediction and
lists a released 8B checkpoint, with Qwen3-VL integration. Its code repository
is Apache-2.0. The publisher revisions are frozen and verified (code HEAD
`05728f40bb34478811bcf86532e38e6499ce4462`, weights KlingTeam/TimePLE HEAD
`f583ef50ea9fc3498d537ff14438af25d5613d38`, BF16, ~17.8 GB publisher
storage, checked 2026-10-16; it needs its own `qwen3_vl_timeple` modeling
code, not plain transformers). The model and base-checkpoint terms must
still be qualified by the license lane. It is an alternative boundary
method; no local score or resource measurement exists yet.

The Pocket-STVG paper describes a sub-90M parameter spatiotemporal grounding
cascade. A paper is not an executable candidate. This lane did not identify an
official code repository or checkpoint from the publisher during this review,
so it remains on watch and was not downloaded.

The current machine is an Apple M5 MacBook Pro with 24 GB unified memory, not
an M5 Pro. This cannot satisfy the M5 Pro benchmark item. No candidate weights
were downloaded or model passes run in this readiness pass. Per-candidate
measurements and the explicit decisions are tracked in
[`2026-10-15-temporal-grounding-candidate-status.json`](2026-10-15-temporal-grounding-candidate-status.json).

## Remaining gates

- Obtain blind calibration labels from at least two independent agent
  labellers on different model families; send disagreements to a third blind
  tie-breaker and retain agreement statistics.
- Freeze the calibrated query set and settings before scoring held-out once;
  held-out label payloads stay with the corpus custodian until then.
- Benchmark M7/M8 against TimeLens2-4B in the authorized academic-research
  context, followed by TimePLE if its candidate precision and license clear.
- Measure onset, offset, temporal IoU, repeated-event recall, false proposals,
  detection, and refinement separately, alongside cold/warm latency, peak
  memory, decode/infer/serialize split, and end-to-end cost.
- Preserve the incumbent unless a challenger wins on the frozen local
  evaluation. Commercial temporal grounding remains on the existing eligible
  path until an eligible challenger is measured.
