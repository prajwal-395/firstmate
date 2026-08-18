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

from library.dashboard.models import (
    Annotation,
    AnnotationBatch,
    ClipInfo,
    GateActionRequest,
    GateStatus,
    PipelineRunRequest,
    PipelineStatus,
    ProjectInfo,
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
        completed = state.get("steps_completed", {})
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
                    completed = state.get("steps_completed", {})
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
                        completed = state.get("steps_completed", {})
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

    completed = state.get("steps_completed", {})
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

    completed = state.get("steps_completed", {})
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

    completed = state.get("steps_completed", {})
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

def _is_pipeline_running(project_dir: str) -> bool:
    pid_file = os.path.join(project_dir, "pipeline.pid")
    if os.path.exists(pid_file):
        with open(pid_file) as f:
            pid_str = f.read().strip()
        if pid_str.isdigit():
            pid = int(pid_str)
            try:
                os.kill(pid, 0)
                return True
            except OSError:
                try:
                    os.remove(pid_file)
                except OSError:
                    pass
    return False

@app.post("/api/pipeline/run")
async def pipeline_run(request: PipelineRunRequest):
    project_dir = _get_project_dir()
    if _is_pipeline_running(project_dir):
        raise HTTPException(400, "Pipeline is already running")
        
    cmd = ["python3", str(REPO_ROOT / "library" / "processes" / "edit_video" / "run_pipeline.py"), "--project", project_dir]
    if request.from_step:
        cmd.extend(["--from", request.from_step])
    if request.single_step:
        cmd.extend(["--step", request.single_step])
    if request.review_mode:
        cmd.append("--review")
        
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "started"}

@app.post("/api/pipeline/resume")
async def pipeline_resume():
    project_dir = _get_project_dir()
    if _is_pipeline_running(project_dir):
        raise HTTPException(400, "Pipeline is already running")
        
    cmd = ["python3", str(REPO_ROOT / "library" / "processes" / "edit_video" / "run_pipeline.py"), "--project", project_dir, "--resume"]
    subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"status": "resumed"}

@app.get("/api/pipeline/status")
async def pipeline_status():
    """Get current pipeline execution status."""
    project_dir = _get_project_dir()
    state = _load_pipeline_state(project_dir)
    pending = list_pending_gates(project_dir)

    return PipelineStatus(
        is_running=_is_pipeline_running(project_dir),
        completed_steps=list(state.get("steps_completed", {}).keys()),
        pending_gates=pending,
        failed_steps=state.get("failed_steps", []),
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
