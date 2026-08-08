"""
Pydantic models for the Review Dashboard API.

Defines request/response shapes for the dashboard endpoints.
Clean separation from pipeline internals - these models are the
contract between the frontend and backend.
"""

from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


# ── Enums ───────────────────────────────────────────────────────────

class StepStatus(str, Enum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    GATE_PENDING = "gate_pending"   # Step completed, awaiting human review
    SKIPPED = "skipped"


class GateAction(str, Enum):
    APPROVE = "approve"
    REJECT = "reject"
    REVISE = "revise"              # Approve with modifications


# ── Step Models ─────────────────────────────────────────────────────

class StepSummary(BaseModel):
    """Lightweight step info for the pipeline overview."""
    id: str
    name: str
    status: StepStatus = StepStatus.PENDING
    elapsed_s: Optional[float] = None
    completed_at: Optional[str] = None
    has_gate: bool = False
    output_keys: List[str] = Field(default_factory=list)


class StepDetail(BaseModel):
    """Full step output for the inspector view."""
    id: str
    name: str
    status: StepStatus
    output: Dict[str, Any] = Field(default_factory=dict)
    summary_md: str = ""           # Human-readable markdown summary
    elapsed_s: Optional[float] = None
    completed_at: Optional[str] = None


# ── Gate Models ─────────────────────────────────────────────────────

class GateStatus(BaseModel):
    """Current state of a review gate."""
    step_id: str
    status: str = "pending"        # pending | approved | rejected | revised
    feedback: Optional[str] = None
    annotations: Dict[str, Any] = Field(default_factory=dict)
    timestamp: Optional[str] = None


class GateActionRequest(BaseModel):
    """Request body for gate approve/reject/revise."""
    action: GateAction
    feedback: str = ""
    revisions: Dict[str, Any] = Field(default_factory=dict)


# ── Annotation Models ──────────────────────────────────────────────

class Annotation(BaseModel):
    """A user annotation on a step output or transcript region."""
    id: str = ""
    step_id: str = ""
    target_path: str = ""          # JSON path within step output (e.g., "speech_sequence.body[2]")
    annotation_type: str = "comment"  # comment | highlight | strikethrough | tag
    content: str = ""
    tag: str = ""                  # Optional tag: "must_include", "cut", "hero_shot", etc.
    created_at: str = ""


class AnnotationBatch(BaseModel):
    """Batch of annotations submitted together."""
    annotations: List[Annotation] = Field(default_factory=list)


# ── Transcript Models ──────────────────────────────────────────────

class TranscriptRegion(BaseModel):
    """A segment of transcript text tied to a source clip."""
    clip_id: str
    text: str
    start: float                   # Source timecode (seconds)
    end: float
    words: List[Dict[str, Any]] = Field(default_factory=list)
    speaker: str = ""
    annotations: List[Annotation] = Field(default_factory=list)


class TranscriptView(BaseModel):
    """Full transcript across all clips for Descript-style display."""
    regions: List[TranscriptRegion] = Field(default_factory=list)
    total_duration_s: float = 0.0
    clip_count: int = 0


# ── Clip Models ─────────────────────────────────────────────────────

class ClipInfo(BaseModel):
    """Clip metadata for the footage library view."""
    clip_id: str
    filename: str
    filepath: str = ""
    duration_s: float = 0.0
    resolution: str = ""
    fps: float = 0.0
    thumbnail_url: str = ""
    transcript_excerpt: str = ""
    mood_tags: List[str] = Field(default_factory=list)
    interest_score: float = 0.0
    detected_objects: List[str] = Field(default_factory=list)
    annotations: List[Annotation] = Field(default_factory=list)


# ── Timeline Models ────────────────────────────────────────────────

class TimelineBlock(BaseModel):
    """A block on the timeline (A-roll, B-roll, or transition)."""
    id: str
    track: str                     # "V1", "V2", "A1", "A2"
    clip_id: str = ""
    clip_name: str = ""
    start_s: float = 0.0
    end_s: float = 0.0
    duration_s: float = 0.0
    block_type: str = ""           # "a_roll", "b_roll", "transition", "music"
    text: str = ""                 # Speech text if A-roll
    thumbnail_url: str = ""


class TimelineView(BaseModel):
    """Rough cut timeline for visualization."""
    blocks: List[TimelineBlock] = Field(default_factory=list)
    total_duration_s: float = 0.0
    track_count: int = 0


# ── Pipeline Control ───────────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    """Request to start/resume/rerun pipeline."""
    from_step: Optional[str] = None
    single_step: Optional[str] = None
    review_mode: bool = True


class PipelineStatus(BaseModel):
    """Current pipeline execution status."""
    is_running: bool = False
    current_step: Optional[str] = None
    completed_steps: List[str] = Field(default_factory=list)
    pending_gates: List[str] = Field(default_factory=list)
    failed_steps: List[str] = Field(default_factory=list)


# ── Project Models ─────────────────────────────────────────────────

class ProjectInfo(BaseModel):
    """Project metadata for the dashboard."""
    slug: str
    name: str
    client: str = ""
    status: str = "draft"
    resolution: str = "1080x1920"
    fps: int = 30
    raw_footage_count: int = 0
    steps_completed: int = 0
    total_steps: int = 0
    project_root: str = ""
