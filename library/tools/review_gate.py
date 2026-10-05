"""
review_gate.py - Gate controller for human-in-the-loop pipeline review.

Manages the pause/resume/feedback cycle at any pipeline step.
The pipeline writes a gate snapshot when pausing; the dashboard reads it,
collects human feedback, and writes it back for the pipeline to consume
on resume.

Gate state is stored per-step in:
    <project>/pipeline_output/gates/<step_id>/
        snapshot.json    - Step output + upstream context at pause time
        feedback.json    - Human feedback (annotations, revisions, action)
        status.json      - Gate status (pending/approved/rejected/revised)
"""

import json
import os
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

from library.tools.project_layout import Area, ProjectLayout


@dataclass
class GateFeedback:
    """Human feedback captured at a review gate."""
    action: str = "pending"         # pending | approved | rejected | revised
    feedback: str = ""              # Free-text feedback from the reviewer
    revisions: Dict[str, Any] = field(default_factory=dict)  # Modified output fields
    annotations: List[Dict[str, Any]] = field(default_factory=list)
    timestamp: str = ""


@dataclass
class GateSnapshot:
    """Snapshot of pipeline state at a gate point."""
    step_id: str
    step_name: str
    step_output: Dict[str, Any] = field(default_factory=dict)
    upstream_context: Dict[str, Any] = field(default_factory=dict)
    created_at: str = ""


def _gates_dir(project_dir: str, *, create: bool = False) -> Path:
    """Get the gates directory for a project.

    Args:
        create: If True, create the directory tree. Read-only callers
                must pass False (the default) so that listing steps
                does not litter the project with empty gate dirs.
    """
    layout = ProjectLayout(project_dir)
    return (layout.write_dir(Area.GATES) if create
            else layout.read_dir(Area.GATES))


class UnsafeGateId(ValueError):
    """A gate id that would write outside the gates area."""


def _assert_safe_gate_id(step_id: str) -> str:
    """A gate id becomes a DIRECTORY NAME, so it may not be a path.

    THIS LANDS WITH THE CHANGE THAT MAKES IT REACHABLE, deliberately.
    Until operation breakpoints, every gate id came from the DAG and this
    could not be exercised; `--break <address>` makes the id something an
    operator types, and `/api/gates/{step_id}` makes it something an HTTP
    path carries.

    It was measured before it was closed - `save_gate_snapshot(project,
    "../../../outside", ...)` wrote `outside/snapshot.json` OUTSIDE the
    project directory entirely, because this function was
    `_gates_dir(...) / step_id` with no validation at all.

    Refused rather than sanitised: an id that needs sanitising is not an
    id, and quietly rewriting what the operator typed is how they end up
    answering a gate that is not the one they armed.
    """
    raw = str(step_id)
    if not raw or raw.strip() != raw or not raw.strip():
        raise UnsafeGateId(
            f"gate id {step_id!r} is empty or padded; it names a directory.")
    if raw in (".", ".."):
        raise UnsafeGateId(f"gate id {step_id!r} names a directory, not a gate.")
    for bad in ("/", "\\", "\x00"):
        if bad in raw:
            raise UnsafeGateId(
                f"gate id {step_id!r} contains {bad!r}. A gate id is a single "
                f"directory name - a step id, or an operation address like "
                f"subtitles.render@45.0-72.0 - never a path.")
    if ".." in raw:
        raise UnsafeGateId(
            f"gate id {step_id!r} contains '..', which would climb out of "
            f"the gates directory. Measured before this check existed: "
            f"'../../../outside' wrote outside the project entirely.")
    return raw


def _gate_dir(project_dir: str, step_id: str, *, create: bool = False) -> Path:
    """Get the gate directory for a specific step or operation address.

    Args:
        create: If True, create the directory tree.
    """
    p = _gates_dir(project_dir, create=create) / _assert_safe_gate_id(step_id)
    if create:
        p.mkdir(parents=True, exist_ok=True)
    return p


def save_gate_snapshot(
    project_dir: str,
    step_id: str,
    step_name: str,
    step_output: Dict[str, Any],
    upstream_context: Optional[Dict[str, Any]] = None,
) -> Path:
    """Save a gate snapshot when the pipeline pauses at a step.

    Returns the path to the snapshot file.
    """
    gate = _gate_dir(project_dir, step_id, create=True)

    snapshot = GateSnapshot(
        step_id=step_id,
        step_name=step_name,
        step_output=step_output,
        upstream_context=upstream_context or {},
        created_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    snapshot_path = gate / "snapshot.json"
    with open(snapshot_path, "w") as f:
        json.dump(asdict(snapshot), f, indent=2)

    # Arming a gate makes it PENDING, and throws away the answer a
    # previous run got.
    #
    # This used to write `status.json` only when there was not one
    # already, so a step re-run after being approved arrived at its
    # breakpoint carrying last time's `approved`, and the next --resume
    # sailed through a pause the captain never saw - applying an old
    # `revised` payload to a freshly computed output while it was at it.
    # A gate that pauses is by definition unanswered.
    feedback_path = gate / "feedback.json"
    if feedback_path.exists():
        try:
            feedback_path.unlink()
        except OSError:
            pass
    status_path = gate / "status.json"
    with open(status_path, "w") as f:
        json.dump({
            "step_id": step_id,
            "status": "pending",
            "created_at": snapshot.created_at,
        }, f, indent=2)

    return snapshot_path


def load_gate_snapshot(project_dir: str, step_id: str) -> Optional[GateSnapshot]:
    """Load a gate snapshot."""
    snapshot_path = _gate_dir(project_dir, step_id) / "snapshot.json"
    if not snapshot_path.exists():
        return None
    with open(snapshot_path) as f:
        data = json.load(f)
    return GateSnapshot(**data)


def get_gate_status(project_dir: str, step_id: str) -> str:
    """Get the current gate status for a step."""
    status_path = _gate_dir(project_dir, step_id) / "status.json"
    if not status_path.exists():
        return "none"
    with open(status_path) as f:
        data = json.load(f)
    return data.get("status", "none")


def get_all_gate_statuses(project_dir: str) -> Dict[str, str]:
    """Get gate statuses for all steps that have gates."""
    gates_dir = _gates_dir(project_dir)
    statuses = {}
    if not gates_dir.exists():
        return statuses
    for gate_subdir in gates_dir.iterdir():
        if gate_subdir.is_dir():
            status_path = gate_subdir / "status.json"
            if status_path.exists():
                with open(status_path) as f:
                    data = json.load(f)
                statuses[gate_subdir.name] = data.get("status", "none")
    return statuses


def save_gate_feedback(
    project_dir: str,
    step_id: str,
    action: str,
    feedback: str = "",
    revisions: Optional[Dict[str, Any]] = None,
    annotations: Optional[List[Dict[str, Any]]] = None,
) -> Path:
    """Save human feedback for a gate.

    action: "approved" | "rejected" | "revised"
    """
    gate = _gate_dir(project_dir, step_id, create=True)

    fb = GateFeedback(
        action=action,
        feedback=feedback,
        revisions=revisions or {},
        annotations=annotations or [],
        timestamp=time.strftime("%Y-%m-%dT%H:%M:%S"),
    )
    feedback_path = gate / "feedback.json"
    with open(feedback_path, "w") as f:
        json.dump(asdict(fb), f, indent=2)

    # Update status
    status_path = gate / "status.json"
    with open(status_path, "w") as f:
        json.dump({
            "step_id": step_id,
            "status": action,
            "updated_at": fb.timestamp,
        }, f, indent=2)

    if action == "rejected":
        file_rejection(project_dir, step_id, feedback)

    return feedback_path


def file_rejection(project_dir: str, step_id: str, reason: str) -> None:
    """File the reviewer's rejection into the edit history (F-02).

    A gate rejection is a verdict on the step's output, and it used to
    die with the run: the next run re-attempted the same step with no
    memory of why it was refused. The rejection is filed under the
    step id as the artifact, with the reviewer's own words as the reason
    and the captain as the rejecting party, so `prior_rejection_of` can
    cite it. Never raises - a history write must not fail the answer
    being recorded.
    """
    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.reel_edit_history import record_rejection

    review_dir = str(ProjectLayout(str(project_dir)).read_dir(Area.REVIEW))
    record_rejection(
        review_dir, str(step_id),
        reason=reason or "rejected without a reason",
        rejecting_party="captain",
        refs={"gate": str(step_id)},
        summary=f"gate {step_id} rejected by reviewer: {reason}")


def load_gate_feedback(project_dir: str, step_id: str) -> Optional[GateFeedback]:
    """Load human feedback for a gate."""
    feedback_path = _gate_dir(project_dir, step_id) / "feedback.json"
    if not feedback_path.exists():
        return None
    with open(feedback_path) as f:
        data = json.load(f)
    return GateFeedback(**data)


def apply_feedback_to_output(
    step_output: Dict[str, Any],
    feedback: GateFeedback,
) -> Dict[str, Any]:
    """Merge human revisions into a step output.

    If the action is 'revised', the revisions dict is shallow-merged
    into the step output. This lets the human override specific fields
    (e.g., change target_mood in creative_direction) without rewriting
    the entire output.

    If the action is 'approved', the output is returned unchanged.
    If the action is 'rejected', returns the output with a rejection marker.
    """
    if feedback.action == "approved":
        return step_output

    if feedback.action == "rejected":
        return {
            **step_output,
            "__rejected": True,
            "__rejection_feedback": feedback.feedback,
        }

    if feedback.action == "revised":
        def deep_merge(target: Dict[str, Any], source: Dict[str, Any]) -> Dict[str, Any]:
            result = {**target}
            for k, v in source.items():
                if isinstance(v, dict) and isinstance(result.get(k), dict):
                    result[k] = deep_merge(result[k], v)
                else:
                    result[k] = v
            return result

        merged = deep_merge(step_output, feedback.revisions)
        merged["__revised"] = True
        merged["__revision_feedback"] = feedback.feedback
        return merged

    return step_output


def clear_gate(project_dir: str, step_id: str) -> None:
    """Clear gate state for a step (e.g., before re-running)."""
    gate = _gate_dir(project_dir, step_id, create=True)
    for filename in ["snapshot.json", "feedback.json", "status.json"]:
        filepath = gate / filename
        if filepath.exists():
            filepath.unlink()


def list_pending_gates(project_dir: str) -> List[str]:
    """List all steps with pending review gates."""
    statuses = get_all_gate_statuses(project_dir)
    return [step_id for step_id, status in statuses.items() if status == "pending"]


# ── Answering a gate without a browser ───────────────────────────────
#
# The gate has always been answerable from the dashboard and from
# nowhere else, which made a breakpoint unusable from a terminal, from a
# script, and - the case that matters now - from inside DaVinci Resolve.
# This is the same three actions and the same files; nothing about the
# protocol changes.
#
#     python3 -m library.tools.review_gate list   --project <dir>
#     python3 -m library.tools.review_gate show   --project <dir> --step <id>
#     python3 -m library.tools.review_gate answer --project <dir> --step <id> \
#         --approve | --reject | --revise '<json>'  [--note "..."]

def _summarise(value: Any, depth: int = 0) -> str:
    """One line describing a value, without printing the whole thing."""
    pad = "  " * depth
    if isinstance(value, dict):
        return f"{pad}{{{len(value)} keys}}"
    if isinstance(value, list):
        return f"{pad}[{len(value)} items]"
    text = str(value)
    return pad + (text if len(text) <= 100 else text[:97] + "...")


def _main(argv: Optional[List[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.review_gate",
        description="Read and answer a pipeline review gate.")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="Every gate this project has")
    p_list.add_argument("--project", required=True)

    p_show = sub.add_parser("show", help="One gate's snapshot, summarised")
    p_show.add_argument("--project", required=True)
    p_show.add_argument("--step", required=True)
    p_show.add_argument("--full", action="store_true",
                        help="Print the whole step output as JSON")

    p_answer = sub.add_parser("answer", help="Approve, reject or revise")
    p_answer.add_argument("--project", required=True)
    p_answer.add_argument("--step", required=True)
    action = p_answer.add_mutually_exclusive_group(required=True)
    action.add_argument("--approve", action="store_true")
    action.add_argument("--reject", action="store_true")
    action.add_argument("--revise", metavar="JSON",
                        help="A JSON object, deep-merged into the step "
                             "output before the run continues")
    p_answer.add_argument("--note", default="",
                          help="Free text recorded with the answer")

    args = parser.parse_args(argv)
    project = os.path.abspath(args.project)

    if args.command == "list":
        statuses = get_all_gate_statuses(project)
        if not statuses:
            print("  (no gates)")
            return 0
        for step_id in sorted(statuses):
            print(f"  {step_id:<28} {statuses[step_id]}")
        return 0

    if args.command == "show":
        snapshot = load_gate_snapshot(project, args.step)
        if snapshot is None:
            print(f"  no snapshot for {args.step}")
            return 2
        print(f"  Step:    {snapshot.step_id} ({snapshot.step_name})")
        print(f"  Taken:   {snapshot.created_at}")
        print(f"  Status:  {get_gate_status(project, args.step)}")
        print(f"  Upstream inputs: "
              f"{', '.join(sorted(snapshot.upstream_context)) or '(none)'}")
        print("  Output:")
        if args.full:
            print(json.dumps(snapshot.step_output, indent=2))
        else:
            for key, value in (snapshot.step_output or {}).items():
                print(f"    {key}: {_summarise(value)}")
        feedback = load_gate_feedback(project, args.step)
        if feedback:
            print(f"  Answer:  {feedback.action} - {feedback.feedback}")
        return 0

    if args.approve:
        act, revisions = "approved", {}
    elif args.reject:
        act, revisions = "rejected", {}
    else:
        try:
            revisions = json.loads(args.revise)
        except ValueError as exc:
            print(f"  --revise is not JSON: {exc}")
            return 2
        if not isinstance(revisions, dict):
            print("  --revise must be a JSON object, so it can be merged "
                  "into the step output.")
            return 2
        act = "revised"
    path = save_gate_feedback(project, args.step, act,
                              feedback=args.note, revisions=revisions)
    print(f"  {args.step}: {act} -> {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
