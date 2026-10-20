# PERF-007 to PERF-014: analysis reuse and escalation

The 2026-10-20 capability punch list, section 7.2, owns eight items:

```
PERF-007  Keep reusable source observations independent of individual editing requests.
PERF-008  Reuse existing speech alignments, embeddings, visual descriptions, and tracking data when valid.
PERF-009  Batch related queries over shared footage.
PERF-010  Apply cheap-to-expensive model escalation using typed evidence requirements.
PERF-011  Use dynamic frame selection and interval refinement rather than repeated uniform sampling.
PERF-012  Maintain model warm-state only when memory pressure and expected reuse justify it.
PERF-013  Limit expensive tracking and segmentation to edit-relevant subjects and intervals.
PERF-014  Use caching and inference provenance to prevent accidental duplicate analysis.
```

This file records, per item, what ALREADY EXISTS (with the module that owns
it), what this lane changed, and what is still open. It is a status record,
not a rule; a rule lives with the code it governs.

The section's definition of done: *typical editing tasks spend most of their
runtime performing necessary creative work, not repeated setup or unnecessary
analysis.*

## PERF-007 - reusable source observations independent of the request

**Already done, before this lane.** The source memory
(`library/tools/source_memory.py`) is keyed by CONTENT DIGEST, not by edit
request: `<memory root>/<content_digest>/`, one slot per measurement lane
(M0-M11, VI-03, VI-09). Two projects cut from the same camera file read the
same memory for free, and nothing in it reaches `pipeline_data.json` (which
is rewritten after every step). The per-lane producer roster and its readers
are pinned in `docs/FOOTAGE_PRODUCER_INVENTORY.md`
(`tests/contracts/test_producer_inventory.py`), and the lane that produces
each slot is `library/tools/footage_analysis.py`, whose `slot_is_fresh()`
serves a fresh slot rather than rebuilding it.

**What this lane found and did NOT change:** the store is already
request-independent. The remaining gap is that the Project Model
(`library/tools/project_model_store.py`) is per-project, so observations that
reach it are project-local unless the explicit mutual opt-in in
`library/tools/project_graph/cross_project.py` is granted. That is a policy
decision about a client's footage, not a performance defect, and it is left
alone.

**Status: DONE before this lane.** No code change.

## PERF-008 - reuse alignments, embeddings, descriptions and tracking

**Already done, before this lane.** The semantic measurement layers
(`library/tools/analysis/measurement_layers.py`) cache the three expensive
layers (windows, objects, picture) by `sha256(layer, source digest, method
digest, parameters)`, so a compose from cached layers calls no model and a
partially warm clip measures only the windows whose keys missed. Speech is
keyed by the AUDIO (`library/tools/transcript_measurement.py`: the sha256 of
the WAV's PCM frames), so it is heard once per distinct signal across the
whole pipeline. Visual descriptions live in the M4 slot and merge per window
(`source_memory.merge_scene_window`). Tracking lives in M3b (identity) and
M3d (region tracks, with a content-addressed, self-verifying mask sidecar).
Planning decisions have their own content identity
(`library/tools/planning_reuse.py`).

**What this lane found and did NOT change:** reuse is complete for the lanes
that have a cache. The holes are DUPLICATE MEASUREMENT, not missing reuse,
and they are recorded per lane in `docs/FOOTAGE_PRODUCER_INVENTORY.md` §3
(rows D3-D8: a per-clip full decode beside the per-source I-frame pass; two
lanes' own decodes; three face detectors over overlapping frames; audio
extracted twice and heard once; the same frames through two VLM question
paths). Fixing those is a lane-scheduling change, and the two this lane
could close without touching a measured producer are PERF-009 and PERF-014
below.

**Status: DONE before this lane** for the reuse mechanisms; the duplicate
lanes remain open and are named in the inventory.

## PERF-009 - batch related queries over shared footage

**Already done, before this lane, in the analysis producers:** the
open-vocabulary detector batches N phrases into one OWLv2 call
(`library/tools/analysis/open_vocabulary_detect.py`, `OWLV2_QUERY_BATCH_SIZE`),
the semantic pipeline batches stills into 15-image host / 4-image local model
calls, the embedder encodes in chunks of 4, and the unified-anchor index
batches four windows per pass.

**What this lane changed.** The ACQUISITION PLANNER did not batch at all.
`library/tools/evidence_acquisition.py` scheduled one step per (requirement,
lane, window), so a requirement graph asking several questions of the same
footage scheduled the SAME producer call several times: the producer ran N
times over one interval, the plan's cost estimate counted N times one
measurement, and `run_acquisition` attributed the result to the LAST
requirement that asked - the earlier one paid for the measurement and lost
it. The planner now coalesces steps by acquisition identity (catalog lane,
lane, interval, producer - the identity the ledger key already carries) and
names every requirement each acquisition serves in `requirement_ids`. The
measurement is unchanged; it is paid for once and answers every question
that asked for it. The same coalescing runs in the escalation round
(`plan_escalation`), which had the same defect.

Measured on the planner's own cost model over measured per-window costs:
"scene description and camera movement" over a 120 s source went from 36
steps / 57.6 s to 12 steps / 19.2 s, and from 36 steps / 1310.4 s to 12 steps
/ 436.8 s when escalated to the OWLv2 tier. `docs/REN_EXECUTION_PERFORMANCE.md`
carries the table. A live run is the live lane's evidence; this lane names it
as the measurement it still needs.

**Status: IMPLEMENTED by this lane.**

## PERF-010 - cheap-to-expensive escalation on typed evidence

**Already done, before this lane.** The typed chain exists end to end:
`library/tools/requirement_graph.py` compiles a typed query into plans,
verifications or an unanswerable answer; `library/tools/evidence_contract.py`
types the evidence (`measurement` / `inference` / `evaluation` /
`creative_judgment`) and refuses a query answered by taste;
`library/tools/evidence_acquisition.py` prices every lane in
`PRODUCER_CATALOG` by tier, cost and peak RSS, picks the CHEAPEST ELIGIBLE
producer, refuses an unauthorized implementation fail-closed, and
`plan_escalation` runs a SECOND round only for the requirements the cheap
tier left open. `library/tools/vlm_routing_policy.py` escalates on OBSERVABLE
signals only (a malformed reply, a schema failure, invalid timestamps, an
empty claim, an abstained scene) - "the reason is always a measured signal,
never taste".

**What this lane found and did NOT change.** The two tiering systems are not
joined: `library/tools/analyzer_providers.py` (`AnalyzerTier`,
`EvidenceRequirement`, `run_ordered_tiers`) and
`library/tools/evidence_acquisition.py` (`PRODUCER_CATALOG`, `TIER_*`) answer
the same question with different vocabularies. The VLM escalation policy is
explicitly POLICY ONLY and says so in its own docstring - nothing routes
through it, and the checked-in evidence says why: the audit
(`docs/benchmarks/2026-10-08-analysis-scheduling-reuse-audit.md`) records
"no adaptive route is justified without a same-clip quality and cost
bakeoff", and the Qwen comparison was 4.8x slower for a quality win on some
examples. Choosing a model by quality judgement is a MEASUREMENT question,
and this lane does not run benchmarks (captain's rule).

**Status: DONE before this lane** at the requirement level (the acquisition
planner's tiering and escalation are live and are what PERF-010 asks for).
The MODEL-level escalation remains unconnected to a production path, and the
reason is recorded rather than guessed at.

## PERF-011 - dynamic frame selection and interval refinement

**Already done, before this lane.** `library/tools/adaptive_frame_sampling.py`
is a policy adapter that selects source-frame timestamps from activity,
novelty, scene and speech boundaries, OCR changes, entity transitions and
retrieval candidates, and returns the intervals it did NOT examine.
`library/tools/semantic_window_planner.py` plans activity-weighted windows
with a uniform discovery floor and returns `refinement_candidates` - "requests
to inspect, not claims that an event exists there". The decoder honours the
schedule: `library/tools/analysis/vision_pipeline_v3.py` decodes an explicit
timestamp list with exact seeks and a policy-keyed cache namespace, so an
adaptive still never masquerades as a dense one.

**What this lane found and did NOT change.** The DEFAULT is still `dense`
(uniform), deliberately: the measured event recall on the scheduling proxy
was mixed (calibration 1 up / 1 tied / 3 down; held out 1 up / 1 narrowly
down), and the checked-in benchmark says "no measured recall win: the default
stays dense". Flipping the default is a measurement question, not a code
question, and this lane does not run benchmarks.

**Status: DONE before this lane**, with the default left on the measured
choice.

## PERF-012 - warm state only when memory and expected reuse justify it

**Already done, before this lane.** MEMORY PRESSURE is enforced against live
OS memory at admission: `library/tools/resource_scheduler.py` reads the real
available memory (macOS `kern.memorystatus_level`, Linux `/proc/meminfo`),
fails closed on error, holds back 10% headroom plus a 2 GB floor, and
`MEASURED_MEMORY_RESERVATIONS_GB` reserves 15 GB for Gemma 4 12B and 8 GB
for SAM 2 from measured `phys_footprint` probes
(`docs/benchmarks/2026-10-15-memory-admission.md`,
`docs/benchmarks/2026-10-15-semantic-memory-bound.md`). Warm state is scoped
rather than unbounded: `library/tools/model_lifecycle.py` caches a model only
while a caller holds a managed lease or an explicit resident batch scope, and
`library/tools/resident_query.py` is a user-session worker that exits after
a bounded idle interval.

**What this lane found.** EXPECTED REUSE is not modelled anywhere: residency
is decided by call structure (is there an active lease or batch scope?), and
the only reuse signal that exists is after the fact (`cache_hits`,
`managed_uses` counters in `resource_accounting()`). A scheduler-level
"will the next call need this model?" judgement does not exist, and inventing
one would be a taste decision wearing a number.

**Status: DONE before this lane** for the memory-pressure half. The
expected-reuse half is reported as OPEN, with the reason: a reuse
prediction needs a measured basis this lane is not permitted to gather.

## PERF-013 - limit expensive tracking and segmentation

**Already done, before this lane.** SAM 2 runs ONLY on the clips a
matte-needing plan names (`library/capabilities/objects/segment.py`,
`matte_trigger`): a `subject_grades` entry in the colour-grade spec, or a
`behind_subject_overlays` segment playing over a span a placed picture clip
covers. The trigger is a pure function of the plans, so the step reports it
before paying anything and the segmenter is not even loaded when nothing is
wanted. Detection is tiered: the cheap class-agnostic Vision tier always runs
and OWLv2 is promoted only for a NAMED object or a higher declared fidelity
(`library/tools/analysis/open_vocabulary_detect.py`,
`detect_image_tiered`).

**What this lane found and did NOT change.** Segmentation is scoped by CLIP,
not by INTERVAL: `segment_clip` propagates over the whole clip, so a clip
needing a matte for three seconds is segmented end to end. The seeds are
edit-relevant (a face box, a detector seed), and the acquisition path
(`library/tools/query_directed_acquisition.py`) is already interval-bounded.
Changing the propagation to an interval needs the segmenter to seek and
re-seed inside the interval, and the face box is a FIRST-FRAME box - an
interval-scoped seed is a different measurement, and the checked-in
measurement (`docs/SUBJECT_MASKING_MEASURED.md`) is per clip.

**Status: DONE before this lane** for the subject-and-clip scoping, which is
what the item's measured cost is about (6.1x realtime, 58.3 MB for 807 s).
Interval scoping is reported as OPEN with that reason.

## PERF-014 - caching and inference provenance against duplicate analysis

**Already done, before this lane.** `library/tools/cache_lifecycle.py` holds
the cache-kind enumeration, the budget and watermarks, value-aware eviction
and the file leases that fence a reader against an eviction.
`library/tools/artifact_lifecycle.py` owns artifact registration and
retirement, and protects unknown ownership. `library/tools/model_evidence_provenance.py`
records which implementation produced each evidence item, with a fail-closed
reuse verdict. Every source-memory slot write carries a validated evidence
envelope (`source_memory._with_evidence_envelope`), and the acquisition
planner carries a dedup identity in the ledger key
(`evidence_acquisition.ledger_key`, ACQ-014).

**What this lane changed.** The dedup identity existed but was only consulted
against the LEDGER - work already done in an EARLIER run. It was never
consulted against the plan's own steps, so the planner scheduled the same
acquisition several times in one run and the run paid for it several times.
That is the accidental duplicate analysis this item names, and the
coalescing described under PERF-009 closes it: one acquisition per (lane,
interval, producer), attributed to every requirement it serves, recorded
once in the ledger.

**Status: IMPLEMENTED by this lane** for the duplicate-within-a-run case.
The cross-lane duplicates recorded in
`docs/FOOTAGE_PRODUCER_INVENTORY.md` §3 (rows D3-D7) remain open and are
named there.
