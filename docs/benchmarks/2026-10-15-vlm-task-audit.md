# VLM task audit - 2026-10-15

Scope: SEM-001 through SEM-004 and SEM-014 from the 2026-10-15 model-upgrade
punchlist. This is a code-path audit of the current main revision
`777ed3ba0`; it makes no model quality claim.

## Existing Gemma inference paths

| Consumer | Input and work | Current route | Task class |
|---|---|---|---|
| `library/tools/analysis/vision_pipeline_v3.py`, step 1.03 | Ten-second video plus audio windows; action changes, scene/environment, camera behavior, content type, and subject visibility | Gemma 4 12B 4-bit MLX; cached by measurement layer | Temporal and relational reasoning; needs a model that can consume video/audio windows |
| Same file, coarse object pass | Sampled stills; object labels and approximate appearance intervals | The existing still executor may use the driving host's vision; local fallback is Gemma | Object presence and scene/object classification; image-only task |
| Same file, detail object pass | Targeted stills selected after the coarse pass; refine transient object presence and ranges | Same still executor and fallback | Narrow presence/refinement; image-only, temporal continuity is inferred from sampled frames |
| `library/tools/still_vision.py` via `ask_the_footage` | User question over one or more stills | Image-capable host first, else Gemma | Depends on the question; object/scene questions are simple, interactions and cross-image questions are relational |
| `library/steps/step_4_03_plan_vfx/bridge.py` | A small still set; visual motion description to accompany measured motion | Host first, Gemma fallback | Still-image description; motion interpretation is a subjective/interpretive reading and must not replace measurements |
| `library/steps/step_5_01_color_grade/bridge.py` | Stills; white-balance appearance commentary beside deterministic measurements | Host first, Gemma fallback | Simple visual description; any color preference remains subjective and separate from measured color data |
| `library/tools/visual_qa_router.py`, `library/tools/qa/subtitle_qa.py` | Rendered stills and targeted questions | Host first, Gemma fallback | Image-level technical inspection; a human-facing preference is not an objective measurement |
| `library/tools/span_verification.py`, `broll_correspondence.py`, `video_segment_analyzer.py` | Still strips or video segments for evidence checks | Direct `VisionModel` calls; default model is Gemma 4 12B 4-bit | Span and correspondence checks are temporal/relational |

`VisionModel` is the shared local still/video interface. It uses a resident
Gemma server when available and falls back to in-process MLX. The semantic
pipeline has its own `VisionAnalyzer` and model-memory/inference reservations;
its video passes intentionally stay local because the host route is only
used for stills. `library/model_registry/registry.json` records Gemma as the
current `visual.describe` implementation, with a conditional Gemma Terms
license record. It does not yet provide a shared model capability adapter
contract; the evaluation-scorecard aggregator exists, but it consumes frozen
reports and does not run inference or select models.

## Simple perception and complex reasoning

The first useful lightweight candidates are limited to image-only questions
whose answer can be represented as a few object labels or one scene class:

- Is a named object visibly present in a sampled frame?
- Which broad scene class is shown in a frame?
- What static, clearly visible objects/features are in this frame?

The following need temporal understanding, relationships, or an explicit
fallback to stronger reasoning:

- Action identity and onset/offset over a window, particularly subtle or
  repeated actions.
- Person-object interaction, object continuity, and whether an object is
  held or merely nearby.
- Camera motion or a scene transition over time.
- Combining spoken delivery/audio with visible behavior.
- Any query spanning multiple frames/windows or asking for a causal,
  interpretive, or uncertain conclusion.

This is a task-level distinction, not a model ranking. Until candidates are
measured on the same calibration evidence, the operational choice remains the
incumbent. A lightweight first pass must never suppress the incumbent for
these temporal/relational tasks.

## Observation and interpretation boundary

The v3 window and object prompts explicitly ask for physically visible
descriptions and reject mood or inferred feelings. Deterministic picture and
motion measurements are produced separately from those descriptions. This
already establishes the essential separation for the audited VLM path. The
still QA callers also record which host or local model supplied the answer.
Keep that distinction in any future normalized output: observable claims
belong under observations; inferred relations and subjective scene readings
must be typed separately and retain their evidence references.

## Benchmark status and dependencies

No candidate comparison is reported here. The corpus manifest points to
`$HOME/Documents/work_stuff/Lucie consulting/Social Media/podcast media/`
and `$HOME/Documents/001/raw/`, but the expected calibration file
`IMG_1808.MOV` is not present at the manifest path or in the mounted volume
search performed for this audit. The benchmark must use the rights-cleared
podcast and Apple 001 originals, read-only; no substitute media was used.

The existing EVAL-04 proposal is explicitly unconfirmed and is not gold
annotation. The evaluator-only action/object consensus has two blind agent
labellers; its disagreement file says a third labeller was requested, so
only agreed calibration cases can be considered before that tie-break is
complete. Structured-output validity, hallucinated object/action/timestamp
rates, missed observations, and the benefit of escalation all require the
same frozen calibration examples and their agent labels.

Candidate slate for the follow-on benchmark:

| Punchlist item | Candidate | Intended benchmark precision | License / status |
|---|---|---|---|
| SEM-005 | Qwen3.5-4B MLX 4-bit | 4-bit on the 24 GB Mac | Qwen upstream card is Apache-2.0; published MLX conversion needs artifact-specific review |
| SEM-006 | SmolVLM2-2.2B MLX 4-bit | 4-bit on the 24 GB Mac | Upstream is Apache-2.0; use only a pinned conversion with recorded license evidence |
| SEM-007 | SmolVLM2-500M and 256M video variants | Full precision for <1.5B models | Upstream Apache-2.0; evaluate only narrow scene/object tasks |
| SEM-008 | Molmo2-4B | 4/8-bit if a supported conversion exists | Not cleared by this lane; defer until vep-mu-license records a permitted use |

Before adopting or changing a default, compare each candidate with Gemma on
identical frozen calibration inputs and record quality, cold/warm latency,
peak memory, decode/infer/serialize time, and end-to-end cost. Held-out
results are report-only after calibration choices are frozen. No model was
downloaded for this audit.

## SEM dispositions

- **SEM-001..004:** audited above.
- **SEM-005..012:** not measured; calibration footage is unavailable at the
  manifest's local paths, and no held-out results have been consulted.
- **SEM-013:** not implemented in this lane because the shared capability
  adapter contract belongs to vep-mu-harness and is not present on this base.
  Implement routing behind that contract after it lands; retain Gemma as the
  default until calibration qualifies an escalation policy.
- **SEM-014:** current prompts already distinguish visible observations from
  feelings/mood; preserve the distinction in the future shared output schema.
