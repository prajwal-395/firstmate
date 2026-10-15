# Temporal action and event localization benchmark

`library.tools.analysis.temporal_event_benchmark` is a deterministic evaluator
for frozen M7 proposals and M8 verifier decisions against human event labels.
It is independent of inference: it imports no model, reads no media and writes
only the requested report. Annotation intervals are seconds from source start,
half-open. Events record identity, event class, contact or state-change
evidence, and completeness. Hard negatives record a plausible non-event and
why it is negative.

The accepted event classes are `reach`, `contact`, `pick_up`, `use`, and
`set_down`. Event matching is one-to-one, source and class constrained. It
maximizes match count first, then summed temporal IoU (default threshold 0.5).
Proposal precision/recall and verified-event
precision/recall are distinct. Verification accepts `yes`, `no`, `abstain`, or
`not_run`; only `yes` proposals count as verified events, and a missing M8
record is `not_run`, not an abstention or negative answer. The report includes
per-class and per-proposal-method results, temporal IoU, mean and p95 boundary
error, identity accuracy, hard-negative rejection, abstentions, verifier calls
and recorded seconds. Proposal generation time is reported per source, and
verifier time and calls are reported overall and by event class. Monetary cost
remains null when the verifier does not record it. Empty denominators report
null rather than zero.

## Evidence contract

The input is a version 1 JSON document with top-level `split: "held_out"` and
`sources`, plus the matching EVAL-01 corpus manifest supplied separately.
The manifest must mark `held_out_access.status` as
`sealed_custodian_only`, `tuning_access` false and
`label_payloads_in_repository` false. Every scored `source_id` must be listed
in that manifest with `split: "held_out"`; a dataset's self-declared split is
not enough. Each source has a unique `source_id`, `duration_seconds`, optional
measured `proposal_generation_seconds`, `proposals`, human `events`, and
`hard_negatives`. Every event has an ID,
`event_class`, `identity_id` (null if unknown), interval, `contact_evidence`,
`state_change_evidence`, and `completeness` (`complete`, `partial`, or
`uncertain`). At least one of the two evidence fields must be populated. Each
human event also carries provenance: `annotator_id`, `review_status`
(`reviewed` or `adjudicated`), and an `evidence_ref` that points to the
source-frame evidence used by the annotator. Each
proposal records its method, class, identity, interval and verifier decision
with model ID and elapsed seconds. Annotations are source-bound and are never
read from training or calibration inputs. The evaluator refuses any split
other than held out.

For a recorded M7/M8 run, one proposal row corresponds to one span in
`events.json`; `proposal_method` records its geometric candidate predicate.
The verifier row carries the matching recorded `verdicts.json` answer, model,
and seconds. The human labels are independently authored
using the corpus annotation schema and this event-specific evidence contract.
No candidate is treated as a human label, and verifier agreement is not used
as ground truth.

## Run

```sh
bin/vep -m library.tools.analysis.temporal_event_benchmark \
  /path/to/held-out-events.json \
  --corpus-manifest /path/to/editorial-eval-corpus.json \
  --out /tmp/temporal-event-report.json
```

This command performs no inference or media work and needs no heavy-work lock.
It returns status 2 for invalid or non-held-out input. The main and branch
acceptance checks are:

```sh
# On main, this fails because the evaluator and its acceptance tests are absent:
bin/vep -m pytest tests/unit/picture/test_temporal_event_benchmark.py -q

# On the EVAL-03 branch:
bin/vep -m pytest tests/unit/picture/test_temporal_event_benchmark.py -q
```

## Measurement status

The EVAL-01 corpus manifest at
[`2026-10-15-editorial-eval-corpus.json`](2026-10-15-editorial-eval-corpus.json)
declares held-out sources, but says action annotation is `not_started`, and
states held-out labels are not created or available to tuning workspaces.
Therefore this change establishes the evidence format, scorer, and hermetic
acceptance tests, but it does not publish a real held-out quality or cost
measurement. A synthetic or unit-test result would not satisfy the held-out
acceptance claim. EVAL-01 must produce and custodian-release held-out action
labels before that claim can be measured. No footage or model pass was run.
