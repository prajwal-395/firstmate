#!/usr/bin/env python3
"""Step 5.01 post-bridge: the colourist's answer becomes one CDL per clip.

Reads `color_correction` and `grade_assessment` off the answer, joins
them to the measurements the pre-bridge took, and assembles
`color_grade_spec`.

**Three things this deliberately does not do.**

It does not substitute a value for a term the answer left out: a term the
colourist did not write is neutral, and neutral means the picture is not
moved on that axis (AGENTS.md 10.5).

It does not clamp. How far a correction may travel is the colourist's,
the same way `house_look` puts no bound on a declared slope. A value that
is not the SHAPE a CDL takes is a different matter and is REFUSED by
name, which is how it reaches the model that wrote it - see
`library/tools/post_bridge_retry.py`.

And it does not read an empty answer as an approval. `decided=True` is
passed because a model answered this step, so an empty list records
`judged_no_correction_needed`; a run that never reached a model records
`no_correction_decision`. Those are different facts and the old output
conflated them into one identity CDL.

The work is `resolve_color_grade`, which takes the merged answer and
returns `color_grade_spec`.  `main()` owns the process: stdin and
stdout.  See AGENTS.md 3.
"""
import json
import sys

from library.steps.step_5_01_color_grade.grade import (
    collect_entries,
    define_color_grade,
    measure_clips,
)
from library.tools.color_correction import (
    ASSESSMENT_FIELD,
    FIELD,
    read_corrections,
)
from library.tools.house_look import effective_house_look


def resolve_color_grade(data: dict) -> dict:
    """Join the colourist's answer to the pre-bridge's measurements.

    `data` is the merged dict the runner hands a post-bridge: the step's
    inputs, the pre-bridge's output, and the model's answer.
    """
    if not isinstance(data, dict):
        raise ValueError("Input data must be a dictionary")

    project_folder = data.get("project_folder", "")
    entries = collect_entries(data)

    # The pre-bridge's own measurement, carried through by the runner.
    # Re-measuring here would run ffprobe a second time over every source
    # file and could answer differently; a step whose two halves disagree
    # about what they measured is the defect, not the saving.
    measured_clips = data.get("clip_exposure")
    if not isinstance(measured_clips, list) or not measured_clips:
        measured_clips = measure_clips(entries, project_folder)

    corrections, dropped = read_corrections(
        data.get(FIELD), [row["clip_id"] for row in measured_clips])

    for entry in dropped:
        print(f"  5.01: dropped a correction for "
              f"{entry.entry.get('clip_id') or '(no clip named)'} - "
              f"{entry.reason}: {entry.detail}", file=sys.stderr)

    style = (data.get("brand_template") or {}).get("style", {})
    result = define_color_grade(
        {"entries": entries},
        project_folder,
        style.get("reference_look_image", ""),
        effective_house_look(style, project_folder),
        measured_clips=measured_clips,
        corrections=corrections,
        dropped=dropped,
        # A model answered this step. The ONLY thing that separates a
        # judged no-correction from an absent decision.
        decided=True,
        assessment=str(data.get(ASSESSMENT_FIELD) or "").strip(),
        subject_grades=data.get("subject_grades") or [],
    )
    for drop in (result["color_grade_spec"].get("subject_grade_drops")
                 or []):
        print(f"  5.01: dropped a subject grade for "
              f"{drop.get('clip_id') or '(no clip named)'} - "
              f"{drop['reason']}: {drop['detail']}", file=sys.stderr)
    for mismatch in (result["color_grade_spec"]["correction_basis"].get(
            "assessment_mismatches") or []):
        print(f"  5.01: grade_assessment disagrees with what shipped - "
              f"{mismatch['detail']}", file=sys.stderr)
    return result


def main():
    json.dump(resolve_color_grade(json.loads(sys.stdin.read())),
              sys.stdout, indent=2)


if __name__ == "__main__":
    main()
