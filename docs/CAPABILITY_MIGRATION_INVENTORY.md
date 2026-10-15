# Capability migration inventory

This inventory covers every Python executable remaining under `library/steps/` on 2026-10-15. A step-path adapter is classified as **legacy-adapted**; its implementation owner is named in the note. The classification is about where executable behavior lives, not whether a DAG node still exists.

| Executable(s) | Classification | Owner / reason |
|---|---|---|
| `step_0_01_validate_sfx_library/step.py` | legacy-adapted | `sfx.library.validate` operation; project setup check remains on the pipeline path. |
| `step_1_01_scan_project/step.py` | legacy-adapted | Adapter for `footage.scan` in `library/capabilities/footage/scan.py`. |
| `step_1_02_catalog_footage/step.py` | legacy-adapted | Adapter for `footage.catalog` in `library/capabilities/footage/catalog.py`. |
| `step_1_03_semantic_analysis/step.py` | legacy-adapted | Adapter for `semantics.analyse` in `library/capabilities/semantics/analyse.py`. |
| `step_1_04_temporal_index/step.py`, `step_1_04_temporal_index/media_decode.py` | legacy-adapted | Adapter(s) for `temporal.index` and media decode in `library/capabilities/temporal/`. |
| `step_1_05_prosody_analysis/step.py` | legacy-adapted | Adapter for `prosody.analyse` in `library/capabilities/prosody/analyse.py`. |
| `step_1_06_object_segmentation/step.py` | legacy-adapted | Adapter for `objects.segment` in `library/capabilities/objects/segment.py`. |
| `step_1_07_ocr_extraction/step.py` | legacy-adapted | Adapter for `ocr.extract` in `library/capabilities/ocr/extract.py`. |
| `step_2_02_speech_sequence/bridge.py`, `post_bridge.py` | legacy-adapted | Prompt bridge and resolver still execute in the numbered step. |
| `step_2_04_music_selection/bridge.py`, `download_track.py`, `post_bridge.py` | legacy-adapted | Music selection and acquisition still execute in the numbered step. |
| `step_2_05_mesh_spine/bridge.py`, `post_bridge.py` | legacy-adapted | Spine planning still executes in the numbered step. |
| `step_2_06_music_analysis/step.py` | legacy-adapted | Pipeline analysis implementation remains in the numbered step. |
| `step_3_00_plan_picture/step.py`, `step_3_01_assign_aroll/step.py` | legacy-adapted | Picture planning and A-roll assignment remain in numbered steps. |
| `step_3_02_select_broll/bridge.py`, `post_bridge.py` | legacy-adapted | B-roll selection remains in the numbered step. |
| `step_3_03_review_rough_cut/step.py` | legacy-adapted | Review implementation remains in the numbered step. |
| `step_3_04_select_reels/bridge.py`, `post_bridge.py`; `step_3_05_judge_reels/bridge.py`, `post_bridge.py` | legacy-adapted | Reel planning and judging remain in numbered steps. |
| `step_4_01_plan_subtitles/bridge.py`, `post_bridge.py`, `step.py` | legacy-adapted | Subtitle planning remains in the numbered step. |
| `step_4_02_plan_transitions/bridge.py`, `post_bridge.py` | legacy-adapted | Transition planning remains in the numbered step. |
| `step_4_03_plan_vfx/bridge.py`, `post_bridge.py` | legacy-adapted | VFX planning remains in the numbered step. |
| `step_4_04_plan_sfx/bridge.py`, `post_bridge.py` | legacy-adapted | SFX planning remains in the numbered step. |
| `step_4_05_render_subtitles/generate_remotion_props.py`, `step.py` | legacy-adapted | Subtitle artifact generation remains attached to the pipeline step. |
| `step_4_06_render_motion_graphics/bridge.py`, `generate_motion_props.py`, `post_bridge.py` | legacy-adapted | Motion graphics planning and artifact generation remain in the numbered step. |
| `step_5_01_color_grade/bridge.py`, `grade.py`, `post_bridge.py` | legacy-adapted | Grade planning and application remain in the numbered step. |
| `step_5_02_audio_mix/bridge.py`, `mix.py`, `post_bridge.py` | legacy-adapted | Mix planning and application remain in the numbered step. |
| `step_5_03_creative_cohesion/step.py` | legacy-adapted | Cohesion review remains in the numbered step. |
| `step_5_04_compile_manifest/step.py` | genuinely pipeline-dependent | Consolidates the run's recorded plan outputs into the assembly manifest. |
| `step_5_05_build_edit_graph/step.py` | genuinely pipeline-dependent | Builds the edit graph from the assembled manifest and project media. |
| `step_6_01_render/build_verification.py`, `probe_resolve_capabilities.py`, `resolve_build_timeline.py`, `step.py` | genuinely pipeline-dependent | Resolve timeline construction and render orchestration require the pipeline's declared project/timeline context. |
| `step_6_02_validate_output/bridge.py`, `post_bridge.py` | legacy-adapted | Validation planning and resolution remain in the numbered step. |
| `step_7_01_build_reels/step.py` | genuinely pipeline-dependent | Reel builds operate on Resolve timelines and pipeline-owned reel state. |
| `step_7_02_verify_reels/step.py` | genuinely pipeline-dependent | Reel verification reads the built Resolve timeline and its evidence. |

No remaining Python executable was identified as obsolete. All non-adapter rows above are candidates for later migration; the next coherent batch should take the remaining preflight/planning modules, beginning with OCR/segmentation already moved here, then music analysis and picture planning. Resolve, manifest assembly, and reel timeline operations retain genuine pipeline context until their execution contracts can express those requirements directly.

## This batch: `objects.segment` and `ocr.extract`

Both implementations now live at stable capability paths independent of step numbering. `library/tools/operations.py` declares their executor paths while `owning_dir` and the step manifests remain compatibility metadata. The existing capability roster supplies project scope, consumed/produced state keys, DAG dependencies, and execution side effects. `library/tools/capability_contracts.py` declares required evidence: segmentation requires catalog evidence; OCR requires scanned footage evidence. Segmentation writes the triggered per-clip SAM 2 matte and model-link sidecar under the project segmentation area, reusing content-matched results; its resource phases declare the model-memory and inference locks. OCR writes per-clip EasyOCR artifacts under the project OCR area and reuses matching schema-versioned artifacts.

The module bodies were moved without behavioral edits. The step entry points remain stdin/stdout adapters for legacy pipeline callers. Existing DAG positions are used only to gather and record pipeline data; capability execution resolves the explicit `executor_path`.
