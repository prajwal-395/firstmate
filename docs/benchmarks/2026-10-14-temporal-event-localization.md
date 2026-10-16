# Temporal action and event localization benchmark

`library.tools.analysis.temporal_event_benchmark` is a deterministic evaluator
for frozen M7 proposals and M8 verifier decisions against event labels.
It is independent of inference: it imports no model, reads no media and writes
only the requested report. Annotation intervals are seconds from source start,
half-open. Events record identity, event class, contact or state-change
evidence, and completeness. Hard negatives record a plausible non-event and
why it is negative.

The built-in vocabulary includes object actions (`reach`, `contact`,
`pick_up`, `use`, `set_down`), short gestures (`hand_raise`, `pointing`,
`nodding`, `head_shake`, `waving`), directional person transitions
(`person_enters`, `person_leaves`), and temporal opposites (`opening`,
`closing`). The class list is a starter vocabulary, not a claim that every
class occurs in the allowed podcast and Apple 001 footage. Event matching is
one-to-one, source and class constrained. It
maximizes match count first, then summed temporal IoU (default threshold 0.5).
Proposal precision/recall and verified-event
precision/recall are distinct. Verification accepts `yes`, `no`, `abstain`, or
`not_run`; only `yes` proposals count as verified events, and a missing M8
record is `not_run`, not an abstention or negative answer. The report includes
per-class and per-proposal-method results, temporal IoU, onset and offset
errors separately, mean and p95 combined boundary error, identity accuracy,
hard-negative rejection, abstentions, verifier calls
and recorded seconds. Proposal generation time is reported per source, and
verifier time and calls are reported overall and by event class. Monetary cost
remains null when the verifier does not record it. Empty denominators report
null rather than zero.

Detection rows default to `stage: "event_detection"`. A refined interval can
be scored independently using `stage: "boundary_refinement"` and a
`refines_proposal_id` that points to a same-class detection row. Output keeps
detection and refinement interval metrics in separate fields. The evaluator
does not itself provide an exact-boundary model.

## Evidence contract

The input is a version 1 JSON document with top-level `split: "held_out"` and
`sources`, plus the matching EVAL-01 corpus manifest supplied separately.
The manifest must mark `held_out_access.status` as
`sealed_custodian_only`, `tuning_access` false and
`label_payloads_in_repository` false. Every scored `source_id` must be listed
in that manifest with `split: "held_out"`; a dataset's self-declared split is
not enough. Each source has a unique `source_id`, `duration_seconds`, optional
measured `proposal_generation_seconds`, `proposals`, event `events`, and
`hard_negatives`. Every event has an ID,
`event_class`, `identity_id` (null if unknown), interval, `contact_evidence`,
`state_change_evidence`, and `completeness` (`complete`, `partial`, or
`uncertain`). At least one of the two evidence fields must be populated. Each
event also carries provenance: `annotator_id`, `review_status`
(`reviewed` or `adjudicated`), and an `evidence_ref` that points to the
source-frame evidence used by the annotator. Each
proposal records its method, class, identity, interval and verifier decision
with model ID and elapsed seconds. Annotations are source-bound and are never
read from training or calibration inputs. The evaluator refuses any split
other than held out.

For a recorded M7/M8 run, one proposal row corresponds to one span in
`events.json`; `proposal_method` records its geometric candidate predicate.
The verifier row carries the matching recorded `verdicts.json` answer, model,
and seconds. Events in a repeated-event group carry the same optional
`repeat_group_id`. The report calculates recall over groups with at least two
instances; matching remains one-to-one, so one proposal cannot cover repeated
instances. Temporal opposites are separate classes (for example
`person_enters` and `person_leaves`) and must not be collapsed into one
directionless label.

The prior evaluator expected a single reviewed human label. The captain's
current annotation policy supersedes that expectation: annotations must be
made by two independent blind agents from different model families, with a
third blind tie-breaker on disagreements. Outputs retain annotator identity
and event intervals; consensus reports agreement and remains identified as
agent-labeled. Agent labels are not human labels. Until those labels are
available, no held-out quality score is claimed. No candidate is ground truth,
and verifier agreement is not a substitute.

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
Therefore the existing change establishes the evidence format, scorer, and
hermetic acceptance tests, but it does not publish a real held-out quality or
cost measurement. It currently has no multi-agent agreement intake: that is
part of this lane's remaining implementation. The label request must be
fulfilled and the dataset custodian must release held-out agent labels before
held-out scores can be measured. No new footage or model pass is represented
by this document.
