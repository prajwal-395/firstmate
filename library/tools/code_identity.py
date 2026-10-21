"""The identity of the code that produced a cached preflight value.

The problem this exists to remove
---------------------------------
The preflight cache is invalidated by the FOOTAGE and never by the CODE
that wrote it.  A fix to a preflight step - a new field, a corrected
measurement, a changed prompt - is silently invisible on any project
that already has a cache: the system reports success while the work did
not happen.  Three commits of work read a path that never fired.

The identity check here is the companion to
``library/tools/footage_identity.py``.  That one watches the footage;
this one watches the code.  Together they make "preflight is skipped
once done" safe rather than merely fast.

What this hashes
----------------
Every ``.py`` and ``.json`` file in the step's own directory, sorted by
name, excluding ``__pycache__``.  This is the step's declared source: its
``step.py``, ``bridge.py``, ``manifest.json`` and ``handoff.md`` are all
there.  ``.md`` files are excluded because they are prose documentation,
not executable code - a typo fix in a handoff doc should not cost 69
minutes of vision analysis.

What this deliberately does NOT hash
-------------------------------------
*Not the model weights.*  A WhisperX upgrade is not a code change to step
1.04.  The operator can ``--rerun temporal_index`` for that.

*Not undeclared imports.*  Plumbing every step shares - the project
layout, stdout claiming, the brand registry's project declarations -
does not change a measured value and is listed in ``EXEMPT_IMPORTS``
rather than hashed, so touching it does not cost a 69-minute vision
re-run.  What IS hashed beyond the step directory is declared below in
``STEP_IMPLEMENTATION_DEPS``: the shared implementation files a step
executes as measurement code.  An undeclared shared import is a gap in
that declaration, and ``tests/unit/context/test_ledgers.py`` refuses it - it
scans every DAG-preflight step for the reference and fails until the
map covers it.

Why the map exists
------------------
Step 1.03's directory holds a launcher; the measurement lives in
``library/tools/analysis/vision_pipeline_v3.py``, which the step runs
as a subprocess.  Hashing the directory alone watches the launcher and
never the algorithm, so a fix to the algorithm - the usable-ranges
A-roll gate - was invisible to the cache on every existing project and
the run reported success while the work did not happen.  The map makes
that file part of step 1.03's identity, and the same shape everywhere
else it occurs: any preflight step whose cached values are computed by
shared code names that code here.

Adoption on first encounter
----------------------------
A ledger entry with no recorded ``code_hash`` is from before this check
existed.  It is ADOPTED - the current hash is recorded and the step is
not invalidated.  This matches the pattern ``apply_source_identity`` uses
for footage fingerprints (line 668-670 of run_pipeline.py), and avoids a
69-minute cold pass on the first run after the upgrade.

The trade-off, stated rather than hidden: an existing cached value
produced by buggy code survives one more run under adoption.  From the
second run onward, the hash is recorded and any code change invalidates
the cache.  An operator who wants to force the recompute can
``--rerun preflight`` once.
"""

import hashlib
from pathlib import Path

# The extensions that count as executable source for a step.
# .md is deliberately excluded - a prose edit in handoff.md is not a code
# change and should not cost a 69-minute recomputation.
_SOURCE_EXTENSIONS = {".py", ".json"}

# Repo root, for resolving the implementation files below.  Overridable
# per call so tests can hash a scratch tree instead of this one.
REPO_ROOT = Path(__file__).resolve().parents[2]


# Shared implementation files whose content belongs to a step's identity,
# keyed by step directory basename and repo-relative.  A file is listed
# here when a change to it can change the step's cached output: the step
# executes it as measurement code (a subprocess script or an imported
# measurement function).  Plumbing a step merely uses - the layout, a
# stdout claim, project declarations that never travel in a preflight
# cache - is NOT listed; it lives in EXEMPT_IMPORTS below.
#
# Keyed by directory, not DAG node id: node ids are the DAG's to rename,
# the directory is what is hashed.  A step with no row is hashed on its
# own directory alone (step 1.02's computation is inline in step.py and
# its only subprocess is the external ffprobe binary, which is not repo
# code and has no content to hash).
#
STEP_IMPLEMENTATION_DEPS = {
    "step_1_06_object_segmentation": (
        # Shared Resolve operation and evidence readers reached by its
        # dependency closure; changes alter cached segmentation inputs.
        "library/tools/alignment_quality.py",
        "library/tools/axi_qualification_dimensions.py",
        "library/tools/axi_qualification_state.py",
        "library/tools/camera_motion.py",
        "library/tools/capability_guidance.py",
        "library/tools/edit_conflict.py",
        "library/tools/edit_critical_events.py",
        "library/tools/editorial_policy.py",
        "library/tools/observation_provenance.py",
        "library/tools/operation_map.py",
        "library/tools/pause_class.py",
        "library/tools/preservation_invariant.py",
        "library/tools/render_perception.py",
        # The report provenance every live measurement carries: it is
        # reached by this step's dependency closure, so a fix to it is a
        # fix to the step's cached inputs.
        "library/tools/report_provenance.py",
        "library/tools/source_quality.py",
        "library/tools/speech_take.py",
        "library/tools/temporal_events.py",
        "library/tools/temporal_query.py",
        "library/tools/artifact_lifecycle.py",
        "library/tools/artifact_policy.py",
        "library/tools/axi_transaction.py",
        "library/tools/candidate_treatments.py",
        "library/tools/cache_lifecycle.py",
        "library/tools/edit_recovery.py",
        "library/tools/editorial_identity.py",
        "library/tools/edit_execution_adapters.py",
        "library/tools/edit_state_preservation.py",
        "library/tools/git_guard.py",
        "library/tools/execution/deliver_audio_mix.py",
        "library/tools/execution/remove_proof.py",
        "library/tools/proof_cleanup.py",
        "library/tools/resolve_axi_sdk/__init__.py",
        "library/tools/retention.py",
        "library/tools/recovery_instruction.py",
        "library/tools/reel_execution_contract.py",
        "library/tools/safe_zone_guide.py",
        "library/tools/sound_class.py",
        "library/tools/target_resolution.py",
        "library/tools/timeline_state_reconcile.py",
        "library/tools/treatment_rulings.py",
        "library/tools/transaction_receipt.py",
        "library/tools/operation_acceptance.py",
        # VC-12/VC-07/VC-08 (PRs 2714/2721/2723): the shared edit-request
        # and transaction orchestration closure reached through
        # resolve_axi_core and edit_patch, so a fix here changes what
        # cached preflight rows resolve through.
        "library/tools/transaction_orchestration.py",
        "library/tools/edit_request.py",
        "library/tools/edit_preparation.py",
        "library/tools/timeline_addressing.py",
        # The legacy step adapter resolves to the capability implementation.
        "library/capabilities/objects/segment.py",
        # The temporal-index step is part of the cached dependency closure.
        "library/tools/motion_evidence.py",
        # VI-09 is part of the same cached preflight dependency closure.
        "library/tools/scene_cinematography.py",
        # The executed measurement: the SAM 2 tracker the step segments
        # each triggered clip with.
        "library/tools/analysis/object_segmentation.py",
        # The catalog dependency closure includes the shared retrieval encoder.
        "library/tools/analysis/mlx_minilm.py",
        # Version-store closure verifies retained timeline DRT contents.
        "library/tools/resolve_archive.py",
        # The segmenter imports the resident SAM lifecycle shared with runs.
        "library/tools/model_lifecycle.py",
        # Imported by step.py: the content identity a stored matte is
        # served from, so a fix here changes which mattes are reused.
        "library/tools/footage_identity.py",
        # Imported by footage_identity: the declaration parsing chain
        # it reads through (same chain as the scan and catalog rows).
        "library/schemas/project_config.py",
        "library/tools/delivery_spec.py",
        "library/tools/portable_paths.py",
        "library/tools/stable_json.py",
        "library/tools/project_format.py",
        "library/tools/overlay_mode.py",
        "library/tools/graphics_renderer.py",
        "library/tools/safe_zone_policy.py",
        "library/tools/platform_safe_zones.py",
        # Lane B: the scan writes Asset/Stream/Clock through the Project
        # Model revision API (footage_model_link.link_scan), so a fix to
        # the link - or to the migration and stores behind it - changes
        # what the scan commits. Scan is seconds, so the transcript
        # chain this drags in re-scans cheaply rather than risking a
        # stale model write. Same rows on the 1.06 side below.
        "library/tools/footage_model_link.py",
        "library/tools/project_model.py",
        # Imported by project_model: the typed certainty/confidence
        # and coverage validation every model write is checked
        # against, so a fix here changes what cached rows validate.
        "library/tools/project_model_contract.py",
        "library/tools/project_model_evidence.py",
        # Imported by Project Model migration for derivation and typed scores.
        "library/tools/evidence_lifecycle.py",
        # Deterministic hand/object action candidates are materialized by
        # Project Model migration and must invalidate segmentation rows.
        "library/tools/temporal_action_candidates.py",
        # Lane 3: migration materializes track re-identification and
        # temporal/physical relation candidates into the model.
        "library/tools/identity_reassociation.py",
        "library/tools/relation_classes.py",
        "library/tools/analysis/open_vocabulary_detect.py",
        "library/tools/analyzer_providers.py",
        "library/tools/query_directed_acquisition.py",
        "library/tools/evidence_query.py",
        "library/tools/ocr_project_model.py",
        "library/tools/evidence_contract.py",
        "library/tools/cross_modal_grounding.py",
        # Imported by project_model_evidence for native visual measurements;
        # changes to these readers alter the evidence and must invalidate it.
        "library/tools/attention_saliency.py",
        "library/tools/expression_classifier.py",
        "library/tools/footage_analysis.py",
        "library/tools/person_measurements.py",
        "library/tools/project_model_store.py",
        "library/tools/source_memory.py",
        "library/tools/speech_measurements.py",
        # Imported by source_memory: the transcript lanes a fresh M1
        # resolves through.
        "library/tools/hybrid_transcription.py",
        "library/tools/protocol_conformance.py",
        "library/tools/shared_environment.py",
        "library/tools/source_primitives.py",
        "library/tools/transcript_measurement.py",
        # Imported by the transcript chain above.
        "library/tools/heard_speech.py",
        "library/tools/mfa_align.py",
        "library/tools/subject_framing.py",
        "library/tools/timeline_transcript.py",
        "library/tools/overlapped_speech.py",
        "library/tools/word_boundaries.py",
        "library/tools/vision_measure.py",
        "library/tools/caption_reading.py",
        "library/tools/single_track_diarization.py",
        "library/tools/model_registry.py",
        "library/tools/evidence_license.py",
        # Diarization's default implementation is selected and its model
        # output reuse terms are checked through these registry readers.
        "library/tools/transcript_confidence.py",
        "library/tools/transcript_corrections.py",
        "library/tools/transcript_fit.py",
        "library/tools/learned_context.py",
        'library/tools/analysis/footage_frames.py',
        'library/tools/analysis/footage_query.py',
        'library/tools/analysis/ocr_combined_query.py',
        'library/tools/concurrency_routing.py',
        'library/tools/conversation_clock.py',
        'library/tools/depth.py',
        # The adopted DA3 backend depth.py selects behind the M3g protocol.
        'library/tools/depth_da3.py',
        'library/tools/event_spans.py',
        'library/tools/footage_intelligence.py',
        'library/tools/analysis_state.py',
        # Analysis composition resolves from capability-owned declarations,
        # so a fix to the contracts changes the order preflight runs in.
        'library/tools/capability_contracts.py',
        'library/tools/heavy_work_lock.py',
        'library/tools/person_entity.py',
        'library/tools/person_regions.py',
        'library/tools/project_registry.py',
        'library/tools/region_stats.py',
        'library/tools/relationships.py',
        'library/tools/world_model.py',
        'library/tools/analysis/footage_segments.py',
        'library/tools/analysis/picture_quality.py',
        'library/tools/analysis/unified_anchors.py',
        'library/tools/brief_attachment.py',
        'library/tools/marker_capture.py',
        'library/tools/mask_seed.py',
        'library/tools/progress.py',
        'library/tools/project_model_masks.py',
        'library/tools/provenance.py',
        'library/tools/region_query.py',
        'library/tools/resource_scheduler.py',
        'library/tools/run_control.py',
        'library/tools/series_registry.py',
        'library/tools/shot_colour.py',
        'library/tools/span_verification.py',
        'library/tools/step_ledger.py',
        'library/tools/brief_reference.py',
        'library/tools/edit_algebra.py',
        'library/tools/reel_read.py',
        'library/tools/region.py',
        'library/tools/resolve_lock.py',
        'library/tools/resolved/client.py',
        'library/tools/run_restart.py',
        'library/tools/scope.py',
        'library/tools/step_exporter.py',
        'library/tools/vision_model.py',
        'library/tools/cdl_readback.py',
        'library/tools/comp_media_window.py',
        'library/tools/computer_use/client.py',
        'library/tools/computer_use/scripts.py',
        'library/tools/creative_direction.py',
        'library/tools/edit_backend.py',
        'library/tools/event_log.py',
        'library/tools/fusion/engine.py',
        'library/tools/fusion/nodes.py',
        'library/tools/magic_mask.py',
        'library/tools/marker_feedback.py',
        'library/tools/native_refresh.py',
        'library/tools/reel_replace_guard.py',
        'library/tools/resolve_swapback.py',
        'library/tools/spine_contract.py',
        'library/tools/tight_box.py',
        'library/tools/timeline_decisions.py',
        'library/tools/timeline_read.py',
        'library/tools/timeline_shadow.py',
        'library/tools/transform_write_log.py',
        'library/tools/trim_qualification.py',
        'library/steps/step_4_01_plan_subtitles/step.py',
        'library/tools/brand_palette.py',
        'library/tools/computer_use/actions.py',
        'library/tools/computer_use/observe.py',
        'library/tools/computer_use/take.py',
        'library/tools/computer_use/templates.py',
        'library/tools/edit_patch.py',
        'library/tools/project_model_editorial.py',
        'library/tools/edit_point_planner.py',
        'library/tools/energy_reading.py',
        'library/tools/feedback_ledger.py',
        'library/tools/fframes_render.py',
        'library/tools/marker_payload.py',
        'library/tools/music_behavior.py',
        'library/tools/native_part_lock.py',
        'library/tools/overlay_carriage.py',
        'library/tools/plan_provenance.py',
        'library/tools/project_file_lock.py',
        'library/tools/qa/subtitle_qa.py',
        'library/tools/qualification_project.py',
        'library/tools/reel_address.py',
        'library/tools/reel_disabled_clip_carry.py',
        'library/tools/ren_native_cache.py',
        'library/tools/ren_native_source.py',
        'library/tools/render_fonts.py',
        'library/tools/resolve_deadline.py',
        'library/tools/resolve_locale.py',
        'library/tools/resolve_switch_guard.py',
        'library/tools/resolve_transform.py',
        'library/tools/continuity_feasibility.py',
        'library/tools/resolved/cancellation.py',
        'library/tools/resolved/jobs.py',
        'library/tools/resolved/scheduler.py',
        'library/tools/resolved/store.py',
        'library/tools/shadow_watcher.py',
        'library/tools/subtitle_segment_id.py',
        'library/tools/undo_journal.py',
        'library/tools/versions/reel_versions.py',
        'library/tools/versions/rounds.py',
        'library/graphics_ir/fonts.py',
        'library/graphics_ir/lowering.py',
        'library/graphics_ir/serialization.py',
        'library/schemas/brand_template.py',
        'library/tools/captain_edits.py',
        'library/tools/cascade_graph.py',
        'library/tools/composed_edit.py',
        'library/tools/decided_value.py',
        'library/tools/fabric_permissions.py',
        'library/tools/hyperframes_render.py',
        'library/tools/manifest_validator.py',
        'library/tools/marker_carry.py',
        'library/tools/marker_resolution.py',
        'library/tools/patch_algebra.py',
        'library/tools/project_asset.py',
        'library/tools/project_model_qualification.py',
        'library/tools/qualification_workflow.py',
        'library/tools/reel_build.py',
        'library/tools/reel_append_placement.py',
        'library/tools/resolve_temporal.py',
        'library/tools/reel_conformance_verifier.py',
        'library/tools/reel_edit_history.py',
        'library/tools/reel_incremental.py',
        'library/tools/reel_proposal.py',
        'library/tools/reel_retirement.py',
        'library/tools/reel_semantic_visual.py',
        'library/tools/reel_state.py',
        'library/tools/reel_touchup.py',
        'library/tools/render_cache.py',
        'library/tools/render_qa.py',
        'library/tools/resolve_axi_core_commands.py',
        'library/tools/resolve_axi.py',
        'library/tools/resolve_axi_core.py',
        # resolve-axi's typed scratch-project operations are imported by
        # its command dispatcher and therefore belong to this closure.
        'library/tools/resolve_scratch_projects.py',
        'library/tools/resolve_bin_layout.py',
        'library/tools/resolved/drt_export_probe.py',
        'library/tools/resolved/drt_recovery_probe.py',
        'library/tools/resolved/marker_loss_probe.py',
        'library/tools/resolved/reel_ending_copy.py',
        'library/tools/resolved/reel_promotion.py',
        'library/tools/resolved/reel_promotion_fixture.py',
        'library/tools/resolved/ui_transaction.py',
        'library/tools/safe_area.py',
        'library/tools/still_placement.py',
        'library/tools/still_vision.py',
        'library/tools/subtitle_coverage.py',
        'library/tools/subtitle_splice.py',
        'library/tools/timeline_diff.py',
        'library/tools/transcript_duration_anomaly.py',
        'library/tools/versions/store.py',
        'library/skills/verify_render/skill.py',
        'library/steps/step_4_05_render_subtitles/generate_remotion_props.py',
        'library/tools/awaiting_model_answers.py',
        'library/tools/bookends.py',
        'library/tools/build_sweep.py',
        'library/tools/camera_sync.py',
        'library/tools/caption_asset_gc.py',
        'library/tools/caption_band.py',
        'library/tools/caption_swap.py',
        'library/tools/caption_timing.py',
        'library/tools/closer_fit.py',
        'library/tools/comparison_retirement.py',
        'library/tools/declaration_keys.py',
        'library/tools/dirty_regions.py',
        'library/tools/display_respell.py',
        'library/tools/do_not_draw.py',
        'library/tools/draw_gain_probe.py',
        'library/tools/drift_check.py',
        'library/tools/edit_ledger.py',
        'library/tools/editor_edit_carry.py',
        'library/tools/execution/fusion_tracks.py',
        'library/tools/execution/organise_media_pool.py',
        'library/tools/execution/resolve_render.py',
        'library/tools/explainer_plan.py',
        'library/tools/external_inputs.py',
        'library/tools/feedback_outcomes.py',
        'library/tools/feedback_patch_apply.py',
        'library/tools/filmcraft_patches.py',
        'library/tools/frame_utils.py',
        'library/tools/full_frame_element.py',
        'library/tools/fusion/comp_builder.py',
        'library/tools/fusion/effects.py',
        'library/tools/fusion/parser.py',
        'library/tools/intellisearch_collect.py',
        'library/tools/layer_coherence.py',
        'library/tools/llm_handshake.py',
        'library/tools/manual_edit_intent.py',
        'library/tools/marker_gate.py',
        'library/tools/marker_routing.py',
        'library/tools/master_loudness.py',
        'library/tools/mg_tight_box.py',
        'library/tools/motion_graphics_plan.py',
        'library/tools/motion_graphics_vocabulary.py',
        'library/tools/music_measurement.py',
        'library/tools/music_over_speech.py',
        'library/tools/native_effects_composite.py',
        'library/tools/native_mcp.py',
        'library/tools/native_ops_apply.py',
        'library/tools/otio_mix.py',
        'library/tools/output_contract.py',
        'library/tools/overlay_draw_intent.py',
        'library/tools/overlay_intent.py',
        'library/tools/overlay_placement.py',
        'library/tools/overlay_verify.py',
        'library/tools/plan_creative_quality.py',
        'library/tools/plan_splice.py',
        'library/tools/pool_stream_meta.py',
        'library/tools/project_model_effectcraft.py',
        'library/tools/project_model_effectcraft_moves.py',
        'library/tools/proof_scope.py',
        'library/tools/reel_angle_plan.py',
        'library/tools/reel_clock.py',
        'library/tools/reel_cta_treatment.py',
        'library/tools/reel_divergence.py',
        'library/tools/reel_ending.py',
        'library/tools/reel_exchange.py',
        'library/tools/reel_framing.py',
        'library/tools/reel_fusion_comps.py',
        'library/tools/reel_ledger.py',
        'library/tools/reel_look.py',
        'library/tools/reel_opening.py',
        'library/tools/reel_otio_placement.py',
        'library/tools/reel_phase_log.py',
        'library/tools/reel_placed_assets.py',
        'library/tools/reel_post_header.py',
        'library/tools/reel_prebuild_census.py',
        'library/tools/reel_quality_bar.py',
        'library/tools/reel_rebuild_need.py',
        'library/tools/reel_signoff.py',
        'library/tools/reel_spine.py',
        'library/tools/reel_thesis.py',
        'library/tools/reference_style.py',
        'library/tools/remotion_brand_linker.py',
        'library/tools/render_watch.py',
        'library/tools/resolve_api_capabilities.py',
        'library/tools/resolve_organization.py',
        'library/tools/resolve_script_store.py',
        'library/tools/retake_scan.py',
        'library/tools/semantic_visual.py',
        'library/tools/source_transcript.py',
        'library/tools/speaker_identity.py',
        'library/tools/speech_loudness.py',
        'library/tools/staging_holds.py',
        'library/tools/still_overlays.py',
        'library/tools/subject_grade.py',
        'library/tools/tail_extend_authorization.py',
        'library/tools/taste_profile.py',
        'library/tools/timeline_conformance.py',
        'library/tools/timeline_layout.py',
        'library/tools/timeline_serializer.py',
        'library/tools/toon_serializer.py',
        'library/tools/transition_overlay.py',
        'library/tools/transition_vocabulary.py',
        'library/tools/tv_frame.py',
        'library/tools/tv_power.py',
        'library/tools/undetermined.py',
        'library/tools/versions/checkpoints.py',
        'library/tools/versions/variants.py',
        'library/tools/video_prefs.py',
        'library/capabilities/temporal/index.py',
        'library/tools/motion_evidence.py',
        'library/tools/analysis/music_pipeline.py',
        'library/tools/analysis/source_separation.py',
        'library/tools/analysis/ocr_extractor.py',
        'library/tools/beat_grid.py',
        'library/tools/brand_library.py',
        'library/tools/broll_correspondence.py',
        'library/tools/caption_correction.py',
        'library/tools/color_page_grade.py',
        'library/tools/creative_tasks.py',
        'library/tools/dialogue_cleanup.py',
        'library/tools/direction_contradiction.py',
        'library/tools/edit_depth.py',
        'library/tools/edit_operations.py',
        'library/tools/edit_spec.py',
        'library/tools/execution/prune_orphans.py',
        'library/tools/execution/retire_empty_bins.py',
        'library/tools/file_measurements.py',
        'library/tools/fusion/played_window.py',
        'library/tools/input_contract.py',
        'library/tools/intent_model.py',
        'library/tools/jl_cut.py',
        'library/tools/journal_naming.py',
        'library/tools/mix_intent.py',
        'library/tools/music_section.py',
        'library/tools/native_ops.py',
        'library/tools/otio_compile.py',
        'library/tools/overlay_keep_clear.py',
        'library/tools/person_matte.py',
        'library/tools/pipeline_skills.py',
        'library/tools/placed_assets.py',
        'library/tools/processes.py',
        'library/tools/project_model_effectcraft_titles.py',
        'library/tools/reel_hearing.py',
        'library/tools/reel_layer_links.py',
        'library/tools/remotion_batch.py',
        'library/tools/render_capability_index.py',
        'library/tools/requirements.py',
        'library/tools/review_panel.py',
        'library/tools/row_shift.py',
        'library/tools/run_scope.py',
        'library/tools/series_look.py',
        'library/tools/still_content.py',
        'library/tools/timed_text_overlay.py',
        'library/tools/timeline_duration.py',
        'library/tools/transform_drift.py',
        'library/tools/uncarried_notes.py',
        'library/tools/versions/edit_variants.py',
        'library/tools/window_frames.py',
        'library/processes/edit_video/run_pipeline.py',
        'library/tools/capability_plan.py',
        'library/tools/project_model_narrative.py',
        'library/capabilities/temporal/media_decode.py',
        'library/tools/analysis/sound_event_pipeline.py',
        'library/tools/audio_chain.py',
        'library/tools/audio_classification.py',
        'library/tools/audio_effects.py',
        'library/tools/brand_motion.py',
        'library/tools/context_projector.py',
        'library/tools/context_views.py',
        'library/tools/correction_loop.py',
        'library/tools/craft_role.py',
        'library/tools/creative_floors.py',
        'library/tools/cutaway_window.py',
        'library/tools/edit_graph.py',
        'library/tools/hearing_settings.py',
        'library/tools/hooks.py',
        'library/tools/model_task.py',
        # The cost of a model answer, reached through model_task: a
        # fix to the costing changes what a step's own identity is.
        'library/tools/model_cost.py',
        # The count a model answer is priced in, reached through
        # model_task: the tokenizer that produced the figure is part
        # of what the step measured.
        'library/tools/token_measure.py',
        # The delivery and overlay code the catalog's executor
        # closure reaches: a fix to any of them changes what the
        # step's cached rows resolve through.
        'library/tools/delivery_identity.py',
        'library/tools/overlay_media.py',
        'library/tools/reel_export.py',
        # The caption rewrite the catalog's closure reaches: a fix to
        # how a caption is redrawn changes what the step measured.
        'library/tools/caption_rewrite.py',
        'library/tools/touchup_phases.py',
        # The broker session the catalog's executor closure holds
        # across a multi-call Resolve operation: a fix to it changes
        # what the step's cached rows resolve through.
        'library/tools/resolve_session.py',
        'library/tools/music_bed.py',
        'library/tools/orphan_removal.py',
        'library/tools/qa_findings.py',
        'library/tools/regional_motion.py',
        'library/tools/room_tone.py',
        'library/tools/semantic_index.py',
        'library/tools/semantic_profile_stream.py',
        'library/tools/sfx_library.py',
        'library/tools/sub_block_anchor.py',
        'library/dashboard/review_channel.py',
        'library/tools/alignment_findings.py',
        'library/tools/breakpoints.py',
        'library/tools/brief_snapshot.py',
        'library/tools/briefing_interview.py',
        'library/tools/camera_stability.py',
        'library/tools/completion_notice.py',
        'library/tools/cut_verdicts.py',
        'library/tools/display_drift.py',
        'library/tools/edit_input_digest.py',
        'library/tools/editorial_types.py',
        'library/tools/empty_table_guard.py',
        'library/tools/intent_receipt.py',
        'library/tools/music_sections.py',
        'library/tools/pipeline_logger.py',
        'library/tools/planning_reuse.py',
        'library/tools/project_context.py',
        'library/tools/prosody_profile.py',
        'library/tools/qa_feedback_loop.py',
        'library/tools/render_review.py',
        'library/tools/review_gate.py',
        'library/tools/run_profile.py',
        'library/tools/run_traceback.py',
        'library/tools/sound_events.py',
        'library/tools/template_loader.py',
        'library/tools/versions/runs.py',
        'library/tools/vision_schema_adapter.py',
        'library/tools/nothing_to_decide.py',
        'library/tools/post_bridge_retry.py',
        'library/tools/scoped_intent.py',
        'library/tools/second_pass.py',
        'library/tools/segment_coverage.py',
        'library/tools/timeline_qa.py',
        'library/tools/visual_qa_prompts.py',
        'library/tools/visual_qa_router.py',
        'library/tools/perceptual_qa.py',
        'library/tools/qa_fidelity.py',
        'library/tools/reference_collection.py',
        'library/tools/segment_renderer.py',
        'library/tools/video_segment_analyzer.py',
        'library/tools/gate_stills.py',
        'library/tools/qa/asset_qa.py',
        'library/tools/qa/timeline_sync_qa.py',
        'library/tools/treatment_verify.py',
        'library/tools/fusion/transition_frames.py',
        # Imported by the declared input/output contract surveys: the
        # session-local AST parse cache this audit split reads through,
        # so a fix here changes what the declared surveys parse.
        'library/tools/ast_cache.py',
        # Version-store closure verifies retained timeline DRT contents.
        'library/tools/resolve_archive.py',
        'library/tools/accepted_edit_replay.py',
        'library/tools/creative_revision.py',
        'library/tools/direct_transaction.py',
        'library/tools/hold_window_repair.py',
        'library/tools/reel_component_manifest.py',
        'library/tools/structural_patch.py',
        'library/tools/versions/reproducibility.py',
        # Imported by the declared temporal-index step: the FI-14
        # camera/subject evidence reader, so a fix here changes what
        # the cached rows resolve through it.
        'library/tools/motion_evidence.py',
        # Transitive closure: edit_algebra imports axi_qualification and
        # retention imports storage_lifecycle, so a fix to either shared
        # module changes what the cached segmentation rows resolve through.
        'library/tools/axi_qualification.py',
        'library/tools/storage_lifecycle.py',
        "library/tools/editorial_decisions.py",
        "library/tools/model_stamps.py",
        "library/tools/assignment_lifecycle.py",
        "library/tools/editorial_intent.py",
        "library/tools/assignment_spine.py",
        "library/tools/assignment_actions.py",
        "library/tools/assignment_task.py",
        "library/tools/assignment_plan.py",
        # The shared readers the declared closure reaches in one hop: the
        # approved-decision evidence (cache_lifecycle), the DRT archive
        # reader (resolve_scratch_projects), the timeline state report
        # (timeline_shadow) and the cross-project graph (source_memory).
        # A fix to any of them changes what the cached rows say, so it
        # must invalidate the cache.
        "library/tools/decision_evidence.py",
        "library/tools/drt_reader.py",
        "library/tools/edit_state_report.py",
        "library/tools/project_graph/cross_project.py",
        # The reel builder records the acceptance of a delivered generation
        # through this module, so a fix to it changes what the cached
        # rows of this step resolve through (the same chain as the
        # catalog row).
        "library/tools/reel_acceptance.py",
        # The explainer and face-track renderers, the frame-exact
        # measurement, the occurrence identity, the plan feasibility,
        # the project reference and the retrieval requirements: the
        # shared code these steps' cached rows are computed through.
        "library/tools/explainer_render.py",
        "library/tools/face_tracks.py",
        "library/tools/frame_exact.py",
        "library/tools/occurrence_identity.py",
        "library/tools/plan_feasibility.py",
        "library/tools/project_ref.py",
        "library/tools/retrieval/requirements.py",
        # The capability registry the catalog reads its operations from,
        # the write settle the executor waits on, the broker's own path
        # helper and the source clocks the measurements are read through:
        # a fix in any of them changes what these cached rows resolve to.
        "library/tools/capability_coverage.py",
        "library/tools/resolve_write_settle.py",
        "library/tools/resolved/broker_paths.py",
        "library/tools/source_clocks.py",
        # The shared code this step's cached rows are computed
        # through, reached by the closure above (the map's rule:
        # every file the closure reaches is named here).
        "library/tools/evidence_graph.py",
        "library/tools/frame_rate.py",
        "library/tools/mutation_verification.py",
    ),
    "step_1_01_scan_project": (
        "library/tools/alignment_quality.py",
        "library/tools/axi_qualification_dimensions.py",
        "library/tools/axi_qualification_state.py",
        "library/tools/camera_motion.py",
        "library/tools/capability_guidance.py",
        "library/tools/edit_conflict.py",
        "library/tools/edit_critical_events.py",
        "library/tools/editorial_policy.py",
        "library/tools/observation_provenance.py",
        "library/tools/operation_map.py",
        "library/tools/pause_class.py",
        "library/tools/preservation_invariant.py",
        "library/tools/render_perception.py",
        # The report provenance every live measurement carries: it is
        # reached by this step's dependency closure, so a fix to it is a
        # fix to the step's cached inputs.
        "library/tools/report_provenance.py",
        "library/tools/source_quality.py",
        "library/tools/speech_take.py",
        "library/tools/temporal_events.py",
        "library/tools/temporal_query.py",
        "library/tools/safe_zone_guide.py",
        # Scan's legacy code closure traverses the shared cleanup/execution
        # modules through catalog support; keep its cache key complete.
        "library/tools/artifact_lifecycle.py",
        "library/tools/cache_lifecycle.py",
        "library/tools/edit_execution_adapters.py",
        "library/tools/edit_state_preservation.py",
        "library/tools/git_guard.py",
        "library/tools/execution/deliver_audio_mix.py",
        "library/tools/execution/remove_proof.py",
        "library/tools/proof_cleanup.py",
        "library/tools/resolve_axi_sdk/__init__.py",
        "library/tools/retention.py",
        "library/tools/transaction_receipt.py",
        "library/tools/operation_acceptance.py",
        # VC-12/VC-07/VC-08 (PRs 2714/2721/2723): the shared edit-request
        # and transaction orchestration closure reached through
        # resolve_axi_core and edit_patch, so a fix here changes what
        # cached preflight rows resolve through.
        "library/tools/transaction_orchestration.py",
        "library/tools/edit_request.py",
        "library/tools/edit_preparation.py",
        "library/tools/timeline_addressing.py",
        # The step adapter delegates the measured scan to its capability-owned implementation.
        "library/capabilities/footage/scan.py",
        # The scan's dependency closure now includes the shared retrieval encoder.
        "library/tools/analysis/mlx_minilm.py",
        # VI-09 extends the cached temporal-index dependency represented here.
        "library/tools/scene_cinematography.py",
        # The temporal-index step is part of the cached dependency closure.
        "library/tools/motion_evidence.py",
        # enumerate_footage produces raw_footage_files, the step's output.
        "library/tools/footage_identity.py",
        # Its declaration parsing: which directory counts as the footage
        # root decides what the scan sees.  Scan is seconds, so a rare
        # schema edit re-scanning is the cheap, correct trade.
        "library/schemas/project_config.py",
        # Imported by project_config: the delivery-platform registry
        # pipeline.delivery_platform validates against, so a fix here
        # changes what the scan accepts.
        "library/tools/delivery_spec.py",
        # Imported by project_config: the `$HOME` / `$PROJECT` token
        # expansion path declarations are read through, so a fix here
        # changes what the scan accepts.
        "library/tools/portable_paths.py",
        # Imported by portable_paths: the canonical spelling a rebind
        # rewrite is written back in.
        "library/tools/stable_json.py",
        # Imported by project_config: the project_format_version key
        # and the supported format range the open refuses outside of,
        # so a fix here changes what the scan accepts.
        "library/tools/project_format.py",
        # Imported by project_config: the subtitle_overlay_geometry /
        # subtitle_overlay_container vocabularies the scan's declaration
        # parsing validates against, so a fix here changes what the scan
        # accepts.
        "library/tools/overlay_mode.py",
        # Imported by project_config: the graphics_renderer vocabulary
        # the scan's declaration parsing validates against (same chain
        # as the overlay_mode row above).
        "library/tools/graphics_renderer.py",
        # Imported by project_config: the pipeline.safe_zones policy
        # the declaration parsing validates against (same chain).
        "library/tools/safe_zone_policy.py",
        # Imported by safe_zone_policy: the platform zones a policy
        # names and is checked against.
        "library/tools/platform_safe_zones.py",
        # Lane B: the scan writes Asset/Stream/Clock through the Project
        # Model revision API (footage_model_link.link_scan), so a fix to
        # the link - or to the migration and stores behind it - changes
        # what the scan commits. Scan is seconds, so the transcript
        # chain this drags in re-scans cheaply rather than risking a
        # stale model write. Same rows on the 1.06 side above.
        "library/tools/footage_model_link.py",
        "library/tools/project_model.py",
        # Imported by project_model: the typed certainty/confidence
        # and coverage validation every model write is checked
        # against, so a fix here changes what cached rows validate.
        "library/tools/project_model_contract.py",
        "library/tools/project_model_evidence.py",
        # Imported by Project Model migration for derivation and typed scores.
        "library/tools/evidence_lifecycle.py",
        # Temporal action candidates are part of the scan's Project Model
        # migration and invalidate scan rows when their rules change.
        "library/tools/temporal_action_candidates.py",
        # Lane 3 migration adds identity and physical relation records.
        "library/tools/identity_reassociation.py",
        "library/tools/relation_classes.py",
        "library/tools/analysis/open_vocabulary_detect.py",
        "library/tools/analyzer_providers.py",
        "library/tools/query_directed_acquisition.py",
        "library/tools/evidence_query.py",
        "library/tools/ocr_project_model.py",
        "library/tools/evidence_contract.py",
        "library/tools/cross_modal_grounding.py",
        # Imported by project_model_evidence for native visual measurements;
        # changes to these readers alter the evidence and must invalidate it.
        "library/tools/attention_saliency.py",
        "library/tools/expression_classifier.py",
        "library/tools/footage_analysis.py",
        "library/tools/person_measurements.py",
        "library/tools/project_model_store.py",
        "library/tools/source_memory.py",
        "library/tools/speech_measurements.py",
        # Imported by source_memory: the transcript lanes a fresh M1
        # resolves through.
        "library/tools/hybrid_transcription.py",
        "library/tools/protocol_conformance.py",
        "library/tools/shared_environment.py",
        "library/tools/source_primitives.py",
        "library/tools/transcript_measurement.py",
        # Imported by the transcript chain above.
        "library/tools/heard_speech.py",
        "library/tools/mfa_align.py",
        "library/tools/subject_framing.py",
        "library/tools/timeline_transcript.py",
        "library/tools/overlapped_speech.py",
        "library/tools/word_boundaries.py",
        "library/tools/vision_measure.py",
        "library/tools/caption_reading.py",
        "library/tools/single_track_diarization.py",
        "library/tools/model_registry.py",
        "library/tools/evidence_license.py",
        # The diarization default and output reuse terms are resolved
        # through the model and evidence-license registries.
        "library/tools/transcript_confidence.py",
        "library/tools/transcript_corrections.py",
        "library/tools/transcript_fit.py",
        "library/tools/learned_context.py",
        'library/tools/analysis/footage_frames.py',
        'library/tools/analysis/footage_query.py',
        'library/tools/analysis/ocr_combined_query.py',
        'library/tools/concurrency_routing.py',
        'library/tools/conversation_clock.py',
        'library/tools/depth.py',
        # The adopted DA3 backend depth.py selects behind the M3g protocol.
        'library/tools/depth_da3.py',
        'library/tools/event_spans.py',
        'library/tools/footage_intelligence.py',
        'library/tools/analysis_state.py',
        # Analysis composition resolves from capability-owned declarations,
        # so a fix to the contracts changes the order preflight runs in.
        'library/tools/capability_contracts.py',
        'library/tools/heavy_work_lock.py',
        'library/tools/person_entity.py',
        'library/tools/person_regions.py',
        'library/tools/project_registry.py',
        'library/tools/region_stats.py',
        'library/tools/relationships.py',
        'library/tools/world_model.py',
        'library/tools/analysis/footage_segments.py',
        'library/tools/analysis/picture_quality.py',
        'library/tools/analysis/unified_anchors.py',
        'library/tools/brief_attachment.py',
        'library/tools/marker_capture.py',
        'library/tools/mask_seed.py',
        'library/tools/progress.py',
        'library/tools/project_model_masks.py',
        'library/tools/provenance.py',
        'library/tools/region_query.py',
        'library/tools/resource_scheduler.py',
        'library/tools/run_control.py',
        'library/tools/series_registry.py',
        'library/tools/shot_colour.py',
        'library/tools/span_verification.py',
        'library/tools/step_ledger.py',
        'library/tools/analysis/object_segmentation.py',
        # Object segmentation imports the resident SAM model lifecycle.
        'library/tools/model_lifecycle.py',
        'library/tools/brief_reference.py',
        'library/tools/edit_algebra.py',
        'library/tools/reel_read.py',
        'library/tools/region.py',
        'library/tools/resolve_lock.py',
        'library/tools/resolved/client.py',
        'library/tools/run_restart.py',
        'library/tools/scope.py',
        'library/tools/step_exporter.py',
        'library/tools/vision_model.py',
        'library/tools/cdl_readback.py',
        'library/tools/comp_media_window.py',
        'library/tools/computer_use/client.py',
        'library/tools/computer_use/scripts.py',
        'library/tools/creative_direction.py',
        'library/tools/edit_backend.py',
        'library/tools/event_log.py',
        'library/tools/fusion/engine.py',
        'library/tools/fusion/nodes.py',
        'library/tools/magic_mask.py',
        'library/tools/marker_feedback.py',
        'library/tools/native_refresh.py',
        'library/tools/reel_replace_guard.py',
        'library/tools/resolve_swapback.py',
        'library/tools/spine_contract.py',
        'library/tools/tight_box.py',
        'library/tools/timeline_decisions.py',
        'library/tools/timeline_read.py',
        'library/tools/timeline_shadow.py',
        'library/tools/transform_write_log.py',
        'library/tools/trim_qualification.py',
        'library/steps/step_4_01_plan_subtitles/step.py',
        'library/tools/brand_palette.py',
        'library/tools/computer_use/actions.py',
        'library/tools/computer_use/observe.py',
        'library/tools/computer_use/take.py',
        'library/tools/computer_use/templates.py',
        'library/tools/edit_patch.py',
        'library/tools/project_model_editorial.py',
        'library/tools/edit_point_planner.py',
        'library/tools/energy_reading.py',
        'library/tools/feedback_ledger.py',
        'library/tools/fframes_render.py',
        'library/tools/marker_payload.py',
        'library/tools/music_behavior.py',
        'library/tools/native_part_lock.py',
        'library/tools/overlay_carriage.py',
        'library/tools/plan_provenance.py',
        'library/tools/project_file_lock.py',
        'library/tools/qa/subtitle_qa.py',
        'library/tools/qualification_project.py',
        'library/tools/reel_address.py',
        'library/tools/reel_disabled_clip_carry.py',
        'library/tools/ren_native_cache.py',
        'library/tools/ren_native_source.py',
        'library/tools/render_fonts.py',
        'library/tools/resolve_deadline.py',
        'library/tools/resolve_locale.py',
        'library/tools/resolve_switch_guard.py',
        'library/tools/resolve_transform.py',
        'library/tools/continuity_feasibility.py',
        'library/tools/resolved/cancellation.py',
        'library/tools/resolved/jobs.py',
        'library/tools/resolved/scheduler.py',
        'library/tools/resolved/store.py',
        'library/tools/shadow_watcher.py',
        'library/tools/subtitle_segment_id.py',
        'library/tools/undo_journal.py',
        'library/tools/versions/reel_versions.py',
        'library/tools/versions/rounds.py',
        'library/graphics_ir/fonts.py',
        'library/graphics_ir/lowering.py',
        'library/graphics_ir/serialization.py',
        'library/schemas/brand_template.py',
        'library/tools/captain_edits.py',
        'library/tools/cascade_graph.py',
        'library/tools/composed_edit.py',
        'library/tools/decided_value.py',
        'library/tools/fabric_permissions.py',
        'library/tools/hyperframes_render.py',
        'library/tools/manifest_validator.py',
        'library/tools/marker_carry.py',
        'library/tools/marker_resolution.py',
        'library/tools/patch_algebra.py',
        'library/tools/project_asset.py',
        'library/tools/project_model_qualification.py',
        'library/tools/qualification_workflow.py',
        'library/tools/reel_build.py',
        'library/tools/reel_append_placement.py',
        'library/tools/resolve_temporal.py',
        'library/tools/reel_conformance_verifier.py',
        'library/tools/reel_edit_history.py',
        'library/tools/reel_incremental.py',
        'library/tools/reel_proposal.py',
        'library/tools/reel_retirement.py',
        'library/tools/reel_semantic_visual.py',
        'library/tools/reel_state.py',
        'library/tools/reel_touchup.py',
        'library/tools/render_cache.py',
        'library/tools/render_qa.py',
        'library/tools/resolve_axi_core_commands.py',
        'library/tools/resolve_axi.py',
        'library/tools/resolve_axi_core.py',
        'library/tools/resolve_scratch_projects.py',
        'library/tools/resolve_bin_layout.py',
        'library/tools/resolved/drt_export_probe.py',
        'library/tools/resolved/drt_recovery_probe.py',
        'library/tools/resolved/marker_loss_probe.py',
        'library/tools/resolved/reel_ending_copy.py',
        'library/tools/resolved/reel_promotion.py',
        'library/tools/resolved/reel_promotion_fixture.py',
        'library/tools/resolved/ui_transaction.py',
        'library/tools/safe_area.py',
        'library/tools/still_placement.py',
        'library/tools/still_vision.py',
        'library/tools/subtitle_coverage.py',
        'library/tools/subtitle_splice.py',
        'library/tools/timeline_diff.py',
        'library/tools/transcript_duration_anomaly.py',
        'library/tools/versions/store.py',
        'library/skills/verify_render/skill.py',
        'library/steps/step_4_05_render_subtitles/generate_remotion_props.py',
        'library/tools/awaiting_model_answers.py',
        'library/tools/bookends.py',
        'library/tools/build_sweep.py',
        'library/tools/camera_sync.py',
        'library/tools/caption_asset_gc.py',
        'library/tools/caption_band.py',
        'library/tools/caption_swap.py',
        'library/tools/caption_timing.py',
        'library/tools/closer_fit.py',
        'library/tools/comparison_retirement.py',
        'library/tools/declaration_keys.py',
        'library/tools/dirty_regions.py',
        'library/tools/display_respell.py',
        'library/tools/do_not_draw.py',
        'library/tools/draw_gain_probe.py',
        'library/tools/drift_check.py',
        'library/tools/edit_ledger.py',
        'library/tools/editor_edit_carry.py',
        'library/tools/execution/fusion_tracks.py',
        'library/tools/execution/organise_media_pool.py',
        'library/tools/execution/resolve_render.py',
        'library/tools/explainer_plan.py',
        'library/tools/external_inputs.py',
        'library/tools/feedback_outcomes.py',
        'library/tools/feedback_patch_apply.py',
        'library/tools/filmcraft_patches.py',
        'library/tools/frame_utils.py',
        'library/tools/full_frame_element.py',
        'library/tools/fusion/comp_builder.py',
        'library/tools/fusion/effects.py',
        'library/tools/fusion/parser.py',
        'library/tools/intellisearch_collect.py',
        'library/tools/layer_coherence.py',
        'library/tools/llm_handshake.py',
        'library/tools/manual_edit_intent.py',
        'library/tools/marker_gate.py',
        'library/tools/marker_routing.py',
        'library/tools/master_loudness.py',
        'library/tools/mg_tight_box.py',
        'library/tools/motion_graphics_plan.py',
        'library/tools/motion_graphics_vocabulary.py',
        'library/tools/music_measurement.py',
        'library/tools/music_over_speech.py',
        'library/tools/native_effects_composite.py',
        'library/tools/native_mcp.py',
        'library/tools/native_ops_apply.py',
        'library/tools/otio_mix.py',
        'library/tools/output_contract.py',
        'library/tools/overlay_draw_intent.py',
        'library/tools/overlay_intent.py',
        'library/tools/overlay_placement.py',
        'library/tools/overlay_verify.py',
        'library/tools/plan_creative_quality.py',
        'library/tools/plan_splice.py',
        'library/tools/pool_stream_meta.py',
        'library/tools/project_model_effectcraft.py',
        'library/tools/project_model_effectcraft_moves.py',
        'library/tools/proof_scope.py',
        'library/tools/reel_angle_plan.py',
        'library/tools/reel_clock.py',
        'library/tools/reel_cta_treatment.py',
        'library/tools/reel_divergence.py',
        'library/tools/reel_ending.py',
        'library/tools/reel_exchange.py',
        'library/tools/reel_framing.py',
        'library/tools/reel_fusion_comps.py',
        'library/tools/reel_ledger.py',
        'library/tools/reel_look.py',
        'library/tools/reel_opening.py',
        'library/tools/reel_otio_placement.py',
        'library/tools/reel_phase_log.py',
        'library/tools/reel_placed_assets.py',
        'library/tools/reel_post_header.py',
        'library/tools/reel_prebuild_census.py',
        'library/tools/reel_quality_bar.py',
        'library/tools/reel_rebuild_need.py',
        'library/tools/reel_signoff.py',
        'library/tools/reel_spine.py',
        'library/tools/reel_thesis.py',
        'library/tools/reference_style.py',
        'library/tools/remotion_brand_linker.py',
        'library/tools/render_watch.py',
        'library/tools/resolve_api_capabilities.py',
        'library/tools/resolve_organization.py',
        'library/tools/resolve_script_store.py',
        'library/tools/retake_scan.py',
        'library/tools/semantic_visual.py',
        'library/tools/source_transcript.py',
        'library/tools/speaker_identity.py',
        'library/tools/speech_loudness.py',
        'library/tools/staging_holds.py',
        'library/tools/still_overlays.py',
        'library/tools/subject_grade.py',
        'library/tools/tail_extend_authorization.py',
        'library/tools/taste_profile.py',
        'library/tools/timeline_conformance.py',
        'library/tools/timeline_layout.py',
        'library/tools/timeline_serializer.py',
        'library/tools/toon_serializer.py',
        'library/tools/transition_overlay.py',
        'library/tools/transition_vocabulary.py',
        'library/tools/tv_frame.py',
        'library/tools/tv_power.py',
        'library/tools/undetermined.py',
        'library/tools/versions/checkpoints.py',
        'library/tools/versions/variants.py',
        'library/tools/video_prefs.py',
        'library/capabilities/temporal/index.py',
        'library/tools/motion_evidence.py',
        'library/tools/analysis/music_pipeline.py',
        'library/tools/analysis/source_separation.py',
        'library/tools/analysis/ocr_extractor.py',
        'library/tools/beat_grid.py',
        'library/tools/brand_library.py',
        'library/tools/broll_correspondence.py',
        'library/tools/caption_correction.py',
        'library/tools/color_page_grade.py',
        'library/tools/creative_tasks.py',
        'library/tools/dialogue_cleanup.py',
        'library/tools/direction_contradiction.py',
        'library/tools/edit_depth.py',
        'library/tools/edit_operations.py',
        'library/tools/edit_spec.py',
        'library/tools/execution/prune_orphans.py',
        'library/tools/execution/retire_empty_bins.py',
        'library/tools/file_measurements.py',
        'library/tools/fusion/played_window.py',
        'library/tools/input_contract.py',
        'library/tools/intent_model.py',
        'library/tools/jl_cut.py',
        'library/tools/journal_naming.py',
        'library/tools/mix_intent.py',
        'library/tools/music_section.py',
        'library/tools/native_ops.py',
        'library/tools/otio_compile.py',
        'library/tools/overlay_keep_clear.py',
        'library/tools/person_matte.py',
        'library/tools/pipeline_skills.py',
        'library/tools/placed_assets.py',
        'library/tools/processes.py',
        'library/tools/project_model_effectcraft_titles.py',
        'library/tools/reel_hearing.py',
        'library/tools/reel_layer_links.py',
        'library/tools/remotion_batch.py',
        'library/tools/render_capability_index.py',
        'library/tools/requirements.py',
        'library/tools/review_panel.py',
        'library/tools/row_shift.py',
        'library/tools/run_scope.py',
        'library/tools/series_look.py',
        'library/tools/still_content.py',
        'library/tools/timed_text_overlay.py',
        'library/tools/timeline_duration.py',
        'library/tools/transform_drift.py',
        'library/tools/uncarried_notes.py',
        'library/tools/versions/edit_variants.py',
        'library/tools/window_frames.py',
        'library/processes/edit_video/run_pipeline.py',
        'library/tools/capability_plan.py',
        'library/tools/project_model_narrative.py',
        'library/capabilities/temporal/media_decode.py',
        'library/tools/analysis/sound_event_pipeline.py',
        'library/tools/audio_chain.py',
        'library/tools/audio_classification.py',
        'library/tools/audio_effects.py',
        'library/tools/brand_motion.py',
        'library/tools/context_projector.py',
        'library/tools/context_views.py',
        'library/tools/correction_loop.py',
        'library/tools/craft_role.py',
        'library/tools/creative_floors.py',
        'library/tools/cutaway_window.py',
        'library/tools/edit_graph.py',
        'library/tools/hearing_settings.py',
        'library/tools/hooks.py',
        'library/tools/model_task.py',
        # The cost of a model answer, reached through model_task: a
        # fix to the costing changes what a step's own identity is.
        'library/tools/model_cost.py',
        # The count a model answer is priced in, reached through
        # model_task: the tokenizer that produced the figure is part
        # of what the step measured.
        'library/tools/token_measure.py',
        # The delivery and overlay code the catalog's executor
        # closure reaches: a fix to any of them changes what the
        # step's cached rows resolve through.
        'library/tools/delivery_identity.py',
        'library/tools/overlay_media.py',
        'library/tools/reel_export.py',
        # The caption rewrite the catalog's closure reaches: a fix to
        # how a caption is redrawn changes what the step measured.
        'library/tools/caption_rewrite.py',
        'library/tools/touchup_phases.py',
        # The broker session the catalog's executor closure holds
        # across a multi-call Resolve operation: a fix to it changes
        # what the step's cached rows resolve through.
        'library/tools/resolve_session.py',
        'library/tools/music_bed.py',
        'library/tools/orphan_removal.py',
        'library/tools/qa_findings.py',
        'library/tools/regional_motion.py',
        'library/tools/room_tone.py',
        'library/tools/semantic_index.py',
        'library/tools/semantic_profile_stream.py',
        'library/tools/sfx_library.py',
        'library/tools/sub_block_anchor.py',
        'library/dashboard/review_channel.py',
        'library/tools/alignment_findings.py',
        'library/tools/breakpoints.py',
        'library/tools/brief_snapshot.py',
        'library/tools/briefing_interview.py',
        'library/tools/camera_stability.py',
        'library/tools/completion_notice.py',
        'library/tools/cut_verdicts.py',
        'library/tools/display_drift.py',
        'library/tools/edit_input_digest.py',
        'library/tools/editorial_types.py',
        'library/tools/empty_table_guard.py',
        'library/tools/intent_receipt.py',
        'library/tools/music_sections.py',
        'library/tools/pipeline_logger.py',
        'library/tools/planning_reuse.py',
        'library/tools/project_context.py',
        'library/tools/prosody_profile.py',
        'library/tools/qa_feedback_loop.py',
        'library/tools/render_review.py',
        'library/tools/review_gate.py',
        'library/tools/run_profile.py',
        'library/tools/run_traceback.py',
        'library/tools/sound_events.py',
        'library/tools/template_loader.py',
        'library/tools/versions/runs.py',
        'library/tools/vision_schema_adapter.py',
        'library/tools/nothing_to_decide.py',
        'library/tools/post_bridge_retry.py',
        'library/tools/scoped_intent.py',
        'library/tools/second_pass.py',
        'library/tools/segment_coverage.py',
        'library/tools/timeline_qa.py',
        'library/tools/visual_qa_prompts.py',
        'library/tools/visual_qa_router.py',
        'library/tools/perceptual_qa.py',
        'library/tools/qa_fidelity.py',
        'library/tools/reference_collection.py',
        'library/tools/segment_renderer.py',
        'library/tools/video_segment_analyzer.py',
        'library/tools/gate_stills.py',
        'library/tools/qa/asset_qa.py',
        'library/tools/qa/timeline_sync_qa.py',
        'library/tools/treatment_verify.py',
        'library/tools/fusion/transition_frames.py',
        # Imported by the declared input/output contract surveys: the
        # session-local AST parse cache this audit split reads through,
        # so a fix here changes what the declared surveys parse.
        'library/tools/ast_cache.py',
        # Imported by the declared temporal-index step: the FI-14
        # camera/subject evidence reader, so a fix here changes what
        # the cached rows resolve through it.
        'library/tools/motion_evidence.py',
        # Version-store closure verifies retained timeline DRT contents.
        'library/tools/resolve_archive.py',
        'library/tools/accepted_edit_replay.py',
        'library/tools/creative_revision.py',
        'library/tools/direct_transaction.py',
        'library/tools/hold_window_repair.py',
        'library/tools/reel_component_manifest.py',
        'library/tools/structural_patch.py',
        'library/tools/versions/reproducibility.py',
        # Transitive closure: edit_algebra imports axi_qualification and
        # retention imports storage_lifecycle, so a fix to either shared
        # module changes what the cached scan rows resolve through.
        'library/tools/axi_qualification.py',
        'library/tools/storage_lifecycle.py',
        "library/tools/artifact_policy.py",
        "library/tools/axi_transaction.py",
        "library/tools/candidate_treatments.py",
        "library/tools/edit_recovery.py",
        "library/tools/editorial_identity.py",
        "library/tools/recovery_instruction.py",
        "library/tools/reel_execution_contract.py",
        "library/tools/sound_class.py",
        "library/tools/target_resolution.py",
        "library/tools/timeline_state_reconcile.py",
        "library/tools/treatment_rulings.py",
        "library/tools/editorial_decisions.py",
        "library/tools/model_stamps.py",
        "library/tools/editorial_intent.py",
        # The shared readers the declared closure reaches in one hop: the
        # approved-decision evidence (cache_lifecycle), the DRT archive
        # reader (resolve_scratch_projects), the timeline state report
        # (timeline_shadow) and the cross-project graph (source_memory).
        # A fix to any of them changes what the cached rows say, so it
        # must invalidate the cache.
        "library/tools/decision_evidence.py",
        "library/tools/drt_reader.py",
        "library/tools/edit_state_report.py",
        "library/tools/project_graph/cross_project.py",
        # The reel builder records the acceptance of a delivered generation
        # through this module, so a fix to it changes what the cached
        # rows of this step resolve through (the same chain as the
        # catalog row).
        "library/tools/reel_acceptance.py",
        # The explainer and face-track renderers, the frame-exact
        # measurement, the occurrence identity, the plan feasibility,
        # the project reference and the retrieval requirements: the
        # shared code these steps' cached rows are computed through.
        "library/tools/explainer_render.py",
        "library/tools/face_tracks.py",
        "library/tools/frame_exact.py",
        "library/tools/occurrence_identity.py",
        "library/tools/plan_feasibility.py",
        "library/tools/project_ref.py",
        "library/tools/retrieval/requirements.py",
        # The capability registry the catalog reads its operations from,
        # the write settle the executor waits on, the broker's own path
        # helper and the source clocks the measurements are read through:
        # a fix in any of them changes what these cached rows resolve to.
        "library/tools/capability_coverage.py",
        "library/tools/resolve_write_settle.py",
        "library/tools/resolved/broker_paths.py",
        "library/tools/source_clocks.py",
        # The shared code this step's cached rows are computed
        # through, reached by the closure above (the map's rule:
        # every file the closure reaches is named here).
        "library/tools/evidence_graph.py",
        "library/tools/frame_rate.py",
        "library/tools/mutation_verification.py",
    ),
    "step_1_02_catalog_footage": (
        # Shared measurement and evidence readers reached by the catalog's
        # import closure; changes alter the cached catalog result.
        "library/tools/alignment_quality.py",
        "library/tools/axi_qualification_dimensions.py",
        "library/tools/axi_qualification_state.py",
        "library/tools/camera_motion.py",
        "library/tools/capability_guidance.py",
        "library/tools/edit_conflict.py",
        "library/tools/edit_critical_events.py",
        "library/tools/editorial_policy.py",
        "library/tools/observation_provenance.py",
        "library/tools/operation_map.py",
        "library/tools/pause_class.py",
        "library/tools/preservation_invariant.py",
        "library/tools/render_perception.py",
        # The report provenance every live measurement carries: it is
        # reached by this step's dependency closure, so a fix to it is a
        # fix to the step's cached inputs.
        "library/tools/report_provenance.py",
        "library/tools/source_quality.py",
        "library/tools/speech_take.py",
        "library/tools/temporal_events.py",
        "library/tools/temporal_query.py",
        # The catalog's executor closure imports state preservation and Git
        # checks before it can safely publish edits to a project timeline.
        "library/tools/edit_state_preservation.py",
        "library/tools/git_guard.py",
        # The catalog reads the container's own rational frame rate through
        # this module, so a fix to how a rate is resolved or how an
        # unmeasurable rate is assumed changes the cached catalog result.
        "library/tools/frame_rate.py",
        # The catalog can commit timeline edit receipts and deliver audio
        # artifacts; keep those execution and cleanup paths in its cache key.
        "library/tools/artifact_lifecycle.py",
        "library/tools/artifact_policy.py",
        "library/tools/assignment_lifecycle.py",
        "library/tools/axi_transaction.py",
        "library/tools/candidate_treatments.py",
        "library/tools/edit_recovery.py",
        "library/tools/editorial_identity.py",
        "library/tools/recovery_instruction.py",
        "library/tools/reel_execution_contract.py",
        # The reel builder records the acceptance of a delivered generation
        # through this module, so a fix to it changes what the cached
        # catalog rows resolve through (the same chain as the row above).
        "library/tools/reel_acceptance.py",
        # The explainer and face-track renderers, the frame-exact
        # measurement, the occurrence identity, the plan feasibility,
        # the project reference and the retrieval requirements: the
        # shared code these steps' cached rows are computed through.
        "library/tools/explainer_render.py",
        "library/tools/face_tracks.py",
        "library/tools/frame_exact.py",
        "library/tools/occurrence_identity.py",
        "library/tools/plan_feasibility.py",
        "library/tools/project_ref.py",
        "library/tools/retrieval/requirements.py",
        # The capability registry the catalog reads its operations from,
        # the write settle the executor waits on, the broker's own path
        # helper and the source clocks the measurements are read through:
        # a fix in any of them changes what these cached rows resolve to.
        "library/tools/capability_coverage.py",
        "library/tools/resolve_write_settle.py",
        "library/tools/resolved/broker_paths.py",
        "library/tools/source_clocks.py",
        "library/tools/sound_class.py",
        "library/tools/target_resolution.py",
        "library/tools/timeline_state_reconcile.py",
        "library/tools/treatment_rulings.py",
        "library/tools/cache_lifecycle.py",
        "library/tools/edit_execution_adapters.py",
        "library/tools/execution/deliver_audio_mix.py",
        "library/tools/execution/remove_proof.py",
        "library/tools/proof_cleanup.py",
        "library/tools/resolve_axi_sdk/__init__.py",
        "library/tools/retention.py",
        "library/tools/safe_zone_guide.py",
        # The AXI CLI shell imports its shared core; keep that transitive
        # source in the catalog's declared source closure.
        "library/tools/resolve_axi_core.py",
        # EditPatch receipts are part of the shared code identity closure;
        # changing their schema must invalidate any cached consumer that
        # imports the transaction coordinator.
        "library/tools/transaction_receipt.py",
        "library/tools/operation_acceptance.py",
        # VC-12/VC-07/VC-08 (PRs 2714/2721/2723): the shared edit-request
        # and transaction orchestration closure reached through
        # resolve_axi_core and edit_patch, so a fix here changes what
        # cached preflight rows resolve through.
        "library/tools/transaction_orchestration.py",
        "library/tools/edit_request.py",
        "library/tools/edit_preparation.py",
        "library/tools/timeline_addressing.py",
        # Transaction receipt verification delegates operation-specific
        # acceptance requirements here; changes affect recorded outcomes.
        # The legacy step adapter delegates the catalog measurement here.
        "library/capabilities/footage/catalog.py",
        # The catalog's retrieval text encoder is shared with footage search;
        # changes to its model loading and embedding path invalidate catalog output.
        "library/tools/analysis/mlx_minilm.py",
        # Resolve frame fields used by the append placer are centralized
        # here; changes to this adapter affect the catalog's placement rows.
        "library/tools/resolve_temporal.py",
        # The shared analysis import closure includes the source-separation
        # helper, whose changes must invalidate cached catalog consumers.
        "library/tools/analysis/source_separation.py",
        # Cache identity's dependency inventory includes each declared
        # preflight implementation path; preserve coverage when that
        # inventory names a newly added shared measurement module.
        "library/tools/motion_evidence.py",
        # VI-09's deterministic profiles are joined to M4 before its
        # source-memory sidecar is exposed by the temporal-index step.
        "library/tools/scene_cinematography.py",
        # The exact-file probe cache changes the measurement reused for
        # unchanged footage, so changes here must invalidate catalog rows.
        "library/tools/file_measurements.py",
        # Catalog publishes its first usable result through this writer.
        "library/tools/assignment_artifacts.py",
        # Promoted timeline recovery verifies the exported DRT's media
        # inventory before recording a version that can be restored.
        "library/tools/resolve_archive.py",
        # Read by step.py for the source block's declared program stream
        # and measure flag: which audio stream counts as the program mix
        # decides what the catalog records, so a fix here changes the
        # cached rows.
        "library/tools/footage_identity.py",
        # Its declaration parsing: the source block is read through the
        # project config schema.
        "library/schemas/project_config.py",
        # Imported by project_config: the delivery-platform registry
        # pipeline.delivery_platform validates against, so a fix here
        # changes what the catalog open accepts.
        "library/tools/delivery_spec.py",
        # Imported by project_config: the `$HOME` / `$PROJECT` token
        # expansion path declarations are read through (same chain as
        # the scan row above).
        "library/tools/portable_paths.py",
        # Imported by portable_paths: the canonical spelling a rebind
        # rewrite is written back in, so a fix here changes the bytes
        # the next open reads.
        "library/tools/stable_json.py",
        # Imported by project_config: the project_format_version key
        # and the supported format range the open refuses outside of
        # (same chain as the scan row above).
        "library/tools/project_format.py",
        # Imported by project_config: the subtitle_overlay_geometry /
        # subtitle_overlay_container vocabularies declaration parsing
        # validates against (same chain as the scan row above).
        "library/tools/overlay_mode.py",
        # Imported by project_config: the graphics_renderer vocabulary
        # declaration parsing validates against (same chain as the
        # overlay_mode row above).
        "library/tools/graphics_renderer.py",
        # Imported by project_config: the pipeline.safe_zones policy
        # the declaration parsing validates against (same chain).
        "library/tools/safe_zone_policy.py",
        # Imported by safe_zone_policy: the platform zones a policy
        # names and is checked against.
        "library/tools/platform_safe_zones.py",
        # Imported by step.py: the per-file lanes, so a fix here
        # changes how many probes measure at once.
        "library/tools/concurrency_routing.py",
        # Imported by concurrency_routing: the lane bound is this
        # machine's measured core count. (Its capabilities read is
        # exempt plumbing.)
        "library/tools/resource_scheduler.py",
        # Imported by the catalog handoff to persist assignment artifacts.
        "library/tools/assignment_artifacts.py",
        # Lane B: the catalog stamps the scan's Project Model revision
        # onto every row through footage_model_link, so a fix to the
        # link changes what the cached rows resolve into. (Same chain
        # as the scan row above.)
        "library/tools/footage_model_link.py",
        "library/tools/project_model.py",
        # Imported by project_model: the typed certainty/confidence
        # and coverage validation every model write is checked
        # against, so a fix here changes what cached rows validate.
        "library/tools/project_model_contract.py",
        "library/tools/project_model_evidence.py",
        # Imported by Project Model migration for derivation and typed scores.
        "library/tools/evidence_lifecycle.py",
        # The catalog's Project Model migration materializes these events.
        "library/tools/temporal_action_candidates.py",
        # Lane 3: migration materializes track re-identification and
        # temporal/physical relation candidates into the model.
        "library/tools/identity_reassociation.py",
        "library/tools/relation_classes.py",
        "library/tools/analysis/open_vocabulary_detect.py",
        "library/tools/analyzer_providers.py",
        "library/tools/query_directed_acquisition.py",
        "library/tools/evidence_query.py",
        "library/tools/ocr_project_model.py",
        "library/tools/evidence_contract.py",
        "library/tools/cross_modal_grounding.py",
        # Imported by project_model_evidence for native visual measurements;
        # changes to these readers alter the evidence and must invalidate it.
        "library/tools/attention_saliency.py",
        "library/tools/expression_classifier.py",
        "library/tools/footage_analysis.py",
        "library/tools/person_measurements.py",
        "library/tools/project_model_store.py",
        "library/tools/source_memory.py",
        "library/tools/speech_measurements.py",
        "library/tools/hybrid_transcription.py",
        "library/tools/protocol_conformance.py",
        "library/tools/shared_environment.py",
        "library/tools/source_primitives.py",
        "library/tools/transcript_measurement.py",
        "library/tools/heard_speech.py",
        "library/tools/mfa_align.py",
        "library/tools/subject_framing.py",
        "library/tools/timeline_transcript.py",
        "library/tools/overlapped_speech.py",
        "library/tools/word_boundaries.py",
        "library/tools/vision_measure.py",
        "library/tools/caption_reading.py",
        "library/tools/single_track_diarization.py",
        "library/tools/model_registry.py",
        "library/tools/evidence_license.py",
        # Diarization routes through the model registry and emits
        # registry-derived license obligations with reusable outputs.
        "library/tools/transcript_confidence.py",
        "library/tools/transcript_corrections.py",
        "library/tools/transcript_fit.py",
        "library/tools/learned_context.py",
        'library/tools/analysis/footage_frames.py',
        'library/tools/analysis/footage_query.py',
        'library/tools/analysis/ocr_combined_query.py',
        'library/tools/conversation_clock.py',
        'library/tools/depth.py',
        # The adopted DA3 backend depth.py selects behind the M3g protocol.
        'library/tools/depth_da3.py',
        'library/tools/event_spans.py',
        'library/tools/footage_intelligence.py',
        'library/tools/analysis_state.py',
        # Analysis composition resolves from capability-owned declarations,
        # so a fix to the contracts changes the order preflight runs in.
        'library/tools/capability_contracts.py',
        'library/tools/heavy_work_lock.py',
        'library/tools/person_entity.py',
        'library/tools/person_regions.py',
        'library/tools/project_registry.py',
        'library/tools/region_stats.py',
        'library/tools/relationships.py',
        'library/tools/world_model.py',
        'library/tools/analysis/footage_segments.py',
        'library/tools/analysis/picture_quality.py',
        'library/tools/analysis/unified_anchors.py',
        'library/tools/brief_attachment.py',
        'library/tools/marker_capture.py',
        'library/tools/mask_seed.py',
        'library/tools/progress.py',
        'library/tools/project_model_masks.py',
        'library/tools/provenance.py',
        'library/tools/region_query.py',
        'library/tools/run_control.py',
        'library/tools/series_registry.py',
        'library/tools/shot_colour.py',
        'library/tools/span_verification.py',
        'library/tools/step_ledger.py',
        'library/tools/analysis/object_segmentation.py',
        # Object segmentation imports the shared resident SAM model
        # lifecycle, so changes to its cache and session behavior affect
        # selected mask measurements.
        'library/tools/model_lifecycle.py',
        'library/tools/brief_reference.py',
        'library/tools/edit_algebra.py',
        'library/tools/reel_read.py',
        'library/tools/region.py',
        'library/tools/resolve_lock.py',
        'library/tools/resolved/client.py',
        'library/tools/run_restart.py',
        'library/tools/scope.py',
        'library/tools/step_exporter.py',
        'library/tools/vision_model.py',
        'library/tools/cdl_readback.py',
        'library/tools/comp_media_window.py',
        'library/tools/computer_use/client.py',
        'library/tools/computer_use/scripts.py',
        'library/tools/creative_direction.py',
        'library/tools/edit_backend.py',
        'library/tools/event_log.py',
        'library/tools/fusion/engine.py',
        'library/tools/fusion/nodes.py',
        'library/tools/magic_mask.py',
        'library/tools/marker_feedback.py',
        'library/tools/native_refresh.py',
        'library/tools/reel_replace_guard.py',
        'library/tools/resolve_swapback.py',
        'library/tools/spine_contract.py',
        'library/tools/tight_box.py',
        'library/tools/timeline_decisions.py',
        'library/tools/timeline_read.py',
        'library/tools/timeline_shadow.py',
        'library/tools/transform_write_log.py',
        'library/tools/trim_qualification.py',
        'library/steps/step_4_01_plan_subtitles/step.py',
        'library/tools/brand_palette.py',
        'library/tools/computer_use/actions.py',
        'library/tools/computer_use/observe.py',
        'library/tools/computer_use/take.py',
        'library/tools/computer_use/templates.py',
        'library/tools/edit_patch.py',
        'library/tools/project_model_editorial.py',
        'library/tools/edit_point_planner.py',
        'library/tools/energy_reading.py',
        'library/tools/feedback_ledger.py',
        'library/tools/fframes_render.py',
        'library/tools/marker_payload.py',
        'library/tools/music_behavior.py',
        'library/tools/native_part_lock.py',
        'library/tools/overlay_carriage.py',
        'library/tools/plan_provenance.py',
        'library/tools/project_file_lock.py',
        'library/tools/qa/subtitle_qa.py',
        'library/tools/qualification_project.py',
        'library/tools/reel_address.py',
        'library/tools/reel_disabled_clip_carry.py',
        'library/tools/ren_native_cache.py',
        'library/tools/ren_native_source.py',
        'library/tools/render_fonts.py',
        'library/tools/resolve_deadline.py',
        'library/tools/resolve_locale.py',
        'library/tools/resolve_switch_guard.py',
        'library/tools/resolve_transform.py',
        'library/tools/continuity_feasibility.py',
        'library/tools/resolved/cancellation.py',
        'library/tools/resolved/jobs.py',
        'library/tools/resolved/scheduler.py',
        'library/tools/resolved/store.py',
        'library/tools/shadow_watcher.py',
        'library/tools/subtitle_segment_id.py',
        'library/tools/undo_journal.py',
        'library/tools/versions/reel_versions.py',
        'library/tools/versions/rounds.py',
        'library/graphics_ir/fonts.py',
        'library/graphics_ir/lowering.py',
        'library/graphics_ir/serialization.py',
        'library/schemas/brand_template.py',
        'library/tools/captain_edits.py',
        'library/tools/cascade_graph.py',
        'library/tools/composed_edit.py',
        'library/tools/decided_value.py',
        'library/tools/fabric_permissions.py',
        'library/tools/hyperframes_render.py',
        'library/tools/manifest_validator.py',
        'library/tools/marker_carry.py',
        'library/tools/marker_resolution.py',
        'library/tools/patch_algebra.py',
        'library/tools/project_asset.py',
        'library/tools/project_model_qualification.py',
        'library/tools/qualification_workflow.py',
        'library/tools/reel_build.py',
        'library/tools/reel_append_placement.py',
        'library/tools/resolve_temporal.py',
        'library/tools/reel_conformance_verifier.py',
        'library/tools/reel_edit_history.py',
        'library/tools/reel_incremental.py',
        'library/tools/reel_proposal.py',
        'library/tools/reel_retirement.py',
        'library/tools/reel_semantic_visual.py',
        'library/tools/reel_state.py',
        'library/tools/reel_touchup.py',
        'library/tools/render_cache.py',
        'library/tools/render_qa.py',
        'library/tools/resolve_axi_core_commands.py',
        'library/tools/resolve_axi.py',
        'library/tools/resolve_axi_core.py',
        'library/tools/resolve_scratch_projects.py',
        'library/tools/resolve_bin_layout.py',
        'library/tools/resolved/drt_export_probe.py',
        'library/tools/resolved/drt_recovery_probe.py',
        'library/tools/resolved/marker_loss_probe.py',
        'library/tools/resolved/reel_ending_copy.py',
        'library/tools/resolved/reel_promotion.py',
        'library/tools/resolved/reel_promotion_fixture.py',
        'library/tools/resolved/ui_transaction.py',
        'library/tools/safe_area.py',
        'library/tools/still_placement.py',
        'library/tools/still_vision.py',
        'library/tools/subtitle_coverage.py',
        'library/tools/subtitle_splice.py',
        'library/tools/timeline_diff.py',
        'library/tools/transcript_duration_anomaly.py',
        'library/tools/versions/store.py',
        'library/skills/verify_render/skill.py',
        'library/steps/step_4_05_render_subtitles/generate_remotion_props.py',
        'library/tools/awaiting_model_answers.py',
        'library/tools/bookends.py',
        'library/tools/build_sweep.py',
        'library/tools/camera_sync.py',
        'library/tools/caption_asset_gc.py',
        'library/tools/caption_band.py',
        'library/tools/caption_swap.py',
        'library/tools/caption_timing.py',
        'library/tools/closer_fit.py',
        'library/tools/comparison_retirement.py',
        'library/tools/declaration_keys.py',
        'library/tools/dirty_regions.py',
        'library/tools/display_respell.py',
        'library/tools/do_not_draw.py',
        'library/tools/draw_gain_probe.py',
        'library/tools/drift_check.py',
        'library/tools/edit_ledger.py',
        'library/tools/editor_edit_carry.py',
        'library/tools/execution/fusion_tracks.py',
        'library/tools/execution/organise_media_pool.py',
        'library/tools/execution/resolve_render.py',
        'library/tools/explainer_plan.py',
        'library/tools/external_inputs.py',
        'library/tools/feedback_outcomes.py',
        'library/tools/feedback_patch_apply.py',
        'library/tools/filmcraft_patches.py',
        'library/tools/frame_utils.py',
        'library/tools/full_frame_element.py',
        'library/tools/fusion/comp_builder.py',
        'library/tools/fusion/effects.py',
        'library/tools/fusion/parser.py',
        'library/tools/intellisearch_collect.py',
        'library/tools/layer_coherence.py',
        'library/tools/llm_handshake.py',
        'library/tools/manual_edit_intent.py',
        'library/tools/marker_gate.py',
        'library/tools/marker_routing.py',
        'library/tools/master_loudness.py',
        'library/tools/mg_tight_box.py',
        'library/tools/motion_graphics_plan.py',
        'library/tools/motion_graphics_vocabulary.py',
        'library/tools/music_measurement.py',
        'library/tools/music_over_speech.py',
        'library/tools/native_effects_composite.py',
        'library/tools/native_mcp.py',
        'library/tools/native_ops_apply.py',
        'library/tools/otio_mix.py',
        'library/tools/output_contract.py',
        'library/tools/overlay_draw_intent.py',
        'library/tools/overlay_intent.py',
        'library/tools/overlay_placement.py',
        'library/tools/overlay_verify.py',
        'library/tools/plan_creative_quality.py',
        'library/tools/plan_splice.py',
        'library/tools/pool_stream_meta.py',
        'library/tools/project_model_effectcraft.py',
        'library/tools/project_model_effectcraft_moves.py',
        'library/tools/proof_scope.py',
        'library/tools/reel_angle_plan.py',
        'library/tools/reel_clock.py',
        'library/tools/reel_cta_treatment.py',
        'library/tools/reel_divergence.py',
        'library/tools/reel_ending.py',
        'library/tools/reel_exchange.py',
        'library/tools/reel_framing.py',
        'library/tools/reel_fusion_comps.py',
        'library/tools/reel_ledger.py',
        'library/tools/reel_look.py',
        'library/tools/reel_opening.py',
        'library/tools/reel_otio_placement.py',
        'library/tools/reel_phase_log.py',
        'library/tools/reel_placed_assets.py',
        'library/tools/reel_post_header.py',
        'library/tools/reel_prebuild_census.py',
        'library/tools/reel_quality_bar.py',
        'library/tools/reel_rebuild_need.py',
        'library/tools/reel_signoff.py',
        'library/tools/reel_spine.py',
        'library/tools/reel_thesis.py',
        'library/tools/reference_style.py',
        'library/tools/remotion_brand_linker.py',
        'library/tools/render_watch.py',
        'library/tools/resolve_api_capabilities.py',
        'library/tools/resolve_organization.py',
        'library/tools/resolve_script_store.py',
        'library/tools/retake_scan.py',
        'library/tools/semantic_visual.py',
        'library/tools/source_transcript.py',
        'library/tools/speaker_identity.py',
        'library/tools/speech_loudness.py',
        'library/tools/staging_holds.py',
        'library/tools/still_overlays.py',
        'library/tools/subject_grade.py',
        'library/tools/tail_extend_authorization.py',
        'library/tools/taste_profile.py',
        'library/tools/timeline_conformance.py',
        'library/tools/timeline_layout.py',
        'library/tools/timeline_serializer.py',
        'library/tools/toon_serializer.py',
        'library/tools/transition_overlay.py',
        'library/tools/transition_vocabulary.py',
        'library/tools/tv_frame.py',
        'library/tools/tv_power.py',
        'library/tools/undetermined.py',
        'library/tools/versions/checkpoints.py',
        'library/tools/versions/variants.py',
        'library/tools/video_prefs.py',
        'library/capabilities/temporal/index.py',
        'library/tools/motion_evidence.py',
        'library/tools/analysis/music_pipeline.py',
        'library/tools/analysis/source_separation.py',
        'library/tools/analysis/ocr_extractor.py',
        'library/tools/beat_grid.py',
        'library/tools/brand_library.py',
        'library/tools/broll_correspondence.py',
        'library/tools/caption_correction.py',
        'library/tools/color_page_grade.py',
        'library/tools/creative_tasks.py',
        'library/tools/dialogue_cleanup.py',
        'library/tools/direction_contradiction.py',
        'library/tools/edit_depth.py',
        'library/tools/edit_operations.py',
        'library/tools/edit_spec.py',
        'library/tools/execution/prune_orphans.py',
        'library/tools/execution/retire_empty_bins.py',
        'library/tools/fusion/played_window.py',
        'library/tools/input_contract.py',
        'library/tools/intent_model.py',
        'library/tools/jl_cut.py',
        'library/tools/journal_naming.py',
        'library/tools/mix_intent.py',
        'library/tools/music_section.py',
        'library/tools/native_ops.py',
        'library/tools/otio_compile.py',
        'library/tools/overlay_keep_clear.py',
        'library/tools/person_matte.py',
        'library/tools/pipeline_skills.py',
        'library/tools/placed_assets.py',
        'library/tools/processes.py',
        'library/tools/project_model_effectcraft_titles.py',
        'library/tools/reel_hearing.py',
        'library/tools/reel_layer_links.py',
        'library/tools/remotion_batch.py',
        'library/tools/render_capability_index.py',
        'library/tools/requirements.py',
        'library/tools/review_panel.py',
        'library/tools/row_shift.py',
        'library/tools/run_scope.py',
        'library/tools/series_look.py',
        'library/tools/still_content.py',
        'library/tools/timed_text_overlay.py',
        'library/tools/timeline_duration.py',
        'library/tools/transform_drift.py',
        'library/tools/uncarried_notes.py',
        'library/tools/versions/edit_variants.py',
        'library/tools/window_frames.py',
        'library/processes/edit_video/run_pipeline.py',
        'library/tools/capability_plan.py',
        'library/tools/project_model_narrative.py',
        'library/capabilities/temporal/media_decode.py',
        'library/tools/analysis/sound_event_pipeline.py',
        'library/tools/audio_chain.py',
        'library/tools/audio_classification.py',
        'library/tools/audio_effects.py',
        'library/tools/brand_motion.py',
        'library/tools/context_projector.py',
        'library/tools/context_views.py',
        'library/tools/correction_loop.py',
        'library/tools/craft_role.py',
        'library/tools/creative_floors.py',
        'library/tools/cutaway_window.py',
        'library/tools/edit_graph.py',
        'library/tools/hearing_settings.py',
        'library/tools/hooks.py',
        'library/tools/model_task.py',
        # The cost of a model answer, reached through model_task: a
        # fix to the costing changes what a step's own identity is.
        'library/tools/model_cost.py',
        # The count a model answer is priced in, reached through
        # model_task: the tokenizer that produced the figure is part
        # of what the step measured.
        'library/tools/token_measure.py',
        # The delivery and overlay code the catalog's executor
        # closure reaches: a fix to any of them changes what the
        # step's cached rows resolve through.
        'library/tools/delivery_identity.py',
        'library/tools/overlay_media.py',
        'library/tools/reel_export.py',
        # The caption rewrite the catalog's closure reaches: a fix to
        # how a caption is redrawn changes what the step measured.
        'library/tools/caption_rewrite.py',
        'library/tools/touchup_phases.py',
        # The broker session the catalog's executor closure holds
        # across a multi-call Resolve operation: a fix to it changes
        # what the step's cached rows resolve through.
        'library/tools/resolve_session.py',
        'library/tools/music_bed.py',
        'library/tools/orphan_removal.py',
        'library/tools/qa_findings.py',
        'library/tools/regional_motion.py',
        'library/tools/room_tone.py',
        'library/tools/semantic_index.py',
        'library/tools/semantic_profile_stream.py',
        'library/tools/sfx_library.py',
        'library/tools/sub_block_anchor.py',
        'library/dashboard/review_channel.py',
        'library/tools/alignment_findings.py',
        'library/tools/breakpoints.py',
        'library/tools/brief_snapshot.py',
        'library/tools/briefing_interview.py',
        'library/tools/camera_stability.py',
        'library/tools/completion_notice.py',
        'library/tools/cut_verdicts.py',
        'library/tools/display_drift.py',
        'library/tools/edit_input_digest.py',
        'library/tools/editorial_types.py',
        'library/tools/empty_table_guard.py',
        'library/tools/intent_receipt.py',
        'library/tools/music_sections.py',
        'library/tools/pipeline_logger.py',
        'library/tools/planning_reuse.py',
        'library/tools/project_context.py',
        'library/tools/prosody_profile.py',
        'library/tools/qa_feedback_loop.py',
        'library/tools/render_review.py',
        'library/tools/review_gate.py',
        'library/tools/run_profile.py',
        'library/tools/run_traceback.py',
        'library/tools/sound_events.py',
        'library/tools/template_loader.py',
        'library/tools/versions/runs.py',
        'library/tools/vision_schema_adapter.py',
        'library/tools/nothing_to_decide.py',
        'library/tools/post_bridge_retry.py',
        'library/tools/scoped_intent.py',
        'library/tools/second_pass.py',
        'library/tools/segment_coverage.py',
        'library/tools/timeline_qa.py',
        'library/tools/visual_qa_prompts.py',
        'library/tools/visual_qa_router.py',
        'library/tools/perceptual_qa.py',
        'library/tools/qa_fidelity.py',
        'library/tools/reference_collection.py',
        'library/tools/segment_renderer.py',
        'library/tools/video_segment_analyzer.py',
        'library/tools/gate_stills.py',
        'library/tools/qa/asset_qa.py',
        'library/tools/qa/timeline_sync_qa.py',
        'library/tools/treatment_verify.py',
        'library/tools/fusion/transition_frames.py',
        # Imported by the declared input/output contract surveys: the
        # session-local AST parse cache this audit split reads through,
        # so a fix here changes what the declared surveys parse.
        'library/tools/ast_cache.py',
        # Imported by the declared temporal-index step: the FI-14
        # camera/subject evidence reader, so a fix here changes what
        # the cached catalog rows resolve through it.
        'library/tools/motion_evidence.py',
        # Creative-revision and Resolve recovery records are imported by
        # catalog's shared identity closure; fixes to their readers must
        # invalidate the cached preflight result.
        'library/tools/accepted_edit_replay.py',
        'library/tools/creative_revision.py',
        'library/tools/direct_transaction.py',
        'library/tools/hold_window_repair.py',
        'library/tools/reel_component_manifest.py',
        'library/tools/structural_patch.py',
        'library/tools/versions/reproducibility.py',
        # Transitive closure: edit_algebra imports axi_qualification and
        # retention imports storage_lifecycle, so a fix to either shared
        # module changes what the cached catalog rows resolve through.
        'library/tools/axi_qualification.py',
        'library/tools/storage_lifecycle.py',
        "library/tools/assignment_spine.py",
        "library/tools/editorial_decisions.py",
        "library/tools/model_stamps.py",
        "library/tools/assignment_actions.py",
        "library/tools/assignment_task.py",
        "library/tools/editorial_intent.py",
        "library/tools/assignment_plan.py",
        # The shared readers the declared closure reaches in one hop: the
        # approved-decision evidence (cache_lifecycle), the DRT archive
        # reader (resolve_scratch_projects), the timeline state report
        # (timeline_shadow) and the cross-project graph (source_memory).
        # A fix to any of them changes what the cached rows say, so it
        # must invalidate the cache.
        "library/tools/decision_evidence.py",
        "library/tools/drt_reader.py",
        "library/tools/edit_state_report.py",
        "library/tools/project_graph/cross_project.py",
        # The shared code this step's cached rows are computed
        # through, reached by the closure above (the map's rule:
        # every file the closure reaches is named here).
        "library/tools/evidence_graph.py",
        "library/tools/mutation_verification.py",
    ),
    "step_1_03_semantic_analysis": (
        "library/tools/alignment_quality.py",
        "library/tools/axi_qualification_dimensions.py",
        "library/tools/axi_qualification_state.py",
        "library/tools/camera_motion.py",
        "library/tools/capability_guidance.py",
        "library/tools/edit_conflict.py",
        "library/tools/edit_critical_events.py",
        "library/tools/editorial_policy.py",
        "library/tools/observation_provenance.py",
        "library/tools/operation_map.py",
        "library/tools/pause_class.py",
        "library/tools/preservation_invariant.py",
        "library/tools/render_perception.py",
        # The report provenance every live measurement carries: it is
        # reached by this step's dependency closure, so a fix to it is a
        # fix to the step's cached inputs.
        "library/tools/report_provenance.py",
        "library/tools/source_quality.py",
        "library/tools/speech_take.py",
        "library/tools/temporal_events.py",
        "library/tools/temporal_query.py",
        "library/tools/artifact_lifecycle.py",
        "library/tools/cache_lifecycle.py",
        "library/tools/edit_execution_adapters.py",
        "library/tools/edit_state_preservation.py",
        "library/tools/git_guard.py",
        "library/tools/execution/deliver_audio_mix.py",
        "library/tools/execution/remove_proof.py",
        "library/tools/proof_cleanup.py",
        "library/tools/resolve_axi_sdk/__init__.py",
        "library/tools/retention.py",
        "library/tools/safe_zone_guide.py",
        "library/tools/transaction_receipt.py",
        "library/tools/operation_acceptance.py",
        # VC-12/VC-07/VC-08 (PRs 2714/2721/2723): the shared edit-request
        # and transaction orchestration closure reached through
        # resolve_axi_core and edit_patch, so a fix here changes what
        # cached preflight rows resolve through.
        "library/tools/transaction_orchestration.py",
        "library/tools/edit_request.py",
        "library/tools/edit_preparation.py",
        "library/tools/timeline_addressing.py",
        # The legacy step delegates analysis here; code changes alter cached profiles.
        "library/capabilities/semantics/analyse.py",
        # Its dependency closure includes the shared retrieval encoder.
        "library/tools/analysis/mlx_minilm.py",
        # Version-store closure verifies retained timeline DRT contents.
        "library/tools/resolve_archive.py",
        # VI-09's temporal intervals are joined into the M4 scene read.
        "library/tools/scene_cinematography.py",
        # The temporal-index step is part of the cached dependency closure.
        "library/tools/motion_evidence.py",
        # The executed measurement (D1: the usable-ranges gate lives here).
        "library/tools/analysis/vision_pipeline_v3.py",
        # The adaptive semantic scheduler and its measured activity inputs
        # change which source windows reach the cached VLM analysis.
        "library/tools/semantic_window_planner.py",
        "library/tools/content_activity.py",
        # Imported by vision_pipeline_v3: the opt-in source-frame sampling
        # policy behind --frame-sampling adaptive, so a fix here changes
        # which stills the cached objects[] were seen by in that mode.
        "library/tools/adaptive_frame_sampling.py",
        # The model run is serialized against other local heavy work.
        "library/tools/heavy_work_lock.py",
        # Imported by heavy_work_lock: admits it by resource demand.
        "library/tools/resource_scheduler.py",
        # Its measurement imports: soft-picture ranges and the steadiness
        # reading both land in the cached profile.
        "library/tools/analysis/picture_quality.py",
        "library/tools/camera_stability.py",
        # Imported by step.py: shapes the cached documents.
        "library/tools/vision_schema_adapter.py",
        # Imported by vision_schema_adapter (and vision_pipeline_v3):
        # renders the undescribed scene[] ranges into the prose every
        # consumer reads, so a fix here changes the cached documents.
        "library/tools/segment_coverage.py",
        # Imported by vision_pipeline_v3: routes the object passes to
        # Codex's subscription CLI or the gemma fallback, so a fix here
        # changes what the cached objects[] were seen by.
        "library/tools/still_vision.py",
        # Imported by still_vision for the unchanged local fallback.
        "library/tools/vision_model.py",
        # Imported by vision_pipeline_v3: the per-layer measurement
        # cache. Its keys decide which stored layers a compose reuses,
        # so a fix here must recompose; a recompose re-measures only
        # the layers whose own keys moved, so this costs little.
        "library/tools/analysis/measurement_layers.py",
        # Imported by vision_pipeline_v3: the source-memory root the
        # reserved lanes are written under, so a fix here changes which
        # stored layers a compose reuses.
        "library/tools/source_memory.py",
        "library/tools/speech_measurements.py",
        # The source-memory chain: source_memory writes and reads the
        # canonical audio and the measured transcripts, so a fix to any
        # of these changes the stored layers a compose reuses.
        "library/tools/hybrid_transcription.py",
        "library/tools/shared_environment.py",
        "library/tools/source_primitives.py",
        "library/tools/transcript_measurement.py",
        # Imported by hybrid_transcription: the heard-speech gate and
        # the word boundaries the forced alignment is anchored to.
        "library/tools/heard_speech.py",
        "library/tools/word_boundaries.py",
        # Imported by shared_environment: the MFA align path and the
        # subject framing the reserved lanes are measured against.
        "library/tools/mfa_align.py",
        "library/tools/subject_framing.py",
        # Lane B: the transcript chain above now mints utterance
        # Observation ids through footage_model_link, so a fix to the
        # link changes what the cached layers resolve into.
        "library/tools/footage_model_link.py",
        # Imported by footage_model_link: the migration and the stores
        # behind the link.
        "library/tools/project_model.py",
        # Imported by project_model: the typed certainty/confidence
        # and coverage validation every model write is checked
        # against, so a fix here changes what cached rows validate.
        "library/tools/project_model_contract.py",
        "library/tools/project_model_evidence.py",
        # Imported by Project Model migration for derivation and typed scores.
        "library/tools/evidence_lifecycle.py",
        "library/tools/temporal_action_candidates.py",
        # Lane 3 migration adds identity and physical relation records.
        "library/tools/identity_reassociation.py",
        "library/tools/relation_classes.py",
        "library/tools/analysis/open_vocabulary_detect.py",
        "library/tools/analyzer_providers.py",
        "library/tools/query_directed_acquisition.py",
        "library/tools/evidence_query.py",
        "library/tools/ocr_project_model.py",
        "library/tools/evidence_contract.py",
        "library/tools/cross_modal_grounding.py",
        # Imported by project_model_evidence for native visual measurements;
        # changes to these readers alter the evidence and must invalidate it.
        "library/tools/attention_saliency.py",
        "library/tools/expression_classifier.py",
        "library/tools/footage_analysis.py",
        "library/tools/person_measurements.py",
        "library/tools/project_model_store.py",
        "library/tools/protocol_conformance.py",
        # Imported by transcript_measurement: the timeline transcript
        # whose segments the stored hearings are served from.
        "library/tools/timeline_transcript.py",
        "library/tools/overlapped_speech.py",
        # Imported by source_primitives / transcript_measurement: the
        # per-clip vision measurements and the transcript corrections
        # and confidence the cached profile is composed from.
        "library/tools/vision_measure.py",
        "library/tools/caption_reading.py",
        "library/tools/single_track_diarization.py",
        "library/tools/model_registry.py",
        "library/tools/evidence_license.py",
        "library/tools/transcript_confidence.py",
        "library/tools/transcript_corrections.py",
        "library/tools/transcript_fit.py",
        # Imported by transcript_corrections: the learned context a
        # correction is checked against.
        "library/tools/learned_context.py",
        # Imported by measurement_layers: the source digest every layer
        # key starts from.
        "library/tools/footage_identity.py",
        # Imported by footage_identity: its declaration parsing (same
        # chain as the scan row above).
        "library/schemas/project_config.py",
        # Imported by project_config: the delivery-platform registry
        # pipeline.delivery_platform validates against (same chain).
        "library/tools/delivery_spec.py",
        # Imported by project_config: the `$HOME` / `$PROJECT` token
        # expansion path declarations are read through (same chain).
        "library/tools/portable_paths.py",
        "library/tools/project_format.py",
        "library/tools/overlay_mode.py",
        "library/tools/graphics_renderer.py",
        "library/tools/safe_zone_policy.py",
        "library/tools/platform_safe_zones.py",
        # Publishes the completed profile records that temporal indexing
        # consumes, so changing its join or serialized profile shape moves
        # this step's cached output.
        "library/tools/semantic_profile_stream.py",
        "library/tools/run_control.py",
        "library/tools/run_restart.py",
        "library/tools/stable_json.py",
        'library/tools/analysis/footage_frames.py',
        'library/tools/analysis/footage_query.py',
        'library/tools/analysis/ocr_combined_query.py',
        'library/tools/concurrency_routing.py',
        'library/tools/conversation_clock.py',
        'library/tools/depth.py',
        # The adopted DA3 backend depth.py selects behind the M3g protocol.
        'library/tools/depth_da3.py',
        'library/tools/event_spans.py',
        'library/tools/footage_intelligence.py',
        'library/tools/analysis_state.py',
        # Analysis composition resolves from capability-owned declarations,
        # so a fix to the contracts changes the order preflight runs in.
        'library/tools/capability_contracts.py',
        'library/tools/person_entity.py',
        'library/tools/person_regions.py',
        'library/tools/project_registry.py',
        'library/tools/region_stats.py',
        'library/tools/relationships.py',
        'library/tools/world_model.py',
        'library/tools/analysis/footage_segments.py',
        'library/tools/analysis/unified_anchors.py',
        'library/tools/brief_attachment.py',
        'library/tools/marker_capture.py',
        'library/tools/mask_seed.py',
        'library/tools/progress.py',
        'library/tools/project_model_masks.py',
        'library/tools/provenance.py',
        'library/tools/region_query.py',
        'library/tools/series_registry.py',
        'library/tools/shot_colour.py',
        'library/tools/span_verification.py',
        'library/tools/step_ledger.py',
        'library/tools/analysis/object_segmentation.py',
        # Object segmentation imports the resident SAM model lifecycle.
        'library/tools/model_lifecycle.py',
        'library/tools/brief_reference.py',
        'library/tools/edit_algebra.py',
        'library/tools/reel_read.py',
        'library/tools/region.py',
        'library/tools/resolve_lock.py',
        'library/tools/resolved/client.py',
        'library/tools/scope.py',
        'library/tools/step_exporter.py',
        'library/tools/cdl_readback.py',
        'library/tools/comp_media_window.py',
        'library/tools/computer_use/client.py',
        'library/tools/computer_use/scripts.py',
        'library/tools/creative_direction.py',
        'library/tools/edit_backend.py',
        'library/tools/event_log.py',
        'library/tools/fusion/engine.py',
        'library/tools/fusion/nodes.py',
        'library/tools/magic_mask.py',
        'library/tools/marker_feedback.py',
        'library/tools/native_refresh.py',
        'library/tools/reel_replace_guard.py',
        'library/tools/resolve_swapback.py',
        'library/tools/spine_contract.py',
        'library/tools/tight_box.py',
        'library/tools/timeline_decisions.py',
        'library/tools/timeline_read.py',
        'library/tools/timeline_shadow.py',
        'library/tools/transform_write_log.py',
        'library/tools/trim_qualification.py',
        'library/steps/step_4_01_plan_subtitles/step.py',
        'library/tools/brand_palette.py',
        'library/tools/computer_use/actions.py',
        'library/tools/computer_use/observe.py',
        'library/tools/computer_use/take.py',
        'library/tools/computer_use/templates.py',
        'library/tools/edit_patch.py',
        'library/tools/project_model_editorial.py',
        'library/tools/edit_point_planner.py',
        'library/tools/energy_reading.py',
        'library/tools/feedback_ledger.py',
        'library/tools/fframes_render.py',
        'library/tools/marker_payload.py',
        'library/tools/music_behavior.py',
        'library/tools/native_part_lock.py',
        'library/tools/overlay_carriage.py',
        'library/tools/plan_provenance.py',
        'library/tools/project_file_lock.py',
        'library/tools/qa/subtitle_qa.py',
        'library/tools/qualification_project.py',
        'library/tools/reel_address.py',
        'library/tools/reel_disabled_clip_carry.py',
        'library/tools/ren_native_cache.py',
        'library/tools/ren_native_source.py',
        'library/tools/render_fonts.py',
        'library/tools/resolve_deadline.py',
        'library/tools/resolve_locale.py',
        'library/tools/resolve_switch_guard.py',
        'library/tools/resolve_transform.py',
        'library/tools/continuity_feasibility.py',
        'library/tools/resolved/cancellation.py',
        'library/tools/resolved/jobs.py',
        'library/tools/resolved/scheduler.py',
        'library/tools/resolved/store.py',
        'library/tools/shadow_watcher.py',
        'library/tools/subtitle_segment_id.py',
        'library/tools/undo_journal.py',
        'library/tools/versions/reel_versions.py',
        'library/tools/versions/rounds.py',
        'library/graphics_ir/fonts.py',
        'library/graphics_ir/lowering.py',
        'library/graphics_ir/serialization.py',
        'library/schemas/brand_template.py',
        'library/tools/captain_edits.py',
        'library/tools/cascade_graph.py',
        'library/tools/composed_edit.py',
        'library/tools/decided_value.py',
        'library/tools/fabric_permissions.py',
        'library/tools/hyperframes_render.py',
        'library/tools/manifest_validator.py',
        'library/tools/marker_carry.py',
        'library/tools/marker_resolution.py',
        'library/tools/patch_algebra.py',
        'library/tools/project_asset.py',
        'library/tools/project_model_qualification.py',
        'library/tools/qualification_workflow.py',
        'library/tools/reel_build.py',
        'library/tools/reel_append_placement.py',
        'library/tools/resolve_temporal.py',
        'library/tools/reel_conformance_verifier.py',
        'library/tools/reel_edit_history.py',
        'library/tools/reel_incremental.py',
        'library/tools/reel_proposal.py',
        'library/tools/reel_retirement.py',
        'library/tools/reel_semantic_visual.py',
        'library/tools/reel_state.py',
        'library/tools/reel_touchup.py',
        'library/tools/render_cache.py',
        'library/tools/render_qa.py',
        'library/tools/resolve_axi_core_commands.py',
        'library/tools/resolve_axi.py',
        'library/tools/resolve_axi_core.py',
        'library/tools/resolve_scratch_projects.py',
        'library/tools/resolve_bin_layout.py',
        'library/tools/resolved/drt_export_probe.py',
        'library/tools/resolved/drt_recovery_probe.py',
        'library/tools/resolved/marker_loss_probe.py',
        'library/tools/resolved/reel_ending_copy.py',
        'library/tools/resolved/reel_promotion.py',
        'library/tools/resolved/reel_promotion_fixture.py',
        'library/tools/resolved/ui_transaction.py',
        'library/tools/safe_area.py',
        'library/tools/still_placement.py',
        'library/tools/subtitle_coverage.py',
        'library/tools/subtitle_splice.py',
        'library/tools/timeline_diff.py',
        'library/tools/transcript_duration_anomaly.py',
        'library/tools/versions/store.py',
        'library/skills/verify_render/skill.py',
        'library/steps/step_4_05_render_subtitles/generate_remotion_props.py',
        'library/tools/awaiting_model_answers.py',
        'library/tools/bookends.py',
        'library/tools/build_sweep.py',
        'library/tools/camera_sync.py',
        'library/tools/caption_asset_gc.py',
        'library/tools/caption_band.py',
        'library/tools/caption_swap.py',
        'library/tools/caption_timing.py',
        'library/tools/closer_fit.py',
        'library/tools/comparison_retirement.py',
        'library/tools/declaration_keys.py',
        'library/tools/dirty_regions.py',
        'library/tools/display_respell.py',
        'library/tools/do_not_draw.py',
        'library/tools/draw_gain_probe.py',
        'library/tools/drift_check.py',
        'library/tools/edit_ledger.py',
        'library/tools/editor_edit_carry.py',
        'library/tools/execution/fusion_tracks.py',
        'library/tools/execution/organise_media_pool.py',
        'library/tools/execution/resolve_render.py',
        'library/tools/explainer_plan.py',
        'library/tools/external_inputs.py',
        'library/tools/feedback_outcomes.py',
        'library/tools/feedback_patch_apply.py',
        'library/tools/filmcraft_patches.py',
        'library/tools/frame_utils.py',
        'library/tools/full_frame_element.py',
        'library/tools/fusion/comp_builder.py',
        'library/tools/fusion/effects.py',
        'library/tools/fusion/parser.py',
        'library/tools/intellisearch_collect.py',
        'library/tools/layer_coherence.py',
        'library/tools/llm_handshake.py',
        'library/tools/manual_edit_intent.py',
        'library/tools/marker_gate.py',
        'library/tools/marker_routing.py',
        'library/tools/master_loudness.py',
        'library/tools/mg_tight_box.py',
        'library/tools/motion_graphics_plan.py',
        'library/tools/motion_graphics_vocabulary.py',
        'library/tools/music_measurement.py',
        'library/tools/music_over_speech.py',
        'library/tools/native_effects_composite.py',
        'library/tools/native_mcp.py',
        'library/tools/native_ops_apply.py',
        'library/tools/otio_mix.py',
        'library/tools/output_contract.py',
        'library/tools/overlay_draw_intent.py',
        'library/tools/overlay_intent.py',
        'library/tools/overlay_placement.py',
        'library/tools/overlay_verify.py',
        'library/tools/plan_creative_quality.py',
        'library/tools/plan_splice.py',
        'library/tools/pool_stream_meta.py',
        'library/tools/project_model_effectcraft.py',
        'library/tools/project_model_effectcraft_moves.py',
        'library/tools/proof_scope.py',
        'library/tools/reel_angle_plan.py',
        'library/tools/reel_clock.py',
        'library/tools/reel_cta_treatment.py',
        'library/tools/reel_divergence.py',
        'library/tools/reel_ending.py',
        'library/tools/reel_exchange.py',
        'library/tools/reel_framing.py',
        'library/tools/reel_fusion_comps.py',
        'library/tools/reel_ledger.py',
        'library/tools/reel_look.py',
        'library/tools/reel_opening.py',
        'library/tools/reel_otio_placement.py',
        'library/tools/reel_phase_log.py',
        'library/tools/reel_placed_assets.py',
        'library/tools/reel_post_header.py',
        'library/tools/reel_prebuild_census.py',
        'library/tools/reel_quality_bar.py',
        'library/tools/reel_rebuild_need.py',
        'library/tools/reel_signoff.py',
        'library/tools/reel_spine.py',
        'library/tools/reel_thesis.py',
        'library/tools/reference_style.py',
        'library/tools/remotion_brand_linker.py',
        'library/tools/render_watch.py',
        'library/tools/resolve_api_capabilities.py',
        'library/tools/resolve_organization.py',
        'library/tools/resolve_script_store.py',
        'library/tools/retake_scan.py',
        'library/tools/semantic_visual.py',
        'library/tools/source_transcript.py',
        'library/tools/speaker_identity.py',
        'library/tools/speech_loudness.py',
        'library/tools/staging_holds.py',
        'library/tools/still_overlays.py',
        'library/tools/subject_grade.py',
        'library/tools/tail_extend_authorization.py',
        'library/tools/taste_profile.py',
        'library/tools/timeline_conformance.py',
        'library/tools/timeline_layout.py',
        'library/tools/timeline_serializer.py',
        'library/tools/toon_serializer.py',
        'library/tools/transition_overlay.py',
        'library/tools/transition_vocabulary.py',
        'library/tools/tv_frame.py',
        'library/tools/tv_power.py',
        'library/tools/undetermined.py',
        'library/tools/versions/checkpoints.py',
        'library/tools/versions/variants.py',
        'library/tools/video_prefs.py',
        'library/capabilities/temporal/index.py',
        'library/tools/motion_evidence.py',
        'library/tools/analysis/music_pipeline.py',
        'library/tools/analysis/source_separation.py',
        'library/tools/analysis/ocr_extractor.py',
        'library/tools/beat_grid.py',
        'library/tools/brand_library.py',
        'library/tools/broll_correspondence.py',
        'library/tools/caption_correction.py',
        'library/tools/color_page_grade.py',
        'library/tools/creative_tasks.py',
        'library/tools/dialogue_cleanup.py',
        'library/tools/direction_contradiction.py',
        'library/tools/edit_depth.py',
        'library/tools/edit_operations.py',
        'library/tools/edit_spec.py',
        'library/tools/execution/prune_orphans.py',
        'library/tools/execution/retire_empty_bins.py',
        'library/tools/file_measurements.py',
        'library/tools/fusion/played_window.py',
        'library/tools/input_contract.py',
        'library/tools/intent_model.py',
        'library/tools/jl_cut.py',
        'library/tools/journal_naming.py',
        'library/tools/mix_intent.py',
        'library/tools/music_section.py',
        'library/tools/native_ops.py',
        'library/tools/otio_compile.py',
        'library/tools/overlay_keep_clear.py',
        'library/tools/person_matte.py',
        'library/tools/pipeline_skills.py',
        'library/tools/placed_assets.py',
        'library/tools/processes.py',
        'library/tools/project_model_effectcraft_titles.py',
        'library/tools/reel_hearing.py',
        'library/tools/reel_layer_links.py',
        'library/tools/remotion_batch.py',
        'library/tools/render_capability_index.py',
        'library/tools/requirements.py',
        'library/tools/review_panel.py',
        'library/tools/row_shift.py',
        'library/tools/run_scope.py',
        'library/tools/series_look.py',
        'library/tools/still_content.py',
        'library/tools/timed_text_overlay.py',
        'library/tools/timeline_duration.py',
        'library/tools/transform_drift.py',
        'library/tools/uncarried_notes.py',
        'library/tools/versions/edit_variants.py',
        'library/tools/window_frames.py',
        'library/processes/edit_video/run_pipeline.py',
        'library/tools/capability_plan.py',
        'library/tools/project_model_narrative.py',
        'library/capabilities/temporal/media_decode.py',
        'library/tools/analysis/sound_event_pipeline.py',
        'library/tools/audio_chain.py',
        'library/tools/audio_classification.py',
        'library/tools/audio_effects.py',
        'library/tools/brand_motion.py',
        'library/tools/context_projector.py',
        'library/tools/context_views.py',
        'library/tools/correction_loop.py',
        'library/tools/craft_role.py',
        'library/tools/creative_floors.py',
        'library/tools/cutaway_window.py',
        'library/tools/edit_graph.py',
        'library/tools/hearing_settings.py',
        'library/tools/hooks.py',
        'library/tools/model_task.py',
        # The cost of a model answer, reached through model_task: a
        # fix to the costing changes what a step's own identity is.
        'library/tools/model_cost.py',
        # The count a model answer is priced in, reached through
        # model_task: the tokenizer that produced the figure is part
        # of what the step measured.
        'library/tools/token_measure.py',
        # The delivery and overlay code the catalog's executor
        # closure reaches: a fix to any of them changes what the
        # step's cached rows resolve through.
        'library/tools/delivery_identity.py',
        'library/tools/overlay_media.py',
        'library/tools/reel_export.py',
        # The caption rewrite the catalog's closure reaches: a fix to
        # how a caption is redrawn changes what the step measured.
        'library/tools/caption_rewrite.py',
        'library/tools/touchup_phases.py',
        # The VLM progress the semantic analysis records while it
        # waits: a fix to it changes what that step measured.
        'library/tools/vision_progress.py',
        # The broker session the catalog's executor closure holds
        # across a multi-call Resolve operation: a fix to it changes
        # what the step's cached rows resolve through.
        'library/tools/resolve_session.py',
        'library/tools/music_bed.py',
        'library/tools/orphan_removal.py',
        'library/tools/qa_findings.py',
        'library/tools/regional_motion.py',
        'library/tools/room_tone.py',
        'library/tools/semantic_index.py',
        'library/tools/sfx_library.py',
        'library/tools/sub_block_anchor.py',
        'library/dashboard/review_channel.py',
        'library/tools/alignment_findings.py',
        'library/tools/breakpoints.py',
        'library/tools/brief_snapshot.py',
        'library/tools/briefing_interview.py',
        'library/tools/completion_notice.py',
        'library/tools/cut_verdicts.py',
        'library/tools/display_drift.py',
        'library/tools/edit_input_digest.py',
        'library/tools/editorial_types.py',
        'library/tools/empty_table_guard.py',
        'library/tools/intent_receipt.py',
        'library/tools/music_sections.py',
        'library/tools/pipeline_logger.py',
        'library/tools/planning_reuse.py',
        'library/tools/project_context.py',
        'library/tools/prosody_profile.py',
        'library/tools/qa_feedback_loop.py',
        'library/tools/render_review.py',
        'library/tools/review_gate.py',
        'library/tools/run_profile.py',
        'library/tools/run_traceback.py',
        'library/tools/sound_events.py',
        'library/tools/template_loader.py',
        'library/tools/versions/runs.py',
        'library/tools/nothing_to_decide.py',
        'library/tools/post_bridge_retry.py',
        'library/tools/scoped_intent.py',
        'library/tools/second_pass.py',
        'library/tools/timeline_qa.py',
        'library/tools/visual_qa_prompts.py',
        'library/tools/visual_qa_router.py',
        'library/tools/perceptual_qa.py',
        'library/tools/qa_fidelity.py',
        'library/tools/reference_collection.py',
        'library/tools/segment_renderer.py',
        'library/tools/video_segment_analyzer.py',
        'library/tools/gate_stills.py',
        'library/tools/qa/asset_qa.py',
        'library/tools/qa/timeline_sync_qa.py',
        'library/tools/treatment_verify.py',
        'library/tools/fusion/transition_frames.py',
        # Imported by the declared input/output contract surveys: the
        # session-local AST parse cache this audit split reads through,
        # so a fix here changes what the declared surveys parse.
        'library/tools/ast_cache.py',
        # Imported by the declared temporal-index step: the FI-14
        # camera/subject evidence reader, so a fix here changes what
        # the cached rows resolve through it.
        'library/tools/motion_evidence.py',
        'library/tools/accepted_edit_replay.py',
        'library/tools/creative_revision.py',
        'library/tools/direct_transaction.py',
        'library/tools/hold_window_repair.py',
        'library/tools/reel_component_manifest.py',
        'library/tools/structural_patch.py',
        'library/tools/versions/reproducibility.py',
        # Transitive closure: edit_algebra imports axi_qualification and
        # retention imports storage_lifecycle, so a fix to either shared
        # module changes what the cached semantic-analysis rows resolve through.
        'library/tools/axi_qualification.py',
        'library/tools/storage_lifecycle.py',
        "library/tools/artifact_policy.py",
        "library/tools/axi_transaction.py",
        "library/tools/candidate_treatments.py",
        "library/tools/edit_recovery.py",
        "library/tools/editorial_identity.py",
        "library/tools/recovery_instruction.py",
        "library/tools/reel_execution_contract.py",
        "library/tools/sound_class.py",
        "library/tools/target_resolution.py",
        "library/tools/timeline_state_reconcile.py",
        "library/tools/treatment_rulings.py",
        "library/tools/editorial_decisions.py",
        "library/tools/model_stamps.py",
        "library/tools/editorial_intent.py",
        # The shared readers the declared closure reaches in one hop: the
        # approved-decision evidence (cache_lifecycle), the DRT archive
        # reader (resolve_scratch_projects), the timeline state report
        # (timeline_shadow) and the cross-project graph (source_memory).
        # A fix to any of them changes what the cached rows say, so it
        # must invalidate the cache.
        "library/tools/decision_evidence.py",
        "library/tools/drt_reader.py",
        "library/tools/edit_state_report.py",
        "library/tools/project_graph/cross_project.py",
        # The reel builder records the acceptance of a delivered generation
        # through this module, so a fix to it changes what the cached
        # rows of this step resolve through (the same chain as the
        # catalog row).
        "library/tools/reel_acceptance.py",
        # The explainer and face-track renderers, the frame-exact
        # measurement, the occurrence identity, the plan feasibility,
        # the project reference and the retrieval requirements: the
        # shared code these steps' cached rows are computed through.
        "library/tools/explainer_render.py",
        "library/tools/face_tracks.py",
        "library/tools/frame_exact.py",
        "library/tools/occurrence_identity.py",
        "library/tools/plan_feasibility.py",
        "library/tools/project_ref.py",
        "library/tools/retrieval/requirements.py",
        # The capability registry the catalog reads its operations from,
        # the write settle the executor waits on, the broker's own path
        # helper and the source clocks the measurements are read through:
        # a fix in any of them changes what these cached rows resolve to.
        "library/tools/capability_coverage.py",
        "library/tools/resolve_write_settle.py",
        "library/tools/resolved/broker_paths.py",
        "library/tools/source_clocks.py",
        # The shared code this step's cached rows are computed
        # through, reached by the closure above (the map's rule:
        # every file the closure reaches is named here).
        "library/tools/evidence_graph.py",
        "library/tools/frame_rate.py",
        "library/tools/mutation_verification.py",
    ),
    "step_1_04_temporal_index": (
        "library/tools/alignment_quality.py",
        "library/tools/axi_qualification_dimensions.py",
        "library/tools/axi_qualification_state.py",
        "library/tools/camera_motion.py",
        "library/tools/capability_guidance.py",
        "library/tools/edit_conflict.py",
        "library/tools/edit_critical_events.py",
        "library/tools/editorial_policy.py",
        "library/tools/observation_provenance.py",
        "library/tools/operation_map.py",
        "library/tools/pause_class.py",
        "library/tools/preservation_invariant.py",
        "library/tools/render_perception.py",
        # The report provenance every live measurement carries: it is
        # reached by this step's dependency closure, so a fix to it is a
        # fix to the step's cached inputs.
        "library/tools/report_provenance.py",
        "library/tools/source_quality.py",
        "library/tools/speech_take.py",
        "library/tools/temporal_events.py",
        "library/tools/temporal_query.py",
        "library/tools/artifact_lifecycle.py",
        "library/tools/cache_lifecycle.py",
        "library/tools/edit_execution_adapters.py",
        "library/tools/edit_state_preservation.py",
        "library/tools/git_guard.py",
        "library/tools/execution/deliver_audio_mix.py",
        "library/tools/execution/remove_proof.py",
        "library/tools/proof_cleanup.py",
        "library/tools/resolve_axi_sdk/__init__.py",
        "library/tools/retention.py",
        "library/tools/safe_zone_guide.py",
        "library/tools/accepted_edit_replay.py",
        "library/tools/creative_revision.py",
        "library/tools/direct_transaction.py",
        "library/tools/hold_window_repair.py",
        "library/tools/reel_component_manifest.py",
        "library/tools/structural_patch.py",
        "library/tools/versions/reproducibility.py",
        "library/tools/transaction_receipt.py",
        "library/tools/operation_acceptance.py",
        # VC-12/VC-07/VC-08 (PRs 2714/2721/2723): the shared edit-request
        # and transaction orchestration closure reached through
        # resolve_axi_core and edit_patch, so a fix here changes what
        # cached preflight rows resolve through.
        "library/tools/transaction_orchestration.py",
        "library/tools/edit_request.py",
        "library/tools/edit_preparation.py",
        "library/tools/timeline_addressing.py",
        # The legacy step delegates indexing here; code changes alter
        # cached indexes. The shared FFmpeg graph that supplies the
        # per-clip visual measurements moved with it.
        "library/capabilities/temporal/index.py",
        # Its cached dependency closure includes the shared retrieval encoder.
        "library/tools/analysis/mlx_minilm.py",
        "library/capabilities/temporal/media_decode.py",
        # FI-14 camera/subject and novelty evidence derived from M2.
        "library/tools/motion_evidence.py",
        # Version-store closure verifies retained timeline DRT contents.
        "library/tools/resolve_archive.py",
        # Deterministic shot hierarchy and evidence-linked profiles.
        "library/tools/scene_cinematography.py",
        # The project's declared transcription language: a fix here
        # changes which language every cached index is heard in.
        "library/tools/footage_identity.py",
        # Imported by footage_identity: the declaration parsing behind
        # the language and footage-root reads, so a schema fix changes
        # what the step accepts.
        "library/schemas/project_config.py",
        # Imported by project_config: the delivery-platform registry
        # pipeline.delivery_platform validates against (same chain).
        "library/tools/delivery_spec.py",
        # Imported by project_config: the `$HOME` / `$PROJECT` token
        # expansion path declarations are read through (same chain).
        "library/tools/portable_paths.py",
        # Imported by project_config: the project_format_version key
        # and the supported format range (same chain as the scan row).
        "library/tools/project_format.py",
        # Imported by project_config: the subtitle_overlay_geometry /
        # subtitle_overlay_container vocabularies the declaration
        # parsing validates against (same chain as the scan entry).
        "library/tools/overlay_mode.py",
        # Imported by project_config: the graphics_renderer vocabulary
        # the declaration parsing validates against (same chain as the
        # overlay_mode entry above).
        "library/tools/graphics_renderer.py",
        # Imported by project_config: the pipeline.safe_zones policy
        # the declaration parsing validates against (same chain).
        "library/tools/safe_zone_policy.py",
        # Imported by safe_zone_policy: the platform zones a policy
        # names and is checked against.
        "library/tools/platform_safe_zones.py",
        # Face measurement feeding the per-clip index.
        "library/tools/subject_framing.py",
        # Imported by subject_framing for the reel punch-in's body-pose
        # aim. Step 1.04 runs no Vision pass of its own (MU-12); the
        # file is declared only because the import graph reaches it.
        "library/tools/vision_measure.py",
        # Span extraction feeding region re-measurement.
        "library/tools/timeline_transcript.py",
        "library/tools/overlapped_speech.py",
        # Lane B: the transcript above now mints utterance Observation
        # ids through footage_model_link, so a fix to the link changes
        # what the cached segments resolve into.
        "library/tools/footage_model_link.py",
        # Imported by footage_model_link: the migration and the stores
        # behind the link.
        "library/tools/project_model.py",
        # Imported by project_model: the typed certainty/confidence
        # and coverage validation every model write is checked
        # against, so a fix here changes what cached rows validate.
        "library/tools/project_model_contract.py",
        "library/tools/project_model_evidence.py",
        # Imported by Project Model migration for derivation and typed scores.
        "library/tools/evidence_lifecycle.py",
        "library/tools/temporal_action_candidates.py",
        # Lane 3 migration adds identity and physical relation records.
        "library/tools/identity_reassociation.py",
        "library/tools/relation_classes.py",
        "library/tools/analysis/open_vocabulary_detect.py",
        "library/tools/analyzer_providers.py",
        "library/tools/query_directed_acquisition.py",
        "library/tools/evidence_query.py",
        "library/tools/ocr_project_model.py",
        "library/tools/evidence_contract.py",
        "library/tools/cross_modal_grounding.py",
        # Imported by project_model_evidence for native visual measurements;
        # changes to these readers alter the evidence and must invalidate it.
        "library/tools/attention_saliency.py",
        "library/tools/expression_classifier.py",
        "library/tools/footage_analysis.py",
        "library/tools/person_measurements.py",
        "library/tools/project_model_store.py",
        "library/tools/protocol_conformance.py",
        # Imported by timeline_transcript: project words bias the
        # transcription and surviving corrections are applied at the
        # transcript root, so a fix here changes the cached segments.
        "library/tools/transcript_corrections.py",
        # Imported by transcript_corrections: surviving corrections are
        # recorded as learnings, so a fix here changes what the cached
        # transcript carries. (Its own imports are exempt plumbing.)
        "library/tools/learned_context.py",
        # Imported by timeline_transcript: one-audio-path timelines are
        # diarized here before transcription, so a fix here changes
        # which speaker and voice embedding the cached segments carry.
        "library/tools/single_track_diarization.py",
        "library/tools/model_registry.py",
        "library/tools/evidence_license.py",
        # The executed sound-event measurement: PANNs AudioSet spans
        # per clip, so a fix here changes the cached sound_events.
        "library/tools/analysis/sound_event_pipeline.py",
        # Imported by step.py's measure_audio_classification: the
        # cross-clip speech level, music genre and ambience
        # classifications that land in the cached per-clip index, so
        # a fix here changes what the index measured.
        "library/tools/audio_classification.py",
        # The confidence vocabulary carried in the cached segments
        # (avg_logprob): the transcription path keys its numbers here.
        "library/tools/transcript_confidence.py",
        # Imported by step.py beside its only use: normalizes the face
        # box that reaches the seeder, so a fix here changes the cached
        # face_boxes.
        "library/tools/analysis/object_segmentation.py",
        # Object segmentation imports the resident SAM model lifecycle.
        'library/tools/model_lifecycle.py',
        # Reads the versioned semantic records and their readiness status
        # before regional motion is composed into each cached index.
        "library/tools/semantic_profile_stream.py",
        "library/tools/run_control.py",
        "library/tools/run_restart.py",
        "library/tools/stable_json.py",
        # Imported by timeline_transcript: the hybrid transcriber is the
        # primary arm that hears each clip, so a fix here changes the
        # cached segments.
        "library/tools/hybrid_transcription.py",
        # Imported by step.py: the canonical transcript measurement
        # serves samples already heard and stores each batched hearing,
        # so a fix here changes the cached segments.
        "library/tools/transcript_measurement.py",
        # Imported by transcript_measurement: the memory root the
        # stored hearings live under, so a fix here changes which
        # hearings the cached segments are served from.
        "library/tools/source_memory.py",
        "library/tools/speech_measurements.py",
        # Imported by step.py: a single-stream source's 16 kHz audio is
        # the source primitive's canonical WAV, so a fix here changes
        # the samples every cached measurement was taken from.
        "library/tools/source_primitives.py",
        # Imported by timeline_transcript beside the hybrid arm: forced
        # alignment where its environment is present, so a fix here
        # moves the cached word timings.
        "library/tools/mfa_align.py",
        # Imported by mfa_align: the shared caption acronym vocabulary
        # determines the words forced alignment hears, so a fix here
        # changes the cached word timings.
        "library/tools/caption_reading.py",
        # Imported by hybrid_transcription: the contained third-party
        # transcriber behind the hybrid arm, so a fix here changes what
        # the cached segments heard.
        "library/tools/heard_speech.py",
        # Imported by mfa_align: which interpreter carries the ML stack
        # decides what the alignment hears, so a fix here changes the
        # cached timings. (Its paths import is exempt plumbing.)
        "library/tools/shared_environment.py",
        # Imported by timeline_transcript: the one word-boundary clamp
        # both paths run, so a fix here moves the cached word ends.
        "library/tools/word_boundaries.py",
        # Imported by timeline_transcript: the row-fit predicate counts
        # the unfitted-text keys onto the cached document, so a fix here
        # changes them. (The per-reel rows_played half lives in
        # reel_hearing beside the Spans it reads, so this module reaches
        # no hearing machinery - pinned by
        # test_the_document_half_reaches_no_hearing_machinery.)
        "library/tools/transcript_fit.py",
        # Selected-span Farneback regions and their fused framing evidence.
        "library/tools/regional_motion.py",
        # Gemma action documents are joined to catalog clip ids before the
        # selected spans are measured. This import is new to temporal_index.
        "library/tools/semantic_index.py",
        # semantic_index imports the vision adapter, whose segment coverage
        # summary is part of the same semantic-document read path.
        "library/tools/vision_schema_adapter.py",
        "library/tools/segment_coverage.py",
        # Imported by build_temporal_index: the per-clip lanes run on
        # these bounded FREE lanes, so a fix here changes how many
        # clips measure at once (and its transitive resource_scheduler
        # read changes what the lanes admit).
        "library/tools/concurrency_routing.py",
        # Imported by concurrency_routing: the lane bound is this
        # machine's measured core count, so a fix here changes the
        # parallelism the cached index was measured under. (Its own
        # capabilities read is exempt plumbing.)
        "library/tools/resource_scheduler.py",
        'library/tools/analysis/footage_frames.py',
        'library/tools/analysis/footage_query.py',
        'library/tools/analysis/ocr_combined_query.py',
        'library/tools/conversation_clock.py',
        'library/tools/depth.py',
        # The adopted DA3 backend depth.py selects behind the M3g protocol.
        'library/tools/depth_da3.py',
        'library/tools/event_spans.py',
        'library/tools/footage_intelligence.py',
        'library/tools/analysis_state.py',
        # Analysis composition resolves from capability-owned declarations,
        # so a fix to the contracts changes the order preflight runs in.
        'library/tools/capability_contracts.py',
        'library/tools/heavy_work_lock.py',
        'library/tools/person_entity.py',
        'library/tools/person_regions.py',
        'library/tools/project_registry.py',
        'library/tools/region_stats.py',
        'library/tools/relationships.py',
        'library/tools/world_model.py',
        'library/tools/analysis/footage_segments.py',
        'library/tools/analysis/picture_quality.py',
        'library/tools/analysis/unified_anchors.py',
        'library/tools/brief_attachment.py',
        'library/tools/marker_capture.py',
        'library/tools/mask_seed.py',
        'library/tools/progress.py',
        'library/tools/project_model_masks.py',
        'library/tools/provenance.py',
        'library/tools/region_query.py',
        'library/tools/series_registry.py',
        'library/tools/shot_colour.py',
        'library/tools/span_verification.py',
        'library/tools/step_ledger.py',
        'library/tools/brief_reference.py',
        'library/tools/edit_algebra.py',
        'library/tools/reel_read.py',
        'library/tools/region.py',
        'library/tools/resolve_lock.py',
        'library/tools/resolved/client.py',
        'library/tools/scope.py',
        'library/tools/step_exporter.py',
        'library/tools/vision_model.py',
        'library/tools/cdl_readback.py',
        'library/tools/comp_media_window.py',
        'library/tools/computer_use/client.py',
        'library/tools/computer_use/scripts.py',
        'library/tools/creative_direction.py',
        'library/tools/edit_backend.py',
        'library/tools/event_log.py',
        'library/tools/fusion/engine.py',
        'library/tools/fusion/nodes.py',
        'library/tools/magic_mask.py',
        'library/tools/marker_feedback.py',
        'library/tools/native_refresh.py',
        'library/tools/reel_replace_guard.py',
        'library/tools/resolve_swapback.py',
        'library/tools/spine_contract.py',
        'library/tools/tight_box.py',
        'library/tools/timeline_decisions.py',
        'library/tools/timeline_read.py',
        'library/tools/timeline_shadow.py',
        'library/tools/transform_write_log.py',
        'library/tools/trim_qualification.py',
        'library/steps/step_4_01_plan_subtitles/step.py',
        'library/tools/brand_palette.py',
        'library/tools/computer_use/actions.py',
        'library/tools/computer_use/observe.py',
        'library/tools/computer_use/take.py',
        'library/tools/computer_use/templates.py',
        'library/tools/edit_patch.py',
        'library/tools/project_model_editorial.py',
        'library/tools/edit_point_planner.py',
        'library/tools/energy_reading.py',
        'library/tools/feedback_ledger.py',
        'library/tools/fframes_render.py',
        'library/tools/marker_payload.py',
        'library/tools/music_behavior.py',
        'library/tools/native_part_lock.py',
        'library/tools/overlay_carriage.py',
        'library/tools/plan_provenance.py',
        'library/tools/project_file_lock.py',
        'library/tools/qa/subtitle_qa.py',
        'library/tools/qualification_project.py',
        'library/tools/reel_address.py',
        'library/tools/reel_disabled_clip_carry.py',
        'library/tools/ren_native_cache.py',
        'library/tools/ren_native_source.py',
        'library/tools/render_fonts.py',
        'library/tools/resolve_deadline.py',
        'library/tools/resolve_locale.py',
        'library/tools/resolve_switch_guard.py',
        'library/tools/resolve_transform.py',
        'library/tools/continuity_feasibility.py',
        'library/tools/resolved/cancellation.py',
        'library/tools/resolved/jobs.py',
        'library/tools/resolved/scheduler.py',
        'library/tools/resolved/store.py',
        'library/tools/shadow_watcher.py',
        'library/tools/subtitle_segment_id.py',
        'library/tools/undo_journal.py',
        'library/tools/versions/reel_versions.py',
        'library/tools/versions/rounds.py',
        'library/graphics_ir/fonts.py',
        'library/graphics_ir/lowering.py',
        'library/graphics_ir/serialization.py',
        'library/schemas/brand_template.py',
        'library/tools/captain_edits.py',
        'library/tools/cascade_graph.py',
        'library/tools/composed_edit.py',
        'library/tools/decided_value.py',
        'library/tools/fabric_permissions.py',
        'library/tools/hyperframes_render.py',
        'library/tools/manifest_validator.py',
        'library/tools/marker_carry.py',
        'library/tools/marker_resolution.py',
        'library/tools/patch_algebra.py',
        'library/tools/project_asset.py',
        'library/tools/project_model_qualification.py',
        'library/tools/qualification_workflow.py',
        'library/tools/reel_build.py',
        'library/tools/reel_append_placement.py',
        'library/tools/resolve_temporal.py',
        'library/tools/reel_conformance_verifier.py',
        'library/tools/reel_edit_history.py',
        'library/tools/reel_incremental.py',
        'library/tools/reel_proposal.py',
        'library/tools/reel_retirement.py',
        'library/tools/reel_semantic_visual.py',
        'library/tools/reel_state.py',
        'library/tools/reel_touchup.py',
        'library/tools/render_cache.py',
        'library/tools/render_qa.py',
        'library/tools/resolve_axi_core_commands.py',
        'library/tools/resolve_axi.py',
        'library/tools/resolve_axi_core.py',
        'library/tools/resolve_scratch_projects.py',
        'library/tools/resolve_bin_layout.py',
        'library/tools/resolved/drt_export_probe.py',
        'library/tools/resolved/drt_recovery_probe.py',
        'library/tools/resolved/marker_loss_probe.py',
        'library/tools/resolved/reel_ending_copy.py',
        'library/tools/resolved/reel_promotion.py',
        'library/tools/resolved/reel_promotion_fixture.py',
        'library/tools/resolved/ui_transaction.py',
        'library/tools/safe_area.py',
        'library/tools/still_placement.py',
        'library/tools/still_vision.py',
        'library/tools/subtitle_coverage.py',
        'library/tools/subtitle_splice.py',
        'library/tools/timeline_diff.py',
        'library/tools/transcript_duration_anomaly.py',
        'library/tools/versions/store.py',
        'library/skills/verify_render/skill.py',
        'library/steps/step_4_05_render_subtitles/generate_remotion_props.py',
        'library/tools/awaiting_model_answers.py',
        'library/tools/bookends.py',
        'library/tools/build_sweep.py',
        'library/tools/camera_sync.py',
        'library/tools/caption_asset_gc.py',
        'library/tools/caption_band.py',
        'library/tools/caption_swap.py',
        'library/tools/caption_timing.py',
        'library/tools/closer_fit.py',
        'library/tools/comparison_retirement.py',
        'library/tools/declaration_keys.py',
        'library/tools/dirty_regions.py',
        'library/tools/display_respell.py',
        'library/tools/do_not_draw.py',
        'library/tools/draw_gain_probe.py',
        'library/tools/drift_check.py',
        'library/tools/edit_ledger.py',
        'library/tools/editor_edit_carry.py',
        'library/tools/execution/fusion_tracks.py',
        'library/tools/execution/organise_media_pool.py',
        'library/tools/execution/resolve_render.py',
        'library/tools/explainer_plan.py',
        'library/tools/external_inputs.py',
        'library/tools/feedback_outcomes.py',
        'library/tools/feedback_patch_apply.py',
        'library/tools/filmcraft_patches.py',
        'library/tools/frame_utils.py',
        'library/tools/full_frame_element.py',
        'library/tools/fusion/comp_builder.py',
        'library/tools/fusion/effects.py',
        'library/tools/fusion/parser.py',
        'library/tools/intellisearch_collect.py',
        'library/tools/layer_coherence.py',
        'library/tools/llm_handshake.py',
        'library/tools/manual_edit_intent.py',
        'library/tools/marker_gate.py',
        'library/tools/marker_routing.py',
        'library/tools/master_loudness.py',
        'library/tools/mg_tight_box.py',
        'library/tools/motion_graphics_plan.py',
        'library/tools/motion_graphics_vocabulary.py',
        'library/tools/music_measurement.py',
        'library/tools/music_over_speech.py',
        'library/tools/native_effects_composite.py',
        'library/tools/native_mcp.py',
        'library/tools/native_ops_apply.py',
        'library/tools/otio_mix.py',
        'library/tools/output_contract.py',
        'library/tools/overlay_draw_intent.py',
        'library/tools/overlay_intent.py',
        'library/tools/overlay_placement.py',
        'library/tools/overlay_verify.py',
        'library/tools/plan_creative_quality.py',
        'library/tools/plan_splice.py',
        'library/tools/pool_stream_meta.py',
        'library/tools/project_model_effectcraft.py',
        'library/tools/project_model_effectcraft_moves.py',
        'library/tools/proof_scope.py',
        'library/tools/reel_angle_plan.py',
        'library/tools/reel_clock.py',
        'library/tools/reel_cta_treatment.py',
        'library/tools/reel_divergence.py',
        'library/tools/reel_ending.py',
        'library/tools/reel_exchange.py',
        'library/tools/reel_framing.py',
        'library/tools/reel_fusion_comps.py',
        'library/tools/reel_ledger.py',
        'library/tools/reel_look.py',
        'library/tools/reel_opening.py',
        'library/tools/reel_otio_placement.py',
        'library/tools/reel_phase_log.py',
        'library/tools/reel_placed_assets.py',
        'library/tools/reel_post_header.py',
        'library/tools/reel_prebuild_census.py',
        'library/tools/reel_quality_bar.py',
        'library/tools/reel_rebuild_need.py',
        'library/tools/reel_signoff.py',
        'library/tools/reel_spine.py',
        'library/tools/reel_thesis.py',
        'library/tools/reference_style.py',
        'library/tools/remotion_brand_linker.py',
        'library/tools/render_watch.py',
        'library/tools/resolve_api_capabilities.py',
        'library/tools/resolve_organization.py',
        'library/tools/resolve_script_store.py',
        'library/tools/retake_scan.py',
        'library/tools/semantic_visual.py',
        'library/tools/source_transcript.py',
        'library/tools/speaker_identity.py',
        'library/tools/speech_loudness.py',
        'library/tools/staging_holds.py',
        'library/tools/still_overlays.py',
        'library/tools/subject_grade.py',
        'library/tools/tail_extend_authorization.py',
        'library/tools/taste_profile.py',
        'library/tools/timeline_conformance.py',
        'library/tools/timeline_layout.py',
        'library/tools/timeline_serializer.py',
        'library/tools/toon_serializer.py',
        'library/tools/transition_overlay.py',
        'library/tools/transition_vocabulary.py',
        'library/tools/tv_frame.py',
        'library/tools/tv_power.py',
        'library/tools/undetermined.py',
        'library/tools/versions/checkpoints.py',
        'library/tools/versions/variants.py',
        'library/tools/video_prefs.py',
        'library/tools/analysis/music_pipeline.py',
        'library/tools/analysis/source_separation.py',
        'library/tools/analysis/ocr_extractor.py',
        'library/tools/beat_grid.py',
        'library/tools/brand_library.py',
        'library/tools/broll_correspondence.py',
        'library/tools/caption_correction.py',
        'library/tools/color_page_grade.py',
        'library/tools/creative_tasks.py',
        'library/tools/dialogue_cleanup.py',
        'library/tools/direction_contradiction.py',
        'library/tools/edit_depth.py',
        'library/tools/edit_operations.py',
        'library/tools/edit_spec.py',
        'library/tools/execution/prune_orphans.py',
        'library/tools/execution/retire_empty_bins.py',
        'library/tools/file_measurements.py',
        'library/tools/fusion/played_window.py',
        'library/tools/input_contract.py',
        'library/tools/intent_model.py',
        'library/tools/jl_cut.py',
        'library/tools/journal_naming.py',
        'library/tools/mix_intent.py',
        'library/tools/music_section.py',
        'library/tools/native_ops.py',
        'library/tools/otio_compile.py',
        'library/tools/overlay_keep_clear.py',
        'library/tools/person_matte.py',
        'library/tools/pipeline_skills.py',
        'library/tools/placed_assets.py',
        'library/tools/processes.py',
        'library/tools/project_model_effectcraft_titles.py',
        'library/tools/reel_hearing.py',
        'library/tools/reel_layer_links.py',
        'library/tools/remotion_batch.py',
        'library/tools/render_capability_index.py',
        'library/tools/requirements.py',
        'library/tools/review_panel.py',
        'library/tools/row_shift.py',
        'library/tools/run_scope.py',
        'library/tools/series_look.py',
        'library/tools/still_content.py',
        'library/tools/timed_text_overlay.py',
        'library/tools/timeline_duration.py',
        'library/tools/transform_drift.py',
        'library/tools/uncarried_notes.py',
        'library/tools/versions/edit_variants.py',
        'library/tools/window_frames.py',
        'library/processes/edit_video/run_pipeline.py',
        'library/tools/capability_plan.py',
        'library/tools/project_model_narrative.py',
        'library/tools/audio_chain.py',
        'library/tools/audio_effects.py',
        'library/tools/brand_motion.py',
        'library/tools/context_projector.py',
        'library/tools/context_views.py',
        'library/tools/correction_loop.py',
        'library/tools/craft_role.py',
        'library/tools/creative_floors.py',
        'library/tools/cutaway_window.py',
        'library/tools/edit_graph.py',
        'library/tools/hearing_settings.py',
        'library/tools/hooks.py',
        'library/tools/model_task.py',
        # The cost of a model answer, reached through model_task: a
        # fix to the costing changes what a step's own identity is.
        'library/tools/model_cost.py',
        # The count a model answer is priced in, reached through
        # model_task: the tokenizer that produced the figure is part
        # of what the step measured.
        'library/tools/token_measure.py',
        # The delivery and overlay code the catalog's executor
        # closure reaches: a fix to any of them changes what the
        # step's cached rows resolve through.
        'library/tools/delivery_identity.py',
        'library/tools/overlay_media.py',
        'library/tools/reel_export.py',
        # The caption rewrite the catalog's closure reaches: a fix to
        # how a caption is redrawn changes what the step measured.
        'library/tools/caption_rewrite.py',
        'library/tools/touchup_phases.py',
        # The broker session the catalog's executor closure holds
        # across a multi-call Resolve operation: a fix to it changes
        # what the step's cached rows resolve through.
        'library/tools/resolve_session.py',
        'library/tools/music_bed.py',
        'library/tools/orphan_removal.py',
        'library/tools/qa_findings.py',
        'library/tools/room_tone.py',
        'library/tools/sfx_library.py',
        'library/tools/sub_block_anchor.py',
        'library/dashboard/review_channel.py',
        'library/tools/alignment_findings.py',
        'library/tools/breakpoints.py',
        'library/tools/brief_snapshot.py',
        'library/tools/briefing_interview.py',
        'library/tools/camera_stability.py',
        'library/tools/completion_notice.py',
        'library/tools/cut_verdicts.py',
        'library/tools/display_drift.py',
        'library/tools/edit_input_digest.py',
        'library/tools/editorial_types.py',
        'library/tools/empty_table_guard.py',
        'library/tools/intent_receipt.py',
        'library/tools/music_sections.py',
        'library/tools/pipeline_logger.py',
        'library/tools/planning_reuse.py',
        'library/tools/project_context.py',
        'library/tools/prosody_profile.py',
        'library/tools/qa_feedback_loop.py',
        'library/tools/render_review.py',
        'library/tools/review_gate.py',
        'library/tools/run_profile.py',
        'library/tools/run_traceback.py',
        'library/tools/sound_events.py',
        'library/tools/template_loader.py',
        'library/tools/versions/runs.py',
        'library/tools/nothing_to_decide.py',
        'library/tools/post_bridge_retry.py',
        'library/tools/scoped_intent.py',
        'library/tools/second_pass.py',
        'library/tools/timeline_qa.py',
        'library/tools/visual_qa_prompts.py',
        'library/tools/visual_qa_router.py',
        'library/tools/perceptual_qa.py',
        'library/tools/qa_fidelity.py',
        'library/tools/reference_collection.py',
        'library/tools/segment_renderer.py',
        'library/tools/video_segment_analyzer.py',
        'library/tools/gate_stills.py',
        'library/tools/qa/asset_qa.py',
        'library/tools/qa/timeline_sync_qa.py',
        'library/tools/treatment_verify.py',
        'library/tools/fusion/transition_frames.py',
        # Imported by the declared input/output contract surveys: the
        # session-local AST parse cache they read through, so a fix
        # here changes what the declared surveys parse.
        'library/tools/ast_cache.py',
        # Transitive closure: edit_algebra imports axi_qualification and
        # retention imports storage_lifecycle, so a fix to either shared
        # module changes what the cached temporal-index rows resolve through.
        'library/tools/axi_qualification.py',
        'library/tools/storage_lifecycle.py',
        "library/tools/artifact_policy.py",
        "library/tools/axi_transaction.py",
        "library/tools/candidate_treatments.py",
        "library/tools/edit_recovery.py",
        "library/tools/editorial_identity.py",
        "library/tools/recovery_instruction.py",
        "library/tools/reel_execution_contract.py",
        "library/tools/sound_class.py",
        "library/tools/target_resolution.py",
        "library/tools/timeline_state_reconcile.py",
        "library/tools/treatment_rulings.py",
        "library/tools/editorial_decisions.py",
        "library/tools/model_stamps.py",
        "library/tools/editorial_intent.py",
        # The shared readers the declared closure reaches in one hop: the
        # approved-decision evidence (cache_lifecycle), the DRT archive
        # reader (resolve_scratch_projects), the timeline state report
        # (timeline_shadow) and the cross-project graph (source_memory).
        # A fix to any of them changes what the cached rows say, so it
        # must invalidate the cache.
        "library/tools/decision_evidence.py",
        "library/tools/drt_reader.py",
        "library/tools/edit_state_report.py",
        "library/tools/project_graph/cross_project.py",
        # The reel builder records the acceptance of a delivered generation
        # through this module, so a fix to it changes what the cached
        # rows of this step resolve through (the same chain as the
        # catalog row).
        "library/tools/reel_acceptance.py",
        # The explainer and face-track renderers, the frame-exact
        # measurement, the occurrence identity, the plan feasibility,
        # the project reference and the retrieval requirements: the
        # shared code these steps' cached rows are computed through.
        "library/tools/explainer_render.py",
        "library/tools/face_tracks.py",
        "library/tools/frame_exact.py",
        "library/tools/occurrence_identity.py",
        "library/tools/plan_feasibility.py",
        "library/tools/project_ref.py",
        "library/tools/retrieval/requirements.py",
        # The capability registry the catalog reads its operations from,
        # the write settle the executor waits on, the broker's own path
        # helper and the source clocks the measurements are read through:
        # a fix in any of them changes what these cached rows resolve to.
        "library/tools/capability_coverage.py",
        "library/tools/resolve_write_settle.py",
        "library/tools/resolved/broker_paths.py",
        "library/tools/source_clocks.py",
        # The shared code this step's cached rows are computed
        # through, reached by the closure above (the map's rule:
        # every file the closure reaches is named here).
        "library/tools/evidence_graph.py",
        "library/tools/frame_rate.py",
        "library/tools/mutation_verification.py",
    ),
    "step_1_05_prosody_analysis": (
        # The legacy step delegates the measurement here.
        "library/capabilities/prosody/analyse.py",
        # The executed measurement.
        "library/tools/analysis/speech_advanced_pipeline.py",
        # Imported by step.py: classifies the cached profile.
        "library/tools/prosody_profile.py",
        # Imported by the pipeline's batch: the per-clip lanes, so a
        # fix here changes how many clips measure at once.
        "library/tools/concurrency_routing.py",
        # Imported by concurrency_routing: the lane bound is this
        # machine's measured core count. (Its capabilities read is
        # exempt plumbing.)
        "library/tools/resource_scheduler.py",
    ),
    "step_1_07_ocr_extraction": (
        # The legacy adapter resolves to this capability implementation.
        "library/capabilities/ocr/extract.py",
        "library/tools/analysis/ocr_extractor.py",
        # Imported by the implementation: the per-clip lanes, so a fix here
        # changes how many clips measure at once.
        "library/tools/concurrency_routing.py",
        # Imported by concurrency_routing: the lane bound is this
        # machine's measured core count. (Its capabilities read is
        # exempt plumbing.)
        "library/tools/resource_scheduler.py",
    ),
    "step_2_06_music_analysis": (
        # The executed measurement: beat grid, tempo, key and sections.
        # This step is EDIT stage, so the ledger's preflight code check
        # never covers it - the step stamps its own cache with this hash
        # instead (finding 8, execution-frontier report 2026-09-24).
        "library/tools/analysis/music_pipeline.py",
        "library/tools/analysis/source_separation.py",
        # Cached step output now includes typed intervals and its source
        # memory sidecar, so both serializers participate in cache identity.
        "library/tools/audio_interval_evidence.py",
        "library/tools/source_memory.py",
    ),
}

# library.tools imports a step may reference WITHOUT declaring them
# above, each with the reason.  Anything else a step references must be
# declared: an undeclared measurement import is D1 again.
EXEMPT_IMPORTS = {
    # Runtime-backend attribution is run metadata only. It neither changes
    # measurements nor their cached outputs.
    "library/tools/runtime_backends.py",
    # Performance instrumentation records execution costs but cannot change
    # the measurement or cached value a preflight step produces.
    "library/tools/perf_ledger.py",
    # Run identity: the one id the runner mints, exports and records
    # across the status file, the provenance runs and the perf rows
    # (Lane A1, R1). Run metadata only, on the same terms as the two
    # entries above - minting and recording an id cannot change a
    # measured footage value, so a fix here must not invalidate a
    # preflight analysis cache.
    "library/tools/run_id.py",
    # Directory plumbing: moving an area does not change a measured value.
    "library/tools/project_layout.py",
    # Project-format migration bookkeeping and refusal rendering. The
    # actual format transform is declared per preflight step; backup,
    # revert and error-display behavior cannot change a measured value.
    "library/tools/project_migration.py",
    "library/tools/ren_refusal.py",
    # Cache-identity plumbing: hashing a step's sources and stamping
    # which code wrote a cache. A fix here can only invalidate good
    # cache (a re-analysis, the safe direction) - never silently bless
    # stale values, which is the failure this map exists to stop.
    # Declaring it instead would drag every file this module NAMES in
    # STEP_IMPLEMENTATION_DEPS into each importing step's hash through
    # the coverage test's transitive scan.
    "library/tools/code_identity.py",
    # Execution policy: resource_scheduler reads capability declarations
    # to admit work, but those declarations change no measured footage
    # result and must not invalidate a preflight analysis cache.
    "library/tools/capabilities.py",
    # Edit routing selects an executor but does not measure footage; it
    # must not invalidate a cached catalog result.
    "library/tools/execution_router.py",
    # The per-evidence model reuse terms: the licence provenance the
    # diarization records, and the gate that reads them.
    "library/tools/model_evidence_provenance.py",
    # The protected experimental routing policy: which unqualified
    # operation may run on a protected staged candidate, and under which
    # guarantees. It selects an executor and measures nothing, on the same
    # terms as the router beside it.
    "library/tools/edit_qualification_policy.py",
    # The operation registry and its DAG-node translator: capability
    # addressing and execution routing.  The preflight measurement
    # chain (vision_pipeline_v3, picture_quality, camera_stability)
    # imports neither, so a fix here changes no measured value - and
    # declaring them would drag the registry's whole import graph into
    # each importing step's hash through the coverage test's transitive
    # scan, the over-invalidation rot EXEMPT_IMPORTS exists to stop.
    "library/tools/operations.py",
    "library/tools/dag_adapter.py",
    # Execution plumbing: the Fabric task record, the single-machine
    # scheduler loop and the lease layer decide where and when a
    # step's units run, never the bytes they measure - and declaring
    # them would drag the broker and manifest probe graphs into each
    # importing step's hash through the coverage test's transitive
    # scan (measured 2026-10-06: 200+ files ending at reel_build
    # itself). The equivalence test in
    # tests/unit/picture/test_m2_scheduled_frames.py pins it: one
    # frame sample's bytes are identical scheduled or inline.
    "library/tools/task_graph.py",
    "library/tools/fabric_scheduler.py",
    "library/tools/resource_leases.py",
    # Centralized path configuration: every entry is a location or an
    # environment override, so a change here fails loud (a binary not
    # found) rather than as a silently stale cached value.  Declaring it
    # instead would re-run transcription over a moved directory - the
    # over-invalidation rot the coverage test exists to stop in the
    # other direction.
    "library/tools/paths.py",
    # Stdout claiming plumbing.
    "library/tools/step_stdout.py",
    # State plumbing: where a recorded output is filed in
    # pipeline_data.json and how a reader finds it.  A fix here moves no
    # measured value; a wrong one fails loud as a missing input.
    "library/tools/capability_outputs.py",
    # Prompt DELIVERY: how a large context table reaches the model (a
    # path and a map of its rows, instead of the rows themselves).  It
    # runs when a step asks a model a question, long after the preflight
    # measurements are frozen, so a fix here can only move what a model
    # is asked - never a measured footage value.  Declaring it instead
    # would drag the serializer and the brief reference into every
    # footage scan's cache key, which is the over-invalidation rot the
    # coverage test exists to stop in the other direction.
    "library/tools/context_reference.py",
    # Project declarations.  step_ledger's rule is that they do NOT travel
    # in a preflight cache, so their code cannot stale one either.
    "library/tools/brand_registry.py",
    # Declaration VALIDATION.  The schema checks a declared delivery
    # format, framing intent or subtitle style when it reads project.yaml.
    # A change here can only move the refuse/accept line for a bad
    # declaration - a refusal surfaces loudly at run time, never as a
    # silently stale cached value for a valid project.  Declaring them
    # would drag brand_palette and safe_area into a footage scan's cache
    # key through their own imports, which is the over-invalidation rot
    # the coverage test exists to stop in the other direction.
    "library/tools/delivery_format.py",
    "library/tools/framing_intent.py",
    "library/tools/subtitle_style.py",
    # Deliver-preset validation vocabulary.  project_config.validate reads
    # only DELIVER_PRESET_KEYS, and only on the deliver_preset branch for
    # a project that declares one - the scan's own declaration block
    # (project_declared_config) carries no deliver keys at all.  A change
    # here can only move the refuse/accept line for a bad preset, which
    # every strict loader refuses loudly, never as a silently stale scan.
    # Declaring it instead would drag the whole reel-render machinery
    # (resolve_render, reel_build, reel_proposal, render_qa,
    # pool_stream_meta) into a footage scan's cache key through
    # reel_deliver's own imports - the over-invalidation rot the
    # coverage test exists to stop in the other direction.
    "library/tools/reel_deliver.py",
    # Reel timeline overlay validation and layout lowering run after the
    # preflight outputs are frozen; they cannot change a footage measurement.
    "library/tools/reel_graphics.py",
    # Resolve overlay geometry only runs during reel construction, after
    # preflight measurement outputs are frozen.
    "library/tools/layout_model.py",
    # Live-Resolve ingest machinery.  The only preflight-reachable
    # reference is timeline_transcript.main, a command-line entry point
    # no run executes; preflight transcription never connects to
    # Resolve.  (It even imports a step file itself - orchestration, not
    # measurement.)  Re-examine the day a preflight step imports it as
    # measurement code.
    "library/tools/timeline_ingest.py",
}


def hash_asset_file(path: str) -> str | None:
    """SHA-256 of a declared asset's bytes, or None when it is absent.

    The same shape as `plan_content_hash`: raw bytes, hex digest. None
    is a FIRST-CLASS answer - a declared file that is not on disk is a
    refusal elsewhere (`full_frame_element.measure_clip`,
    `placed_assets`), and a hash of nothing would match any other
    nothing. What calls this records the absence rather than hashing
    around it.

    Sized for media: the 16 MB logo card this was written for hashes in
    a fraction of a second; a caller hashing gigabyte-scale sources
    should say so rather than silently paying it every build.
    """
    try:
        with open(path, "rb") as handle:
            digest = hashlib.sha256()
            for chunk in iter(lambda: handle.read(1 << 20), b""):
                digest.update(chunk)
        return digest.hexdigest()
    except OSError:
        return None


def step_code_hash(step_dir: str, extra_files=()) -> str | None:
    """A content hash of every source file in a step's directory.

    Returns None if the directory does not exist or contains no source
    files - callers treat that as "no identity to check".

    The hash is deterministic: files are sorted by name, each one
    contributes its relative name and its content, and the result is a
    single SHA-256 hex digest.

    ``extra_files`` are additional absolute paths folded in after the
    directory, each contributing its repo-relative name and its content.
    Missing files are skipped: a step cannot run without its
    implementation, so it fails loudly at run time, and the hash simply
    covers what is there.
    """
    step_path = Path(step_dir)
    if not step_path.is_dir():
        return None

    digest = hashlib.sha256()
    found = False
    for child in sorted(step_path.iterdir()):
        if child.is_dir():
            continue
        if child.suffix not in _SOURCE_EXTENSIONS:
            continue
        found = True
        # Include the filename so that renaming a file changes the hash.
        digest.update(child.name.encode("utf-8"))
        digest.update(child.read_bytes())

    for extra in sorted(set(extra_files)):
        try:
            data = Path(extra).read_bytes()
        except OSError:
            continue
        found = True
        digest.update(str(extra).encode("utf-8"))
        digest.update(data)

    return digest.hexdigest() if found else None


# The stamp file a step keeps beside its own cached artifacts, naming the
# code that wrote them.  The ledger-level check (`apply_code_identity` in
# run_pipeline.py) forgets a step whose code changed so the step re-runs -
# but several steps resume from their on-disk artifacts, and a re-run that
# reuses artifacts its own new method would not have produced reports
# success while the work did not happen (finding 8, execution-frontier
# report 2026-09-24: music_analysis reused an old librosa grid after
# beat_this landed; semantic analysis printed "invalidating" then reused
# all 17 profiles).  A step compares this stamp on entry and treats a
# mismatch as "nothing cached": the invalidation the ledger promised,
# carried through to the files.
CODE_STAMP_FILENAME = ".step_code_hash"


def current_code_hash(step_dir, repo_root=None) -> str | None:
    """This step's code identity, by the same rule the ledger checks.

    ``step_dir`` is the step's own directory (or anything
    ``implementation_deps`` can key on by basename); the hash folds in
    the step directory plus its declared ``STEP_IMPLEMENTATION_DEPS``,
    so it moves exactly when the ledger's hash for the step moves.
    None when there is nothing to hash - the caller then cannot stamp
    and must not claim a match.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    try:
        deps = implementation_deps(str(step_dir), root)
    except Exception:  # noqa: BLE001 - no deps hashes the step alone
        deps = []
    return step_code_hash(str(step_dir), extra_files=deps)


def read_code_stamp(output_dir: str) -> str | None:
    """The hash the cached artifacts in ``output_dir`` were written under.

    None when no stamp is on disk - a cache from before stamps existed,
    which reads as "written by unknown code" and never as a match.
    """
    try:
        text = Path(output_dir, CODE_STAMP_FILENAME).read_text(
            encoding="utf-8")
    except OSError:
        return None
    digest = text.strip().split()[0] if text.strip() else ""
    return digest or None


def write_code_stamp(output_dir: str, code_hash: str | None) -> None:
    """Record which code wrote this run's artifacts. Best-effort.

    A stamp that cannot be written must not fail the analysis it
    describes - the next run then re-analyzes, which is the safe
    direction. A None hash writes nothing for the same reason: no
    stamp is "unknown code", never a match.
    """
    if not code_hash:
        return
    try:
        Path(output_dir).mkdir(parents=True, exist_ok=True)
        Path(output_dir, CODE_STAMP_FILENAME).write_text(
            code_hash + "\n", encoding="utf-8")
    except OSError:
        pass


def implementation_deps(step_dir: str, repo_root=None) -> list:
    """Absolute paths of the declared shared files for one step directory.

    Looks the step up by directory basename in
    ``STEP_IMPLEMENTATION_DEPS`` and resolves each row against
    ``repo_root`` (default ``REPO_ROOT``).  Unknown steps have no
    declared implementation beyond their own directory.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    rels = STEP_IMPLEMENTATION_DEPS.get(Path(step_dir).name, ())
    return [str(root / rel) for rel in rels]


def code_hashes_for(step_dirs: dict[str, str],
                    repo_root=None) -> dict[str, str]:
    """{node_id: hash} for every step directory provided.

    A step whose directory is missing or empty is simply absent from the
    result.  Each step's hash folds in its declared
    ``STEP_IMPLEMENTATION_DEPS`` files, so a change to shared
    measurement code invalidates exactly the steps that execute it.
    """
    out: dict[str, str] = {}
    for node_id, step_dir in step_dirs.items():
        h = step_code_hash(
            step_dir,
            extra_files=implementation_deps(step_dir, repo_root),
        )
        if h is not None:
            out[node_id] = h
    return out


# ── VC-13: producing-implementation versions ──────────────────────────
#
# `reel_rebuild_need.engine_code_digest` digests whole source trees, so
# during active engine work every build invalidates every reel - correct,
# and the price VC-13 removes. A reel component (captions, lower thirds,
# placements, grade, ...) is produced by a NAMED implementation: the
# operation-registry entry whose `run` is the step's own function. The
# proof below versions each producing implementation by content and
# resolves the operation DYNAMICALLY from the registry at call time, so
# a rename or a new producing step reaches the proof with nothing to
# update. Anything unresolvable versions to None, and a proof with a
# None in it is INCOMPLETE - the caller fails closed to a rebuild,
# never to a skip.


def implementation_fingerprint(ref: str, repo_root=None) -> str | None:
    """The content version of one producing implementation.

    `ref` is a repo-relative path (`library/tools/reel_build.py`), an
    absolute path, or a step directory (absolute or basename, resolved
    under `library/steps`). Directories hash by the `step_code_hash`
    rule (own sources plus declared `STEP_IMPLEMENTATION_DEPS`);
    files hash by bytes. None when the ref resolves to nothing - an
    unresolvable implementation is never a matching one.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    candidate = Path(str(ref))
    targets: list[Path] = []
    if candidate.is_absolute():
        targets = [candidate]
    else:
        targets = [root / candidate]
        if len(candidate.parts) == 1:
            targets.append(root / "library" / "steps" / candidate)
    for target in targets:
        try:
            if target.is_dir():
                hashed = step_code_hash(
                    str(target),
                    extra_files=implementation_deps(str(target), root),
                )
                if hashed is not None:
                    return hashed
            elif target.is_file():
                return hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            continue
    return None


def operation_implementation_ref(op_name: str) -> str | None:
    """The repo-relative implementation path of one registry operation.

    Resolved DYNAMICALLY from `library/tools/operations.py` at call
    time - never a static table - so the proof follows renames with
    nothing to update. None for an unknown operation name, which the
    caller reads as unprovable and fails closed on.
    """
    try:
        from library.tools import operations as _ops
    except Exception:                                   # noqa: BLE001
        return None
    try:
        registry = _ops._REGISTRY
    except AttributeError:
        return None
    for operation in registry:
        if getattr(operation, "name", None) != op_name:
            continue
        owning = getattr(operation, "owning_dir", "")
        body = getattr(operation, "body", "")
        if not owning or not body:
            return None
        return f"library/steps/{owning}/{body}"
    return None


def operation_implementation_version(op_name: str,
                                     repo_root=None) -> str | None:
    """The content version of the implementation behind one operation.

    The producing implementation's version: for a Python body, the
    owning step's `current_code_hash` (own sources plus declared shared
    measurement deps, exactly the ledger's rule); for a prompt body,
    the prompt file's bytes. None when the operation is unknown or its
    implementation cannot be hashed - fail-closed, never a match.
    """
    ref = operation_implementation_ref(op_name)
    if ref is None:
        return None
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    target = root / ref
    if not target.is_file():
        return None
    if target.suffix == ".md":
        try:
            return hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            return None
    owning = ref.split("/")[2] if len(ref.split("/")) > 2 else ref
    step_dir = root / "library" / "steps" / owning
    if step_dir.is_dir():
        return current_code_hash(str(step_dir), root)
    return implementation_fingerprint(ref, root)


def implementation_versions_for(refs, repo_root=None) -> dict[str, str | None]:
    """The input record for a set of producing implementations.

    `{ref: version}` for every ref asked about. A ref may be an
    operation name (resolved through the registry) or a repo-relative
    path. Unresolvable refs are recorded as None - asked and
    unanswerable, which `implementation_proof` reads as incomplete.
    """
    out: dict[str, str | None] = {}
    for ref in refs or ():
        version = None
        if "/" not in str(ref):
            # A bare name may be an operation id - the registry is
            # asked first, and a name it does not know falls through
            # to the path reading below (a step basename).
            version = operation_implementation_version(
                str(ref), repo_root)
            if version is None and operation_implementation_ref(
                    str(ref)) is not None:
                out[str(ref)] = None
                continue
        if version is None:
            version = implementation_fingerprint(str(ref), repo_root)
        out[str(ref)] = version
    return out


def implementation_proof(refs, repo_root=None) -> dict:
    """The version proof for a component's producing implementations.

    Returns `{"versions", "unproven", "complete", "detail"}`. `complete`
    is True only when every ref versioned to a real hash. Anything
    else - an unknown operation, a missing file - lands in `unproven`
    and the proof is incomplete: the caller rebuilds, and `detail`
    says which implementation could not be proven.
    """
    versions = implementation_versions_for(refs, repo_root)
    unproven = sorted(
        ref for ref, version in versions.items() if not version)
    if unproven:
        detail = ("implementation proof INCOMPLETE - no version for: "
                  + ", ".join(unproven) + "; the change is unprovable "
                  "and the component rebuilds")
    else:
        detail = (f"implementation proof complete - {len(versions)} "
                  f"producing implementation(s) versioned")
    return {"versions": versions, "unproven": unproven,
            "complete": not unproven, "detail": detail}
