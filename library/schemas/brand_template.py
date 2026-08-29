import json
from dataclasses import dataclass, field, asdict
from typing import List, Dict, Any, Optional


def _delivery_format_names() -> List[str]:
    """Enumerated delivery formats, imported late to keep this module leaf-level."""
    from library.tools.delivery_format import format_names
    return format_names()

@dataclass
class StyleSlots:
    color_palette: List[str] = field(default_factory=list)
    house_look: str = ""
    reference_look_image: str = ""

    typography: Dict[str, Any] = field(default_factory=dict)
    # "" is "declares none".  It used to default to "moderate", which is a
    # level, and a level nobody chose is exactly the thing an undeclared
    # slot must not assert.  The energy the pipeline reads is the creative
    # direction's own `target_energy` (library/tools/energy_reading.py);
    # this slot reaches only step 2.01's brand constraints text.
    energy_profile: str = ""
    framing_intent: Optional[float] = None  # 0.0=letterbox, 1.0=fill, None=auto

@dataclass
class EffectSlots:
    transition_types: List[str] = field(default_factory=list)
    transition_duration_ms: Dict[str, int] = field(default_factory=dict)
    vfx_intensity: float = 0.0
    subtitle_style: str = ""
    # NO READER.  `audio_reactive_sfx.scale_sfx_density` was the only one
    # and was deleted (AGENTS.md 10.5).  The field is kept because shipped
    # templates still declare it and dropping it would fail to parse them;
    # "" is "declares none" and nothing reads either value.
    sfx_density: str = ""
    # "lowercase" | "as_written".  The one creative value that survives an
    # absent brand template, and the reason every caption card on project
    # 001 is lowercase.  PARKED: which case the copy is set in is the
    # captain's open decision, so it is inventoried rather than changed.
    # See ABSENT_SLOT_READINGS in library/tools/brand_registry.py.
    caption_case: str = "lowercase"
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
    # Intro / outro / end card (Q7, 2026-08-16). A template declares which
    # card fills which slot, or omits the key and gets none. The
    # declaration shape, the two production modes and the path it takes to
    # the timeline all live in library/tools/bookends.py.
    # This replaced `intro_template` / `outro_template`, which named two
    # Fusion .setting macros nothing imported - a full-frame red slate and
    # a "Subscribe!" card the channel spec forbids by name.
    bookends: Optional[Dict[str, Any]] = None
    watermark: Dict[str, Any] = field(default_factory=dict)
    # NO READER.  Step 2.04's handoff names `brand_content.music_genre`,
    # but no manifest routes `brand_content` to step 2.04, so the slot
    # reaches no prompt from any template.  Routing it means changing that
    # manifest; the handoff is under the captain's freeze and needs none.
    music_genre: List[str] = field(default_factory=list)
    target_duration_seconds: Dict[str, int] = field(default_factory=dict)

@dataclass
class BrandTemplate:
    series_id: str
    # The frame the PRODUCT ships in (captain's ruling, 2026-08-19). A
    # name from library/tools/delivery_format.DELIVERY_FORMATS; empty
    # means "declares nothing" and yields the vertical default. It sits
    # at the top level rather than inside style/effect/content because it
    # is none of those - it is what the series delivers, and every one of
    # the three slot groups is read as creative direction.
    delivery_format: str = ""
    style: StyleSlots = field(default_factory=StyleSlots)
    effect: EffectSlots = field(default_factory=EffectSlots)
    content: ContentSlots = field(default_factory=ContentSlots)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> 'BrandTemplate':
        series_id = data.get("series_id", "default")
        style = StyleSlots(**data.get("style", {}))
        effect = EffectSlots(**data.get("effect", {}))
        content = ContentSlots(**data.get("content", {}))
        return cls(series_id=series_id,
                   delivery_format=data.get("delivery_format", "") or "",
                   style=style, effect=effect, content=content)

    @staticmethod
    def get_json_schema() -> Dict[str, Any]:
        return {
            "type": "object",
            "properties": {
                "series_id": {"type": "string"},
                "delivery_format": {
                    "type": "string",
                    "enum": _delivery_format_names(),
                    "description": "The frame this series ships in. Omit for the vertical default. See library/tools/delivery_format.py."
                },
                "style": {
                    "type": "object",
                    "properties": {
                        "color_palette": {"type": "array", "items": {"type": "string"}},
                        "house_look": {"type": "string"},
                        "reference_look_image": {"type": "string"},

                        "typography": {"type": "object"},
                        "energy_profile": {"type": "string", "enum": ["", "calm", "moderate", "high"]},
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
                        "sfx_density": {"type": "string", "enum": ["", "sparse", "moderate", "dense"]},
                        "caption_case": {"type": "string", "enum": ["lowercase", "as_written"], "default": "lowercase"}
                    }
                },
                "content": {
                    "type": "object",
                    "properties": {
                        "series_title": {"type": "string"},
                        "channel_name": {"type": "string"},
                        "bookends": {
                            "type": "object",
                            "description": "Intro / outro / end card. Omit for none. See library/tools/bookends.py.",
                            "properties": {
                                slot: {
                                    "type": "object",
                                    "properties": {
                                        "composition": {"type": "string", "description": "Remotion composition to render"},
                                        "source": {"type": "string", "description": "Project-owned .tsx for that composition"},
                                        "asset": {"type": "string", "description": "A clip that already exists"},
                                        "duration_seconds": {"type": "number", "exclusiveMinimum": 0},
                                        "props": {"type": "object"},
                                        "has_audio": {"type": "boolean"}
                                    },
                                    "required": ["duration_seconds"]
                                }
                                for slot in ("intro", "outro", "end_card")
                            },
                            "additionalProperties": False
                        },
                        "watermark": {"type": "object"},
                        "music_genre": {"type": "array", "items": {"type": "string"}},
                        "target_duration_seconds": {"type": "object"}
                    }
                }
            },
            "required": ["series_id"]
        }
