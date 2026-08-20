"""
Dashboard API Server - FastAPI backend for the review dashboard.

Serves pipeline state, step outputs, and review controls to the frontend.
Also serves static files (HTML/CSS/JS) and thumbnails.

Start with: python3 -m library.dashboard.server --project /path/to/project
Or via: python3 manage_project.py dashboard <slug>
"""

import json
import os
import sys
import time
import asyncio
import subprocess
from pathlib import Path
from typing import Any, Dict, List, Optional

# Ensure repo root is on path
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

from library.dashboard import review_channel
from library.dashboard.models import (
    Annotation,
    AnnotationBatch,
    ClipInfo,
    GateActionRequest,
    GateStatus,
    PipelinePauseRequest,
    PipelineRunRequest,
    PipelineStepRequest,
    PipelineStatus,
    ProjectInfo,
    ReviewNoteRequest,
    ReviewReplyRequest,
    ReviewSendRequest,
    StepDetail,
    StepSummary,
    StepStatus,
    TimelineBlock,
    TimelineView,
    TranscriptRegion,
    TranscriptView,
    AgentMessage,
    UserResponse,
)
from library.tools.review_gate import (
    apply_feedback_to_output,
    get_all_gate_statuses,
    get_gate_status,
    list_pending_gates,
    load_gate_feedback,
    load_gate_snapshot,
    save_gate_feedback,
    save_gate_snapshot,
)
from library.tools.step_exporter import (
    export_step_output,
    load_step_output,
    load_step_summary,
    list_exported_steps,
)
from library.tools.thumbnail_extractor import get_thumbnail_url
from library.tools import run_control
from library.tools import step_ledger


# ── App Setup ───────────────────────────────────────────────────────

app = FastAPI(
    title="Video Pipeline Review Dashboard",
    description="Human-in-the-loop review layer for the video editing pipeline",
    version="0.1.0",
)

# Static files
STATIC_DIR = Path(__file__).parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

# State - set via CLI args or startup
_project_dir: Optional[str] = None
_project_slug: Optional[str] = None



def _get_project_dir() -> str:
    """Get the current project directory, raising if not set."""
    if not _project_dir:
        raise HTTPException(500, "No project directory configured. Start the server with --project.")
    return _project_dir


# Global event for long-polling
message_response_event = asyncio.Event()

# Fires when the reviewer sends a batch of anchored notes, waking /api/review/poll
review_batch_event = asyncio.Event()

# ── Message Helpers ─────────────────────────────────────────────────

def _messages_dir(project_dir: str) -> Path:
    p = Path(project_dir) / "pipeline_output" / "messages"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _load_message(project_dir: str, message_id: str) -> Optional[dict]:
    p = _messages_dir(project_dir) / f"{message_id}.json"
    if not p.exists():
        return None
    with open(p) as f:
        return json.load(f)


def _save_message(project_dir: str, message: dict):
    p = _messages_dir(project_dir) / f"{message['id']}.json"
    with open(p, "w") as f:
        json.dump(message, f, indent=2)


def _list_messages(project_dir: str) -> List[dict]:
    d = _messages_dir(project_dir)
    if not d.exists():
        return []
    messages = []
    for f in d.glob("*.json"):
        with open(f) as fp:
            try:
                messages.append(json.load(fp))
            except Exception:
                pass
    return sorted(messages, key=lambda x: x.get("created_at", ""), reverse=True)


# ── Catalog Key Helpers ─────────────────────────────────────────

def _format_resolution(clip: dict) -> str:
    """Build a resolution string from the catalog's width/height keys.

    The catalog producer emits ``width`` and ``height`` as separate ints.
    Falls back to a legacy ``resolution`` string if the structured keys
    are missing.
    """
    w = clip.get("width")
    h = clip.get("height")
    if w and h:
        return f"{w}x{h}"
    return clip.get("resolution", "")


def _extract_summary(sem: dict) -> str:
    """Extract a summary string from a semantic analysis document.

    v3 puts it under ``assessment.summary``; legacy had it at the top level.
    """
    assessment = sem.get("assessment", {})
    if isinstance(assessment, dict):
        s = assessment.get("summary", "")
        if s:
            return str(s)
    s = sem.get("summary", "")
    return str(s) if s else ""


def _extract_interest_score(sem: dict) -> float:
    """Extract interest_score from a semantic analysis document.

    v3 nests it under ``assessment.interest_score``.
    """
    assessment = sem.get("assessment", {})
    if isinstance(assessment, dict):
        score = assessment.get("interest_score")
        if score is not None:
            return float(score)
    return float(sem.get("interest_score", 0))


# ── Pipeline State Helpers ──────────────────────────────────────────

def _load_pipeline_state(project_dir: str) -> dict:
    """Load pipeline_data.json for the project."""
    state_path = os.path.join(project_dir, "pipeline_data.json")
    if not os.path.exists(state_path):
        return {}
    with open(state_path) as f:
        return json.load(f)


def _save_pipeline_state(project_dir: str, state: dict):
    """Save pipeline_data.json."""
    state_path = os.path.join(project_dir, "pipeline_data.json")
    state["last_updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    with open(state_path, "w") as f:
        json.dump(state, f, indent=2)


def _load_dag() -> dict:
    """Load the pipeline DAG definition."""
    dag_path = REPO_ROOT / "library" / "processes" / "edit_video" / "dag.json"
    with open(dag_path) as f:
        return json.load(f)


def _load_project_config(project_dir: str) -> dict:
    """Load project.yaml as a dict."""
    yaml_path = os.path.join(project_dir, "project.yaml")
    if not os.path.exists(yaml_path):
        return {}
    try:
        import yaml
        with open(yaml_path) as f:
            return yaml.safe_load(f) or {}
    except ImportError:
        return {}


def _load_annotations(project_dir: str, step_id: str) -> List[dict]:
    """Load annotations for a step."""
    ann_path = Path(project_dir) / "pipeline_output" / "annotations" / f"{step_id}.json"
    if not ann_path.exists():
        return []
    with open(ann_path) as f:
        return json.load(f)


def _save_annotations(project_dir: str, step_id: str, annotations: list):
    """Save annotations for a step."""
    ann_dir = Path(project_dir) / "pipeline_output" / "annotations"
    ann_dir.mkdir(parents=True, exist_ok=True)
    ann_path = ann_dir / f"{step_id}.json"
    with open(ann_path, "w") as f:
        json.dump(annotations, f, indent=2)


# ── Root ────────────────────────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main dashboard page."""
    index_path = STATIC_DIR / "index.html"
    if index_path.exists():
        return HTMLResponse(index_path.read_text())
    return HTMLResponse("<h1>Dashboard not found. Build static files first.</h1>")


# ── Project Endpoints ──────────────────────────────────────────────

from pydantic import BaseModel
class SelectProjectRequest(BaseModel):
    project_dir: str

@app.get("/api/projects")
async def get_projects():
    """List all available projects (registered and unregistered)."""
    from library.tools.project_registry import list_projects
    from library.tools.paths import PROJECTS_ROOT

    registered = list_projects()
    registered_paths = {str(p.project_root) for p in registered}
    
    projects = []
    
    # 1. Add registered
    for config in registered:
        state = _load_pipeline_state(str(config.project_root))
        completed = step_ledger.all_completed(state)
        projects.append(ProjectInfo(
            slug=config.slug,
            name=config.name,
            client=config.client,
            status=config.status.value,
            resolution=config.source.resolution,
            fps=config.source.fps,
            raw_footage_count=0,
            steps_completed=len(completed),
            total_steps=0,
            project_root=str(config.project_root),
        ))

    # 2. Scan for unregistered
    if PROJECTS_ROOT and PROJECTS_ROOT.exists():
        for entry in PROJECTS_ROOT.iterdir():
            if not entry.is_dir() or entry.name.startswith((".", "_")):
                continue
            
            # Flat layout
            if str(entry) not in registered_paths:
                if (entry / "pipeline_data.json").exists() or (entry / "project.yaml").exists():
                    state = _load_pipeline_state(str(entry))
                    completed = step_ledger.all_completed(state)
                    projects.append(ProjectInfo(
                        slug=entry.name,
                        name=entry.name,
                        client="",
                        status="unregistered",
                        resolution="1080x1920",
                        fps=30,
                        raw_footage_count=0,
                        steps_completed=len(completed),
                        total_steps=0,
                        project_root=str(entry),
                    ))
            
            # Grouped layout
            for sub_entry in entry.iterdir():
                if not sub_entry.is_dir() or sub_entry.name.startswith((".", "_")):
                    continue
                if str(sub_entry) not in registered_paths:
                    if (sub_entry / "pipeline_data.json").exists() or (sub_entry / "project.yaml").exists():
                        state = _load_pipeline_state(str(sub_entry))
                        completed = step_ledger.all_completed(state)
                        projects.append(ProjectInfo(
                            slug=sub_entry.name,
                            name=sub_entry.name,
                            client=entry.name,
                            status="unregistered",
                            resolution="1080x1920",
                            fps=30,
                            raw_footage_count=0,
                            steps_completed=len(completed),
                            total_steps=0,
                            project_root=str(sub_entry),
                        ))
                        
    return projects

@app.post("/api/projects/select")
async def select_project(request: SelectProjectRequest):
    global _project_dir, _project_slug
    _project_dir = request.project_dir
    _project_slug = os.path.basename(request.project_dir)
    return {"status": "ok"}

@app.get("/api/project")
async def get_project():
    """Get current project info."""
    project_dir = _get_project_dir()
    config = _load_project_config(project_dir)
    state = _load_pipeline_state(project_dir)
    dag = _load_dag()

    # Count raw footage
    raw_dir = os.path.join(project_dir, "raw")
    raw_count = 0
    if os.path.isdir(raw_dir):
        raw_count = len([
            f for f in os.listdir(raw_dir)
            if f.lower().endswith((".mov", ".mp4", ".avi", ".mkv"))
        ])

    completed = step_ledger.all_completed(state)
    return ProjectInfo(
        slug=config.get("slug", _project_slug or os.path.basename(project_dir)),
        name=config.get("name", os.path.basename(project_dir)),
        client=config.get("client", ""),
        status=config.get("status", "draft"),
        resolution=config.get("source", {}).get("resolution", "1080x1920"),
        fps=config.get("source", {}).get("fps", 30),
        raw_footage_count=raw_count,
        steps_completed=len(completed),
        total_steps=len(dag.get("nodes", [])),
        project_root=project_dir,
    )


# ── Step Endpoints ─────────────────────────────────────────────────

@app.get("/api/steps")
async def list_steps():
    """List all pipeline steps with their status."""
    project_dir = _get_project_dir()
    state = _load_pipeline_state(project_dir)
    dag = _load_dag()
    gate_statuses = get_all_gate_statuses(project_dir)

    completed = step_ledger.all_completed(state)
    outputs = state.get("step_outputs", {})

    steps = []
    for node in dag.get("nodes", []):
        node_id = node["id"]
        status = StepStatus.PENDING

        if node_id in state.get("failed_steps", []):
            status = StepStatus.FAILED
        elif node_id in completed:
            # Check if there is a pending gate
            gate_status = gate_statuses.get(node_id, "none")
            if gate_status == "pending":
                status = StepStatus.GATE_PENDING
            else:
                status = StepStatus.COMPLETED
        elif node_id in state.get("awaiting_llm", []):
            status = StepStatus.PENDING  # Awaiting LLM is still pending

        comp_data = completed.get(node_id, {})
        output_keys = list(outputs.get(node_id, {}).keys())

        steps.append(StepSummary(
            id=node_id,
            name=node["name"],
            status=status,
            elapsed_s=comp_data.get("elapsed_s"),
            completed_at=comp_data.get("completed_at"),
            has_gate=True,  # Every step is inspectable
            output_keys=output_keys,
        ))

    return steps


@app.get("/api/steps/{step_id}")
async def get_step_detail(step_id: str):
    """Get full step output and summary for the inspector."""
    project_dir = _get_project_dir()
    state = _load_pipeline_state(project_dir)
    dag = _load_dag()

    # Find the node
    node = None
    for n in dag.get("nodes", []):
        if n["id"] == step_id:
            node = n
            break
    if not node:
        raise HTTPException(404, f"Step '{step_id}' not found in DAG")

    completed = step_ledger.all_completed(state)
    outputs = state.get("step_outputs", {})
    output = outputs.get(step_id, {})

    # Try to load exported summary, or generate one
    summary_md = load_step_summary(project_dir, step_id)
    if not summary_md and output:
        # Generate and export on the fly
        from library.tools.step_exporter import generate_summary
        summary_md = generate_summary(step_id, node["name"], output)

    gate_status = get_gate_status(project_dir, step_id)
    status = StepStatus.PENDING
    if step_id in completed:
        status = StepStatus.GATE_PENDING if gate_status == "pending" else StepStatus.COMPLETED

    comp_data = completed.get(step_id, {})

    return StepDetail(
        id=step_id,
        name=node["name"],
        status=status,
        output=output,
        summary_md=summary_md,
        elapsed_s=comp_data.get("elapsed_s"),
        completed_at=comp_data.get("completed_at"),
    )


# ── Gate Endpoints ─────────────────────────────────────────────────

@app.get("/api/gates")
async def list_gates():
    """List all gates and their statuses."""
    project_dir = _get_project_dir()
    statuses = get_all_gate_statuses(project_dir)
    return statuses


@app.get("/api/gates/{step_id}")
async def get_gate(step_id: str):
    """Get gate details for a specific step."""
    project_dir = _get_project_dir()
    status = get_gate_status(project_dir, step_id)
    snapshot = load_gate_snapshot(project_dir, step_id)
    feedback = load_gate_feedback(project_dir, step_id)

    return {
        "step_id": step_id,
        "status": status,
        "snapshot": {
            "step_output": snapshot.step_output if snapshot else {},
            "upstream_context": snapshot.upstream_context if snapshot else {},
            "created_at": snapshot.created_at if snapshot else "",
        } if snapshot else None,
        "feedback": {
            "action": feedback.action,
            "feedback": feedback.feedback,
            "revisions": feedback.revisions,
            "annotations": feedback.annotations,
            "timestamp": feedback.timestamp,
        } if feedback else None,
    }


@app.post("/api/gates/{step_id}/action")
async def gate_action(step_id: str, request: GateActionRequest):
    """Approve, reject, or revise a gate."""
    project_dir = _get_project_dir()

    action_map = {
        "approve": "approved",
        "reject": "rejected",
        "revise": "revised"
    }
    mapped_action = action_map.get(request.action.value, request.action.value)

    # Save the feedback
    save_gate_feedback(
        project_dir,
        step_id,
        action=mapped_action,
        feedback=request.feedback,
        revisions=request.revisions,
    )

    # If approved or revised, apply feedback to pipeline state
    if mapped_action in ("approved", "revised"):
        state = _load_pipeline_state(project_dir)
        outputs = state.get("step_outputs", {})
        step_output = outputs.get(step_id, {})

        if mapped_action == "revised" and request.revisions:
            feedback = load_gate_feedback(project_dir, step_id)
            if feedback:
                merged = apply_feedback_to_output(step_output, feedback)
                outputs[step_id] = merged
                state["step_outputs"] = outputs
                _save_pipeline_state(project_dir, state)

    return {"status": "ok", "action": request.action.value, "step_id": step_id}


# ── Message Endpoints ──────────────────────────────────────────────

@app.post("/api/messages")
async def create_message(message: AgentMessage):
    """Agent posts a new message."""
    project_dir = _get_project_dir()
    _save_message(project_dir, message.model_dump())
    return {"status": "ok", "id": message.id}


@app.get("/api/messages")
async def get_messages():
    """Get all messages."""
    project_dir = _get_project_dir()
    return _list_messages(project_dir)


@app.get("/api/messages/pending")
async def get_pending_messages():
    """Get messages awaiting user response."""
    project_dir = _get_project_dir()
    msgs = _list_messages(project_dir)
    return [m for m in msgs if m.get("requires_response") and not m.get("responded_at")]


@app.post("/api/messages/{message_id}/respond")
async def respond_to_message(message_id: str, response: UserResponse):
    """User submits a response to a message."""
    project_dir = _get_project_dir()
    msg = _load_message(project_dir, message_id)
    if not msg:
        raise HTTPException(404, "Message not found")
    
    msg["responded_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    msg["response"] = response.model_dump()
    _save_message(project_dir, msg)
    
    # Notify pollers
    message_response_event.set()
    message_response_event.clear()
    
    # Sync gate status if this is a decision tied to a step
    step_id = msg.get("step_id")
    if step_id and msg.get("type") == "decision":
        mapped_action = "approved"
        if response.action == "reject":
            mapped_action = "rejected"
        elif response.action in ("revise", "comment", "choose"):
            mapped_action = "revised"
            
        save_gate_feedback(
            project_dir,
            step_id,
            action=mapped_action,
            feedback=response.comment or "",
            revisions=response.annotations or {}
        )
        
        # Apply feedback if approved or revised
        if mapped_action in ("approved", "revised"):
            state = _load_pipeline_state(project_dir)
            outputs = state.get("step_outputs", {})
            step_output = outputs.get(step_id, {})

            if mapped_action == "revised" and response.annotations:
                feedback = load_gate_feedback(project_dir, step_id)
                if feedback:
                    merged = apply_feedback_to_output(step_output, feedback)
                    outputs[step_id] = merged
                    state["step_outputs"] = outputs
                    _save_pipeline_state(project_dir, state)

    return {"status": "ok"}


@app.get("/api/messages/poll")
async def poll_messages(message_id: Optional[str] = None, timeout: int = 300):
    """Long-poll endpoint that blocks until a response exists."""
    project_dir = _get_project_dir()
    
    # Check if already responded
    if message_id:
        msg = _load_message(project_dir, message_id)
        if msg and msg.get("response"):
            return msg.get("response")

    try:
        await asyncio.wait_for(message_response_event.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        return {"status": "timeout"}
    
    if message_id:
        msg = _load_message(project_dir, message_id)
        if msg and msg.get("response"):
            return msg.get("response")
    
    return {"status": "event_fired"}


# ── Annotation Endpoints ──────────────────────────────────────────

@app.get("/api/steps/{step_id}/annotations")
async def get_annotations(step_id: str):
    """Get annotations for a step."""
    project_dir = _get_project_dir()
    return _load_annotations(project_dir, step_id)


@app.post("/api/steps/{step_id}/annotations")
async def save_annotations_endpoint(step_id: str, batch: AnnotationBatch):
    """Save annotations for a step."""
    project_dir = _get_project_dir()
    existing = _load_annotations(project_dir, step_id)

    # Add new annotations with timestamps
    for ann in batch.annotations:
        ann_dict = ann.model_dump()
        ann_dict["created_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        ann_dict["step_id"] = step_id
        if not ann_dict.get("id"):
            ann_dict["id"] = f"ann_{step_id}_{len(existing)}_{int(time.time())}"
        existing.append(ann_dict)

    _save_annotations(project_dir, step_id, existing)
    return {"status": "ok", "count": len(existing)}


@app.delete("/api/steps/{step_id}/annotations/{ann_idx}")
async def delete_annotation(step_id: str, ann_idx: int):
    """Delete an annotation by index."""
    project_dir = _get_project_dir()
    existing = _load_annotations(project_dir, step_id)
    if 0 <= ann_idx < len(existing):
        existing.pop(ann_idx)
        _save_annotations(project_dir, step_id, existing)
    return {"status": "ok", "count": len(existing)}


# ── Review Return Channel ─────────────────────────────────────────
#
# Two properties, both adopted from the captain's Lavish review pages
# (ruling 2026-08-17: extend this dashboard, do not author a per-run page):
# a note anchored to a specific element, and one batched send that wakes an
# agent which replies onto this same surface. The store and the agent-side
# CLI live in library/dashboard/review_channel.py; these routes are the
# browser's half plus an HTTP wake-up for an agent that prefers the network.

@app.get("/api/review/notes")
async def review_notes(view: str = "", status: str = ""):
    """Every note, newest last, with the replies threaded onto each one."""
    project_dir = _get_project_dir()
    return review_channel.list_notes(project_dir, view=view, status=status)


@app.post("/api/review/notes")
async def review_queue_note(request: ReviewNoteRequest):
    """Queue one anchored note. Queued notes are invisible to agents until sent."""
    project_dir = _get_project_dir()
    try:
        return review_channel.queue_note(
            project_dir, request.text, request.anchor.model_dump()
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc))


@app.delete("/api/review/notes/{note_id}")
async def review_delete_note(note_id: str):
    """Drop a note that has not been sent yet."""
    project_dir = _get_project_dir()
    try:
        removed = review_channel.delete_note(project_dir, note_id)
    except ValueError as exc:
        raise HTTPException(409, str(exc))
    if not removed:
        raise HTTPException(404, "Note not found")
    return {"status": "ok"}


@app.post("/api/review/send")
async def review_send(request: ReviewSendRequest):
    """Send the queued notes as ONE batch and wake whoever is polling."""
    project_dir = _get_project_dir()
    batch = review_channel.send_queued(project_dir, request.note_ids or None)
    if batch is None:
        raise HTTPException(400, "Nothing queued to send")
    review_batch_event.set()
    review_batch_event.clear()
    return batch


@app.get("/api/review/poll")
async def review_poll(timeout: float = 300):
    """Agent wake-up: block until a batch is waiting, then hand it over.

    Returns the batch with its notes and their anchors, or
    ``{"status": "timeout"}`` when nothing arrived inside `timeout` seconds.
    """
    project_dir = _get_project_dir()
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        batch = review_channel.pending_batch(project_dir)
        if batch:
            review_channel.mark_delivered(project_dir, batch["id"])
            return batch
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {"status": "timeout"}
        try:
            await asyncio.wait_for(review_batch_event.wait(), timeout=min(remaining, 5.0))
        except asyncio.TimeoutError:
            pass


@app.post("/api/review/reply")
async def review_reply(request: ReviewReplyRequest):
    """Agent replies onto the same surface, next to the anchors it answers."""
    project_dir = _get_project_dir()
    try:
        return review_channel.add_reply(
            project_dir,
            request.batch_id,
            request.text,
            note_ids=request.note_ids or None,
            author=request.author,
        )
    except KeyError as exc:
        raise HTTPException(404, str(exc))
    except ValueError as exc:
        raise HTTPException(400, str(exc))


# ── Transcript Endpoint ───────────────────────────────────────────

@app.get("/api/transcript")
async def get_transcript():
    """Get combined transcript across all clips for Descript-style display."""
    project_dir = _get_project_dir()
    state = _load_pipeline_state(project_dir)
    outputs = state.get("step_outputs", {})

    # Get temporal index (has transcripts).
    # The temporal_index step emits under "temporal_event_indices" (not "temporal_index").
    temporal = outputs.get("temporal_index", {}).get("temporal_event_indices", [])
    if not temporal:
        # Legacy fallback: some older runs may use "temporal_index" as the inner key.
        temporal = outputs.get("temporal_index", {}).get("temporal_index", {})

    regions = []
    total_duration = 0.0

    # Handle both list and dict formats
    clips_data = temporal
    if isinstance(temporal, dict):
        clips_data = list(temporal.values())
    elif not isinstance(temporal, list):
        clips_data = []

    for clip in clips_data:
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("clip_id", "")
        speech_regions = clip.get("speech_regions", clip.get("segments", []))

        for region in speech_regions:
            if not isinstance(region, dict):
                continue
            start = float(region.get("start", 0))
            end = float(region.get("end", 0))
            text = region.get("text", "")
            words = region.get("words", [])
            speaker = region.get("speaker", "")

            regions.append(TranscriptRegion(
                clip_id=clip_id,
                text=text,
                start=start,
                end=end,
                words=words,
                speaker=speaker,
            ))
            total_duration = max(total_duration, end)

    return TranscriptView(
        regions=regions,
        total_duration_s=total_duration,
        clip_count=len(set(r.clip_id for r in regions)),
    )


# ── Clips Endpoint ────────────────────────────────────────────────

@app.get("/api/clips")
async def get_clips():
    """Get clip catalog with thumbnails and metadata."""
    project_dir = _get_project_dir()
    state = _load_pipeline_state(project_dir)
    outputs = state.get("step_outputs", {})

    catalog_data = outputs.get("catalog", {}).get("clip_catalog", [])
    if isinstance(catalog_data, dict):
        catalog_data = list(catalog_data.values())

    semantic_data = outputs.get("semantic_analysis", {}).get("semantic_analysis_documents", [])
    if isinstance(semantic_data, dict):
        semantic_data = list(semantic_data.values())

    # Index semantic data by clip_id for enrichment
    semantic_by_clip = {}
    for doc in (semantic_data if isinstance(semantic_data, list) else []):
        if isinstance(doc, dict):
            semantic_by_clip[doc.get("clip_id", "")] = doc

    clips = []
    for clip in (catalog_data if isinstance(catalog_data, list) else []):
        if not isinstance(clip, dict):
            continue
        clip_id = clip.get("clip_id", clip.get("filename", ""))
        sem = semantic_by_clip.get(clip_id, {})

        # Get thumbnail URL
        thumb_url = get_thumbnail_url(project_dir, clip_id)

        # Extract mood/energy tags from semantic analysis
        mood_tags = []
        mood = sem.get("mood", sem.get("overall_mood", ""))
        if mood:
            mood_tags.append(mood)
        energy = sem.get("energy", sem.get("overall_energy", ""))
        if energy:
            mood_tags.append(energy)

        # Detected objects - handle both v3 list-of-dicts and legacy flat-dict
        objects = sem.get("detected_objects", sem.get("objects", []))
        if isinstance(objects, dict):
            objects = list(objects.keys())
        elif isinstance(objects, list) and objects and isinstance(objects[0], dict):
            objects = [o.get("label", str(o)) for o in objects if isinstance(o, dict)]

        clips.append(ClipInfo(
            clip_id=clip_id,
            filename=clip.get("filename", clip_id),
            filepath=clip.get("filepath", clip.get("path", "")),
            duration_s=float(clip.get("duration_seconds", clip.get("duration_s", 0))),
            resolution=_format_resolution(clip),
            fps=float(clip.get("frame_rate", clip.get("fps", 0))),
            thumbnail_url=thumb_url,
            transcript_excerpt=_extract_summary(sem)[:200],
            mood_tags=mood_tags,
            interest_score=_extract_interest_score(sem),
            detected_objects=objects[:10] if isinstance(objects, list) else [],
        ))

    return clips


# ── Timeline Endpoint ─────────────────────────────────────────────

@app.get("/api/timeline")
async def get_timeline():
    """Get rough cut timeline data for visualization."""
    project_dir = _get_project_dir()
    state = _load_pipeline_state(project_dir)
    outputs = state.get("step_outputs", {})

    blocks = []
    total_dur = 0.0

    # A-roll assignments - clip details live inside video_segments[]
    aroll = outputs.get("assign_aroll", {}).get("a_roll_assignments", [])
    if isinstance(aroll, dict):
        aroll = list(aroll.values())
    for i, a in enumerate(aroll if isinstance(aroll, list) else []):
        if not isinstance(a, dict):
            continue
        start = float(a.get("timeline_start", 0))
        end = float(a.get("timeline_end", 0))

        # Extract clip info from video_segments (the producer nests it there)
        vsegs = a.get("video_segments", [])
        first_seg = vsegs[0] if isinstance(vsegs, list) and vsegs else {}
        clip_id = first_seg.get("clip_id", a.get("clip_id", ""))
        clip_name = clip_id
        block_text = first_seg.get("text", a.get("text", ""))

        blocks.append(TimelineBlock(
            id=a.get("entry_id", f"aroll_{i}"),
            track="V1",
            clip_id=clip_id,
            clip_name=clip_name,
            start_s=start,
            end_s=end,
            duration_s=end - start,
            block_type="a_roll",
            text=block_text[:80],
            thumbnail_url=get_thumbnail_url(project_dir, clip_id),
        ))
        
        blocks.append(TimelineBlock(
            id=f"a1_{a.get('entry_id', i)}",
            track="A1",
            clip_id=clip_id,
            clip_name=clip_name,
            start_s=start,
            end_s=end,
            duration_s=end - start,
            block_type="a_roll_audio",
        ))
        total_dur = max(total_dur, end)

    # B-roll assignments
    broll = outputs.get("select_broll", {}).get("b_roll_assignments", [])
    if isinstance(broll, dict):
        broll = list(broll.values())
    for i, b in enumerate(broll if isinstance(broll, list) else []):
        if not isinstance(b, dict):
            continue
        start = float(b.get("timeline_start", 0))
        end = float(b.get("timeline_end", b.get("timeline_start", 0)))
        dur = float(b.get("duration", end - start))
        blocks.append(TimelineBlock(
            id=b.get("entry_id", f"broll_{i}"),
            track="V2",
            clip_id=b.get("clip_id", ""),
            clip_name=b.get("clip_id", ""),
            start_s=start,
            end_s=start + dur,
            duration_s=dur,
            block_type="b_roll",
            thumbnail_url=get_thumbnail_url(project_dir, b.get("clip_id", "")),
        ))

    # Music track (A2).
    # The music_selection step wraps its output under "music_selection".
    music = outputs.get("music_selection", {}).get("music_selection", {})
    if isinstance(music, dict) and (music.get("title") or music.get("audio_path")):
        blocks.append(TimelineBlock(
            id="music_1",
            track="A2",
            clip_id=music.get("title", "music"),
            clip_name=music.get("title", "Music"),
            start_s=0.0,
            end_s=total_dur,
            duration_s=total_dur,
            block_type="music",
        ))

    return TimelineView(
        blocks=blocks,
        total_duration_s=total_dur,
        track_count=2 if any(b.track == "V2" for b in blocks) else 1,
    )


# ── Pipeline Control ──────────────────────────────────────────────
#
# Four controls, and each one has to be true rather than merely present:
# Start, the handbrake (Pause), Resume, and Step.  The file protocol they
# speak lives in library/tools/run_control.py so the runner in another
# process cannot disagree with this one about a name.
#
# Start launches `--full-auto agy` and does NOT force `--review`.  Review
# gates are a separate, opt-in press: forcing them turns a 26-step run
# into 26 stops, which is not what running the pipeline means.
#
# Step reuses `run_pipeline.py --step <id>`, which already exists and is
# already proven.  The only new thing here is resolving *which* step -
# the first in topological order the project has not completed - so the
# press advances exactly one.

RUNNER = REPO_ROOT / "library" / "processes" / "edit_video" / "run_pipeline.py"

# How long to wait after spawning before believing a launch worked.  A
# runner that dies on an import error dies well inside this, and the
# alternative is a green "started" over a process that never existed.
_LAUNCH_SETTLE_S = 0.6


def _is_pipeline_running(project_dir: str) -> bool:
    return run_control.is_running(project_dir)


def _run_log_path(project_dir: str) -> Path:
    log_dir = Path(project_dir) / "pipeline_output" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    return log_dir / f"run_{time.strftime('%Y%m%d_%H%M%S')}.log"


def _step_order() -> List[str]:
    """The DAG in execution order."""
    sys.path.insert(0, str(REPO_ROOT / "library" / "processes" / "edit_video"))
    try:
        from library.processes.edit_video.run_pipeline import topological_sort
        return topological_sort(_load_dag())
    except Exception:
        return [n["id"] for n in _load_dag().get("nodes", [])]


def _resolve_next_step(project_dir: str) -> Optional[str]:
    state = _load_pipeline_state(project_dir)
    return run_control.next_runnable_step(
        _step_order(), step_ledger.all_completed(state))


async def _launch(project_dir: str, extra_args: List[str]) -> Dict[str, Any]:
    """Spawn the runner and report what actually happened.

    Two things here are load-bearing.  The interpreter is `sys.executable`
    - the dashboard is started from the pipeline's own .venv, and a bare
    `python3` is a different interpreter without the ML dependencies, so
    every run launched that way died on an import before touching a step.
    And the child's output goes to a file rather than DEVNULL, because a
    control whose failures are discarded reports success over nothing.
    """
    log_path = _run_log_path(project_dir)
    cmd = [sys.executable, str(RUNNER), "--project", project_dir] + extra_args

    env = os.environ.copy()
    env["PYTHONPATH"] = str(REPO_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    env.setdefault("PYTHONUNBUFFERED", "1")

    log_file = open(log_path, "w", encoding="utf-8")
    proc = subprocess.Popen(
        cmd, cwd=str(REPO_ROOT), env=env,
        stdout=log_file, stderr=subprocess.STDOUT,
    )

    await asyncio.sleep(_LAUNCH_SETTLE_S)
    returncode = proc.poll()
    if returncode is not None and returncode != 0:
        try:
            tail = "\n".join(
                log_path.read_text(encoding="utf-8", errors="replace")
                .splitlines()[-25:])
        except OSError:
            tail = "(no log)"
        raise HTTPException(
            500,
            f"Runner exited immediately with code {returncode}.\n"
            f"Command: {' '.join(cmd)}\n{tail}",
        )

    return {
        "pid": proc.pid,
        "command": cmd,
        "log_file": str(log_path),
        "exited_immediately": returncode is not None,
        "returncode": returncode,
    }


@app.post("/api/pipeline/run")
async def pipeline_run(request: PipelineRunRequest):
    """Start a run.  Defaults to `--full-auto agy`, review gates off."""
    project_dir = _get_project_dir()
    if _is_pipeline_running(project_dir):
        raise HTTPException(400, "Pipeline is already running")

    # Starting means going.  A handbrake left engaged from a previous
    # hold would stop this run before its first step.
    released = run_control.release_hold(project_dir)

    args: List[str] = []
    if request.from_step:
        args.extend(["--from", request.from_step])
    if request.single_step:
        args.extend(["--step", request.single_step])
    if request.full_auto:
        args.extend(["--full-auto", request.full_auto])
    if request.auto_mode:
        args.append("--auto")
    if request.review_mode:
        args.append("--review")
    if request.llm_timeout:
        args.extend(["--llm-timeout", str(request.llm_timeout)])

    launched = await _launch(project_dir, args)
    return {
        "status": "started",
        "mode": run_control.describe_mode(
            full_auto=request.full_auto, auto_mode=request.auto_mode,
            review_mode=request.review_mode, from_step=request.from_step,
            single_step=request.single_step,
        ),
        "hold_released": released,
        **launched,
    }


@app.post("/api/pipeline/pause")
async def pipeline_pause(request: Optional[PipelinePauseRequest] = None):
    """Engage the handbrake: hold after the current step finishes.

    This does not kill anything.  The step in flight writes its output
    and its state, and the runner stops at the next step boundary, so
    what is on disk is always a real boundary the next run can start
    from.
    """
    project_dir = _get_project_dir()
    reason = (request.reason if request else "") or ""
    record = run_control.request_hold(project_dir, requested_by="dashboard",
                                      reason=reason)
    status = run_control.read_run_status(project_dir)
    running = _is_pipeline_running(project_dir)
    return {
        "status": "hold_requested",
        "was_running": running,
        # Said plainly: with no run up, this arms the handbrake for the
        # next one rather than doing nothing.
        "effect": ("will hold after the current step completes" if running
                   else "armed; the next run will hold before its first step"),
        "holding_after_step": status.get("current_step"),
        "hold": record,
    }


@app.delete("/api/pipeline/pause")
async def pipeline_release_hold():
    """Disengage the handbrake without launching anything."""
    project_dir = _get_project_dir()
    released = run_control.release_hold(project_dir)
    return {"status": "hold_released" if released else "no_hold_engaged",
            "released": released}


@app.post("/api/pipeline/resume")
async def pipeline_resume(request: Optional[PipelineRunRequest] = None):
    """Resume after a handbrake hold or a review gate.

    `--resume` covers both: it walks the DAG, skips what is complete,
    applies any gate feedback on the way past, and picks up at the first
    step that has not run.
    """
    project_dir = _get_project_dir()
    if _is_pipeline_running(project_dir):
        raise HTTPException(400, "Pipeline is already running")

    released = run_control.release_hold(project_dir)

    req = request or PipelineRunRequest()
    args = ["--resume"]
    if req.full_auto:
        args.extend(["--full-auto", req.full_auto])
    if req.auto_mode:
        args.append("--auto")
    if req.review_mode:
        args.append("--review")
    if req.llm_timeout:
        args.extend(["--llm-timeout", str(req.llm_timeout)])

    launched = await _launch(project_dir, args)
    return {
        "status": "resumed",
        "mode": run_control.describe_mode(
            full_auto=req.full_auto, auto_mode=req.auto_mode,
            review_mode=req.review_mode, resume_mode=True,
        ),
        "hold_released": released,
        "next_step": _resolve_next_step(project_dir),
        **launched,
    }


@app.post("/api/pipeline/step")
async def pipeline_step(request: Optional[PipelineStepRequest] = None):
    """Advance exactly one step.

    Nothing new in the runner backs this: it is `--step <id>`, which
    already runs precisely one node and stops.  All this endpoint adds is
    picking the id - the first step in topological order the project has
    not completed - unless the caller names one to steer to.
    """
    project_dir = _get_project_dir()
    if _is_pipeline_running(project_dir):
        raise HTTPException(400, "Pipeline is already running")

    req = request or PipelineStepRequest()
    step_id = req.step_id or _resolve_next_step(project_dir)
    if not step_id:
        raise HTTPException(400, "Every step in the DAG is already complete")
    if step_id not in {n["id"] for n in _load_dag().get("nodes", [])}:
        raise HTTPException(404, f"Unknown step '{step_id}'")

    # One step is one step.  A hold left engaged would stop it before it
    # started, so release it - and re-engage it after, so that a stray
    # Resume press does not turn a single-step session into a full run.
    run_control.release_hold(project_dir)

    args = ["--step", step_id]
    if req.full_auto:
        args.extend(["--full-auto", req.full_auto])
    if req.auto_mode:
        args.append("--auto")
    if req.review_mode:
        args.append("--review")
    if req.llm_timeout:
        args.extend(["--llm-timeout", str(req.llm_timeout)])

    launched = await _launch(project_dir, args)
    return {
        "status": "stepping",
        "step_id": step_id,
        "step_name": next(
            (n["name"] for n in _load_dag().get("nodes", [])
             if n["id"] == step_id), step_id),
        "mode": run_control.describe_mode(
            full_auto=req.full_auto, auto_mode=req.auto_mode,
            review_mode=req.review_mode, single_step=step_id,
        ),
        **launched,
    }


@app.get("/api/pipeline/status")
async def pipeline_status():
    """Get current pipeline execution status."""
    project_dir = _get_project_dir()
    state = _load_pipeline_state(project_dir)
    pending = list_pending_gates(project_dir)
    run_status = run_control.read_run_status(project_dir)
    hold = run_control.hold_requested(project_dir)
    is_running = _is_pipeline_running(project_dir)

    # `current_step` is what the runner last said it was working on, and
    # only counts while a process is actually alive.  A stale field from
    # a killed run reading as "running" is exactly the sort of report
    # this dashboard has been punished for before.
    current_step = run_status.get("current_step") if is_running else None

    return PipelineStatus(
        is_running=is_running,
        current_step=current_step,
        current_step_name=run_status.get("current_step_name") if current_step else None,
        completed_steps=list(step_ledger.all_completed(state).keys()),
        pending_gates=pending,
        failed_steps=state.get("failed_steps", []),
        hold_requested=hold is not None,
        hold_requested_at=(hold or {}).get("requested_at"),
        held_before_step=run_status.get("held_before_step"),
        last_completed_step=run_status.get("last_completed_step"),
        next_step=_resolve_next_step(project_dir),
        run_state=run_status.get("status", "idle"),
        mode=run_status.get("mode"),
        started_at=run_status.get("started_at"),
        finished_at=run_status.get("finished_at"),
    )


# ── Thumbnails ────────────────────────────────────────────────────

@app.get("/thumbnails/{filename}")
async def serve_thumbnail(filename: str):
    """Serve a thumbnail image."""
    project_dir = _get_project_dir()
    thumb_path = Path(project_dir) / "pipeline_output" / "thumbnails" / filename
    if not thumb_path.exists():
        raise HTTPException(404, "Thumbnail not found")
    return FileResponse(str(thumb_path), media_type="image/jpeg")


# ── Server Startup ────────────────────────────────────────────────

def start_server(
    project_dir: Optional[str] = None,
    slug: str = "",
    host: str = "127.0.0.1",
    port: int = 8420,
):
    """Start the dashboard server."""
    global _project_dir, _project_slug
    
    if not project_dir:
        from library.tools.paths import PROJECTS_ROOT
        all_dirs = []
        if PROJECTS_ROOT and PROJECTS_ROOT.exists():
            for entry in PROJECTS_ROOT.rglob("pipeline_data.json"):
                all_dirs.append(entry.parent)
            for entry in PROJECTS_ROOT.rglob("project.yaml"):
                all_dirs.append(entry.parent)
        
        all_dirs = list(set(all_dirs))
        if all_dirs:
            def get_mtime(d):
                p_data = d / "pipeline_data.json"
                if p_data.exists(): return p_data.stat().st_mtime
                return d.stat().st_mtime
            
            all_dirs.sort(key=get_mtime, reverse=True)
            project_dir = str(all_dirs[0])
            slug = all_dirs[0].name
        else:
            raise RuntimeError("No projects found to auto-discover. Start server with --project.")

    _project_dir = os.path.abspath(project_dir)
    _project_slug = slug

    print(f"\n  Review Dashboard")
    print(f"  Project: {_project_dir}")
    print(f"  URL:     http://{host}:{port}")
    print(f"  Press Ctrl+C to stop\n")

    uvicorn.run(app, host=host, port=port, log_level="info")


def main():
    """CLI entry point."""
    import argparse
    parser = argparse.ArgumentParser(description="Review Dashboard Server")
    group = parser.add_mutually_exclusive_group(required=False)
    group.add_argument("--project", help="Project directory (absolute path)")
    group.add_argument("--slug", help="Project slug (looked up from registry)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8420)
    args = parser.parse_args()

    project_dir = args.project
    slug = args.slug or ""

    if args.slug:
        try:
            from library.tools.paths import project_root
            project_dir = str(project_root(args.slug))
            slug = args.slug
        except (ImportError, FileNotFoundError) as e:
            print(f"Error resolving slug '{args.slug}': {e}", file=sys.stderr)
            sys.exit(1)

    start_server(project_dir, slug=slug, host=args.host, port=args.port)


if __name__ == "__main__":
    main()
