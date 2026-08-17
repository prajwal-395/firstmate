import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional

@dataclass
class StyleSlots:
    color_palette: List[str] = field(default_factory=list)
    house_look: str = ""
    reference_look_image: str = ""

    typography: Dict[str, Any] = field(default_factory=dict)
    energy_profile: str = "moderate"
    framing_intent: Optional[float] = None  # 0.0=letterbox, 1.0=fill, None=auto

@dataclass
class EffectSlots:
    transition_types: List[str] = field(default_factory=list)
    transition_duration_ms: Dict[str, int] = field(default_factory=dict)
    vfx_intensity: float = 0.0
    subtitle_style: str = ""
    sfx_density: str = "moderate"
    caption_case: str = "lowercase"  # "lowercase" | "as_written"
    # Motion graphics (P3.1). Both default to today's behaviour: the
    # corner accents and the progress bar were drawn unconditionally, and
    # whether the house style should keep them is Q3, a captain's call.
    motion_accents: Optional[bool] = None
    motion_progress_bar: Optional[bool] = None
    # Timed text overlay (Q7, 2026-08-16). A template declares the text
    # moments that appear as a transparent overlay, or omits the key to
    # get nothing. Same opt-in shape as motion_accents.
    timed_text_overlay: Optional[Dict[str, Any]] = None

@dataclass
class ContentSlots:
    series_title: str = ""
    channel_name: str = ""
    intro_template: str = ""
    outro_template: str = ""
    watermark: Dict[str, Any] = field(default_factory=dict)
    music_genre: List[str] = field(default_factory=list)
    target_duration_seconds: Dict[str, int] = field(default_factory=dict)

@dataclass
class BrandTemplate:
    series_id: str
    style: StyleSlots = field(default_factory=StyleSlots)
    effect: EffectSlots = field(default_factory=EffectSlots)
    content: ContentSlots = field(default_factory=ContentSlots)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BrandTemplate':
        series_id = data.get("series_id", "default")
        style = StyleSlots(**data.get("style", {}))
        effect = EffectSlots(**data.get("effect", {}))
        content = ContentSlots(**data.get("content", {}))
        return cls(series_id=series_id, style=style, effect=effect, content=content)

    @staticmethod
    def get_json_schema() -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "series_id": {"type": "string"},
                "style": {
                    "type": "object",
                    "properties": {
                        "color_palette": {"type": "array", "items": {"type": "string"}},
                        "house_look": {"type": "string"},
                        "reference_look_image": {"type": "string"},

                        "typography": {"type": "object"},
                        "energy_profile": {"type": "string", "enum": ["calm", "moderate", "high"]},
                        "framing_intent": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "Default clip framing: 0.0=full letterbox, 1.0=complete fill. Omit for auto."
                        }
                    }
                },
                "effect": {
                    "type": "object",
                    "properties": {
                        "transition_types": {"type": "array", "items": {"type": "string"}},
                        "transition_duration_ms": {"type": "object"},
                        "vfx_intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "subtitle_style": {"type": "string"},
                        "motion_accents": {"type": "boolean"},
                        "motion_progress_bar": {"type": "boolean"},
                        "timed_text_overlay": {
                            "type": "object",
                            "description": "Timed text moments rendered as a transparent overlay. Omit for no overlay.",
                            "properties": {
                                "font_family": {"type": "string"},
                                "moments": {
                                    "type": "array",
                                    "items": {
                                        "type": "object",
                                        "properties": {
                                            "text": {"type": "string"},
                                            "color": {"type": "string"},
                                            "font_size": {"type": "integer"},
                                            "start_frame": {"type": "integer"},
                                            "duration_frames": {"type": "integer"},
                                            "x": {"type": "number"},
                                            "y": {"type": "number"},
                                            "fade_in_frames": {"type": "integer"},
                                            "fade_out_frames": {"type": "integer"}
                                        },
                                        "required": ["text", "color", "start_frame", "duration_frames"]
                                    }
                                }
                            }
                        },
                        "sfx_density": {"type": "string", "enum": ["sparse", "moderate", "dense"]},
                        "caption_case": {"type": "string", "enum": ["lowercase", "as_written"], "default": "lowercase"}
                    }
                },
                "content": {
                    "type": "object",
                    "properties": {
                        "series_title": {"type": "string"},
                        "channel_name": {"type": "string"},
                        "intro_template": {"type": "string"},
                        "outro_template": {"type": "string"},
                        "watermark": {"type": "object"},
                        "music_genre": {"type": "array", "items": {"type": "string"}},
                        "target_duration_seconds": {"type": "object"}
                    }
                }
            },
            "required": ["series_id"]
        }
