# vep-mu-edit calibration: incumbent vs EDIT modules

Real measurements, read-only, from the 001 project (`pipeline_data.json`, `1_04_temporal_index` clip indices) and the IMG_1808 calibration take. Held-out sources sealed (the script refuses their filename prefixes). No model downloaded; precision: venv python3 + cv2 (see report).

| measurement | value | basis |
|---|---|---|
| real_words_total | 140 | 001 speech_sequence body+hook word_timestamps |
| real_clips_with_words | 3 | distinct clip_id in speech_sequence |
| before_word_edge_cuts_total | 140 | one cut at every real word end |
| before_word_edge_cuts_in_pause | 1 | within 80ms of a measured >=0.15s pause center |
| after_pause_center_cuts | 25 | pause_center_cuts on the same real words |
| after_pause_gap_median_s | 0.661 | median measured inter-word gap carrying a cut |
| incumbent_aroll_boundaries | 8 | 001 aroll.assign video_segments in/out |
| before_boundaries_mid_word | 0 | incumbent edges strictly inside a real word span |
| after_refine_placed | 16 | refine_cut on incumbent edges |
| after_refine_moved | 0 | refine_cut on incumbent edges |
| after_refine_vetoed | 0 | refine_cut on incumbent edges |
| after_phrases_detected | 7 | phrase_spans over clip_011 (72 real words, time-ordered) |
| after_guard_full_span | ok | guard over the whole real-word span |
| after_guard_isolated_word | refused | guard over the first real word alone |
| after_guard_isolated_reason | isolated_word | refusal reason for the isolated word |
| real_motion_samples | 5656 | clip_011 motion_energy values at 30Hz |
| after_phase_action_status | phased | phase_action over real motion around a high-motion peak |
| after_action_boundary_cuts | 2 | action_boundary_cuts from the phased real motion |
| after_shot_reject_verdict | reject | reject_intervals over the real IMG_1808 10-40s window |
| real_soft_picture_windows | 1 | soft_picture_ranges over IMG_1808 10-40s at 5Hz |
| after_candidate_strength | strong | build_candidate over 50 real words |
| after_alternative_policies | ['earliest', 'latest', 'longest_verified'] | alternative_plans over two real-word candidates |
| after_event_fidelity_speech | verified | word_phrase_boundary with word evidence present |
| after_event_fidelity_scene_unmeasured | uncertain | scene_transition with one measured scene (no boundary tick) |
| after_mapping_valid | True | verify_source_timeline_map over 6 incumbent segments |
| after_av_sync | True | placed A/V spans identical |
| after_continuity_status | continuous | single present-throughout person track at mid-timeline cut |
| render_mechanical_unmixed_loudness | fail | mechanical_report over typical unmixed render numbers |
| render_story_uncovered | False | storytelling_report with the required moment uncovered |
| demo_task_defined | coherent_answer | requires speech,diarization,semantic_retrieval,edit_planning |
| demo_task_defined | better_take | requires speech_comparison,technical_measurements,editorial_preferences |
| demo_task_defined | visible_speaker | requires diarization,active_speaker,identity |
| demo_brief_cut_ranges | 1 | build_demo_brief coherent_answer over the real span |
| demo_split_editing_recall | 0.5 | retrieval 1/2, edit 1/2 on the provisional slice |
| demo_product_pickup_attribution | perception | corpus has no product footage; perception failure recorded |
| demo_product_pickup_prioritize | 3 | causes feeding EDIT-033 prioritization |
Resolve execution proof (EDIT-029/030), 2026-10-16:

| resolve_proof_timeline | vep-mu-edit-proof_20261016_002908_10s | resolve_build_timeline.build_timeline success:true in Ren Scratch - vep-mu-edit |
| resolve_proof_render | /tmp/vep-mu-edit-resolve/render/vep-mu-edit-proof.mp4 (14681466 bytes, job Complete) | resolve_render.render_timeline H264 mp4, IMG_1808 5-15s, program CH1 declared |
| resolve_render_geometry | 1920x1080 24.0fps 10.069333s 1 audio stream | ffprobe of the rendered proof file |
| resolve_render_lufs | -23.52 | render_qa.measure_lufs loudnorm on the proof render (unmixed camera audio) |
| resolve_render_true_peak_dbtp | -7.12 | render_qa.measure_lufs on the proof render |
| resolve_mechanical_correct | False | edit_render_check: loudness fail (-23.52 vs -14 target); geometry/streams/black/freeze pass |
| resolve_story_satisfied | True | edit_render_check: required calib-window 5-15s fully covered |

## Blind agent label agreement (round 4)

Two independent agents from different model families labeled the calibration
packet. The detailed item-level record is
`2026-10-16-vep-mu-edit-agent-labels-consensus.json`.

| Item | Agreement | Consensus / disposition |
|---|---:|---|
| IMG_1808 visible softness and shake | 12/12 bin-property values | All six bins (0-27.77s) are labeled not visibly soft and not shaky. The earlier objective reject was measured over 10-40s, so the different source window must be reconciled before calculating detector accuracy. |
| Render intent score and defects | 2/2 fields | Both score the one-take window 4/5 and report no black, freeze, or digital-silence defect. |
| Render speech level | 2/3 agents say no | Third-family tie-break agrees speech is too quiet relative to the programme; the only word is around -33 to -40 dBFS RMS. A/B reported integrated loudness -22.9 LUFS and true peak -7.1 dBTP. |
| Podcast Q/A units | A/B exact full-unit agreement: 0 | Third-family tie-break finds no complete unit in 0-60 and one complete retake in 60-150: question 77.0-81.3s, answer 81.46-102.37s. |
| IMG_1808 action phases | A/B exact event-plus-phase-span agreement: 0 | Third-family tie-break supplies six final action spans; see the consensus JSON. B and C identify end-of-clip camera movement, overlapping at 27.0-27.77s. Phase boundaries are approximate to about +/-0.5s. |
| Podcast visible speaker | A/B 0/9 bins | Third-family tie-break maps the single visible man to left; final bins are left at 60-80, 100-110 and 120-130, otherwise neither. |

The blind packet did not contain the provisional `clip_011` coherent-answer
moment or a second take for comparison. Neither is scored as an agent consensus
label. The single-take packet cannot establish a comparative take preference.
The third-family results are final tie-break labels, not majority votes or
human labels.

Prediction scoring is limited by the available matching outputs. The IMG_1808
softness result is not comparable until its source window is reconciled. For
the render, the agent story score is 4/5 and the measured edit coverage passes;
the mechanical loudness result fails at -23.52 LUFS against the -14 LUFS target,
matching the final speech-level label of `no`. A/B agree there are no black,
freeze, or digital-silence defects; C was not asked to re-evaluate those fields.
The Q/A, action-phase, and visible-speaker labels are gold examples, but this
report contains no predictions for those exact packet items, so their model
accuracy is not scored. `demo_split_editing_recall` above remains a provisional
slice score because the blind packet did not label its `clip_011` moment.
