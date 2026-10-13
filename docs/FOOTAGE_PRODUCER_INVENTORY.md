# Footage measurement producer/consumer inventory + duplicate-decode audit

**Date:** 2026-10-12. **At:** `ad27452` (`origin/main` tip; the FI grounding
scout read `a3cd2dd`, two commits earlier - line numbers below are at this
HEAD). **Item:** FI-02 of the footage-intelligence backlog (Phase 0 tasks 1-3).

**Method:** static analysis and doc reads only. No pipeline run, no ffmpeg
invocation, no model load, no Resolve. Every cost class below is an
**ESTIMATE**, labelled as such; FI-01 / FI-23 measure them. Quoted measured
numbers (4.7 min I-frame pass, ms/frame figures) are re-cited from the
modules and docs named, not re-measured here.

**Open calls:** this item touches none of the six captain calls (report
section 9). It records the current producer list FI-03's registry builds on.

## 1. Lane inventory: every lane in `LANES` with its producer, readers, source and clock

Source of the lane list: `library/tools/footage_analysis.py:179-224`
(`LANE_NAMES`, 14 lanes). The machine-checked roster in section 4 pins this
table to that tuple; a lane added there without a row here fails
`tests/contracts/test_producer_inventory.py`.

"Authoritative source + clock" names what the signal is measured in. Rule
throughout: **source ticks are authoritative**; M9's per-second bins are an
access optimization over them, never a reduction of them.

| Lane | Signal | Producer (code path) | Slots written | Lane `reads` | Readers (verified) | Authoritative source + clock |
|---|---|---|---|---|---|---|
| `transcript` (M0+M1) | probe, program track, whole-source words | `library/tools/source_memory.py` `build_project` (M0 via `library/tools/source_primitives.py`; M1 as a projection of the canonical `library/tools/transcript_measurement.py` record, keyed by PCM sha) | `source.json`, `program.16k.wav`, `transcript.words.json` | () | M6 (`conversation_clock.py` n-gram offsets); text index (`analysis/footage_query.py:841` `build_index`); `analysis/footage_segments.py`; M9 speech (`world_model.py:314`); `project_model_evidence.py:340`; `memory_export.py`; `source_transcript.py`; 1.04's batched transcription serves the same canonical record (`step_1_04_temporal_index/step.py:613`) | M0: ffprobe streams + measured track levels; program-track choice recorded with basis (`declared`/`single`/`loudest-live`). M1: source seconds, MFA word boundaries; transcription keyed by 16 kHz mono PCM sha, so two extractions of the same samples are heard once |
| `frames` (M2) | one shared I-frame sample per source | `library/tools/source_memory.py` `build_project_frames` / `extract_iframes` (`:1162`): `-skip_frame nokey`, 384 px JPEGs, `-hwaccel videotoolbox` first with software retry | `frames/`, `frames.index.json` | () | M3, M3b (frame times), M3c (face crops), M3e (thumbnails), M3f (thumbnails), M3g (thumbnails), frame index (`analysis/footage_frames.py:219`, no ffmpeg of its own), M8 span frames (`span_verification.py`) | `frames[].t` is `showinfo`'s own `pts_time` per output frame, never a computed step; `rate_hz_nominal: 2.0` is a label for the 12-frame-GOP cadence, not an enforced target |
| `persons` (M3) | Apple Vision faces, lips, hands per frame | `library/tools/person_measurements.py` `build_project_persons` through the shared helper (`library/tools/vision_measure.py` `measure_frames`, persistence filter included) | `persons.json` | M2 | M3c (boxes + lip landmarks); M7 (`event_spans.py`); M9 faces (`world_model.py:315`); M10 (`relationships.py`); evidence `_migrate_faces` (`:475`), `_migrate_m3_frames` (`:743`) | M2 `t`; boxes image-normalised, top-left origin; lips mapped out of landmark-box space at write time (`docs/SOURCE_MEMORY.md` M3) |
| `identity` (M3b + M1b) | face/voice tracks within the source; diarized turns | `library/tools/person_entity.py` `build_project_identity` (faces: InsightFace `buffalo_l` ArcFace; voices: SpeechBrain ECAPA via the landed diarization); writes M1b `speakers.json` beside M3b via `source_memory.write_speakers` | `identity.json`, `speakers.json` | M2 | M3d face-track links; M7 `assign_face_tracks`; M9 entities (`world_model.py:316`); M10 owner resolution; `ren search --person` / `filter --person` via `resolve_person_tracks`; evidence `_migrate_identity` (`:402`), `_migrate_speech_face_links`, `_migrate_voice_matches`; M1b read by `source_transcript.py:210`, evidence `:398` | Faces at the nearest M2 I-frame times when M2 is fresh, else own planned times - `instrument.frame_source` (`M2-times-source-decode` / `own-decode`) says which; boxes drawn at 1408 px (`FACE_INPUT_MAX_WIDTH`), never the 384 px thumbnails |
| `expressions` (M3c) | expression labels per face per frame | `library/tools/expression_classifier.py` `build_project_expressions`: geometric lip features + edge-density image features over M2 face crops (cv2, no decode of its own) | `expressions.json` | M3, M2 | M9 faces (`world_model.py:323`, `_faces_at`); no evidence migration (FI-04 confirms/adds) | M2 `t`; `instrument.method: expression-classifier-v2-m2-crops` |
| `regions` (M3d) | person instance masks + region tracks | `library/tools/person_regions.py`: `VNGeneratePersonInstanceMaskRequest` over its **own 1152 px I-frame pass** (`DECODE_WIDTH = 1152`, via `source_memory.extract_iframes`); masks stored on the M2 grid + content-addressed sidecar | `regions.json`, `regions.masks.<sha>.json.gz` | M2, M3, M3b | M3f; M3g (median depth per region); M10 (hand ownership by region, `looking_at` ray targets); `overlay_keep_clear.py:122`; evidence `_migrate_regions` (`:936`) | M2 grid (`grid: [384, 216]`); sidecar bytes refused unless they hash to `sidecar.sha256`; `instrument.method: person-instance-mask-v1` |
| `saliency` (M3e) | attention-saliency boxes per frame | `library/tools/attention_saliency.py` `build_source_saliency` over the M2 thumbnails (no decode of its own) | `saliency.json` | M2 | `overlay_keep_clear.py` (keep-clear report); no evidence migration (FI-04 confirms/adds) | M2 `t`; `instrument.method: attention-saliency-v1-m2`; heatmap discarded, boxes only. **Build-time defect at this HEAD:** `_helper` / `_measure` (`:71-85`) import `vision_measure` from `library.steps.step_1_04_temporal_index`, which has no such submodule - every build raises `ImportError`. Row D2. Fix + test belong to a follow-up item, not FI-02 |
| `region_stats` (M3f) | whole-frame and exact subject/object-region luma, RGB colour and sharpness; legacy skin vector; outside all regions | `library/tools/region_stats.py`: arithmetic over cached M2 thumbnails + M3d masks on their grid (no decode, no model) | `region_stats.json` | M2, M3d | step 5.01 bridge `clip_region_stats`; `project_model_evidence` emits typed `image_measurement` observations alongside compatibility `region_stats` rows | M2 grid; each reading records pixel resolution, cell coverage and exact mask SHA-256; method `region-stats-v2-m2`; 4-neighbour Laplacian uses only whole-stencil cells |
| `depth` (M3g) | relative-depth medians inside each person region | `library/tools/depth.py` `build_source_depth`: Depth Anything V2 Small (vits, torch MPS, 70 ms/frame warm) over the M2 thumbnails + M3d masks on their grid; maps estimated on demand, never stored (`docs/benchmarks/2026-10-12-depth-on-demand.md`) | `depth.json` | M2, M3d | evidence `_migrate_depth` (`:1155`); behind-object foreground layering (`behind_subject.apply_behind_object`) | M2 grid; brighter-is-nearer (5/5 faces above the frame median on real footage); pooled p02-p98 normalisation per source; `instrument.m3d_sidecar_sha256` pins the masks measured; `instrument.method: depth-v1-m2` |
| `clock` (M6) | multicam offsets between transcripts | `library/tools/conversation_clock.py` `build_project`: unique word 4-gram agreement between M1 transcripts (measured groups, never declared) | `clock.json` | M1 | M9 `multicam` (`world_model.py:319`); M7 cross-angle placement (`event_spans.py` query path); `ren search --predicate` placement | `offset_to_reference_seconds` with `direct_measurement` (agreement fraction, p5/p95, tolerance); transitive offsets carry `direct_measurement: null`; a source in no group gets no file, never a zero offset |
| `events` (M7) | on-screen / speaking spans per person + geometric candidates | `library/tools/event_spans.py` `build_project_events` over M3 + M3b (no decode, no model, no lock) | `events.json` | M3, M3b | M9 events (`world_model.py:320`); structured query (`ren search --person ... --predicate`); evidence `_migrate_events` (`:628`) | Source seconds; `hand_near_mouth` and motion/boundary spans are CANDIDATES under `candidates`, never predicates |
| `world_model` (M9) | joined per-second timeline | `library/tools/world_model.py` `build_project_world_model`: pure join, no decode, no model, no lock; runs as a `ren analyze` lane AND via its own `build` command | `world_model.json` | M1, M3, M3b, M7 (lane declares) - **but the join reads M0/M1/M3/M3b/M3c/M4/M5/M6/M7/M8/M10** (`world_model.py:313-323`) | project-graph footage join (`project_graph/footage.py` `_build_world_models` via `read_world_model`); `world_model query` CLI; evidence treats M9 as derived, not migrated (FI-04) | Per-second bins are an access optimization over authoritative source ticks (A3 stays: bins unchanged by FI-03). Lane label says "over M1/M3/M3b/M7" - understates the join; lane `reads` omit M3c/M4/M5/M6/M8/M10, so a late write there does not freshen M9, and the lane runs BEFORE `relationships` (row-1 ordering lane owns this; FI-06 builds on it). Row D9 |
| `relationships` (M10) | holding / pointing / looking-at candidates | `library/tools/relationships.py` `build_project_relationships` over M3 + M3b + M3d (no decode, no model, no lock); refuses without M3/M3b/M3d per source | `relationships.json` | M3, M3b, M3d | M9 `relationships` (`world_model.py:322`); evidence `_migrate_relationships` (`:701`) | Source seconds; all three are geometric CANDIDATES for VLM verification, never answers; `instrument.method: relationships-v2 over M3 pose + M3d regions` |
| `text_index` (index) | speech/vision text index | `library/tools/analysis/footage_query.py:841` `build_index` over M1 (+ vision text); writes into project scratch, no memory slot | () slots; reads M1 | `ren search` | Inherits M1's clock |
| `frame_index` (index) | CLIP frame index | `library/tools/analysis/footage_frames.py:219` `build_frame_index`: `openai/clip-vit-large-patch14` over a `step_s`-thinned pick of M2 frames; no ffmpeg call of its own; clips without fresh M2 are SKIPPED | () slots; reads M2 | `ren search --visual` | M2 `t`; thinning (`_select_m2_frames`) caps CLIP cost vs dense ~2 Hz embedding (module docstring: ~17x) |

## 2. Producers that are not lanes

Slots with writers outside `LANES` (this is why section 42 of the report
says the B1 list "partly" exists as lanes - the code is complete, the lane
table is not the whole producer list):

| Slot | Producer (code path) | Written from | Readers (verified) |
|---|---|---|---|
| M4 `scenes.json` | step 1.03 semantic analysis via `vision_pipeline_v3.write_scene_memory` (`:3967`): per-clip VLM scene pass + 1.04 scene boundaries, serialized into the per-source digest-keyed slot; no new measurement | 10 s VLM windows (`ACTION_WINDOW_S`, `:130`; explicit `window_starts_s`, `:1364-1421`) | M9 scene (`world_model.py:317`); evidence `_migrate_scenes` (`:593`) |
| M5 `sound.json` | step 1.04 via `write_sound_memory` (`step_1_04_temporal_index/step.py:2113`): per-clip PANNs `sound_events` + audio classification, serialized into the per-source digest-keyed slot; an unmeasured record never demotes a measured one | per-clip audio (`clip_audio_16k`, `:168`) | M9 sound (`world_model.py:318`); evidence `_migrate_sound` (`:611`) |
| M8 `verdicts.json` | `span_verification.verify_spans` (`span_verification.py:133`): one local-VLM call per candidate span (<= 4 whole M2 frames, fixed `PROMPT_VERSION`); accumulates on demand via `ren search --verify`, keyed by prompt/model/statement/frame-times so repeats cost nothing | M2 frames + M7/M10 candidate spans | M9 event verdicts (`world_model.py:321`); `--verify` hits as YES spans with `verification`, NO spans as `rejected` |
| M1b `speakers.json` | identity lane via `source_memory.write_speakers` (`person_entity.py:791,899`) | same diarization run as M3b voices | `source_transcript.py:210`; evidence `:398` |

## 3. Duplicate-work audit

Each row: the code path, what repeats, and an ESTIMATE cost class (no
measurement runs - FI-01/FI-23 measure them). "Residual" means the
duplication is already retired and only leftovers are listed.

| # | Overlap | Code paths | Estimate |
|---|---|---|---|
| D1 (residual) | 1.04's own 5 Hz Apple Vision pass vs M3 | The pass is GONE (MU-12): `step_1_04_temporal_index/step.py:2151-2163` records the retired `vision_*` keys and `shed_retired_vision_keys` (`:2166`) drops them from cached indexes on reuse. 1.04 face presence is now ffmpeg + OpenCV Haar (`:2355`), not Vision | ESTIMATE: zero going forward; a cached pre-MU-12 index still carries the dead keys until it is reused and rewritten |
| D2 (defect) | M3e helper import points at a module that does not exist | `attention_saliency.py:71-85` imports `vision_measure` from `library.steps.step_1_04_temporal_index` (contains only `manifest.json`, `media_decode.py`, `step.py`); the shared helper both M3 and M3d use is `library/tools/vision_measure.py` (`person_measurements.py:76-77,203`, `person_regions.py:527-538`). Verified: the import raises `ImportError` at this HEAD, so the saliency lane cannot build | ESTIMATE: no runtime cost - the lane never runs; all M3e signal is unmeasured until a follow-up item fixes the two import lines and pins them with a test |
| D3 | 1.04 per-clip full decode vs M2 per-source I-frame decode | `index_clip` (`step.py:2909`) calls `decode_temporal_media` (`media_decode.py:32`) PER CLIP: one ffmpeg filter graph splitting into scene/motion-30fps/flow-5fps/face-5fps/hue-1fps rawvideo scratch files - the 30 fps branch forces a full decode. M2 (`source_memory.extract_iframes`, `:1162`) samples `-skip_frame nokey` I-frames only, once per source digest. Re-cited prior measurement (`docs/SOURCE_MEMORY.md` M2): ~4.7 min I-frame pass per 82 min file vs ~76 min full-decode pass | ESTIMATE: ~1 full source decode per clip + 1 I-frame pass per source digest; N clips sharing one source pay N full decodes for the same bytes |
| D4 | Per-lane own decodes beside M2 | M3b: sparse parallel `ffmpeg -ss` seeks at 1408 px (`person_entity.py:224-255`), at M2 times when fresh else own planned times (`:798-826`). M3d: own 1152 px I-frame pass (`person_regions.py:38,113`). NOT duplicated: M3c crops cached M2 thumbnails via cv2 (`expression_classifier.py:4-5,91-121`); CLIP frame index reads M2 thumbnails with no ffmpeg (`footage_frames.py:226`); M8 verdicts show M2 frames; M3f is arithmetic over thumbnails + masks | ESTIMATE: M3b O(<=120) sparse seeks per source; M3d ~1 extra I-frame pass per source at 1152 px (about half its wall time per its module docstring) |
| D5 | Three face detectors over overlapping frames | Apple Vision faces per M2 frame (M3); InsightFace detector inside `buffalo_l` per sparse M3b sample at 1408 px (`FACE_INPUT_MAX_WIDTH`, `person_entity.py:117`); OpenCV Haar per 5 Hz 1.04 face frame (`step.py:2355`, sizes at `:2272`) | ESTIMATE: same faces detected 2-3x at different cadences/resolutions; M3b never reads M2 thumbnails (resolution study, `person_entity.py:74`) so its pass is intentional, Haar-vs-Vision is the unexamined pair |
| D6 | Audio extracted twice, heard once | 1.04 extracts per-clip 16 kHz audio (`clip_audio_16k`, `step.py:132-192`, cached per clip); M0 demuxes the whole-source `program.16k.wav` once per digest. The hearing is deduplicated: `transcribe_clips_batched` (`:613`) goes through `transcript_measurement` keyed by PCM sha (`:639-653`), and MFA runs once per language per step run (`:551-571`) | ESTIMATE: 2x demux bytes (per-clip + per-source), 1x transcription per distinct sample; per-clip cache misses re-extract |
| D7 | Same frames through two VLM question paths | 1.03 semantic windows per clip (10 s tiling, `vision_pipeline_v3.py:130,1364-1421`) feed M4; M8 `verify_spans` re-shows M2 frames per candidate span for yes/no verdicts (`span_verification.py:133-188`, <= 4 frames/span, measured 257 calls / 17.3 min for 1.51 h in `docs/SOURCE_MEMORY.md` M8) | ESTIMATE: one dense Gemma pass per clip + one sparse Gemma pass per candidate span; same pixels, different prompts, no shared cache between them |
| D8 | Same pixels resident in several arrays | 1.04 scratch: 160x90 gray @30 fps full duration + flow @5 fps + face rgb @5 fps + hue rgb @1 fps (`media_decode.py:52-71`); M2 JPEGs on disk (~10.3 KB/frame at 384 px per `docs/SOURCE_MEMORY.md` M2); M3d transient 1152 px frames; M3b transient 1408 px JPEGs; masks sidecar (`runs-rowmajor-v1`); region_stats grid reads | ESTIMATE: per source-second, ~1.4 MB 1.04 scratch (30 fps gray leg dominates) + ~20 KB M2 JPEG + transient lane decodes; nothing shares one buffer (FI-10's "one frame buffer" is the fix) |
| D9 | M9 reads more than its lane declares, after its lane runs | Lane `reads` (`footage_analysis.py:211-214`): M1/M3/M3b/M7 only; join reads M0-M8+M10 (`world_model.py:313-323`); lane order puts `world_model` before `relationships` (`:211-219`) | ESTIMATE: staleness, not runtime - a first full run's M9 always lacks M10, and late M4/M5/M3c/M8 writes never freshen M9. Owned by the row-1 M9/M10-ordering lane (dependency) + FI-06; not changed here |

Retired with the browser: nothing in this inventory is a dashboard surface.

## 4. Machine-checked lane roster

The test `tests/contracts/test_producer_inventory.py` parses this block
and requires it to equal `footage_analysis.LANE_NAMES` exactly. A lane
with no section-1 row fails the companion assertion there.

<!-- FI02-LANE-ROSTER-START
transcript
frames
persons
identity
expressions
regions
saliency
region_stats
depth
clock
events
world_model
relationships
text_index
frame_index
FI02-LANE-ROSTER-END -->

## 5. Stale sentences corrected with this item

Three sentences found during grounding, corrected in this PR (not here -
in the files named):

1. `docs/SOURCE_MEMORY.md` M9 section: claimed `footage_analysis.LANES`
   carries no `world_model` lane so `ren analyze` never writes the slot -
   false since the lane at `footage_analysis.py:211-215`.
2. `docs/SOURCE_MEMORY.md` "analysis-only run" section: claimed the lanes
   fill "M0-M3d, M6, M7 and M10" with M9 built only by its own command -
   omits the M3e/M3f/M9 lanes.
3. `library/tools/source_memory.py` slot comment: claimed the slots after
   the first seven "are RESERVED ... with no code behind them yet" while
   every named slot now has a writer (the paragraph below it already says
   "No slot is reserved now").
