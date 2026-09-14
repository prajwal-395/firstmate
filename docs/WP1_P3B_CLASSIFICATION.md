# WP1 P3b classification: 232 candidate sites

Work package WP1 of `firstmate/data/vep-vision/plan.md` (principle 3:
"Scripts stop safely and report when the world surprises them; agents
read, interpret, and decide"). AGENTS.md 10.3 states the rule; this file
is the classification the Firstmate spec demands: one verdict per
candidate site, with the reasoning surviving the PR.

## Method

The staged `p3b-sites.txt` / `audit_p3b.py` were absent from the task
state dir and task tmp, so the audit was reconstructed from the brief's
description (AST pass: `except` handler lexically inside a loop whose
body neither raises, exits, logs, nor records) and re-run against
`library/`. That run found **232 sites vs the 177 quoted** - a different
detector implementation (this one also counts local-fallback
assignments such as `x = 0.0`, which are exactly the
default-as-measured shape), not a different codebase. Every site below
was judged from its loop's product and the downstream reader, with
context gathered per file cluster. Detector false positives (handlers
that DO record via `drop()` / `results.fail()` / `_refuse()` /
`_undetermined()`) are classified LEGITIMATE with that reason.

## The decision line

- **DEFECT**: the swallowed item was required data, or the handler lets
  an unmeasured value publish as though measured.
- **LEGITIMATE**: the item was genuinely optional and its absence is
  already an admitted absence downstream (skipped item absent from
  output with no measured claim; fallback of None / "unknown" /
  "unmeasured" / drop-with-reason / refusal / fail-closed gate).

One boundary call, stated so the captain can move sites across it: word-
parse skips that neuter a *backstop* refusal while the model-approved
plan stands (reel_build 1622/1638/1662/1683/2170/2233) are LEGITIMATE -
nothing is invented and no new act is taken. Skips that supply the
*positive condition* for an otherwise-guarded act (absorb over "silence"
at 2067, cover placed over "no overlap" at 8827, redraw accepted on a
"clean edge" at captain_edits:592) are DEFECT.

## DEFECT (16)

Tranche 1 - fixed in this PR, with the admitted-absence vocabulary each
fix follows:

| site | shape | fix |
|---|---|---|
| step_1_04_temporal_index/step.py:1004 | all shift candidates raise -> magnitude 0.0 + `"static"` | failed pair contributes no vector; empty run reads `"unknown"`, the function's existing failure vocabulary |
| analysis/music_pipeline.py:177 | zero windowed key estimates -> `consistency` 1.0 | `None` + note; mirrors the module's own `method: None` absence shape |
| analysis/music_pipeline.py:503 | zero windowed chord estimates -> `chord_count` 0 | `None` + note |
| render_check.py:157 | garbled overlay fps probed at 30.0 | fail-closed `cannot verify placement` finding, the function's existing vocabulary |
| render_check.py:161 | garbled source offset probed at 0.0 | same finding shape |

Tranche 2 - classified, not yet fixed (reel/render/captain-edits
subsystems; each fix is small and named here so the next lane can take
them without re-reading):

| site | shape | intended fix |
|---|---|---|
| step_3_03_review_rough_cut/step.py:389 | garbled `timeline_start` placed at 0.0 on the review surface | None + `@unknown` label, mirroring :396/:461 beside it |
| step_6_01_render/resolve_build_timeline.py:1364 | unreadable `GetStart()` -> planned start as "actual" -> offset 0 ("aligned") | leave the block out of `block_offsets` with a warning; the missing-block consumer path already fails closed |
| reel_look.py:270 | whole-row list failure skips silently, violating the module's own record-every-skip rule | `record["skipped"]` entry naming the row |
| reel_build.py:612 | failed `before` inventory reads as empty -> pre-existing items look ADDED -> sweep can DELETE them | failed row -> unknown -> skip enforcement on that row, reported unverified |
| reel_build.py:642 | failed `after` inventory reads as none-added -> spill missed | failed row reported unverified ("spill unchecked") |
| reel_build.py:1046 | failed census row never checked -> "none unlinked" -> silent place | failed row appended to `unlinked` so the existing `OffsetRefused` fires |
| reel_build.py:2067 | malformed timed word -> `None` read as silence -> remnant absorbed (deleted) | malformed timed word counts as spoken -> existing REFUSING error |
| reel_build.py:8827 | malformed word -> no overlap found -> cover placed over possible speech | malformed word refuses, like the width/height refusal just below it |
| reel_deliver.py:434 | unreadable bin skipped -> `[]` reads "every overlay decodes" per the docstring | unreadable bin recorded as a stale entry with reason |
| reel_deliver.py:462 | unreadable pool item skipped, same claim | same |
| captain_edits.py:592 | malformed timed word -> edge reads clean -> redraw applied | malformed timed word -> `False` (CANNOT APPLY, the existing path) |

## LEGITIMATE (216)

Absence already admitted, fail-closed, fail-safe, or recorded - one line
each. "SKIP" = item absent from output, no measured claim made.

Preflight and measurement:

- step_1_02_catalog_footage/step.py:300 SKIP: all date formats fail -> None; only the sort key floors.
- step_1_04 (n_frames<2 early return): single frame cannot move; `"static"` describes the only evidence, not a guess.
- step_1_05_prosody_analysis/step.py:87 SKIP: corrupt index reads as no-regions, the same shape a region-less clip takes; analysis still measures real audio.
- step_1_05:197 SKIP: unreadable cache re-analysed (self-healing).
- step_1_05:262 SKIP: corrupt profile dropped; Rejected count printed.
- analysis/footage_segments.py:368 SKIP: prototype index loses a span; no zero published.
- analysis/footage_segments.py:517 SKIP: fingerprint excludes the unreadable blob (a flake flips the digest, the safe direction).
- analysis/speech_advanced_pipeline.py:198 SKIP: sample-level loss in a dense contour; the empty case is admitted unmeasured downstream.
- analysis/vision_pipeline_v3.py:256 SKIP: greedy JSON recovery shortens the list; no zero-object claim.
- analysis/vision_pipeline_v3.py:669 SKIP: index None prints "not found"; boundaries [].
- analysis/vision_pipeline_v3.py:716 SKIP: transcript strategy falls through; "" is explicit.
- analysis/vision_pipeline_v3.py:1789 SKIP: corrupt cache counted in the skipped totals; no profile fabricated.
- beat_grid.py:147 SKIP: grid narrows; under 8 beats -> [] is the documented do-not-snap signal (pinned by the sweep test).
- speech_loudness.py:152 SKIP: optional keys omitted; the required path carries measured/reason.
- subject_framing.py:244,319,488,497,876 SKIP/fallback-None: losses resolve toward unmeasurable-None or MISS, each distinguished from a measured value (244 pinned by the sweep test).
- person_object_profiles.py:95 SKIP: unparseable span excluded from an aggregate sum; no value invented.
- subject_grade.py:452 SKIP: `_refuse()` names the clip and reason.
- segment_coverage.py:59,107 SKIP: skipped share reported undescribed by design, never repaired by guessing.
- music_bed.py:482 FALLBACK None: docstring-admitted; unchecked rather than guessed.
- music_search.py:463 SKIP: corrupt result line narrows the choice set.
- semantic_visual.py:178,191 SKIP: fewer anchorable words; the miss becomes a named drop downstream.
- reel_semantic_visual.py:744,750 SKIP: unmappable words; the miss becomes a drop with reason.
- reel_semantic_visual.py:1077 SKIP: `drop()` records reason and detail.
- render_qa.py:410,420,440 SKIP: fewer samples; an unreadable frame earns no clean bill.
- render_qa.py:896 FALLBACK stream_done: normal end-of-stream termination, not a swallow.
- render_qa.py:2367 SKIP: shorter key-frame list; no placeholder path.
- render_watch.py:582 SKIP: corrupt sidecar -> reel not listed delivered -> `NotDelivered` refuses.

Planning and selection:

- step_3_03:396 FALLBACK None: `@unknown` label names the absence.
- step_3_03:461 FALLBACK None: missing list carries the label.
- step_3_03:474 FALLBACK 0.0: guarded downstream (`> 0 else 30.0`).
- step_4_03_plan_vfx/bridge.py:165 SKIP: absent camera segs -> "" description (no claim).
- step_4_04_plan_sfx/bridge.py:156 SKIP: count None renders the "not measured" cell.
- step_4_04_plan_sfx/post_bridge.py:133 SKIP: fail-safe; the lead gate drops loudly on a too-recent cut.
- step_5_01_color_grade/grade.py:211 SKIP: empty samples -> unmeasured dict with reason.
- step_5_04_compile_manifest/step.py:1257 FALLBACK -1: mismatch raises `OverlaySegmentMissing`; -1 never reads as measured.
- probe_resolve_capabilities.py:160,324,876: `results.fail()` records each failure.
- probe_resolve_capabilities.py:333 SKIP: reset-only; the measure pass sets every prop explicitly first.
- resolve_build_timeline.py:656,661,667 FALLBACK/SKIP: unreadable pool state reads as "nothing pooled" -> everything re-imported (fail-safe).
- resolve_build_timeline.py:788 SKIP: falls back to the declared timeline fps; misplacement is downstream-verified.
- resolve_build_timeline.py:1369 SKIP: printed skip plus continue.
- resolve_build_timeline.py:2341 FALLBACK label: warning text only.
- resolve_build_timeline.py:2374 FALLBACK []: missed links surface as warnings.
- resolve_build_timeline.py:2380 SKIP: an unreadable handle cannot be linked.
- resolve_build_timeline.py:2528,2534 SKIP: undecidable rows kept; the record claims only deletions made.
- motion_graphics_plan.py:642 SKIP: `drop()` records reason and detail.
- visual_component_plan.py:652,668,724,766 FALLBACKS: each forces a named drop (`parts=0`, `nan`, `()` fail their own guards).
- overlay_intent.py:291 SKIP: "" means no disagreement found (findings-list semantics).
- overlay_placement.py:268,287,312,345 SKIP/FALLBACK False: misses return verdict strings; sets are verified by read-back.
- mix_intent.py:193,361,406,506,518,532 SKIP/FALLBACK -1: misses become STALE / skipped records with reasons.
- layer_coherence.py:292,302 SKIP: misses become divergence entries.
- reel_proposal.py:525 SKIP: docstring withholds skipped words as evidence.
- reel_proposal.py:658 SKIP: None recorded in the repair report.
- reel_quality_bar.py:1675 SKIP: reel reads UNJUDGED plus the not_read list.
- reel_quality_bar.py:1916 SKIP: unreadable judgement -> (None, "").
- reel_divergence.py:648 FALLBACK `_undetermined()`: the admitted-absence constructor.
- reel_divergence.py:892 SKIP: docstring resolves a missing ending as undetermined, never falsely absent.
- reel_ending.py:869 SKIP: falls back to the conservative shot-end bound.
- reel_look.py:279 FALLBACK "","": skipped-with-reason record.
- reel_conformance_verifier.py:2080,2471 SKIP: excluded from evidence; missing timings earn the F8 finding.
- reel_conformance_verifier.py:5630 SKIP: loud WARNING names what is not read.
- toon_serializer.py:313 FALLBACK scalar: lenient parse preserves content; no value invented.

Reel build, touchup, deliver, read:

- reel_build.py:210,430 SKIP/placeholder: omitted angle; the 1 is documented unread.
- reel_build.py:330 FALLBACK None: admitted; the refusal path takes it.
- reel_build.py:343 FALLBACK 1: single-stream mechanical default, matching the `.get` default beside it.
- reel_build.py:378,386,394 SKIP: omitted source -> `ReelBuildError` refusal.
- reel_build.py:756,768 FALLBACK []: missed rows surface as unlinked warnings.
- reel_build.py:915 FALLBACK []: absence raises `OffsetRefused` explicitly.
- reel_build.py:1622,1638,1662,1683 SKIP: skipped evidence cannot trigger a backstop refusal; the plan stands, nothing invented or deleted (the documented boundary call).
- reel_build.py:1821 SKIP: admitted empty/unknown sentence.
- reel_build.py:2170,2233 SKIP: no finding claimed; "no timings yields no findings, never a pass".
- reel_build.py:3927 SKIP: disagreement -> None; the caller reports absence.
- reel_build.py:4765 SKIP: search continues; a whole-tree miss is admitted not-found.
- reel_build.py:6035,6042 SKIP: blank rows kept silently; no deletion claimed.
- reel_build.py:6565 SKIP: best-effort snapshot filtered downstream.
- reel_build.py:6760 FALLBACK None: gated record.
- reel_build.py:7464 SKIP: reel surveyed without an ending.
- reel_build.py:8108 SKIP: absent binding -> `check_footage_binding` False with reason.
- reel_build.py:8766,8771,8776 FALLBACK None: each raises `OffsetRefused` explicitly.
- reel_touchup.py:889,894,900 SKIP/FALLBACK []: search continues; overall miss is admitted None.
- reel_touchup.py:961 FALLBACK {}: the module's declared never-raise policy; the graded case refuses explicitly above it.
- reel_deliver.py:468 FALLBACK path: label only, not a measurement.
- reel_read.py:510 SKIP: miss returns the `measured: False` dict.

Captain edits and transcript corrections (operator / learning surfaces):

- captain_edits.py:430 SKIP: emphasis words kept while text updates; operator-supervised, no measurement.
- captain_edits.py:529 SKIP: frame keys optional by guard.
- captain_edits.py:552,805,909,1027 SKIP: misses become explicit STALE records.
- captain_edits.py:622 SKIP: shortened fallback tokens take the mismatch path.
- captain_edits.py:1217 SKIP: pass-through; downstream placement gates decide.
- captain_edits.py:1759,1780 FALLBACK None: both raise `CaptainEditError` explicitly.
- transcript_corrections.py:242,259,282,550 SKIP: skipped learnings leave ranges unchanged.
- transcript_corrections.py:596,686 SKIP: wordless input grows nothing; the nub refuses at the gate.

Plumbing (dashboards, gates, surveys, cleanup - absence is the output):

- dashboard/footage_search.py:150 SKIP: absent fps -> no timecode (documented).
- dashboard/server.py:139 SKIP: corrupt message file contributes nothing.
- dashboard/server.py:806 SKIP: poll timeout retries; the timeout status is explicit.
- processes/edit_video/run_pipeline.py:749 SKIP: invalid target reported by `apply_rerun_requests`.
- processes/edit_video/run_pipeline.py:3436 FALLBACK: literal placeholder review text, not a measurement.
- skills/verify_treatment/skill.py:66 SKIP: failed still -> fewer stills.
- marker_capture.py:267 SKIP: unreadable fps -> `CaptureError` refuses.
- marker_feedback.py:361 SKIP: 0.0 guarded to None downstream.
- marker_feedback.py:1007 SKIP: bad pull JSON absent from the seen set.
- marker_resolution.py:452 SKIP: bad file absent from the list.
- marker_resolution.py:690,722 SKIP: non-int keys get explicit miss reasons.
- marker_resolution.py:932,938,946 SKIP: unreadable level -> None -> refusal reasons.
- marker_routing.py:1330 SKIP: bad delivery line counted in neither outcome.
- caption_asset_gc.py:233 SKIP: unreadable file not counted in tree bytes.
- caption_asset_gc.py:265 FALLBACK 0: reclaim-reporting sums only.
- caption_asset_gc.py:643 FALLBACK "": key omitted (admitted).
- caption_asset_gc.py:660 FALLBACK 0: the ledger count is re-listed live at compile; any mismatch refuses.
- caption_asset_gc.py:1119 SKIP: attribution survey; absent contributes nothing.
- caption_band.py:184 SKIP: bad block contributes no span, documented never-zero.
- capture_fusion_comps.py:60 SKIP: unreadable input -> absent key.
- code_identity.py:267 SKIP: missing file excluded from the hash (documented).
- color_page_grade.py:274 SKIP: None label -> caller REFUSES.
- comp_media_window.py:147 SKIP: bad tool -> None absence; caller records unreadable.
- composed_edit.py:280 FALLBACK 0: zero comps iterated; None absence reported per docstring.
- data_map.py:621,644 SKIP: bad file adds no shape.
- display_drift.py:211 SKIP: bad fingerprint absent from snapshot and count.
- display_drift.py:278 FALLBACK: explicit printed sentence, not a measurement.
- execution/apply_fusion_comps.py:512 FALLBACK None: horizon-checked with the receipt saying so.
- execution/organise_media_pool.py:503, execution/prune_orphans.py:324, execution/resolve_render.py:124, execution/retire_empty_bins.py:334 SKIP: bad entries absent from undo/size/fresh/journal lists (never zero).
- feedback_ledger.py:329 FALLBACK []: shape check defaults to ASK, the documented fail-safe to the captain.
- field_flow.py:1176 FALLBACK absolute path: still keyed and identified.
- field_flow.py:1184 SKIP: bad file absent from modules.
- footage_identity.py:205 SKIP: vanished file reads as removed downstream.
- gemma_shim.py:219 SKIP: failed fd close at launch.
- gemma_shim.py:450 FALLBACK error string: surfaced in `BackendUnavailable`.
- gemma_shim.py:1233 SKIP: uninstallable signal handler.
- grillme.py:158 SKIP: bad file yields no coverage.
- hooks.py:670 SKIP: bad line absent from the fingerprint check.
- input_contract.py:572,586,748,1081 SKIP: survey lists; bad file contributes nothing.
- output_contract.py:638,662 SKIP: survey sets; bad parse skipped.
- model_lifecycle.py:60,83,91 SKIP: failed cpu-move still freed; no value produced.
- mg_tight_box.py:942 FALLBACK None: explicit error entry appended.
- panel/frame_attach.py:174 SKIP: failed delete absent from removed.
- panel/trace.py:178 SKIP: display-only byte undercount; the file is still counted.
- panel/trace.py:511 FALLBACK 0: display string only.
- pipeline_skills.py:503 SKIP: absent receipt -> `GatingSkillSkipped` refuses.
- plan_provenance.py:678 SKIP: unparseable plan skipped, rest stands.
- project_context.py:124 SKIP: bad file absent from the map.
- project_data_guard.py:130,143 SKIP: unparseable JSON outside the guard's shape by docstring.
- project_migration.py:153 SKIP: missing file not counted in bytes.
- provenance.py:351,376,537 SKIP: bad lines absent from runs/artifacts.
- qa/subtitle_qa.py:232,248 SKIP: cleanup-only; the verdict is already recorded.
- remotion_batch.py:262,457 SKIP: bad/chatter lines absent from results.
- remotion_batch.py:557,588 SKIP: pipe-close/spec-unlink cleanup.
- replay_bench/snapshot.py:95 SKIP: missing entry absent from the drift hash.
- timeline_conformance.py:141 SKIP: absent type unchecked, never claimed over.
- timeline_variants.py:477 SKIP: bad reel adds no variant name.
- briefing_interview.py:104 SKIP: bad manifest not declaring.
- transform_drift.py:103,111 SKIP: skipped placement absent from drift rows.
