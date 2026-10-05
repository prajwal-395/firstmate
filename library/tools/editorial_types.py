"""The three editorial types: fact, judgment, request.

The review's rule, which this module makes enforceable: keep separate what
the footage objectively contains (facts), what Ren believes makes a good
edit (judgments), and what the editor explicitly requested (requests).  A
judgment must never silently become a fact or a request, and a fact is
never written by a planner.

The three categories already existed as separate state keys; what they
lacked was a type system.  `pipeline_data.json` carried a judgment and a
measurement side by side with nothing recording which was which, so a
planner could state a judgment as though the footage had measured it and
nothing would catch it.  This module is that record.

**A fact is a measurement.**  It is produced by a step that measures the
footage or the render - the preflight steps, `music_analysis`, the
transcriber, the render and verify steps.  A fact key's producer is
recorded in `KEY_PRODUCERS`, and `check_contract` refuses a fact whose
producer is a planner step: a planner stating a judgment as a fact is
the defect the type system exists to catch.

**A judgment is a decision.**  It is produced by a step that reasons
about the edit - the creative planners.  A judgment must cite the facts
it rests on and the requests it honors.  `JUDGMENT_PROVENANCE` records
those citations, and `validate_state` refuses a judgment that rests on
no fact or request present in the state: a judgment with nothing under
it is a judgment presented as a fact.

**A request is an instruction.**  It comes from the editor - the brief,
the project's own declarations, the captain's timeline notes, the
user's taste profile, the brand template.  A request is honored or
explicitly declined; it is never silently converted into a judgment.

The `decided_value` ladder (STATED -> DIRECTED -> REASONED -> FALLBACK)
is the per-value shape of the same separation: a value's STATED rung is
a request, its REASONED rung is a judgment over measurements.  This
module is the state-key half of that rule.

Rules for this module
---------------------
- **Every editorial state key is typed.**  A key in `KEY_TYPES` is one
  of the three types, and `check_contract` refuses a fourth.
- **A judgment cites at least one fact or request.**  The citation is
  recorded in `JUDGMENT_PROVENANCE`; a judgment with no citation, or
  whose citations name no fact or request key, is refused.
- **No fact key is written by a planner.**  `PLANNER_STEPS` is the set
  of steps that reason about the edit; a fact key produced by one of
  them is refused.
- **A view is never built from a request unless it is itself a
  request.**  `check_view_type` enforces this on the context views: a
  measurement or judgment reading the editor's taste and presenting it
  as its own is the "judgment silently becomes a fact" defect at the
  prompt layer.

`tests/contracts/test_editorial_types.py` holds the behavioural proofs.
"""

from __future__ import annotations

from dataclasses import dataclass

FACT = "fact"
JUDGMENT = "judgment"
REQUEST = "request"

TYPES = (FACT, JUDGMENT, REQUEST)


class TypeContractError(ValueError):
    """A state key or view violates the fact/judgment/request contract."""


@dataclass(frozen=True)
class EditorialTypes:
    """The whole contract in one object, so a test can mutate a copy.

    The shipped registry is `SHIPPED` below; `check_contract` and
    `validate_state` take one of these so a test can prove the gate
    fails on a broken registry rather than only passing on the good one.
    """

    key_types: dict[str, str]
    key_producers: dict[str, str]
    judgment_provenance: dict[str, tuple[str, ...]]
    planner_steps: frozenset[str]


# ── The registry ─────────────────────────────────────────────────
#
# Every editorial state key and the type it carries.  A key absent
# from this table is not an editorial state key (run metadata such as
# `pipeline` or `capability_outputs` is not typed here); a step output
# key absent from this table is a gap `check_contract` refuses.
KEY_TYPES: dict[str, str] = {
    'a_roll_assignments': 'judgment',
    'actual_script': 'judgment',
    'answering_speaker': 'judgment',
    'assembly_manifest': 'judgment',
    'audio_catalog': 'fact',
    'audio_indices': 'fact',
    'audio_mix_spec': 'judgment',
    'audio_spine': 'judgment',
    'b_roll_assignments': 'judgment',
    'b_roll_interjections': 'judgment',
    'bed_measurements': 'judgment',
    'behind_subject_overlays': 'judgment',
    'brand_refinement': 'judgment',
    'brand_template': 'request',
    'broll_candidates_toon': 'judgment',
    'broll_window_frames': 'judgment',
    'camera_match': 'judgment',
    'caption_feedback_context': 'judgment',
    'caption_feedback_report': 'judgment',
    'cleanup_context': 'judgment',
    'clip_catalog': 'fact',
    'clip_exposure': 'judgment',
    'cohesion_review': 'judgment',
    'color_grade_spec': 'judgment',
    'content_rules': 'request',
    'creative_brief': 'request',
    'creative_direction': 'judgment',
    'cut_adjacency': 'judgment',
    'cut_decisions': 'judgment',
    'cuts_toon': 'judgment',
    'cuts_unjudged': 'judgment',
    'declared_look': 'request',
    'declared_speakers': 'request',
    'deterministic_validation': 'fact',
    'duration_zone': 'judgment',
    'edit_graph': 'judgment',
    'enhancement_spec': 'judgment',
    'footage_analysis_reference': 'judgment',
    'grade_terms_legend': 'judgment',
    'hook_assignment': 'judgment',
    'index_dir': 'fact',
    'lead_speaker': 'judgment',
    'length_guidance_seconds': 'request',
    'matte_trigger': 'fact',
    'mix_decision_legend': 'judgment',
    'mix_windows': 'judgment',
    'motion_axes_toon': 'judgment',
    'motion_elements_toon': 'judgment',
    'motion_graphics_frame': 'judgment',
    'motion_graphics_overlay': 'judgment',
    'music_analysis': 'fact',
    'music_candidates': 'judgment',
    'music_selection': 'judgment',
    'object_segmentation': 'fact',
    'ocr_extraction': 'fact',
    'picture_holes': 'judgment',
    'project_config': 'request',
    'project_fps': 'fact',
    'prosody_analysis': 'fact',
    'raw_audio_files': 'fact',
    'raw_footage_files': 'fact',
    'reel_ask': 'judgment',
    'reel_build': 'judgment',
    'reel_candidates': 'judgment',
    'reel_diagnostics_reference': 'judgment',
    'reel_judgement': 'judgment',
    'reel_selection': 'judgment',
    'reel_verification': 'fact',
    'reels_not_readable': 'judgment',
    'reels_to_read': 'judgment',
    'render_output': 'fact',
    'render_review': 'judgment',
    'render_watch_frames': 'fact',
    'rough_cut_review': 'judgment',
    'roughcut_window_frames': 'judgment',
    'semantic_analysis_documents': 'fact',
    'sfx_candidates_toon': 'judgment',
    'sfx_catalog_reference': 'judgment',
    'sfx_envelope_legend': 'judgment',
    'sfx_library_shape': 'judgment',
    'sfx_library_status': 'fact',
    'sfx_spec': 'judgment',
    'shot_stills': 'judgment',
    'skipped_files': 'fact',
    'source': 'fact',
    'source_resolution': 'fact',
    'speech_sequence': 'judgment',
    'still_colour_notes': 'judgment',
    'still_motion_notes': 'judgment',
    'subtitle_overlay': 'judgment',
    'subtitle_plan': 'judgment',
    'target_length_note': 'request',
    'target_length_seconds': 'request',
    'taste_profile': 'request',
    'temporal_event_indices': 'fact',
    'timed_spine': 'judgment',
    'timed_text_overlay': 'judgment',
    'timeline_context_toon': 'judgment',
    'timeline_notes': 'request',
    'timeline_transcript': 'fact',
    'topics_toon': 'judgment',
    'total_clips': 'fact',
    'total_clips_analyzed': 'fact',
    'total_failed': 'fact',
    'total_files': 'fact',
    'total_indexed': 'fact',
    'total_reused': 'fact',
    'transcripts_toon': 'judgment',
    'transition_spec': 'judgment',
    'transitions_toon': 'judgment',
    'turns': 'judgment',
    'undetermined': 'judgment',
    'validation_result': 'judgment',
    'value_decisions': 'judgment',
    'vfx_candidates_toon': 'judgment',
    'vfx_shot_stills': 'judgment',
    'visual_qa': 'fact',
    'voiceover_assignments': 'judgment',
    'who_leads_was_inferred': 'judgment',
}

# ── The producers ────────────────────────────────────────────────
#
# Which step (or named source) writes each key.  A step id is the
# `manifest.json` id; a bare name is a source that is not a DAG step
# (the captain, the user, the project, the transcriber).
KEY_PRODUCERS: dict[str, str] = {
    'a_roll_assignments': 'step_3_01_assign_aroll',
    'actual_script': 'step_3_03_review_rough_cut',
    'answering_speaker': 'step_3_04_select_reels',
    'assembly_manifest': 'step_5_04_compile_manifest',
    'audio_catalog': 'step_1_02_catalog_footage',
    'audio_indices': 'step_1_04_temporal_index',
    'audio_mix_spec': 'step_5_02_audio_mix',
    'audio_spine': 'step_2_05_mesh_spine',
    'b_roll_assignments': 'step_3_02_select_broll',
    'b_roll_interjections': 'step_3_02_select_broll',
    'bed_measurements': 'step_5_02_audio_mix',
    'behind_subject_overlays': 'step_4_06_render_motion_graphics',
    'brand_refinement': 'step_4_06_render_motion_graphics',
    'brand_template': 'project',
    'broll_candidates_toon': 'step_3_02_select_broll',
    'broll_window_frames': 'step_3_02_select_broll',
    'camera_match': 'step_5_01_color_grade',
    'caption_feedback_context': 'step_4_01_plan_subtitles',
    'caption_feedback_report': 'step_4_01_plan_subtitles',
    'cleanup_context': 'step_5_02_audio_mix',
    'clip_catalog': 'step_1_02_catalog_footage',
    'clip_exposure': 'step_5_01_color_grade',
    'cohesion_review': 'step_5_03_creative_cohesion',
    'color_grade_spec': 'step_5_01_color_grade',
    'content_rules': 'step_3_04_select_reels',
    'creative_brief': 'captain',
    'creative_direction': 'step_2_01_creative_direction',
    'cut_adjacency': 'step_5_01_color_grade',
    'cut_decisions': 'step_3_03_review_rough_cut',
    'cuts_toon': 'step_4_02_plan_transitions',
    'cuts_unjudged': 'step_4_02_plan_transitions',
    'declared_look': 'step_5_01_color_grade',
    'declared_speakers': 'step_3_04_select_reels',
    'deterministic_validation': 'step_6_02_validate_output',
    'duration_zone': 'step_2_05_mesh_spine',
    'edit_graph': 'step_5_05_build_edit_graph',
    'enhancement_spec': 'step_4_03_plan_vfx',
    'footage_analysis_reference': 'step_3_02_select_broll',
    'grade_terms_legend': 'step_5_01_color_grade',
    'hook_assignment': 'step_3_01_assign_aroll',
    'index_dir': 'step_1_04_temporal_index',
    'lead_speaker': 'step_3_04_select_reels',
    'length_guidance_seconds': 'step_3_04_select_reels',
    'matte_trigger': 'step_1_06_object_segmentation',
    'mix_decision_legend': 'step_5_02_audio_mix',
    'mix_windows': 'step_5_02_audio_mix',
    'motion_axes_toon': 'step_4_06_render_motion_graphics',
    'motion_elements_toon': 'step_4_06_render_motion_graphics',
    'motion_graphics_frame': 'step_4_06_render_motion_graphics',
    'motion_graphics_overlay': 'step_4_06_render_motion_graphics',
    'music_analysis': 'step_2_06_music_analysis',
    'music_candidates': 'step_2_04_music_selection',
    'music_selection': 'step_2_04_music_selection',
    'object_segmentation': 'step_1_06_object_segmentation',
    'ocr_extraction': 'step_1_07_ocr_extraction',
    'picture_holes': 'step_3_04_select_reels',
    'project_config': 'step_1_01_scan_project',
    'project_fps': 'step_1_02_catalog_footage',
    'prosody_analysis': 'step_1_05_prosody_analysis',
    'raw_audio_files': 'step_1_01_scan_project',
    'raw_footage_files': 'step_1_01_scan_project',
    'reel_ask': 'step_7_01_build_reels',
    'reel_build': 'step_7_01_build_reels',
    'reel_candidates': 'step_3_04_select_reels',
    'reel_diagnostics_reference': 'step_3_04_select_reels',
    'reel_judgement': 'step_3_05_judge_reels',
    'reel_selection': 'step_3_04_select_reels',
    'reel_verification': 'step_7_02_verify_reels',
    'reels_not_readable': 'step_3_05_judge_reels',
    'reels_to_read': 'step_3_05_judge_reels',
    'render_output': 'step_6_01_render',
    'render_review': 'step_6_01_render',
    'render_watch_frames': 'step_6_02_validate_output',
    'rough_cut_review': 'step_3_03_review_rough_cut',
    'roughcut_window_frames': 'step_3_03_review_rough_cut',
    'semantic_analysis_documents': 'step_1_03_semantic_analysis',
    'sfx_candidates_toon': 'step_4_04_plan_sfx',
    'sfx_catalog_reference': 'step_4_04_plan_sfx',
    'sfx_envelope_legend': 'step_4_04_plan_sfx',
    'sfx_library_shape': 'step_4_04_plan_sfx',
    'sfx_library_status': 'step_0_01_validate_sfx_library',
    'sfx_spec': 'step_4_04_plan_sfx',
    'shot_stills': 'step_5_01_color_grade',
    'skipped_files': 'step_1_02_catalog_footage',
    'source': 'step_1_04_temporal_index',
    'source_resolution': 'step_1_02_catalog_footage',
    'speech_sequence': 'step_2_02_speech_sequence',
    'still_colour_notes': 'step_5_01_color_grade',
    'still_motion_notes': 'step_4_03_plan_vfx',
    'subtitle_overlay': 'step_4_05_render_subtitles',
    'subtitle_plan': 'step_4_01_plan_subtitles',
    'target_length_note': 'step_3_04_select_reels',
    'target_length_seconds': 'step_3_04_select_reels',
    'taste_profile': 'user',
    'temporal_event_indices': 'step_1_04_temporal_index',
    'timed_spine': 'step_2_05_mesh_spine',
    'timed_text_overlay': 'step_4_06_render_motion_graphics',
    'timeline_context_toon': 'step_4_06_render_motion_graphics',
    'timeline_notes': 'captain',
    'timeline_transcript': 'transcriber',
    'topics_toon': 'step_2_02_speech_sequence',
    'total_clips': 'step_1_02_catalog_footage',
    'total_clips_analyzed': 'step_1_03_semantic_analysis',
    'total_failed': 'step_1_04_temporal_index',
    'total_files': 'step_1_01_scan_project',
    'total_indexed': 'step_1_04_temporal_index',
    'total_reused': 'step_1_04_temporal_index',
    'transcripts_toon': 'step_2_02_speech_sequence',
    'transition_spec': 'step_4_02_plan_transitions',
    'transitions_toon': 'step_4_04_plan_sfx',
    'turns': 'step_3_04_select_reels',
    'undetermined': 'step_3_04_select_reels',
    'validation_result': 'step_6_02_validate_output',
    'value_decisions': 'decided_value',
    'vfx_candidates_toon': 'step_4_03_plan_vfx',
    'vfx_shot_stills': 'step_4_03_plan_vfx',
    'visual_qa': 'step_6_01_render',
    'voiceover_assignments': 'step_3_01_assign_aroll',
    'who_leads_was_inferred': 'step_3_04_select_reels',
}

# ── The planner steps ────────────────────────────────────────────
#
# The steps that REASON about the edit: phases 2-5 reaching a model
# (llm_only, hybrid, deterministic_with_llm) and producing at least
# one judgment.  Preflight (1.x), the deterministic measurement and
# merger steps (2.06, 3.01, 4.05, 5.03, 5.04) and the execution and
# verification steps (6.x, 7.x) are not planners: they may write
# facts.  A fact key produced by one of these is a planner stating
# a judgment as a fact.
PLANNER_STEPS = frozenset(
{
    'step_2_01_creative_direction',
    'step_2_02_speech_sequence',
    'step_2_04_music_selection',
    'step_2_05_mesh_spine',
    'step_3_02_select_broll',
    'step_3_03_review_rough_cut',
    'step_3_04_select_reels',
    'step_3_05_judge_reels',
    'step_4_01_plan_subtitles',
    'step_4_02_plan_transitions',
    'step_4_03_plan_vfx',
    'step_4_04_plan_sfx',
    'step_4_06_render_motion_graphics',
    'step_5_01_color_grade',
    'step_5_02_audio_mix',
})

# ── The provenance contract ───────────────────────────────────────
#
# Each judgment key and the fact/request keys it rests on.  A
# judgment must cite at least one key typed fact or request; the
# citation is what stops a planner from stating a judgment as though
# the footage had measured it.  `validate_state` checks the
# citation against the state: a judgment whose citations name no
# fact or request PRESENT in the state rests on nothing.
JUDGMENT_PROVENANCE: dict[str, tuple[str, ...]] = {
    'a_roll_assignments': ['clip_catalog', 'timed_spine'],
    'actual_script': ['clip_catalog', 'speech_sequence', 'timed_spine'],
    'answering_speaker': ['clip_catalog', 'creative_brief', 'timeline_transcript'],
    'assembly_manifest': ['clip_catalog', 'timed_spine', 'transition_spec', 'enhancement_spec', 'sfx_spec', 'color_grade_spec', 'audio_mix_spec', 'subtitle_plan'],
    'audio_mix_spec': ['music_analysis', 'music_selection', 'speech_sequence', 'timed_spine'],
    'audio_spine': ['creative_direction', 'music_analysis', 'music_selection', 'speech_sequence'],
    'b_roll_assignments': ['clip_catalog', 'semantic_analysis_documents', 'timed_spine', 'a_roll_assignments', 'creative_direction'],
    'b_roll_interjections': ['clip_catalog', 'semantic_analysis_documents', 'timed_spine', 'a_roll_assignments', 'creative_direction'],
    'bed_measurements': ['music_analysis', 'timed_spine'],
    'behind_subject_overlays': ['object_segmentation', 'timed_spine', 'creative_direction'],
    'brand_refinement': ['brand_template', 'timed_spine'],
    'broll_candidates_toon': ['clip_catalog', 'semantic_analysis_documents', 'timed_spine', 'creative_direction'],
    'broll_window_frames': ['clip_catalog', 'timed_spine'],
    'camera_match': ['clip_catalog', 'timed_spine'],
    'caption_feedback_context': ['subtitle_plan', 'render_output'],
    'caption_feedback_report': ['subtitle_plan', 'render_output'],
    'cleanup_context': ['audio_mix_spec', 'music_analysis', 'mix_windows'],
    'clip_exposure': ['clip_catalog', 'timed_spine'],
    'cohesion_review': ['clip_catalog', 'timed_spine', 'transition_spec', 'enhancement_spec', 'sfx_spec', 'audio_mix_spec', 'color_grade_spec'],
    'color_grade_spec': ['brand_template', 'clip_catalog', 'semantic_analysis_documents', 'timed_spine'],
    'creative_direction': ['clip_catalog', 'semantic_analysis_documents', 'temporal_event_indices', 'prosody_analysis', 'creative_brief', 'project_config', 'taste_profile'],
    'cut_adjacency': ['clip_catalog', 'timed_spine'],
    'cut_decisions': ['clip_catalog', 'speech_sequence', 'timed_spine'],
    'cuts_toon': ['music_analysis', 'timed_spine'],
    'cuts_unjudged': ['music_analysis', 'timed_spine'],
    'duration_zone': ['creative_direction', 'music_selection', 'speech_sequence', 'temporal_event_indices'],
    'edit_graph': ['clip_catalog', 'semantic_analysis_documents'],
    'enhancement_spec': ['creative_direction', 'music_analysis', 'semantic_analysis_documents', 'timed_spine'],
    'footage_analysis_reference': ['clip_catalog', 'semantic_analysis_documents'],
    'grade_terms_legend': ['clip_catalog', 'color_grade_spec'],
    'hook_assignment': ['clip_catalog', 'timed_spine'],
    'lead_speaker': ['clip_catalog', 'creative_brief', 'timeline_transcript'],
    'mix_decision_legend': ['audio_mix_spec', 'music_analysis'],
    'mix_windows': ['music_analysis', 'timed_spine'],
    'motion_axes_toon': ['creative_direction', 'temporal_event_indices', 'timed_spine'],
    'motion_elements_toon': ['creative_direction', 'temporal_event_indices', 'timed_spine'],
    'motion_graphics_frame': ['brand_template', 'timed_spine'],
    'motion_graphics_overlay': ['brand_template', 'timed_spine'],
    'music_candidates': ['audio_catalog', 'creative_brief', 'creative_direction'],
    'music_selection': ['audio_catalog', 'creative_brief', 'creative_direction'],
    'picture_holes': ['clip_catalog', 'timed_spine'],
    'reel_ask': ['assembly_manifest', 'clip_catalog', 'timed_spine'],
    'reel_build': ['assembly_manifest', 'clip_catalog', 'timed_spine'],
    'reel_candidates': ['clip_catalog', 'creative_brief', 'project_config', 'timeline_transcript', 'timed_spine'],
    'reel_diagnostics_reference': ['clip_catalog', 'timed_spine'],
    'reel_judgement': ['clip_catalog', 'reel_candidates', 'semantic_analysis_documents'],
    'reel_selection': ['clip_catalog', 'creative_brief', 'project_config', 'timeline_transcript', 'timed_spine'],
    'reels_not_readable': ['clip_catalog', 'reel_candidates'],
    'reels_to_read': ['clip_catalog', 'reel_candidates'],
    'render_review': ['assembly_manifest', 'render_output'],
    'rough_cut_review': ['clip_catalog', 'speech_sequence', 'timed_spine'],
    'roughcut_window_frames': ['clip_catalog', 'timed_spine'],
    'sfx_candidates_toon': ['sfx_library_status', 'timed_spine'],
    'sfx_catalog_reference': ['sfx_library_status'],
    'sfx_envelope_legend': ['sfx_library_status'],
    'sfx_library_shape': ['sfx_library_status'],
    'sfx_spec': ['music_analysis', 'sfx_library_status', 'timed_spine', 'transition_spec'],
    'shot_stills': ['clip_catalog', 'timed_spine'],
    'speech_sequence': ['creative_brief', 'creative_direction', 'semantic_analysis_documents', 'temporal_event_indices'],
    'still_colour_notes': ['clip_catalog', 'timed_spine'],
    'still_motion_notes': ['temporal_event_indices', 'timed_spine'],
    'subtitle_overlay': ['subtitle_plan', 'temporal_event_indices', 'timed_spine'],
    'subtitle_plan': ['creative_brief', 'creative_direction', 'speech_sequence', 'timed_spine'],
    'timed_spine': ['creative_direction', 'music_analysis', 'music_selection', 'speech_sequence'],
    'timed_text_overlay': ['brand_template', 'timed_spine'],
    'timeline_context_toon': ['creative_direction', 'temporal_event_indices', 'timed_spine'],
    'topics_toon': ['creative_direction', 'temporal_event_indices'],
    'transcripts_toon': ['temporal_event_indices'],
    'transition_spec': ['creative_direction', 'music_analysis', 'music_selection', 'timed_spine'],
    'transitions_toon': ['temporal_event_indices', 'timed_spine', 'transition_spec'],
    'turns': ['clip_catalog', 'timeline_transcript'],
    'undetermined': ['reel_candidates', 'timeline_transcript'],
    'validation_result': ['assembly_manifest', 'render_output'],
    'value_decisions': ['clip_catalog', 'temporal_event_indices'],
    'vfx_candidates_toon': ['music_analysis', 'timed_spine'],
    'vfx_shot_stills': ['clip_catalog', 'timed_spine'],
    'voiceover_assignments': ['clip_catalog', 'timed_spine'],
    'who_leads_was_inferred': ['clip_catalog', 'timeline_transcript'],
}

SHIPPED = EditorialTypes(
    key_types=KEY_TYPES,
    key_producers=KEY_PRODUCERS,
    judgment_provenance=JUDGMENT_PROVENANCE,
    planner_steps=PLANNER_STEPS,
)


def check_contract(types: EditorialTypes) -> list[str]:
    """Every violation of the type/provenance contract in a registry.

    Returns a list of problems; empty means the registry is well-typed.
    This is the static half: it checks the registry against itself (every
    key typed, every judgment citing, no fact planner-produced).  The
    runtime half is `validate_state`, which checks a state dict.
    """
    problems: list[str] = []
    for key, ktype in types.key_types.items():
        if ktype not in TYPES:
            problems.append(
                f"{key}: type {ktype!r} is not one of {TYPES}")
        producer = types.key_producers.get(key)
        if producer is None:
            problems.append(f"{key}: no producing step recorded")
        elif ktype == FACT and producer in types.planner_steps:
            problems.append(
                f"{key}: fact written by planner step {producer}")
    for key, ktype in types.key_types.items():
        if ktype != JUDGMENT:
            continue
        citations = types.judgment_provenance.get(key)
        if not citations:
            problems.append(
                f"{key}: judgment cites no fact or request")
            continue
        if not any(types.key_types.get(c) in (FACT, REQUEST)
                   for c in citations):
            problems.append(
                f"{key}: judgment cites no fact or request key")
    return problems


def registry_problems() -> list[str]:
    """The contract checked against the shipped registry."""
    return check_contract(SHIPPED)


def validate_state(state: dict,
                   types: EditorialTypes | None = None) -> list[str]:
    """Every judgment in a state that rests on no fact or request present.

    A judgment key must cite, through `JUDGMENT_PROVENANCE`, at least one
    key typed fact or request that is ALSO in the state.  A judgment with
    nothing under it is a judgment presented as a fact - the defect the
    type system exists to catch.  Returns a list of problems; empty
    means every judgment in the state is grounded.
    """
    types = types or SHIPPED
    problems: list[str] = []
    for key in state:
        if types.key_types.get(key) != JUDGMENT:
            continue
        citations = types.judgment_provenance.get(key, ())
        grounded = [c for c in citations
                    if c in state
                    and types.key_types.get(c) in (FACT, REQUEST)]
        if not grounded:
            problems.append(
                f"{key}: judgment rests on no fact or request "
                f"present in the state")
    return problems


def check_view_type(view_type: str, source_keys,
                    types: EditorialTypes | None = None) -> None:
    """Refuse a view built from a request it does not carry.

    A measurement or judgment reading the editor's taste and presenting
    it as its own is the "judgment silently becomes a fact" defect at
    the prompt layer: the model cannot tell a measurement from the
    editor's request.  A view typed FACT or JUDGMENT therefore reads no
    REQUEST key; a view typed REQUEST reads only REQUEST keys.
    """
    types = types or SHIPPED
    for source in source_keys:
        source_type = types.key_types.get(source)
        if source_type is None:
            continue
        if view_type == REQUEST and source_type != REQUEST:
            raise TypeContractError(
                f"a request view cannot be built from {source} "
                f"(typed {source_type})")
        if view_type in (FACT, JUDGMENT) and source_type == REQUEST:
            raise TypeContractError(
                f"a {view_type} view cannot be built from {source} "
                f"(typed request)")
