#!/usr/bin/env python3
"""
Step 3.3: Review Rough Cut — Mechanical Validation

Runs the deterministic checks that must pass before the LLM narrative
review runs. These are hard invariants — any failure is a reject.

Classification: Deterministic / Evaluation
Idempotent: Yes

Input:  {
    "a_roll_assignments": [...],
    "b_roll_assignments": [...],
    "audio_spine": {...},
    "project_folder": "string"
}
Output: {
    "mechanical_checks": {...},
    "passed": bool
}
Output (when strips could be drawn): {
    "roughcut_window_frames": "string"
}

The second key is the same routed sight step 3.02's bridge gives its
model (`library/tools/window_frames.py`), but for PLACED windows: every
A-roll video segment and every B-roll overlay this cut actually plays,
in timeline order. The deterministic half draws them because drawing is
measurement, not judgement; the narrative half (handoff.md) reads them.
Sight here is routed but ADVISORY: a window whose strip could not be
drawn is NAMED in the map and judged from the prose, and a run that
draws nothing emits no key at all. A missing picture never fails a cut.
"""
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from library.tools import window_frames as wf
from library.tools.duration_tolerance import DURATION_TOLERANCE
from library.tools.project_layout import Area, ProjectLayout


OUTPUT_KEY = "roughcut_window_frames"


def check_duration_invariant(a_roll_assignments: list) -> dict:
    """
    For every A-roll clip: source_out - source_in must equal
    timeline_end - timeline_start (within tolerance).
    """
    violations = []
    for ar in a_roll_assignments:
        tl_start = ar.get("timeline_start", 0)
        tl_end = ar.get("timeline_end", 0)
        tl_dur = round(tl_end - tl_start, 4)
        
        # Sum source duration from video_segments
        segments = ar.get("video_segments", [])
        src_dur = round(sum(
            seg.get("video_out", 0) - seg.get("video_in", 0)
            for seg in segments
        ), 4)
        
        delta = round(abs(src_dur - tl_dur), 4)
        if delta > DURATION_TOLERANCE:
            violations.append({
                "block_position": ar.get("spine_block_position", "?"),
                "block_type": ar.get("block_type", "?"),
                "source_duration": src_dur,
                "timeline_start": tl_start,
                "timeline_end": tl_end,
                "timeline_duration": tl_dur,
                "delta_seconds": delta,
                "segment_count": len(segments),
            })
    return {"passed": len(violations) == 0, "violations": violations}


def check_b_roll_duration_invariant(
    b_roll_assignments: list, b_roll_interjections: list = None,
) -> dict:
    """
    For every B-roll clip on V2, source_duration >= timeline_duration.

    Interjections are checked alongside assignments: they land on the same
    track and a cutaway that outruns its source freezes there just the
    same, whichever list it came from.
    """
    violations = []

    # step_3_02 emits video_in/video_out (source domain) and
    # timeline_start/timeline_end. Reading source_in/timeline_in here -
    # keys the assignment never carries - made src_dur 0 for every
    # clip, so this invariant never examined a real assignment.
    clips = [
        (br, br, "assignment") for br in (b_roll_assignments or [])
    ] + [
        (interj, interj["assigned_clip"], "interjection")
        for interj in (b_roll_interjections or [])
    ]

    for timing, source, kind in clips:
        src_dur = round(source["video_out"] - source["video_in"], 4)
        tl_dur = round(timing["timeline_end"] - timing["timeline_start"], 4)

        if src_dur < tl_dur - DURATION_TOLERANCE:
            violations.append({
                "clip_id": source.get("clip_id", "?"),
                "kind": kind,
                "source_duration": src_dur,
                "timeline_duration": tl_dur,
                "deficit": round(tl_dur - src_dur, 4)
            })
    return {"passed": len(violations) == 0, "violations": violations}


def check_timeline_continuity(a_roll_assignments: list) -> dict:
    """
    A-roll clips should form a continuous sequence on V1 with no
    unintended gaps between speech blocks.
    """
    gaps = []
    sorted_ar = sorted(a_roll_assignments, key=lambda x: x.get("timeline_start", 0))

    for i in range(1, len(sorted_ar)):
        prev_end = sorted_ar[i - 1].get("timeline_end", 0)
        curr_start = sorted_ar[i].get("timeline_start", 0)
        gap = round(curr_start - prev_end, 4)

        # A-roll can have intentional gaps (transition slots, B-roll-only
        # sections). Only flag negative gaps (overlaps).
        if gap < -DURATION_TOLERANCE:
            gaps.append({
                "between": [
                    sorted_ar[i - 1].get("spine_block_position", "?"),
                    sorted_ar[i].get("spine_block_position", "?"),
                ],
                "overlap_seconds": abs(gap),
            })

    return {
        "passed": len(gaps) == 0,
        "gaps": gaps,
    }


def check_source_files(a_roll_assignments: list, b_roll_assignments: list,
                        b_roll_interjections: list, project_folder: str) -> dict:
    """
    Every source_file must resolve to an existing file on disk.
    """
    missing = []
    layout = ProjectLayout(project_folder or os.getcwd())
    
    # A-roll: source_file is in video_segments
    for ar in a_roll_assignments:
        for seg in ar.get("video_segments", []):
            sf = seg.get("source_file")
            if not sf:
                continue
            # Absolute, or relative to the project - and the project is
            # what the layout owner anchors it to, so this resolves the
            # same whatever directory the step was run from.
            full_path = str(layout.resolve_project_relative(sf))
            if not os.path.exists(full_path):
                missing.append({
                    "clip_id": seg.get("clip_id", "?"),
                    "source_file": sf,
                    "resolved_path": full_path,
                })
    
    # B-roll: source_file is at top level
    for br in (b_roll_assignments or []):
        sf = br.get("source_file")
        if not sf:
            continue
        full_path = str(layout.resolve_project_relative(sf))
        if not os.path.exists(full_path):
            missing.append({
                "clip_id": br.get("clip_id", "?"),
                "source_file": sf,
                "resolved_path": full_path,
            })
    # B-roll interjections: source_file is in assigned_clip
    for interj in (b_roll_interjections or []):
        clip = interj.get("assigned_clip", {})
        sf = clip.get("source_file")
        if not sf:
            continue
        full_path = str(layout.resolve_project_relative(sf))
        if not os.path.exists(full_path):
            missing.append({
                "clip_id": clip.get("clip_id", "?"),
                "source_file": sf,
                "resolved_path": full_path,
            })
    
    return {"passed": len(missing) == 0, "missing": missing}


def check_no_duplicate_ranges(a_roll_assignments: list) -> dict:
    """
    No two A-roll clips should occupy overlapping timeline ranges.
    """
    conflicts = []
    sorted_ar = sorted(a_roll_assignments, key=lambda x: x.get("timeline_start", 0))

    for i in range(len(sorted_ar)):
        for j in range(i + 1, len(sorted_ar)):
            a = sorted_ar[i]
            b = sorted_ar[j]
            # Check overlap
            if (a.get("timeline_start", 0) < b.get("timeline_end", 0) and
                    b.get("timeline_start", 0) < a.get("timeline_end", 0)):
                conflicts.append({
                    "clip_a": a.get("spine_block_position", "?"),
                    "clip_b": b.get("spine_block_position", "?"),
                    "range_a": [a.get("timeline_start"), a.get("timeline_end")],
                    "range_b": [b.get("timeline_start"), b.get("timeline_end")],
                })

    return {
        "passed": len(conflicts) == 0,
        "conflicts": conflicts,
    }


def check_total_duration(a_roll_assignments: list, audio_spine: dict,
                         data: dict) -> dict:
    """
    Total timeline duration must not exceed the target duration by more
    than 50%.  This is a hard mechanical check - not LLM judgment.

    Reads target from project_config.target_duration_seconds (set by
    step_1_01 from project.json).  Defaults to 60 seconds.
    """
    # Compute actual duration from audio_spine or a_roll_assignments
    actual_duration = 0.0

    # Try audio_spine first (most accurate representation of timeline length)
    structure = audio_spine.get("structure", []) if isinstance(audio_spine, dict) else []
    if structure:
        actual_duration = max(
            (block.get("timeline_end", 0) for block in structure),
            default=0.0,
        )

    # Fallback to a_roll_assignments
    if actual_duration <= 0 and a_roll_assignments:
        actual_duration = max(
            (ar.get("timeline_end", 0) for ar in a_roll_assignments),
            default=0.0,
        )

    # Determine target duration zone from precedence rules
    from library.tools.duration_targets import (
        NO_TARGET_DECLARED, get_target_duration_zone)
    zone = get_target_duration_zone(data)

    if zone is None:
        # A gate with nothing to judge against says so.  It used to be
        # handed 54-66s by `duration_targets` whatever the project
        # declared, which reads as coverage (AGENTS.md 10.4).
        return {
            "passed": True,
            "checked": False,
            "actual_duration_seconds": round(actual_duration, 2),
            "reason": NO_TARGET_DECLARED,
        }

    min_dur, target_duration, max_dur = zone
    exceeded = actual_duration > max_dur or actual_duration < min_dur

    result = {
        "passed": not exceeded,
        "checked": True,
        "actual_duration_seconds": round(actual_duration, 2),
        "target_duration_seconds": round(target_duration, 2),
        "min_duration_seconds": round(min_dur, 2),
        "max_duration_seconds": round(max_dur, 2),
    }
    if exceeded:
        result["overshoot_ratio"] = round(actual_duration / target_duration, 2)

    return result


def run_mechanical_checks(data: dict) -> dict:
    """Run all mechanical checks and return combined result."""
    a_rolls = data.get("a_roll_assignments", [])
    b_rolls = data.get("b_roll_assignments", [])
    b_interjections = data.get("b_roll_interjections", [])
    project_folder = data.get("project_folder", ".")

    audio_spine = data.get("audio_spine", {})

    duration = check_duration_invariant(a_rolls)
    b_roll_duration = check_b_roll_duration_invariant(b_rolls, b_interjections)
    continuity = check_timeline_continuity(a_rolls)
    source_files = check_source_files(a_rolls, b_rolls, b_interjections, project_folder)
    duplicates = check_no_duplicate_ranges(a_rolls)
    total_dur = check_total_duration(a_rolls, audio_spine, data)

    all_passed = all([
        duration["passed"],
        b_roll_duration["passed"],
        continuity["passed"],
        source_files["passed"],
        duplicates["passed"],
        total_dur["passed"],
    ])

    result = {
        "passed": all_passed,
        "mechanical_checks": {
            "duration_invariant": duration,
            "b_roll_duration_invariant": b_roll_duration,
            "timeline_continuity": continuity,
            "source_files_exist": source_files,
            "no_duplicate_ranges": duplicates,
            "total_duration": total_dur,
        },
    }

    # Build rejection reasons
    rejection_reasons = []
    if not duration["passed"]:
        for v in duration["violations"]:
            rejection_reasons.append(
                f"Duration mismatch on {v.get('block_position', '?')} ({v.get('block_type', '?')}): "
                f"source is {v['source_duration']}s but timeline allocates "
                f"{v['timeline_duration']}s. Delta: {v['delta_seconds']}s."
            )
    if not b_roll_duration["passed"]:
        for v in b_roll_duration["violations"]:
            rejection_reasons.append(
                f"B-roll overruns source on clip {v.get('clip_id', '?')}: "
                f"source is {v['source_duration']}s but timeline needs "
                f"{v['timeline_duration']}s. Deficit: {v['deficit']}s."
            )
    if not continuity["passed"]:
        for g in continuity["gaps"]:
            rejection_reasons.append(
                f"Timeline overlap between {g['between'][0]} and "
                f"{g['between'][1]}: {g['overlap_seconds']}s overlap"
            )
    if not source_files["passed"]:
        for m in source_files["missing"]:
            rejection_reasons.append(
                f"Source file not found: {m['source_file']} "
                f"(resolved: {m['resolved_path']})"
            )
    if not duplicates["passed"]:
        for c in duplicates["conflicts"]:
            rejection_reasons.append(
                f"Overlapping ranges: {c['clip_a']} and {c['clip_b']}"
            )
    if not total_dur["passed"]:
        rejection_reasons.append(
            f"Total duration ({total_dur['actual_duration_seconds']}s) is outside "
            f"the target zone [{total_dur['min_duration_seconds']}s, {total_dur['max_duration_seconds']}s] "
            f"(target: {total_dur['target_duration_seconds']}s)"
        )

    result["rejection_reasons"] = rejection_reasons

    return result


def build_actual_script(a_roll_assignments: list, audio_spine: dict) -> dict:
    """What the viewer HEARS, block by block, from measured words.

    Check 5 asks the model to look up the temporal index for each
    A-roll range and concatenate the words in timeline order - range
    lookup plus string joining over word timings the pipeline already
    holds. This builds it instead, so the narrative review judges the
    measured script rather than a reconstruction.

    The words are the spine's own `word_timestamps`: the temporal
    index's words as step 2.02 bound them to each block, in the source
    domain the A-roll segments also play in. Reading them here needs
    no second mechanism beside `timeline_transcript.py` - that module
    rebuilds timeline audio and transcribes it with Whisper for LIVE
    Resolve timelines that carry no measured words at all, which is a
    different job for a different input. Forcing this concatenation
    into it would be a second mechanism in the module, so it lives
    here, beside the mechanical checks whose inputs it shares.

    Returns {"blocks": [...], "full_text": str, "unvoiced": [...]}.
    A played range with no measured words is NAMED in `unvoiced`,
    never silently skipped: heard audio with no transcript is a fact
    the review must see, not a gap the join may close over.
    """
    structure = (audio_spine or {}).get("structure", [])
    by_position = {}
    for block in structure or []:
        if isinstance(block, dict) and "position" in block:
            by_position[str(block["position"])] = block

    blocks = []
    unvoiced = []
    ordered = sorted(a_roll_assignments or [],
                     key=lambda ar: ar.get("timeline_start", 0))
    for ar in ordered:
        if not isinstance(ar, dict):
            continue
        position = ar.get("spine_block_position", "?")
        block = by_position.get(str(position), {})
        words = block.get("word_timestamps") or []
        for seg in ar.get("video_segments", []) or []:
            clip_id = seg.get("clip_id", "?")
            try:
                lo = float(seg.get("video_in"))
                hi = float(seg.get("video_out"))
            except (TypeError, ValueError):
                unvoiced.append({
                    "spine_block_position": position,
                    "clip_id": clip_id,
                    "reason": "the segment names no source range, so no "
                              "words can be looked up for it",
                })
                continue
            if not words:
                unvoiced.append({
                    "spine_block_position": position,
                    "clip_id": clip_id,
                    "source_in": lo,
                    "source_out": hi,
                    "reason": "the spine block carries no word timings, "
                              "so what this range says is undetermined",
                })
                continue
            spoken = [
                w for w in words
                if isinstance(w, dict)
                and w.get("source_start") is not None
                and w.get("source_end") is not None
                and float(w["source_start"]) < hi
                and float(w["source_end"]) > lo
            ]
            text = " ".join(
                str(w.get("word", "")) for w in spoken).strip()
            if not text:
                unvoiced.append({
                    "spine_block_position": position,
                    "clip_id": clip_id,
                    "source_in": lo,
                    "source_out": hi,
                    "reason": "no measured words fall inside the played "
                              "range - silence, music, or an untranscribed "
                              "stretch",
                })
                continue
            blocks.append({
                "spine_block_position": position,
                "block_type": ar.get("block_type",
                                    block.get("block_type", "?")),
                "clip_id": clip_id,
                "timeline_start": ar.get("timeline_start", 0.0),
                "source_in": lo,
                "source_out": hi,
                "text": text,
                "word_count": len(spoken),
            })

    return {
        "blocks": blocks,
        "full_text": " ".join(b["text"] for b in blocks),
        "unvoiced": unvoiced,
    }


def placed_windows(data: dict) -> list:
    """Every source window the cut plays, in timeline order.

    A-roll rows come from each assignment's `video_segments`; B-roll
    rows from the assignments and the interjections' `assigned_clip`.
    A row is `{clip_id, position, timeline_start, source_file,
    video_in, video_out, fps, kind}`. Rows that name no range carry
    `video_in`/`video_out` as None - the caller NAMES them, so the
    absence is not read as an absence of the window. A row whose
    assignment names no readable `timeline_start` carries it as None
    for the same reason: the strip is still drawn from the source
    range, and the map names the placement unknown rather than
    placing the window at 0.0.
    """
    rows = []
    for ar in data.get("a_roll_assignments", []) or []:
        position = ar.get("spine_block_position", "?")
        try:
            base = float(ar.get("timeline_start", 0.0))
        except (TypeError, ValueError):
            base = None
        offset = 0.0
        for seg in ar.get("video_segments", []) or []:
            try:
                video_in = float(seg.get("video_in"))
                video_out = float(seg.get("video_out"))
            except (TypeError, ValueError):
                video_in, video_out = None, None
            rows.append({
                "clip_id": seg.get("clip_id", "?"),
                "position": position,
                "timeline_start": (round(base + offset, 3)
                                   if base is not None else None),
                "source_file": seg.get("source_file") or "",
                "video_in": video_in,
                "video_out": video_out,
                "fps": seg.get("frame_rate") or 0.0,
                "kind": "a_roll",
            })
            if video_in is not None and video_out is not None:
                offset += max(0.0, video_out - video_in)
    for br in data.get("b_roll_assignments", []) or []:
        rows.append({
            "clip_id": br.get("clip_id", "?"),
            "position": br.get("spine_block_position", "?"),
            "timeline_start": br.get("timeline_start", 0.0),
            "source_file": br.get("source_file") or "",
            "video_in": br.get("video_in"),
            "video_out": br.get("video_out"),
            "fps": br.get("frame_rate") or 0.0,
            "kind": "b_roll",
        })
    for interj in data.get("b_roll_interjections", []) or []:
        clip = interj.get("assigned_clip", {}) or {}
        rows.append({
            "clip_id": clip.get("clip_id", "?"),
            "position": interj.get("over_spine_block_position",
                                  interj.get("spine_block_position", "?")),
            "timeline_start": interj.get("timeline_start", 0.0),
            "source_file": clip.get("source_file") or "",
            "video_in": clip.get("video_in"),
            "video_out": clip.get("video_out"),
            "fps": clip.get("frame_rate") or 0.0,
            "kind": "b_roll",
        })
    # Timeline order, and at one instant the HEARD window before the
    # SEEN one: the map says which is which, and the order says it too.
    rows.sort(key=lambda r: (
        float(r.get("timeline_start") or 0.0),
        0 if r["kind"] == "a_roll" else 1,
    ))
    return rows


def build_review_frames(data: dict) -> str:
    """Draw a strip per placed window, and map them for the prompt."""
    project_folder = data.get("project_folder") or ""
    if not project_folder:
        print("  No project_folder: no review frames drawn",
              file=sys.stderr)
        return ""

    layout = ProjectLayout(project_folder)
    directory = layout.write_dir(Area.ROUGHCUT_FRAMES,
                                 step="review_rough_cut")

    rows, missing, drawn = [], [], 0
    for window in placed_windows(data):
        try:
            video_in = float(window["video_in"])
            video_out = float(window["video_out"])
            bounds = (video_in, video_out)
        except (TypeError, ValueError):
            bounds = None
        label = (f"{window['clip_id']}@{window['video_in']}s"
                 if bounds is not None
                 else f"{window['clip_id']}@unknown")
        source = window.get("source_file") or ""
        full = str(layout.resolve_project_relative(source)) if source else ""
        if bounds is None or not full or not os.path.exists(full):
            missing.append(label)
            continue
        video_in, video_out = bounds
        try:
            fps = float(window.get("fps") or 0.0)
        except (TypeError, ValueError):
            fps = 0.0
        if drawn >= wf.MAX_STRIPS:
            missing.append(f"{label} (past the {wf.MAX_STRIPS}-strip bound)")
            continue
        times = wf.sample_times(video_in, video_out, fps)
        # The name carries what was DRAWN, so a strip drawn under an
        # older sampling rule is a different file and is never read as
        # this one. See library/tools/window_frames.py.
        name = wf.strip_filename(window["clip_id"], video_in, times)
        drawn += 1
        if not wf.draw_strip(full, times, str(directory / name)):
            missing.append(label)
            continue
        rows.append({
            "clip_id": window["clip_id"],
            "position": window["position"],
            # An unplaced window still draws its strip from the source
            # range; the map names the placement unknown rather than
            # printing the sort's 0.0 as a measurement.
            "timeline_start": (window["timeline_start"]
                               if window["timeline_start"] is not None
                               else "unknown"),
            "video_in": video_in,
            "video_out": video_out,
            "frames": len(times),
            "file": name,
        })

    print(f"  {len(rows)} review frame strip(s) at {directory}"
          + (f"; {len(missing)} not drawn" if missing else ""),
          file=sys.stderr)
    if not rows:
        return ""
    return wf.build_review_block(str(directory), rows, missing)


def main():
    data = json.loads(sys.stdin.read())

    result = run_mechanical_checks(data)

    # Print summary to stderr
    checks = result["mechanical_checks"]
    total = len(checks)
    passed = sum(1 for c in checks.values() if c["passed"])
    status = "✅ PASSED" if result["passed"] else "❌ REJECTED"

    print(f"\n{'='*60}", file=sys.stderr)
    print(f"  Rough Cut Review — Mechanical Checks", file=sys.stderr)
    print(f"{'='*60}", file=sys.stderr)
    for name, check in checks.items():
        icon = "✅" if check["passed"] else "❌"
        print(f"  {icon} {name}", file=sys.stderr)
    print(f"\n  {status} ({passed}/{total} checks passed)", file=sys.stderr)

    if result["rejection_reasons"]:
        print(f"\n  Rejection reasons:", file=sys.stderr)
        for r in result["rejection_reasons"]:
            print(f"    • {r}", file=sys.stderr)

    print(f"{'='*60}\n", file=sys.stderr)

    out = {"rough_cut_review": result}
    # Check 5's reconstruction, built by the script instead of the
    # model: what the viewer hears, from the spine's measured words.
    # Declared in manifest.json outputs and context_fields, so the
    # narrative review reads this rather than re-deriving it.
    out["actual_script"] = build_actual_script(
        data.get("a_roll_assignments", []), data.get("audio_spine", {}))
    frames_block = build_review_frames(data)
    if frames_block:
        out[OUTPUT_KEY] = frames_block

    json.dump(out, sys.stdout, indent=2)

    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
