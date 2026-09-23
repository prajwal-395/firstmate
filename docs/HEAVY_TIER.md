# The heavy tier

The slowest tests run as their own phase of the local full-suite gate
(`scripts/full_suite_gate.sh`), not in the default lane. An ordinary
working-lane run stops paying for them; the batch gate still runs
everything before anything merges.

## The rule

A test whose measured duration is **1.0 seconds or more** carries the
`heavy` marker and is deselected from the default lane (`-m "not
heavy_ml and not heavy"`). The heavy selection (`-m heavy`) runs as
phase 3 of the same gate, through the same sharded lanes.

`heavy` marks live on the test functions themselves (registered in
`pyproject.toml`), next to the `heavy_ml` marks they follow in shape.
What is slow is a measured property of the test, so the mark is
checked in with it - but the SET below is derived, not chosen: see
"Provenance".

## Why 1.0s

Derived 2026-09-23 from one serial full-suite run (9,697 cases, 382.9
test-seconds - see "Provenance"). One run's durations give every
threshold candidate at once, so no threshold costs an extra run:

| threshold | cases | share of suite | test-seconds moved | share of runtime |
| --- | --- | --- | --- | --- |
| >= 0.5s | 150 | 1.5% | 282.2 | 73.7% |
| >= 1.0s | 95 | 1.0% | 246.7 | 64.4% |
| >= 2.0s | 56 | 0.6% | 198.2 | 51.8% |
| >= 5.0s | 11 | 0.1% | 79.5 | 20.8% |

1.0s chosen over 0.5s: 0.5s buys 35 more test-seconds for 55 more
tiered cases, and a half-second boundary no contributor can defend -
many honest unit tests take half a second. 1.0s chosen over 2.0s: 2.0s
leaves 39 slow cases (48 test-seconds) in the default lane, keeping
half the heat the tier exists to remove. A second is the boundary a
future contributor can defend: a unit test should not take a second.

What would move the threshold: if the default lane needs to be cheaper
than ~136 test-seconds, raise it toward 2.0s and accept the smaller
saving; if the tier's own phase grows past what a batch gate tolerates,
raise it the same way. Never substitute a rank rule (slowest-N): it
rots the moment anyone adds a test.

Default-lane cost: 382.9 test-seconds with the tier in it, ~136.2 with
the tier moved out (plus skips). The tier phase carries the other
~246.7.

## The verdict contract

The tier is mandatory and the verdict says so:

- `--skip-heavy` runs the default lane alone for a working lane that
  needs to stay cool. The verdict degrades to **NARROWED PASS naming
  the heavy tier**, never a clean PASS.
- A heavy phase that runs and fails (or crashes) fails the gate.
- A heavy phase that was attempted but measured nothing narrows the
  verdict the same way a skip does: an unmeasured tier is not a pass.
- NOTHING MERGES WITHOUT THE HEAVY TIER HAVING RUN ON IT. A tier
  nobody runs is deletion with extra steps.

Pinned behaviourally by `tests/test_ci_is_deliberate.py` (phase
exists, skip narrows by name, failure fails) and demonstrated on the
landing PR, which quotes a `--skip-heavy` verdict line.

## What the tier does NOT touch

- The `heavy_ml` tier is separate (capability-gated, phase 2) and
  unchanged. No case carries both marks.
- The ratchet pins `no_creative_floors` and `brief_snapshot` stay in
  the default lane by the captain's 2026-09-23 ruling. At derivation
  they measured 0.175s and 0.171s max - nowhere near any threshold -
  so the ruling cost nothing here. If a pin ever measures >= 1.0s,
  ask before moving it; being slow is not grounds.
- Tiering moves WHEN a test runs and never removes one. Tests that
  should not exist are follow-up, not tiering.

## Skips are not measurements

5 cases skipped in the derivation run. 3 recorded ~0s with `<skipped/>`
(all `test_prosody_failure_is_loud` missing-dependency cases):
unmeasured, i.e. UNKNOWN, not evidence of cheapness - counted here,
kept in the default lane where they skip cheaply today, tiered on
re-derivation if they ever start running slow. The other 2 (SFX
library unresolvable without a project folder) recorded small but
nonzero setup times before skipping; same treatment. The renderer path
was available in the derivation run, so the render-gated pixel tests
measured their real cost instead of skipping - the single-run
skip-trap (a 115s render test reading 0.000) did not apply to this
derivation. A future derivation whose run skips render tests must NOT
read those zeros as fast: consult a run where the renderer was present
and take the max.

## Provenance (re-derive, do not inherit)

The set below was derived 2026-09-23 against main `097ec79a` from ONE
serial run, because the suite's history is method, never answer: an
earlier 292-case set measured on a deleted tree shares only its shape
with this one.

```
bin/vep -m pytest tests/ \
  --ignore=tests/test_marker_capture_against_resolve.py \
  --ignore=tests/test_marker_feedback_against_resolve.py \
  -rs --tb=short --junitxml=timing.xml -p no:cacheprovider
# 9692 passed, 5 skipped in 388.85s
```

(Serial, not the gate's parallel lanes: the gate deletes passing
reports, and uncontended durations are the honest per-test cost.
Threshold choice needs no extra runs - every candidate threshold reads
off the same report.)

To re-derive: run the above on the current tree, take `time >= 1.0`
per `classname::name` (skipped-with-~0 as UNKNOWN per above), mark
whole functions (every slow nodeid of the function must be slow, else
mark per-param), and verify `-m heavy` collects exactly the derived
set with zero drift.

## The 95 cases (seconds, one per line)

```
11.918 tests.test_composer::test_no_plan_names_a_blind_capability
8.541 tests.test_ml_dependencies_real::test_transcription_produces_real_timed_words
8.357 tests.test_bridge_outputs_are_declared::test_no_bridge_key_is_an_unexpected_extra_field
8.117 tests.test_bridge_outputs_are_declared::test_every_bridge_key_is_declared
7.649 tests.test_post_bridge_rejection_reaches_the_model::test_the_feedback_blocks_accumulate_and_stay_bounded
7.642 tests.test_post_bridge_rejection_reaches_the_model::test_the_retry_is_bounded_and_fails_carrying_the_last_violation
5.977 tests.test_tests_never_reach_real_projects::test_collecting_the_suite_binds_nothing_under_a_populated_projects_root
5.917 tests.test_gemma_shim::test_in_flight_request_blocks_teardown
5.210 tests.test_reel_look::test_a_longer_run_extends_the_shared_render
5.082 tests.test_post_bridge_refusal_stdout::test_a_post_bridge_refusal_written_to_stdout_reaches_the_retry_message_non_empty
5.081 tests.test_post_bridge_rejection_reaches_the_model::test_the_second_context_carries_the_violation
4.081 tests.test_reel_look::test_a_shorter_reuse_extends_nothing_and_renders_nothing
4.056 tests.test_gemma_shim::test_shim_self_exit_disabled_stays_up
4.036 tests.test_output_contract::test_uncalled_does_not_report_a_function_that_is_called
3.937 tests.test_gemma_shim::test_hold_blocks_teardown_and_scope_releases
3.134 tests.test_output_contract::test_uncalled_reports_something_and_names_where
2.900 tests.test_ci_is_deliberate::test_the_local_gate_cannot_report_a_pass_it_did_not_earn
2.820 tests.test_gemma_shim::test_shim_exits_only_after_backend_stopped
2.800 tests.test_parallel_gate_verdict.TestLaneExitsReachTheVerdict::test_clean_lane_reports_with_dirty_exits_fail
2.690 tests.test_ml_dependencies_real::test_prosody_produces_real_measurement
2.601 tests.test_parallel_gate_verdict.TestLazyXdistDetect::test_missing_xdist_is_did_not_run
2.526 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[color_grade]
2.516 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[mesh_spine]
2.516 tests.test_direction_contradiction::test_the_question_reaches_the_prompt_and_the_answer_is_recorded[answer2-unevidenced]
2.515 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[plan_sfx]
2.515 tests.test_direction_contradiction::test_the_question_reaches_the_prompt_and_the_answer_is_recorded[answer1-contradicted]
2.515 tests.test_direction_contradiction::test_the_question_reaches_the_prompt_and_the_answer_is_recorded[answer0-nothing_contradicted]
2.514 tests.test_direction_contradiction::test_the_question_reaches_the_prompt_and_the_answer_is_recorded[answer3-not_declared]
2.514 tests.test_briefing_interview::test_a_step_handed_a_brief_is_not_interviewed
2.513 tests.test_briefing_interview::test_a_declined_brief_asks_and_the_answer_is_recorded[answer2-not_declared]
2.512 tests.test_transcript_view::test_a_recorded_request_carries_the_transcript_and_no_word_timings
2.512 tests.test_briefing_interview::test_a_declined_brief_asks_and_the_answer_is_recorded[answer0-nothing_to_ask]
2.512 tests.test_brand_constraints_reach_the_prompt::test_the_brand_reaches_the_text_handed_to_the_model
2.511 tests.test_post_bridge_rejection_reaches_the_model::test_a_step_with_no_post_bridge_still_calls_once
2.511 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[select_reels]
2.511 tests.test_direction_contradiction::test_a_step_that_does_not_flag_is_not_asked
2.511 tests.test_creative_brief_reaches_prompt::test_a_brief_in_a_read_only_planning_tree_reaches_the_request
2.510 tests.test_llm_context_routing::test_a_step_with_something_to_ask_still_calls_the_model
2.510 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[plan_transitions]
2.510 tests.test_creative_brief_reaches_prompt::test_context_field_projection_does_not_drop_the_brief
2.510 tests.test_briefing_interview::test_the_questions_reach_the_run_summary_naming_the_step
2.509 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[speech_sequence]
2.509 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[plan_vfx]
2.508 tests.test_briefing_interview::test_the_prompt_tells_the_step_to_decide_anyway
2.506 tests.test_llm_context_routing::test_a_bridge_table_reaches_the_prompt[select_broll]
2.506 tests.test_briefing_interview::test_a_declined_brief_asks_and_the_answer_is_recorded[answer1-asked]
2.506 tests.test_brand_constraints_reach_the_prompt::test_a_project_that_selected_no_brand_contributes_no_brand_text
2.504 tests.test_briefing_interview::test_a_project_with_no_brief_at_all_is_also_interviewed
2.503 tests.test_creative_brief_reaches_prompt::test_the_brief_reaches_the_text_handed_to_the_model
2.305 tests.test_marker_writes_hold_lease::test_contended_marker_write_refuses_then_lands
2.299 tests.test_motion_graphics_overlay_modes::test_a_predicted_clamp_refusal_is_retried_from_pixels
2.293 tests.test_reel_look::test_two_run_lengths_share_one_overlay_artefact
2.268 tests.test_ml_dependencies_real::test_prosody_speaking_rate_comes_from_handed_regions
2.121 tests.test_parallel_lane_routing.TestWholeTreePinning::test_every_file_lands_in_exactly_one_lane
2.068 tests.test_replay_bench::test_verify_reproduces_a_qa_retry_rather_than_excusing_it
2.006 tests.test_parallel_lane_routing.TestWholeTreePinning::test_measured_safe_shapes_stay_parallel
1.951 tests.test_gemma_shim::test_idle_teardown_stops_backend_process
1.938 tests.test_parallel_lane_routing.TestWholeTreePinning::test_serial_lane_holds_nothing_undeclared
1.888 tests.test_parallel_lane_routing.TestWholeTreePinning::test_measured_unsafe_set_routes_serial
1.869 tests.test_replay_bench::test_verify_reports_a_step_whose_state_has_moved
1.646 tests.test_resolve_lock::test_a_contended_acquisition_records_its_wait_and_its_holder
1.526 tests.test_resolve_lock::test_an_acquisition_waits_while_the_captain_is_in_resolve
1.525 tests.test_color_grade_is_decided::test_a_project_with_no_brand_template_still_gets_a_reasoned_grade
1.447 tests.test_brand_motion::test_mezzanine_rebuilds_when_the_source_is_newer
1.418 tests.test_subtitle_overlay_modes::test_tight_video_edge_touch_falls_back_to_full_canvas
1.401 tests.test_replay_bench::test_compare_diffs_answers_when_both_are_supplied
1.263 tests.test_tests_never_reach_real_projects::test_no_test_module_hardcodes_a_path_under_a_users_home
1.249 tests.test_replay_bench::test_compare_at_one_revision_against_itself_finds_no_difference
1.240 tests.test_subtitle_overlay_modes::test_tight_render_drawing_nothing_records_full_geometry
1.226 tests.test_subtitle_qa_sampling::test_an_empty_overlay_still_fails
1.223 tests.test_input_declarations_are_true::test_every_step_and_every_declared_input_is_surveyed
1.192 tests.test_external_inputs::test_a_music_selection_whose_file_carries_no_audio_is_refused
1.169 tests.test_no_unfailable_tests::test_no_test_in_this_repo_is_unfailable
1.158 tests.test_external_inputs::test_a_whole_pipeline_collapses_to_the_step_that_still_has_work
1.153 tests.test_timeline_variants::test_merge_cutaway_with_cta_and_grade
1.111 tests.test_motion_graphics_overlay_modes::test_accents_stay_full_canvas_when_tight_is_asked
1.109 tests.test_asked_fields_have_readers::test_unread_fields_across_the_pipeline
1.099 tests.test_agent_wait_narrowing::test_no_test_patches_the_global_sleep_or_clock
1.098 tests.test_resolve_lock::test_a_waiter_waits
1.098 tests.test_color_grade_is_decided::test_a_project_with_a_template_still_gets_that_templates_look
1.092 tests.test_motion_graphics_overlay_modes::test_explicit_full_declares_itself_on_the_artefact
1.079 tests.test_input_declarations_are_true::test_the_cli_agrees_with_the_suite
1.064 tests.test_color_grade_is_decided::test_a_judged_no_correction_is_not_an_ungraded_run
1.044 tests.test_motion_graphics_overlay_modes::test_a_predicted_refusal_with_no_ink_stays_full_and_named
1.043 tests.test_asked_fields_have_readers::test_no_code_reads_a_creative_direction_key_that_cannot_exist
1.040 tests.test_output_contract::test_a_stale_exemption_is_a_disagreement
1.034 tests.test_cursor_discipline::test_every_cursor_setter_is_registered
1.024 tests.test_step_timeout::test_a_step_that_outlives_the_ceiling_still_times_out
1.020 tests.test_input_declarations_are_true::test_the_new_findings_report_and_do_not_fail
1.013 tests.test_fusion_tool_inputs::test_no_tool_type_the_engine_writes_is_one_resolve_does_not_have
1.012 tests.test_remotion_batch::test_a_wedged_child_times_out_rather_than_hanging_the_run
1.008 tests.test_visual_qa::test_render_segment_timeout
1.005 tests.test_resolve_lock::test_a_signal_that_never_clears_raises_instead_of_wedging
1.005 tests.test_resolve_guard_wiring::test_assert_current_timeline_still_has_its_callers
1.003 tests.test_reel_read::test_no_module_outside_the_readers_touches_resolve_directly
```
