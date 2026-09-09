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


class MessageType(str, Enum):
    DECISION = "decision"
    STATUS = "status"
    QUESTION = "question"
    PREVIEW = "preview"
    ERROR = "error"


class DecisionOption(BaseModel):
    id: str
    label: str
    description: str
    thumbnail_url: Optional[str] = None


class AgentMessage(BaseModel):
    id: str
    type: MessageType
    step_id: Optional[str] = None
    title: str
    body: str
    options: List[DecisionOption] = Field(default_factory=list)
    preview_data: Optional[Dict[str, Any]] = None
    requires_response: bool = False
    created_at: str
    responded_at: Optional[str] = None
    response: Optional[Dict[str, Any]] = None


class UserResponse(BaseModel):
    message_id: str
    action: str  # approve, reject, choose, comment
    chosen_option_id: Optional[str] = None
    comment: Optional[str] = None
    annotations: Optional[Dict[str, Any]] = None


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
    """A block on the timeline (A-roll, B-roll, caption, or transition).

    `track` uses the built timeline's own numbering, so what the
    dashboard draws and what step 6.01 places carry the same names.
    """
    id: str
    track: str                     # "V1", "V2", "V3", "A1", "A2"
    clip_id: str = ""              # Empty for a caption: it is not a clip
    clip_name: str = ""
    start_s: float = 0.0
    end_s: float = 0.0
    duration_s: float = 0.0
    block_type: str = ""           # "a_roll", "b_roll", "subtitle",
                                   # "transition", "music", "a_roll_audio"
    text: str = ""                 # Speech text if A-roll; the caption if V3
    thumbnail_url: str = ""


class TimelineView(BaseModel):
    """Rough cut timeline for visualization."""
    blocks: List[TimelineBlock] = Field(default_factory=list)
    total_duration_s: float = 0.0
    track_count: int = 0           # Tracks actually carrying something


# ── Pipeline Control ───────────────────────────────────────────────

class PipelineRunRequest(BaseModel):
    """Request to start/resume/rerun pipeline.

    ``full_auto`` defaults to ``"agent"`` and ``review_mode`` to False,
    because that is how the pipeline is actually driven.  The old default
    of ``review_mode=True`` turned one Start press into 26 stops.
    """
    from_step: Optional[str] = None
    single_step: Optional[str] = None
    full_auto: Optional[str] = "agent"   # agent | api | mock | None (manual LLM)
    auto_mode: bool = False            # --auto: complete hybrids from the bridge
    review_mode: bool = False          # --review: opt in, never forced
    llm_timeout: Optional[int] = None


class PipelineStepRequest(BaseModel):
    """Request to advance the pipeline by exactly one step.

    ``step_id`` is optional: left unset the server resolves the first
    step in topological order the project has not completed, which is
    what "advance one step" means.  Setting it is how a reviewer steers
    to a specific step instead.
    """
    step_id: Optional[str] = None
    full_auto: Optional[str] = "agent"
    auto_mode: bool = False
    review_mode: bool = False
    llm_timeout: Optional[int] = None


class PipelinePauseRequest(BaseModel):
    """Request to engage the handbrake."""
    reason: str = ""


class PipelineStatus(BaseModel):
    """Current pipeline execution status.

    Everything here is read back off disk from what the runner itself
    wrote.  Nothing is inferred from the fact that a launch request
    returned 200.
    """
    is_running: bool = False
    current_step: Optional[str] = None
    current_step_name: Optional[str] = None
    completed_steps: List[str] = Field(default_factory=list)
    pending_gates: List[str] = Field(default_factory=list)
    failed_steps: List[str] = Field(default_factory=list)
    hold_requested: bool = False
    hold_requested_at: Optional[str] = None
    held_before_step: Optional[str] = None
    last_completed_step: Optional[str] = None
    next_step: Optional[str] = None
    run_state: str = "idle"            # idle | running | held | gate_pending | success | failed | partial
    mode: Optional[str] = None
    started_at: Optional[str] = None
    finished_at: Optional[str] = None


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


# ── Review Return Channel ──────────────────────────────────────────

class ReviewAnchor(BaseModel):
    """Where on screen a note is attached. Computed in the BROWSER.

    `selector` is the CSS path the browser measured for the element; it is
    what lets the note find its element again after the view re-renders.
    `tag`/`text` are the fallback identity when the path no longer matches.
    See library/dashboard/review_channel.py.
    """
    selector: str
    tag: str = ""
    text: str = ""               # Visible text of the anchored element
    label: str = ""              # Human label for the note list
    view: str = ""               # Dashboard view the note was written on
    step_id: str = ""


class ReviewNoteRequest(BaseModel):
    """A note the reviewer queues against one element."""
    text: str
    anchor: ReviewAnchor


class ReviewSendRequest(BaseModel):
    """Send the queued notes as one batch. Empty note_ids means all of them."""
    note_ids: List[str] = Field(default_factory=list)


class ReviewReplyRequest(BaseModel):
    """An agent's reply, landing on the notes it answers."""
    batch_id: str
    text: str
    note_ids: List[str] = Field(default_factory=list)
    author: str = "agent"


# ── Footage Search ─────────────────────────────────────────────────

class FootageSearchRequest(BaseModel):
    """One search of the project's own footage, from the browser.

    `query` and `filters` are independent halves. A query with no filters
    is a search; filters with no query is a selection ("steady wide
    footage with nobody in frame"), and both together is the useful case.

    `floor` is the minimum dense cosine a segment needs to count as
    evidence, so that a search for something the footage does not contain
    can answer "nothing" instead of three confident wrong rows. Omitted
    means the measured default in `footage_query.DENSE_SCORE_FLOOR`; 0
    means the reviewer asked to see the ranking with no floor at all.
    """
    query: str = ""
    top_k: int = 10
    mode: str = "hybrid"
    floor: Optional[float] = None
    filters: Dict[str, Any] = Field(default_factory=dict)
