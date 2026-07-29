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


DURATION_TOLERANCE = 0.1  # seconds


def check_duration_invariant(a_roll_assignments: list) -> dict:
    """
    For every A-roll clip: source_out - source_in must equal
    timeline_end - timeline_start (within tolerance).
    """
    violations = []

    for ar in a_roll_assignments:
        src_in = ar.get("source_in", 0)
        src_out = ar.get("source_out", 0)
        tl_start = ar.get("timeline_start", 0)
        tl_end = ar.get("timeline_end", 0)

        src_dur = round(src_out - src_in, 4)
        tl_dur = round(tl_end - tl_start, 4)
        delta = round(abs(src_dur - tl_dur), 4)

        if delta > DURATION_TOLERANCE:
            violations.append({
                "entry_id": ar.get("entry_id", "?"),
                "clip_id": ar.get("clip_id", "?"),
                "source_in": src_in,
                "source_out": src_out,
                "source_duration": src_dur,
                "timeline_start": tl_start,
                "timeline_end": tl_end,
                "timeline_duration": tl_dur,
                "delta_seconds": delta,
                "fix": (
                    f"Expand timeline to {src_dur}s to play the full speech block, "
                    f"OR produce sub-segments that total {tl_dur}s with filler removed"
                ),
            })

    return {
        "passed": len(violations) == 0,
        "violations": violations,
    }


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
                    sorted_ar[i - 1].get("entry_id", "?"),
                    sorted_ar[i].get("entry_id", "?"),
                ],
                "overlap_seconds": abs(gap),
            })

    return {
        "passed": len(gaps) == 0,
        "gaps": gaps,
    }


def check_source_files(a_roll_assignments: list, b_roll_assignments: list,
                        project_folder: str) -> dict:
    """
    Every source_file must resolve to an existing file on disk.
    """
    missing = []

    all_assignments = list(a_roll_assignments)
    for br in (b_roll_assignments or []):
        clip = br.get("assigned_clip", {})
        if clip.get("source_file"):
            all_assignments.append(clip)

    for ar in all_assignments:
        sf = ar.get("source_file")
        if not sf:
            continue
        full_path = os.path.join(project_folder, sf)
        if not os.path.exists(full_path):
            missing.append({
                "entry_id": ar.get("entry_id", ar.get("clip_id", "?")),
                "source_file": sf,
                "resolved_path": full_path,
            })

    return {
        "passed": len(missing) == 0,
        "missing": missing,
    }


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
                    "clip_a": a.get("entry_id", "?"),
                    "clip_b": b.get("entry_id", "?"),
                    "range_a": [a.get("timeline_start"), a.get("timeline_end")],
                    "range_b": [b.get("timeline_start"), b.get("timeline_end")],
                })

    return {
        "passed": len(conflicts) == 0,
        "conflicts": conflicts,
    }


def run_mechanical_checks(data: dict) -> dict:
    """Run all mechanical checks and return combined result."""
    a_rolls = data.get("a_roll_assignments", [])
    b_rolls = data.get("b_roll_assignments", [])
    project_folder = data.get("project_folder", ".")

    duration = check_duration_invariant(a_rolls)
    continuity = check_timeline_continuity(a_rolls)
    source_files = check_source_files(a_rolls, b_rolls, project_folder)
    duplicates = check_no_duplicate_ranges(a_rolls)

    all_passed = all([
        duration["passed"],
        continuity["passed"],
        source_files["passed"],
        duplicates["passed"],
    ])

    result = {
        "passed": all_passed,
        "mechanical_checks": {
            "duration_invariant": duration,
            "timeline_continuity": continuity,
            "source_files_exist": source_files,
            "no_duplicate_ranges": duplicates,
        },
    }

    # Build rejection reasons
    rejection_reasons = []
    if not duration["passed"]:
        for v in duration["violations"]:
            rejection_reasons.append(
                f"Duration mismatch on {v['clip_id']} ({v['entry_id']}): "
                f"source is {v['source_duration']}s but timeline allocates "
                f"{v['timeline_duration']}s. Delta: {v['delta_seconds']}s. "
                f"Fix: {v['fix']}"
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

    json.dump(result, sys.stdout, indent=2)

    if not result["passed"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
