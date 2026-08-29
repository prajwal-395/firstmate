from typing import List, Dict, Optional, Any
from pydantic import BaseModel, Field

class V1Clip(BaseModel):
    source_file: str
    source_in: Optional[float] = None
    source_out: Optional[float] = None
    timeline_in: Optional[float] = None
    timeline_out: Optional[float] = None
    timeline_in_frame: int
    timeline_out_frame: Optional[int] = None
    audio_src_in: Optional[float] = None
    audio_src_out: Optional[float] = None
    label: Optional[str] = None
    needs_conform: Optional[bool] = None
    fill_zoom: Optional[float] = None
    framing_pan_x: Optional[float] = None  # pixels
    framing_pan_y: Optional[float] = None  # pixels

class V2Clip(BaseModel):
    source_file: str
    source_in: Optional[float] = None
    source_out: Optional[float] = None
    timeline_in: Optional[float] = None
    timeline_out: Optional[float] = None
    timeline_in_frame: int
    timeline_out_frame: Optional[int] = None
    label: Optional[str] = None
    needs_conform: Optional[bool] = None
    fill_zoom: Optional[float] = None
    framing_pan_x: Optional[float] = None
    framing_pan_y: Optional[float] = None

class A2Clip(BaseModel):
    source_file: str
    source_in: Optional[float] = None
    timeline_in: float
    timeline_out: float
    volume_db: Optional[float] = None

class SFXClip(BaseModel):
    source_file: str
    source_in: Optional[float] = None
    timeline_in_frame: int
    timeline_out_frame: int
    volume_db: Optional[float] = None
    label: Optional[str] = None

class SubtitleSegment(BaseModel):
    overlay_path: str
    timeline_start: float
    timeline_end: float
    total_frames: Optional[int] = None

class MotionGraphicsSegment(BaseModel):
    overlay_path: str
    timeline_start: float
    timeline_end: float
    total_frames: Optional[int] = None

class Transition(BaseModel):
    transition_type: str
    from_block: int
    to_block: int
    duration_frames: int

class VFXEntry(BaseModel):
    timeline_start: float
    effect_type: str
    params: Dict[str, Any] = Field(default_factory=dict)

class ColorGradeAdjustment(BaseModel):
    source_file: str
    cdl_values: Dict[str, float]
    # The measured average luma, or None when nothing measured it. Never
    # a number standing in for an absent measurement - see step 5.01.
    measured_luma: Optional[float] = None
    measured_luma_method: Optional[str] = None

class ColorGrade(BaseModel):
    per_clip_adjustments: List[ColorGradeAdjustment] = Field(default_factory=list)
    # The NAME the brand template's `style.house_look` declaration
    # carries. None means no template declared a look, which means no
    # grade at all - there is no house look to fall back to.
    house_look: Optional[str] = None


class DuckingCurve(BaseModel):
    time_ms: float
    volume_db: float

class MusicAutomation(BaseModel):
    timeline_start: float
    target_level_db: float
    music_behavior: str

class ResolveManifest(BaseModel):
    project: Dict[str, Any]
    tracks: Dict[str, Dict[str, Any]]
    subtitles: List[SubtitleSegment] = Field(default_factory=list)
    transitions: List[Transition] = Field(default_factory=list)
    vfx: List[VFXEntry] = Field(default_factory=list)
    sfx: List[SFXClip] = Field(default_factory=list)
    color_grade: Optional[ColorGrade] = None
    audio_mix: Dict[str, Any] = Field(default_factory=dict)
    subtitle_overlay: Dict[str, Any] = Field(default_factory=dict)
    motion_graphics_overlay: Dict[str, Any] = Field(default_factory=dict)
    timed_text_overlay: Dict[str, Any] = Field(default_factory=dict)
