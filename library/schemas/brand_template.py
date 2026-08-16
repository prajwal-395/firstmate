import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any

@dataclass
class StyleSlots:
    color_palette: List[str] = field(default_factory=list)
    preferred_powergrade: str = ""
    reference_look_image: str = ""

    typography: Dict[str, Any] = field(default_factory=dict)
    pacing: Dict[str, float] = field(default_factory=dict)
    energy_profile: str = "moderate"

@dataclass
class EffectSlots:
    transition_types: List[str] = field(default_factory=list)
    transition_duration_ms: Dict[str, int] = field(default_factory=dict)
    vfx_intensity: float = 0.0
    subtitle_style: str = ""
    sfx_density: str = "moderate"
    caption_case: str = "lowercase"  # "lowercase" | "as_written"

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
                        "preferred_powergrade": {"type": "string"},
                        "reference_look_image": {"type": "string"},

                        "typography": {"type": "object"},
                        "pacing": {"type": "object"},
                        "energy_profile": {"type": "string", "enum": ["calm", "moderate", "high"]}
                    }
                },
                "effect": {
                    "type": "object",
                    "properties": {
                        "transition_types": {"type": "array", "items": {"type": "string"}},
                        "transition_duration_ms": {"type": "object"},
                        "vfx_intensity": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                        "subtitle_style": {"type": "string"},
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
