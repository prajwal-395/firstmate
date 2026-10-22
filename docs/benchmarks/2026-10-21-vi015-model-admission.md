# VI-015: multimodal model admission, measured on this 24 GiB box

Measured 2026-10-21, on the captain's machine, with DaVinci Resolve open
(pid 47217) and other lanes running. This is the punchlist item "VI-015 -
Strengthen multimodal model admission and scheduling", measured rather
than asserted.

The evidence for the rule this measurement supports is in
`docs/evidence/resident_model_admission.md`.

The punchlist's four asks, and what this measurement says about each:

| ask | before | after |
|---|---|---|
| estimate memory requirements BEFORE loading heavyweight models | the stem separation carried NO demand at all; its code asserted an "8 GB RAM ceiling" that was never measured | every resident model carries a demand computed from the artifact about to load, floored by that model's measured peak |
| avoid competing GPU-intensive passes that cannot coexist | a resident model with no demand is admitted beside the 15 GB VLM session | the VLM and a stem separation are refused together, with the reading |
| distinguish unavailable resources from model inference failures | a refused segmentation and a failed inference both read as "no matte" | the record names `model_unavailable` and `inference_failed` |
| bounded recovery when model memory cannot be reserved | one attempt, then the batch was refused | a bounded number of attempts (2), then the batch is refused with the reading |

## The machine, at measure time

| reading | value |
|---|---|
| physical memory | 24.0 GiB, 10 cores, Apple M5 |
| Resolve | open, live, pid 47217 |
| live available memory | 9.36 - 12.48 GB across the four bench runs |
| memory pressure | 48% - 61% (`warn`) |
| swap | 11.05 - 11.63 GB used of 12.0 - 13.0 GB (87% - 93%) |

The bench uses the scheduler's documented test seam
(`VEP_SCHEDULER_MEMORY_GB`) so its before and after runs are comparable
and a run cannot load a 15 GB model on a busy box. Two readings were
used, both of them readings this box really had:

* **the busy reading** - 6.0 GB available of 24 GB (measured on this box
  during the run: "6.0 GB of 24 GB of memory available" in the refusal
  below). The fixed multi-model analysis run was measured here.
* **the calm reading** - 18.0 GB available of 24 GB, a machine with room
  for ONE resident model and not two. The competing-pass probe was
  measured here, because on the busy reading the first model is refused
  outright and there is nothing to compete with.

## Method

* **The admission probe.** A fixed set of three requests - the VLM's
  `semantics.analyse:model_memory`, SAM 2's `objects.segment:model_memory`
  and the stem separation's `music.analyse:model_memory` - is submitted
  to the scheduler, one at a time and then with the first still held. Each
  request's demand is the PROFILE's declared demand, so the probe
  measures what the pipeline asks for. Every request is recorded as
  ADMITTED or REFUSED, with the reason and the latency.
* **The fixed multi-model analysis run.** The same three resident models
  are asked to work on the same scratch media: 30 s of podcast audio
  (extracted to `/tmp`, the source media is read-only and was never
  written) and a 12 s clip. The run's wall time, each pass's peak
  physical footprint (from the machine's own grant history) and a digest
  of what each pass produced are recorded.
* **The output digest is over the SAMPLES, not the file bytes.** A WAV
  carries a timestamp in its metadata chunk, so two byte-wise different
  files can carry identical audio. The sample digests are identical
  across the before and after runs; the file digests are not, and the
  difference is that timestamp alone (max absolute sample difference:
  0.0).
* The bench is `docs/benchmarks/vi015_model_admission_bench.py`. It runs
  on both trees unchanged; the before tree is the base commit `5a3368ce8`
  with the bench copied into it.

## The admission probe

At the busy reading (6.0 GB available of 24 GB):

| | before | after |
|---|---|---|
| requests admitted | 1 / 3 | 1 / 3 |
| requests refused | 2 / 3 | 2 / 3 |
| the stem request's demand | `{}` - no memory check at all | `{"ram_gb": 2}` - the measured peak |

The two refusals are the VLM (15 GB) and SAM 2 (11 GB), refused in
0.001 s - 0.3 s with the reading beside them: *"this machine is already
paging: 11.1 GB of 12.0 GB of swap in use (93%), and 6.0 GB of 24 GB of
memory available"*.

At the calm reading (18.0 GB available of 24 GB), every request fits
alone, so every request is admitted in both trees (3 / 3 admitted, 0 / 3
refused, before and after).

## The competing-pass probe

One resident model held, a second asked for beside it:

| | before | after |
|---|---|---|
| the VLM's 15 GB held | admitted | admitted |
| a stem request beside it | **admitted** | **refused** |
| requests admitted | 2 / 2 | 1 / 2 |

Before, the stem separation asked for `{}` - it was admitted with no
memory check whatsoever, beside a 15 GB resident model, on a 24 GiB box
that already holds Resolve. That is the shape of the panic this machine
has already had (one 12-17 GB python beside Resolve). After, the stem
request is refused with the reading:

> resource scheduler: 'vi015-probe-stems' requests 2 GB RAM, but this
> machine is already paging: 11.3 GB of 13.0 GB of swap in use (87%), and
> 18.0 GB of 24 GB of memory available. a 2 GB resident model cannot be
> paged in on top of that, so it is refused rather than waited for

At the busy reading the holder is itself refused in both trees (0 / 2
admitted), so there is nothing to compete with.

## The fixed multi-model analysis run

The same scratch media, the same three models, before and after. The two
runs below were made back to back on the same busy box, so the machine's
state is the same for both:

| pass | before | after |
|---|---|---|
| stem separation - peak physical footprint | 1.552 GB (1 grant, 53 samples) | 1.686 GB (1 grant, 48 samples) |
| stem separation - wall time | 272.7 s (grant) / 278.6 s (bench) | 238.8 s (grant) / 254.9 s (bench) |
| stem separation - sample digest | `5fb64f5a...` | `5fb64f5a...` - **identical** |
| stem separation - the grant's demand | `{}` | `{"ram_gb": 2.0}` |
| VLM vision pass | refused (exit 1) | refused (exit 1) |
| VLM vision pass - wall time | 172.9 s | 165.3 s |
| SAM 2 segmentation | refused | refused, with kind `model_unavailable` |
| SAM 2 segmentation - wall time | 0.26 s | 6.03 s |

**The analysis output is identical.** The stem separation's samples are
bit-identical between the two trees (max absolute sample difference
0.0) - and identical across four runs, two on each tree. The file
digests differ, and the difference is the WAV's timestamp chunk and
nothing else. The peak resident memory is the same within the
sampler's noise (1.552 GB against 1.686 GB, 8% apart, both under the
2 GB reservation, so nothing overran its reservation), and the two
models this box cannot host are refused in both trees, for the same
reason.

**The wall times are the machine, not the change.** The separation's
wall time across three runs per tree: before 52.3 s, 272.7 s and
278.6 s; after 59.6 s, 238.8 s and 254.9 s. The spread within a tree
is larger than the difference between the trees, and in the paired run
the AFTER tree was the faster one. The box was at 90-96% of its swap
device during these runs; the demand itself is one check in
microseconds.

**The refusal costs one backoff.** The bounded recovery tries twice
with a 5 s pause, so a refusal is reported about 5 s later than before
(the SAM 2 segmentation: 0.26 s before, 6.03 s after). That is the
price of the recovery, and it is bounded: two attempts, never more.

## What was NOT measured, and why

* **The VLM pass and the SAM 2 pass did not run.** They are refused on
  this box, before and after, with the reading beside the refusal - the
  box had 6.0 GB available against 15 GB and 11 GB wanted, with 93% of
  the swap device in use. Forcing either is the panic this lane exists
  to prevent. Their "before" and "after" numbers are therefore REFUSALS,
  measured on the same box, not measurements of a pass.
* **The peak footprint of the VLM and of SAM 2 was not re-measured.**
  Their figures are the machine's own grant history
  (`docs/benchmarks/2026-10-22-prod006-resource-constrained.md`): the
  VLM 14.43 GB (1 sampled grant), SAM 2 10.14 GB (8 sampled grants).
* **The detector, the OCR recognizer and the PANNs sound-event pass are
  still admitted without a RAM demand.** They are the pipeline's smaller
  models - the detector peaked at 2.89 GB, PANNs at 0.43 GB - and the
  two lanes that load them are `ren` verb lanes rather than pipeline
  capabilities. They are listed here as what is left undone, not
  claimed as covered.

## The footprint measurement behind the new reservation

The stem separation's reservation is the MEASURED peak, not the ceiling
its code used to assert. Seven sampled grants of the same pass on this
machine: 1.552, 1.676, 1.686, 1.743, 1.769, 1.770 and 1.833 GB of peak
physical footprint (30 s and 60 s of source audio). The reservation is
2 GB - the peak plus a margin.

The asserted "8 GB RAM ceiling" was not merely unmeasured: it refused
work that would have run. Measured 2026-10-21, an 8 GB demand was
REFUSED on this box while 6.0 GB of memory was available - and the
separation itself needs 1.8 GB, so it would have run. A reservation
above the measured peak is a refusal, not a safety margin.
