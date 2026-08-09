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


def _gates_dir(project_dir: str) -> Path:
    """Get the gates directory for a project."""
    p = Path(project_dir) / "pipeline_output" / "gates"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _gate_dir(project_dir: str, step_id: str) -> Path:
    """Get the gate directory for a specific step."""
    p = _gates_dir(project_dir) / step_id
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
    gate = _gate_dir(project_dir, step_id)

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

    # Initialize gate status as pending
    status_path = gate / "status.json"
    if not status_path.exists():
        status = {
            "step_id": step_id,
            "status": "pending",
            "created_at": snapshot.created_at,
        }
        with open(status_path, "w") as f:
            json.dump(status, f, indent=2)

    # Automatically create an agent message for this gate
    try:
        from library.tools.step_exporter import load_step_summary, generate_summary
        summary_md = load_step_summary(project_dir, step_id)
        if not summary_md:
            summary_md = generate_summary(step_id, step_name, step_output)
    except Exception:
        summary_md = "Review required for this step."

    msg_id = f"msg_{step_id}_{int(time.time())}"
    msg = {
        "id": msg_id,
        "type": "decision",
        "step_id": step_id,
        "title": f"Review Gate: {step_name}",
        "body": summary_md,
        "options": [
            {"id": "approve", "label": "Approve", "description": "Proceed with the current output"},
            {"id": "reject", "label": "Reject", "description": "Reject the output"},
            {"id": "revise", "label": "Revise", "description": "Approve with modifications"}
        ],
        "requires_response": True,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")
    }
    
    msg_dir = Path(project_dir) / "pipeline_output" / "messages"
    msg_dir.mkdir(parents=True, exist_ok=True)
    with open(msg_dir / f"{msg_id}.json", "w") as f:
        json.dump(msg, f, indent=2)

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
    gate = _gate_dir(project_dir, step_id)

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

    return feedback_path


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
    gate = _gate_dir(project_dir, step_id)
    for filename in ["snapshot.json", "feedback.json", "status.json"]:
        filepath = gate / filename
        if filepath.exists():
            filepath.unlink()


def list_pending_gates(project_dir: str) -> List[str]:
    """List all steps with pending review gates."""
    statuses = get_all_gate_statuses(project_dir)
    return [step_id for step_id, status in statuses.items() if status == "pending"]
