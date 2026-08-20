# Step Data Flow Map

This maps every pipeline step: what it reads, what it writes, and what depends on it.
Generated from [dag.json](file:///Users/prajwal/Documents/content_stuff/video_editing_pilot/library/processes/edit_video/dag.json).

## Phase 0 — Prerequisites

### `step_0_01` — Validate SFX Library
- **Writes**: `sfx_library_status`
- **Feeds**: plan_sfx (4.04)

---

## Preflight — Ingest & Analysis

### `step_1_01` — Scan Project
- **Writes**: `raw_footage_files`, `project_folder`
- **Feeds**: catalog (1.02), semantic_analysis (1.03), temporal_index (1.04), prosody (1.05)

### `step_1_02` — Catalog Footage
- **Reads**: `raw_footage_files`
- **Writes**: `clip_catalog`
- **Feeds**: assign_aroll (3.01)

### `step_1_03` — Semantic Analysis
- **Reads**: `raw_footage_files`, `project_folder`
- **Writes**: `semantic_analysis_documents`
- **Feeds**: creative_direction (2.01), select_broll (3.02), plan_vfx (4.03), plan_sfx (4.04)

### `step_1_04` — Temporal Index
- **Reads**: `raw_footage_files`, `project_folder`
- **Writes**: `temporal_index`
- **Feeds**: prosody (1.05), creative_direction (2.01), speech_sequence (2.02), mesh_spine (2.05), review (3.03), subtitles (4.01)

### `step_1_05` — Prosody Analysis
- **Reads**: `temporal_index`, `project_folder`
- **Writes**: `prosody_analysis`
- **Feeds**: creative_direction (2.01)

---

## Phase 2 — Creative Planning

### `step_2_01` — Creative Direction ⚡ LLM
- **Reads**: `semantic_analysis_documents`, `temporal_index`, `prosody_analysis`
- **Writes**: `creative_direction`
- **Feeds**: speech_sequence (2.02), music (2.04), broll (3.02), review (3.03), transitions (4.02), vfx (4.03), sfx (4.04), color (5.01), audio (5.02)

### `step_2_02` — Speech Sequence ⚡ LLM
- **Reads**: `creative_direction`, `temporal_index`
- **Writes**: `speech_sequence`
- **Feeds**: mesh_spine (2.05), review (3.03)

### `step_2_04` — Music Selection ⚡ LLM
- **Reads**: `creative_direction`
- **Writes**: `music_selection`
- **Feeds**: music_analysis (2.06), mesh_spine (2.05), audio (5.02), manifest (5.04)

### `step_2_06` — Music Analysis
- **Reads**: `music_selection`, `project_folder`
- **Writes**: `music_analysis`
- **Feeds**: mesh_spine (2.05), plan_sfx (4.04)

### `step_2_05` — Mesh Spine ⚡ LLM
- **Reads**: `speech_sequence`, `music_selection`, `music_analysis`, `temporal_index`
- **Writes**: `spine`
- **Feeds**: assign_aroll (3.01), review (3.03), subtitles (4.01), sfx (4.04), render_subs (4.05), audio (5.02)

---

## Phase 3 — Assembly

### `step_3_01` — Assign A-Roll
- **Reads**: `spine`, `clip_catalog`
- **Writes**: `aroll_assignments`
- **Feeds**: select_broll (3.02), review (3.03)

### `step_3_02` — Select B-Roll ⚡ LLM
- **Reads**: `aroll_assignments`, `semantic_analysis_documents`, `creative_direction`
- **Writes**: `broll_selections`
- **Feeds**: review (3.03)

### `step_3_03` — Review Rough Cut ⚡ LLM
- **Reads**: `spine`, `aroll_assignments`, `broll_selections`, `creative_direction`, `temporal_index`, `speech_sequence`
- **Writes**: `rough_cut_review`
- **Feeds**: subtitles (4.01), transitions (4.02), vfx (4.03), sfx (4.04), color (5.01), manifest (5.04)

---

## Phase 4 — Post-Production

### `step_4_01` — Plan Subtitles
- **Reads**: `spine`, `temporal_index`, `rough_cut_review`
- **Writes**: `subtitle_plan`
- **Feeds**: render_subtitles (4.05)

### `step_4_02` — Plan Transitions ⚡ LLM
- **Reads**: `rough_cut_review`, `creative_direction`
- **Writes**: `transition_plan`
- **Feeds**: manifest (5.04)

### `step_4_03` — Plan VFX ⚡ LLM
- **Reads**: `rough_cut_review`, `semantic_analysis_documents`, `creative_direction`
- **Writes**: `vfx_plan`
- **Feeds**: manifest (5.04)

### `step_4_04` — Plan SFX ⚡ LLM
- **Reads**: `rough_cut_review`, `spine`, `semantic_analysis_documents`, `creative_direction`, `music_analysis`, `sfx_library_status`
- **Writes**: `sfx_plan`
- **Feeds**: manifest (5.04)

### `step_4_05` — Render Subtitles
- **Reads**: `subtitle_plan`, `spine`
- **Writes**: `rendered_subtitles`
- **Feeds**: manifest (5.04)

---

## Phase 5 — Finishing

### `step_5_01` — Color Grade ⚡ LLM
- **Reads**: `rough_cut_review`, `creative_direction`
- **Writes**: `color_grade_plan`
- **Feeds**: manifest (5.04)

### `step_5_02` — Audio Mix
- **Reads**: `spine`, `music_selection`, `creative_direction`
- **Writes**: `audio_mix_plan`
- **Feeds**: manifest (5.04)

### `step_5_04` — Compile Manifest
- **Reads**: `spine`, `broll_selections`, `music_selection`, `rough_cut_review`, `transition_plan`, `vfx_plan`, `sfx_plan`, `rendered_subtitles`, `color_grade_plan`, `audio_mix_plan`
- **Writes**: `assembly_manifest.json`
- **Feeds**: render (6.01)

---

## Phase 6 — Render

### `step_6_01` — Render (Resolve Assembly)
- **Reads**: `assembly_manifest.json`
- **Entry point**: `resolve_build_timeline.py`
- **Feeds**: validate (6.02)

### `step_6_02` — Validate Output
- **Reads**: rendered output files
- **Writes**: `validation_report`

---

## Key Data Objects

| Object | Created By | Used By |
|--------|-----------|---------|
| `assembly_manifest.json` | step_5_04 | step_6_01 (Resolve timeline builder) |
| `spine` | step_2_05 | 6 downstream steps |
| `creative_direction` | step_2_01 | 9 downstream steps (most connected) |
| `rough_cut_review` | step_3_03 | 6 downstream steps |
| `semantic_analysis_documents` | step_1_03 | 4 downstream steps |
| `temporal_index` | step_1_04 | 6 downstream steps |
