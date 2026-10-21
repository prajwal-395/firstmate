"""QA-008: prove the final acceptance state on scratch outputs.

Three scratch tasks, each judged by the ENGINE'S OWN legs - AXI-013's
operation contract, QA-001's structural read-back, QA-005's look
comparison, QA-002/003/004's delivered-file gates and QA-006's
editorial review - and each ending in a different state:

* one FULLY ACCEPTED;
* one TECHNICALLY VALID BUT EDITORIALLY REJECTED;
* one with an UNMEASURED leg.

Everything is built under a temporary directory and the file is removed
when the run ends: the proof leaves nothing behind, and it never reads
or touches a captain's project.

    python3 scripts/acceptance_state_proof.py [--base <sha>]

`--base` names the commit the BEFORE measurement is taken from (the
merge-base with origin/main by default), so the report says what the
task records could express before this change and what they can
express now, with denominators.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import (
    acceptance_state,
    deliverable_qc,
    delivery_spec,
)
from library.tools import (
    assignment_lifecycle as lifecycle,
)
from library.tools.editorial_acceptance import judgement

FPS = 15
FRAMES = 90
WIDTH = 540
HEIGHT = 960


# ── the scratch material ─────────────────────────────────────────────────

def _clip(unique_id, record_in, record_out, **over):
    """One timeline item, in the shape a live snapshot carries."""
    clip = {
        "unique_id": unique_id,
        "name": unique_id,
        "record_in": record_in,
        "record_out": record_out,
        "duration": record_out - record_in,
        "source_in_frame": 0,
        "source_out_frame": record_out - record_in,
        "source_frames": 1000,
        "source_file": f"/media/{unique_id}.mov",
        "media_pool_item_id": f"media-{unique_id}",
        "media_type": "video",
        "track_type": "video",
        "enabled": True,
        "speed": 100.0,
        "transform": {},
        "linked_unique_ids": [],
    }
    clip.update(over)
    return clip


def _snapshot(*clips, **over):
    snapshot = {
        "timeline": "Reel 01",
        "project": "Scratch",
        "start_frame": 0,
        "end_frame": 1000,
        "tracks": [{"type": "video", "index": 1, "name": "V1",
                    "clips": [dict(clip) for clip in clips]}],
    }
    snapshot.update(over)
    return snapshot


def _render(path: Path) -> str:
    """A synthetic deliverable: moving picture and a tone at the spec.

    The picture MOVES (a freeze would be the picture spec's own
    failure) and the audio is normalized to the delivery spec's
    loudness, so the audio leg is judged on the audio and not on the
    ffmpeg defaults.
    """
    subprocess.run([
        "ffmpeg", "-y", "-loglevel", "error",
        "-f", "lavfi", "-i",
        f"testsrc2=size={WIDTH}x{HEIGHT}:rate={FPS}:duration={FRAMES / FPS}",
        "-f", "lavfi", "-i",
        f"sine=frequency=440:duration={FRAMES / FPS}",
        "-vf", f"scale={WIDTH}:{HEIGHT}",
        "-af", "loudnorm=I=-14:TP=-1.5:LRA=11",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-c:a", "aac",
        "-shortest", str(path),
    ], check=True)
    return str(path)


def _subjective_rows(refused_leg: str = "") -> list[dict]:
    """A judge's rows for all eight legs of the editorial review."""
    rows = []
    for leg in judgement.REVIEW_LEGS:
        status = "fail" if leg.id == refused_leg else "pass"
        rows.append({
            "leg": leg.id,
            "status": status,
            "rationale": (f"the judge read the delivered file and judged "
                          f"{leg.id}"),
            "evidence": [{"artifact": "scratch/reel-01.mp4",
                          "observation": f"the judge watched {leg.id}"}],
            "uncertainty": {"level": "low",
                            "note": "one reading, and the judge says so"},
        })
    return rows


def _review(refused_leg: str = "") -> dict:
    """The editorial review, off QA-006's own contract."""
    return judgement.review(
        task_id="scratch",
        brief={"instruction": "cut the pricing moments into a reel",
               "project_state": {},
               "acceptance_criteria": []},
        render={"sha256": "0" * 64, "evidence": [], "duration_seconds": 6.0},
        judgments=_subjective_rows(refused_leg))


# ── the three tasks ──────────────────────────────────────────────────────

def _facts(**overrides):
    facts = {
        "applied_mutations": True,
        "has_timeline": True,
        "has_approved_look": True,
        "has_delivered_file": True,
        "has_captions": False,
        "requires_approval": True,
    }
    facts.update(overrides)
    return facts


def _operation_results():
    """AXI-013's own acceptance contract, judged with its evidence."""
    return acceptance_state.operation_result([{
        "op": "clip.trim",
        "acceptance_op": "clip.trim",
        "readback_passed": True,
        "evidence": {
            "approved_edits_preserved": True,
            "linked_audio_preserved": True,
            "unrelated_items_unchanged": True,
        },
    }])


def _build_tasks(scratch: Path) -> list[dict]:
    """The three scratch tasks, each with the engine's own measurements."""
    video = _render(scratch / "reel-01.mp4")
    spec = delivery_spec.resolve_delivery_spec("tiktok")
    qc = deliverable_qc.run_deliverable_qc(
        video, spec, FRAMES / FPS,
        expected_width=WIDTH, expected_height=HEIGHT)

    clean = _snapshot(_clip("clip-1", 0, 1000))
    # A look that MOVED with nothing to say whether it may: the
    # difference is real and it is unprovable, which is not a pass.
    moved = _snapshot(_clip("clip-1", 0, 1000,
                            color={"node_count": 4, "cdl": {"gain": 1.1}}))
    unchanged = _snapshot(_clip("clip-1", 0, 1000,
                                color={"node_count": 3, "cdl": {"gain": 1.0}}))

    delivered = acceptance_state.delivered_results(
        video, spec, expected_duration=FRAMES / FPS,
        expected_width=WIDTH, expected_height=HEIGHT, qc=qc)

    def _base_results() -> dict:
        """Every leg measured and passing, read from the engine's own gates."""
        results = {
            "operation_pre_post": _operation_results(),
            "timeline_structure": acceptance_state.structure_result(clean),
            "look_preservation": acceptance_state.look_result(
                unchanged, unchanged),
        }
        results.update(delivered)
        results["editorial_review"] = acceptance_state.editorial_result(
            _review())
        results["reviewer_approval"] = acceptance_state.approval_result(
            {"approved": True, "reviewer": "the captain"})
        return results

    tasks = []

    # 1. fully accepted
    tasks.append({"id": "scratch-accepted", "facts": _facts(),
                  "results": _base_results()})

    # 2. technically valid, editorially rejected
    results = _base_results()
    results["editorial_review"] = acceptance_state.editorial_result(
        _review("pacing_and_rhythm"))
    tasks.append({"id": "scratch-editorially-rejected", "facts": _facts(),
                  "results": results})

    # 3. a leg nobody could judge
    results = _base_results()
    results["look_preservation"] = acceptance_state.look_result(
        unchanged, moved)
    tasks.append({"id": "scratch-unmeasured", "facts": _facts(),
                  "results": results})
    return tasks


# ── the report ───────────────────────────────────────────────────────────

def _before_measurement(base: str) -> dict:
    """What a task record could express BEFORE this change."""
    def _read(path: str) -> str:
        result = subprocess.run(
            ["git", "show", f"{base}:{path}"], capture_output=True,
            text=True, encoding="utf-8", check=False)
        return result.stdout if result.returncode == 0 else ""

    lifecycle_source = _read("library/tools/assignment_lifecycle.py")
    match = re.search(r"^STATES = \((.*?)\)\s*$", lifecycle_source,
                      re.DOTALL | re.MULTILINE)
    states = re.findall(r'"([a-z_]+)"', match.group(1)) if match else []
    contract = _read("library/tools/assignment_contract.py")
    return {
        "base": base,
        "states": states,
        "state_count": len(states),
        "acceptance_states": [state for state in states
                              if state in acceptance_state.ACCEPTANCE_STATES],
        "carries_an_acceptance_verdict": '"final"' in contract,
    }


def _after_measurement() -> dict:
    states = list(lifecycle.STATES)
    return {
        "states": states,
        "state_count": len(states),
        "acceptance_states": [state for state in states
                              if state in acceptance_state.ACCEPTANCE_STATES],
        "carries_an_acceptance_verdict": True,
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="acceptance_state_proof",
        description="Prove the final acceptance state on scratch outputs")
    parser.add_argument("--base", default=None,
                        help="the commit the BEFORE measurement is taken from")
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    args = parser.parse_args(argv)

    base = args.base
    if base is None:
        result = subprocess.run(
            ["git", "merge-base", "HEAD", "origin/main"],
            capture_output=True, text=True, encoding="utf-8", check=False)
        base = result.stdout.strip() or "HEAD"

    scratch = Path(tempfile.mkdtemp(prefix="qa008-acceptance-"))
    report = {"before": _before_measurement(base),
              "after": _after_measurement(), "tasks": []}
    try:
        tasks = _build_tasks(scratch)
        project = scratch / "project"
        project.mkdir()
        (project / "project.yaml").write_text(
            'name: "Scratch"\nslug: scratch\n', encoding="utf-8")
        for task in tasks:
            aid = task["id"]
            lifecycle.create(str(project), aid, brief="a scratch task",
                             request="a scratch task", kind="reels")
            for state in ("draft", "review", "approved", "delivered"):
                lifecycle.transition(str(project), aid, state)
            verdict = acceptance_state.record(
                str(project), aid, facts=task["facts"],
                results=task["results"])
            stored = lifecycle.load(str(project), aid)
            report["tasks"].append({
                "assignment_id": aid,
                "state": stored["state"],
                "outcome": verdict["outcome"],
                "reason": verdict["reason"],
                "counts": verdict["counts"],
                "legs": [{"id": row["id"], "kind": row["kind"],
                          "status": row["status"],
                          "observation": row["observation"]}
                         for row in verdict["legs"]],
            })
    finally:
        subprocess.run(["rm", "-rf", str(scratch)], check=False)

    totals = {"total": 0, "applicable": 0, "passed": 0, "failed": 0,
              "unmeasured": 0, "not_applicable": 0}
    for task in report["tasks"]:
        for key in totals:
            totals[key] += task["counts"].get(key, 0)
    report["denominators"] = {
        "tasks": len(report["tasks"]),
        "legs_per_task": len(acceptance_state.LEG_IDS),
        "leg_readings": totals["total"],
        **totals,
    }

    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
        return 0

    print(f"BEFORE (base {report['before']['base']})")
    print(f"  task states: {report['before']['state_count']} "
          f"({', '.join(report['before']['states'])})")
    print(f"  states that can express an acceptance: "
          f"{len(report['before']['acceptance_states'])} of "
          f"{report['before']['state_count']}")
    print(f"  a task record can carry an acceptance verdict: "
          f"{report['before']['carries_an_acceptance_verdict']}")
    print("AFTER")
    print(f"  task states: {report['after']['state_count']} "
          f"({', '.join(report['after']['states'])})")
    print(f"  states that can express an acceptance: "
          f"{len(report['after']['acceptance_states'])} of "
          f"{report['after']['state_count']}")
    print("  a task record can carry an acceptance verdict: True")
    print()
    for task in report["tasks"]:
        print(f"{task['assignment_id']}: state={task['state']} "
              f"outcome={task['outcome']}")
        print(f"  {task['reason']}")
        for row in task["legs"]:
            print(f"    {row['status']:<15} {row['id']} ({row['kind']})")
        print()
    denominators = report["denominators"]
    print(f"DENOMINATORS: {denominators['tasks']} tasks x "
          f"{denominators['legs_per_task']} legs = "
          f"{denominators['leg_readings']} leg readings; "
          f"{denominators['passed']} passed, {denominators['failed']} failed, "
          f"{denominators['unmeasured']} unmeasured, "
          f"{denominators['not_applicable']} not applicable")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
