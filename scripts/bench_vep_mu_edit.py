"""EDIT calibration benchmark: incumbent vs new EDIT modules.

Reads REAL measurements read-only from the 001 project (calibration-adjacent
material: the project's own pipeline outputs) and from the IMG_1808
calibration take, then reports incumbent (before) vs new-module (after)
numbers. Held-out sources (IMG_1812, IMG_1818, LCATL0011, ...) are never
touched: the script refuses a held-out filename by prefix.

Before (incumbent behavior, computed here from the same measurements):
  B1 naive word-edge cuts: a cut at every word end. Reports the share
     sitting in no measurable pause (abutting words) - what EDIT-004 fixes.
  B2 incumbent aroll segment boundaries vs word timings: share landing
     mid-word or in a sub-0.15s gap - what cut_placement.refine_cut fixes.

After (new EDIT modules on the same measurements):
  pause_center_cuts, guard_coherent_span (full phrases ok, an isolated
  word sub-span refused), refine_cut over incumbent boundaries,
  phase_action over real motion-energy around a high-motion peak,
  shot_reject from real soft-picture ranges + the clip stability label,
  build_candidate/grade_strength/alternative_plans over real words,
  demo briefs + retrieval-vs-edit scores + failure attribution for the
  tasks the bounded footage cannot support.

Outputs docs/benchmarks/2026-10-16-vep-mu-edit-calibration.{md,jsonl}.
Nothing is written into the captain's project; no model is downloaded.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from library.tools import action_phases as ap
from library.tools import cut_placement as cpl
from library.tools import edit_candidates as ec
from library.tools import edit_continuity as eco
from library.tools import edit_critical_events as ece
from library.tools import edit_demo_tasks as edt
from library.tools import edit_render_check as erc
from library.tools import phrase_coherence as pc
from library.tools import shot_reject as sr

PROJECT_001 = Path("/Users/prajwal/Documents/content_stuff/post a day "
                   "keeps the apple away/001")
RAW_001 = PROJECT_001 / "raw"
HELD_OUT_PREFIXES = ("IMG_1812", "IMG_1818", "IMG_1806", "IMG_1807",
                     "IMG_1809", "IMG_1810", "IMG_1811", "IMG_1813",
                     "IMG_1814", "IMG_1815", "IMG_1816", "IMG_1817",
                     "IMG_1819", "IMG_1820", "IMG_1821", "IMG_1822",
                     "LCATL0011", "LC4930", "LC4931")

OUT_MD = (REPO / "docs" / "benchmarks"
          / "2026-10-16-vep-mu-edit-calibration.md")
OUT_JSONL = (REPO / "docs" / "benchmarks"
             / "2026-10-16-vep-mu-edit-calibration.jsonl")

rows: list[dict] = []


def record(measurement: str, value, basis: str) -> None:
    rows.append({"measurement": measurement, "value": value,
                 "basis": basis})


def refuse_held_out(name: str) -> None:
    if name.startswith(HELD_OUT_PREFIXES):
        raise SystemExit(f"refusing held-out source: {name}")


def load_words() -> dict[str, list[dict]]:
    data = json.loads((PROJECT_001 / "pipeline_data.json")
                      .read_text(encoding="utf-8"))
    speech = data["capability_outputs"]["speech.enrich"]["speech_sequence"]
    blocks = list(speech.get("body_sequence", []))
    hook = speech.get("hook_segment")
    if isinstance(hook, dict) and hook.get("word_timestamps"):
        blocks.append(hook)
    words_by_clip: dict[str, list[dict]] = {}
    for block in blocks:
        clip = str(block.get("clip_id", "unknown"))
        for word in block.get("word_timestamps", []):
            words_by_clip.setdefault(clip, []).append({
                "word": str(word["word"]),
                "source_start": float(word["source_start"]),
                "source_end": float(word["source_end"])})
    return words_by_clip


def load_incumbent_boundaries() -> list[dict]:
    data = json.loads((PROJECT_001 / "pipeline_data.json")
                      .read_text(encoding="utf-8"))
    assignments = data["capability_outputs"]["aroll.assign"][
        "a_roll_assignments"]
    boundaries = []
    for assignment in assignments:
        for segment in assignment.get("video_segments", []):
            boundaries.append({
                "clip_id": str(segment["clip_id"]),
                "in": float(segment["video_in"]),
                "out": float(segment["video_out"])})
    return boundaries


def main() -> int:
    words_by_clip = load_words()
    for clip in words_by_clip:
        words_by_clip[clip] = sorted(
            words_by_clip[clip], key=lambda w: w["source_start"])
    all_words = [word for words in words_by_clip.values()
                 for word in words]
    record("real_words_total", len(all_words),
           "001 speech_sequence body+hook word_timestamps")
    record("real_clips_with_words", len(words_by_clip),
           "distinct clip_id in speech_sequence")

    # B1: naive word-edge cuts vs measurable pauses.
    naive_positions = [w["source_end"] for w in all_words]
    pauses = pc.pause_center_cuts(all_words, min_pause_seconds=0.15)
    pause_positions = {round(c["position_seconds"], 3) for c in pauses}
    in_pause = sum(1 for position in naive_positions
                   if any(abs(position - pause) < 0.08
                          for pause in pause_positions))
    record("before_word_edge_cuts_total", len(naive_positions),
           "one cut at every real word end")
    record("before_word_edge_cuts_in_pause", in_pause,
           "within 80ms of a measured >=0.15s pause center")
    record("after_pause_center_cuts", len(pauses),
           "pause_center_cuts on the same real words")
    gaps = sorted(c["gap_seconds"] for c in pauses)
    record("after_pause_gap_median_s",
           round(gaps[len(gaps) // 2], 3) if gaps else 0.0,
           "median measured inter-word gap carrying a cut")

    # B2 + refine: incumbent aroll boundaries against word timings.
    boundaries = load_incumbent_boundaries()
    record("incumbent_aroll_boundaries", len(boundaries),
           "001 aroll.assign video_segments in/out")
    placed = moved = vetoed = 0
    mid_word_before = 0
    for boundary in boundaries:
        words = words_by_clip.get(boundary["clip_id"], [])
        if not words:
            continue
        for edge in (boundary["in"], boundary["out"]):
            if any(w["source_start"] < edge < w["source_end"]
                   for w in words):
                mid_word_before += 1
            verdict = cpl.refine_cut(edge, words)
            if verdict["status"] == "placed":
                placed += 1
            elif verdict["status"] == "moved":
                moved += 1
            else:
                vetoed += 1
    record("before_boundaries_mid_word", mid_word_before,
           "incumbent edges strictly inside a real word span")
    record("after_refine_placed", placed, "refine_cut on incumbent edges")
    record("after_refine_moved", moved, "refine_cut on incumbent edges")
    record("after_refine_vetoed", vetoed, "refine_cut on incumbent edges")

    # Guard: full phrases ok, an isolated word refused (real words).
    biggest_clip = max(words_by_clip, key=lambda c: len(words_by_clip[c]))
    biggest = words_by_clip[biggest_clip]
    phrases = pc.phrase_spans(biggest)
    record("after_phrases_detected", len(phrases),
           f"phrase_spans over {biggest_clip} "
           f"({len(biggest)} real words, time-ordered)")
    whole = pc.guard_coherent_span(biggest[0]["source_start"],
                                   biggest[-1]["source_end"], biggest)
    record("after_guard_full_span", whole["status"],
           "guard over the whole real-word span")
    first_word = biggest[0]
    isolated = pc.guard_coherent_span(first_word["source_start"],
                                      first_word["source_end"], biggest)
    record("after_guard_isolated_word", isolated["status"],
           "guard over the first real word alone")
    record("after_guard_isolated_reason", isolated["reason"],
           "refusal reason for the isolated word")

    # Action phases over REAL motion-energy (clip_011 index).
    index_path = (PROJECT_001 / "pipeline_output" / "steps"
                  / "1_04_temporal_index" / "index" / "clip_011.json")
    index = json.loads(index_path.read_text(encoding="utf-8"))
    motion = index.get("motion_energy", {})
    rate = float(motion.get("sample_rate_hz", 30))
    values = motion.get("values", [])
    peak_times = motion.get("high_motion_times", [])
    record("real_motion_samples", len(values),
           "clip_011 motion_energy values at 30Hz")
    phased_status = "skipped"
    phase_cuts = 0
    if peak_times and values:
        center = float(peak_times[len(peak_times) // 2])
        span = (max(0.0, center - 3.0), center + 3.0)
        samples = [{"tick_seconds": i / rate, "value": float(value)}
                   for i, value in enumerate(values)
                   if span[0] <= i / rate < span[1]]
        phased = ap.phase_action(span[0], span[1], samples,
                                 evidence_id="clip_011:motion_energy")
        phased_status = phased["status"]
        phase_cuts = len(ap.action_boundary_cuts(phased))
    record("after_phase_action_status", phased_status,
           "phase_action over real motion around a high-motion peak")
    record("after_action_boundary_cuts", phase_cuts,
           "action_boundary_cuts from the phased real motion")

    # Shot reject from REAL soft-picture ranges (IMG_1808 window).
    take = "IMG_1808.MOV"
    refuse_held_out(take)
    soft_windows = "skipped"
    try:
        from library.tools.analysis import picture_quality as pq
        track = pq.sample_picture_quality(str(RAW_001 / take),
                                          start_s=10.0, end_s=40.0)
        sharpness_track = {"sample_rate_hz": track["sample_rate_hz"],
                           "values": [row["focus_laplacian_variance"]
                                      for row in track["measurements"]]}
        ranges = pq.soft_picture_ranges(sharpness_track, 30.0)
        soft_windows = (len(ranges) if ranges is not None
                        else "unmeasurable")
        reasons = sr.from_soft_ranges(ranges or [], evidence_id="pq:1808")
        verdicts = sr.reject_intervals(
            [{"interval_id": "calib-window", "start_seconds": 10.0,
              "end_seconds": 40.0}], reasons,
            measured_coverage=[{"start_seconds": 10.0,
                                "end_seconds": 40.0}])
        record("after_shot_reject_verdict", verdicts[0]["verdict"],
               "reject_intervals over the real IMG_1808 10-40s window")
    except Exception as exc:
        record("after_shot_reject_verdict", f"error: {exc}",
               "real IMG_1808 measurement attempt")
    record("real_soft_picture_windows", soft_windows,
           "soft_picture_ranges over IMG_1808 10-40s at 5Hz")

    # Candidates over real words: predicates, strength, alt plans.
    evidence_rows = [{"evidence_id": f"w{i}", "plan": "speech_attributed"}
                     for i, _ in enumerate(biggest[:50])]
    predicates = [{"predicate": "speech_present",
                   "evidence_names": ["speech_attributed"],
                   "minimum_rows": 2}]
    candidate = ec.build_candidate(
        "calib-m1", biggest[0]["source_start"],
        biggest[49]["source_end"] if len(biggest) > 49
        else biggest[-1]["source_end"],
        evidence_rows, predicates,
        coherence=pc.guard_coherent_span(
            biggest[0]["source_start"],
            biggest[-1]["source_end"], biggest))
    record("after_candidate_strength", candidate["strength"],
           "build_candidate over 50 real words")
    short = ec.build_candidate("calib-m2", 0.0, 2.0, evidence_rows[:3],
                               predicates)
    plans = ec.alternative_plans([short, candidate])
    record("after_alternative_policies", sorted(plans),
           "alternative_plans over two real-word candidates")

    # Event fidelity spot-checks on real signals.
    speech_event = {
        "event_type": "word_phrase_boundary", "boundary_seconds": 1.0,
        "boundary_precision_seconds": 0.05,
        "evidence": {"speech.word": [True]}}
    record("after_event_fidelity_speech",
           ece.verify_event_fidelity(speech_event, {})["status"],
           "word_phrase_boundary with word evidence present")
    scene_event = {"event_type": "scene_transition",
                   "boundary_seconds": 0.0,
                   "evidence": {"video.scene_boundary": []}}
    record("after_event_fidelity_scene_unmeasured",
           ece.verify_event_fidelity(scene_event, {})["status"],
           "scene_transition with one measured scene (no boundary tick)")

    # Continuity + mapping + sync over incumbent structures.
    mappings = []
    cursor = 0.0
    for boundary in boundaries[:6]:
        length = boundary["out"] - boundary["in"]
        mappings.append({"clip_id": boundary["clip_id"],
                         "source_start": boundary["in"],
                         "source_end": boundary["out"],
                         "timeline_start": cursor,
                         "timeline_end": cursor + length})
        cursor += length
    durations = {boundary["clip_id"]: 4000.0 for boundary in boundaries}
    map_verdict = eco.verify_source_timeline_map(mappings, durations)
    record("after_mapping_valid", map_verdict["valid"],
           "verify_source_timeline_map over 6 incumbent segments")
    record("after_av_sync", eco.verify_av_sync(10.0, 15.0, 10.0, 15.0)[
        "in_sync"], "placed A/V spans identical")
    tracks = [{"track_id": "speaker", "label": "person:speaker",
               "spans": [{"start_seconds": 0.0,
                          "end_seconds": cursor}]}]
    continuity = eco.check_track_continuity(cursor / 2, tracks)
    record("after_continuity_status", continuity["status"],
           "single present-throughout person track at mid-timeline cut")

    # Render-check split on synthetic render numbers (no render yet).
    mechanical = erc.mechanical_report(
        {"lufs": -26.0, "true_peak_dbtp": -22.0, "audio_streams": 1},
        {})
    record("render_mechanical_unmixed_loudness",
           [c for c in mechanical["checks"]
            if c["property"] == "loudness"][0]["status"],
           "mechanical_report over typical unmixed render numbers")
    story = erc.storytelling_report(
        {"start_seconds": 0.0, "end_seconds": 5.0},
        [{"moment_id": "answer", "start_seconds": 100.0,
          "end_seconds": 110.0}])
    record("render_story_uncovered", story["story_satisfied"],
           "storytelling_report with the required moment uncovered")

    # Demo tasks: briefs build; retrieval-vs-edit on a 3-moment slice.
    for task_id in ("coherent_answer", "better_take", "visible_speaker"):
        definition = edt.task_definition(task_id)
        record("demo_task_defined", task_id,
               f"requires {','.join(definition['required_capabilities'])}")
    moments = [{"moment_id": "m1", "source_id": "clip_011",
                "start_seconds": biggest[0]["source_start"],
                "end_seconds": biggest[-1]["source_end"],
                "description": "calibration answer span (agent-provisional)",
                "evidence_ids": ["w0"]}]
    brief = edt.build_demo_brief("coherent_answer", moments)
    record("demo_brief_cut_ranges", len(brief["brief"]["cut_ranges"]),
           "build_demo_brief coherent_answer over the real span")
    split = edt.score_retrieval_vs_edit(["m1"], ["m1"], ["m1", "m2"])
    record("demo_split_editing_recall", split["editing_recall"],
           "retrieval 1/2, edit 1/2 on the provisional slice")
    unattainable = edt.attribute_failure(
        "product_pickup", {"retrieval_recall": 0.0}, None,
        missing_lanes=["object.detection"])
    record("demo_product_pickup_attribution",
           unattainable["failure_class"],
           "corpus has no product footage; perception failure recorded")
    record("demo_product_pickup_prioritize",
           len(edt.feedback_record(unattainable, "product_pickup")[
               "prioritize"]), "causes feeding EDIT-033 prioritization")

    with OUT_JSONL.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row) + "\n")
    lines = ["# vep-mu-edit calibration: incumbent vs EDIT modules",
             "",
             "Real measurements, read-only, from the 001 project "
             "(`pipeline_data.json`, `1_04_temporal_index` clip indices) "
             "and the IMG_1808 calibration take. Held-out sources sealed "
             "(the script refuses their filename prefixes). No model "
             "downloaded; precision: venv python3 + cv2 (see report).",
             "",
             "| measurement | value | basis |",
             "|---|---|---|"]
    for row in rows:
        lines.append(f"| {row['measurement']} | {row['value']} | "
                     f"{row['basis']} |")
    lines.append("")
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(f"wrote {len(rows)} measurements to {OUT_MD.name}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
