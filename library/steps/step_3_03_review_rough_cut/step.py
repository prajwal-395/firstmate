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
"""
import json
import os
import sys


DURATION_TOLERANCE = 0.15  # seconds


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
    
    # A-roll: source_file is in video_segments
    for ar in a_roll_assignments:
        for seg in ar.get("video_segments", []):
            sf = seg.get("source_file")
            if not sf:
                continue
            # Try absolute, then relative to project
            if not os.path.isabs(sf):
                full_path = os.path.join(project_folder, sf)
            else:
                full_path = sf
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
        if not os.path.isabs(sf):
            full_path = os.path.join(project_folder, sf)
        else:
            full_path = sf
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
        if not os.path.isabs(sf):
            full_path = os.path.join(project_folder, sf)
        else:
            full_path = sf
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

    # Determine target duration from project_config
    target_duration = 60.0  # default
    project_config = data.get("project_config", {})
    if isinstance(project_config, dict):
        pct = project_config.get("target_duration_seconds")
        if pct is not None and float(pct) > 0:
            target_duration = float(pct)

    threshold = target_duration * 1.5
    exceeded = actual_duration > threshold

    result = {
        "passed": not exceeded,
        "actual_duration_seconds": round(actual_duration, 2),
        "target_duration_seconds": round(target_duration, 2),
        "threshold_seconds": round(threshold, 2),
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
    continuity = check_timeline_continuity(a_rolls)
    source_files = check_source_files(a_rolls, b_rolls, b_interjections, project_folder)
    duplicates = check_no_duplicate_ranges(a_rolls)
    total_dur = check_total_duration(a_rolls, audio_spine, data)

    all_passed = all([
        duration["passed"],
        continuity["passed"],
        source_files["passed"],
        duplicates["passed"],
        total_dur["passed"],
    ])

    result = {
        "passed": all_passed,
        "mechanical_checks": {
            "duration_invariant": duration,
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
            f"Total duration ({total_dur['actual_duration_seconds']}s) exceeds "
            f"target ({total_dur['target_duration_seconds']}s) by more than 50% "
            f"(threshold: {total_dur['threshold_seconds']}s, "
            f"ratio: {total_dur.get('overshoot_ratio', 'N/A')}x)"
        )

    result["rejection_reasons"] = rejection_reasons

    return result


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

    json.dump({"rough_cut_review": result}, sys.stdout, indent=2)

    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
