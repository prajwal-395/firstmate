# Multimodal model adapter contract

`library.tools.model_adapter_contract` is the boundary for model candidates.
It is deliberately separate from the canonical Project Model: adapters return
normalized transient results, and an owning evidence producer decides how
those results become Project Model observations. Candidate-specific SDK types
and raw model payloads do not cross that boundary.

## Request

Every `CapabilityInput` names a capability, source asset checksum, and
authoritative source clock tied to the same Project Model asset. Audiovisual
requests declare distinct source and sample clocks and must carry a measured
Project Model `TimeMap` that overlaps their requested ranges; audio is never
aligned to video by matching raw tick values or wall time.
`input_fingerprint` gives decoded/preprocessed
fixtures a deterministic cache key from source identity, interval, prompt,
coordinates, options, and stage revision. Video requests use half-open source
frame ranges and declare the
oriented source-display dimensions plus the exact crop, resize, and letterbox
transform. Dimensions describe the frame after applying recorded orientation;
crop and model pixels are mapped in that oriented space. Audio requests use
half-open sample ranges and their sample rate.
An audiovisual request may carry both. Each request carries authoritative
`source_intervals` on those source clocks. Time is never inferred from a
model's batch index or wall clock; intervals may retain exact frame or sample
endpoints. This supports variable frame rate media where frame timestamps,
not a nominal frame rate, define time. Clock conversion belongs to the producer
that owns the source clock. `interval_from_frames` and
`interval_from_samples` are conveniences for constant-rate video and sampled
audio; they use exact rational arithmetic and round outward to integer ticks.

`prompt` is the exact request text when the capability is prompt-conditioned.
`options` records deterministic implementation settings. `ComputeBudget`
contains caller limits for wall time, peak RSS, frames, and audio samples; an
adapter may refuse unsupported or over-budget work. Scheduling and resident
model ownership remain with Ren (`heavy_work_lock`, `resource_scheduler`, and
`model_lifecycle`); adapters do not start a parallel scheduler.

The contract registers the twenty capability names in punchlist §4.1 and
checks each capability's audio, video, or audiovisual input shape. Retrieval,
grounding, event-location, detector queries, and audio separation refuse an
empty/missing prompt.

## Result

`AdapterResult` records the actual supported and unsupported feature sets.
Boxes and points use normalized top-left source-display coordinates; masks
name an external artifact in source-display dimensions; embeddings name their
dimension and vector space; all temporal evidence uses source clock ticks.
Missing masks or other unsupported output cannot be represented as an empty
successful capability claim.

Every score keeps its raw number and explicit semantics. A raw score is not a
probability unless a calibration id and calibrated probability are present.
The raw response is separately persisted as a `RawArtifact` with a checksum;
normalization therefore does not erase debugging or calibration evidence.
Results distinguish completed work, unsupported capability, a named failure
(`model_unavailable`, `out_of_memory`, `unsupported_input`,
`execution_error`, or `low_confidence`), and completed searches with
`no_evidence`.

`CapabilityMedia` reads requested video/audio inputs by source ID and checksum;
decoded video frames keep their source frame indices and audio keeps its exact
sample range. `read_video_frames` refuses out-of-range, duplicate, unordered,
or wrong-dimension frames and enforces the frame budget;
`read_audio_samples` checks exact sample endpoints and rate. The media reader
is separate from model-specific code and allows decoding to be timed
independently from inference.

Implement `CapabilityAdapter.infer(request, model, media)` as a thin wrapper
around an already lifecycle-managed model. `run_adapter` checks capability
routing and declared feature support, but does not load, cache, schedule, or
project model data. Resolve remains the render and qualification test path;
the native engine is not an adapter target.

## Qualification records

The existing `ren_evaluation_report/1` input and
`ren_intelligence_scorecard/1` aggregator remain the frozen per-modality
measurement path. The qualification package schema is
[`ren-model-qualification-v1.schema.json`](ren-model-qualification-v1.schema.json):
it adds the explicit adoption decision, compute/fidelity comparison,
regression risks, and unsupported cases around those reports. It records
publisher revisions, never fingerprints files inside Resolve. Held-out
measurements are report-only; calibration data alone can select settings.

`python3 -m library.tools.model_qualification --record <frozen-record.json>`
verifies the dataset and report checksums, checks that the baseline and
candidate model revisions match their frozen reports, and emits a
machine-readable verdict. It applies the approximate 2x end-to-end target to
speed-only adoption and evaluates fidelity gates on calibration rows only.
An adoption decision also needs measured utility on an actual Ren task; an
always-on policy needs a measured calibration gain in downstream utility.
Held-out results remain report-only. Hardware and resources are compared
separately, and adoption requires both implementations to have passed a
Resolve-open concurrency run on the target M5 Pro with 24 GB unified memory.
Expensive specialist implementations can declare `on_demand` explicitly.

See `library/tools/evaluation_scorecard.py` and
`docs/benchmarks/evaluation-scorecard.md` for the frozen report aggregator
and checksum/split rules. The qualification command is explicit and stays
outside ordinary unit and model-analysis runs. The unified input suite and its
current label status are documented in `multimodal-evaluation.md`.

## Resource measurement

`library.tools.qualification_resources` is the shared recorder bakeoff lanes
use to produce the `resources.{baseline,candidate}` block the gate consumes.
It times caller-supplied phase callables (cold model load once, then
decode/preprocess, inference, postprocess/serialize) and reads process peak
RSS, deriving source-hours per wall-hour and useful evidence per compute
second from the caller's measured source duration and evidence count.
Utilizations, memory pressure, and the Resolve-open concurrency outcome are
caller-measured and validated, never invented: unmeasured stays `None`,
`"unknown"`, or `"not_run"`, and adoption is refused until those are
measured. `tests/contracts/test_qualification_ci_separation.py` keeps the
whole harness (contract, dataset, recorder, gate) free of model runtimes and
unreachable from pipeline runtime, so heavy qualification runs only when a
lane explicitly requests it.
