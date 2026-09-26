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
    # The look this series DECLARES, as values - a mapping read by
    # `library/tools/series_look.resolve_look`.  It used to be a NAME into
    # a catalogue of four looks this engine shipped, and their strengths
    # were numbers nobody chose (captain, 2026-08-28: "there are no house
    # glow looks, there are no settled house grain or anything").  The
    # catalogue is gone and was not relocated: None means this template
    # declares no look, and a project under it gets no CDL, no contrast,
    # no glow, no grain and no vignette.
    series_look: Optional[Dict[str, Any]] = None
    reference_look_image: str = ""

    typography: Dict[str, Any] = field(default_factory=dict)
    # "" is "declares none".  It used to default to "moderate", which is a
    # level, and a level nobody chose is exactly the thing an undeclared
    # slot must not assert.  The energy the pipeline reads is the creative
    # direction's own `target_energy` (library/tools/energy_reading.py);
    # this slot reaches only step 2.01's brand constraints text.
    energy_profile: str = ""
    framing_intent: Optional[float] = None  # 0.0=letterbox, 1.0=fill, None=auto
    # The punched-in TV-frame look (2026-09-09, captain's Reel 20 marker).
    # A mapping with `asset` (the frame PNG, absolute or project-relative
    # path), `punch_in` (the V1 zoom under it, default 2.30) and `power`
    # (the switch-on/off timings) - or None for no look.  The asset is a
    # REFERENCE, not artwork: the file lives with the project, never in
    # the engine (§14).  Shape and readers: library/tools/tv_frame.py.
    tv_frame: Optional[Dict[str, Any]] = None

#: The caption case a project that declares none renders in. The one
#: creative value that survives an absent brand template, and the reason
#: every caption card on project 001 is lowercase. PARKED: which case
#: the copy is set in is the captain's open decision. Single owner -
#: `video_prefs` and `step_4_01_plan_subtitles` read this rather than
#: restating it, so there is nothing left to drift.
DEFAULT_CAPTION_CASE = "lowercase"


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
    # "lowercase" | "as_written".  See DEFAULT_CAPTION_CASE above and
    # ABSENT_SLOT_READINGS in library/tools/brand_registry.py.
    caption_case: str = DEFAULT_CAPTION_CASE
    # Rung 7 (finding 31): words per caption card. None is "declares
    # nothing" and reads as the grouping default (6); a series that
    # wants short cards ("2 words max") declares the number, and the
    # per-request form will ride the same slot when rung 6 routes it.
    caption_words_per_card: Optional[int] = None
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
    # The lines at the bottom of the two-line closing animation
    # (captain, 2026-09-21). A template declares the copy this bookend
    # sets - `lines` plus the `color` they are set in - or omits the
    # key and gets the logo-only animation. Deliberately NOT part of
    # `bookends`: those slots become spine blocks, and this is not a
    # card the spine places - it is type inside an asset
    # `library/tools/logo_bulb.py` renders. Shape and readers: there.
    closing_lockup: Optional[Dict[str, Any]] = None
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
        # `series_look` was called `house_look` until 2026-09-10 (the
        # captain's ruling; `library/tools/series_look.py` carries it).
        # A template written before the rename is READ, never rewritten:
        # dropping it would silently ungrade a series, and refusing it
        # would break a file nobody had a reason to touch.
        style_data = dict(data.get("style", {}) or {})
        legacy = style_data.pop("house_look", None)
        if legacy is not None and style_data.get("series_look") is None:
            style_data["series_look"] = legacy
        style = StyleSlots(**style_data)
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
                        "series_look": {
                            "type": ["object", "null"],
                            "description": "The look this series declares, as values. Omit for no look; there is no engine default. Shape: library/tools/series_look.describe_declaration_shape().",
                        },
                        "reference_look_image": {"type": "string"},

                        "typography": {"type": "object"},
                        "energy_profile": {"type": "string", "enum": ["", "calm", "moderate", "high"]},
                        "framing_intent": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1.0,
                            "description": "Default clip framing: 0.0=full letterbox, 1.0=complete fill. Omit for auto."
                        },
                        "tv_frame": {
                            "type": ["object", "null"],
                            "description": "The punched-in TV-frame look. Omit for no look. Shape: {asset, punch_in, power} - see library/tools/tv_frame.py."
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
                        "caption_case": {"type": "string", "enum": ["lowercase", "as_written"], "default": "lowercase"},
                        "caption_words_per_card": {"type": "integer", "minimum": 1, "description": "Words per caption card (rung 7, finding 31). Omit for the grouping default of 6. See library/steps/step_4_01_plan_subtitles/step.py."}
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
                        "closing_lockup": {
                            "type": "object",
                            "description": "The lines at the bottom of the two-line closing animation. Omit for the logo-only animation. See library/tools/logo_bulb.py.",
                            "properties": {
                                "lines": {"type": "array", "items": {"type": "string"}},
                                "color": {"type": "string"},
                                "motion": {
                                    "type": "object",
                                    "description": "How the lines arrive and leave. Omit for the static version. See library/tools/logo_bulb.ClosingTextMotion.",
                                    "properties": {
                                        "style": {"type": "string"},
                                        "line1_in": {"type": "array", "items": {"type": "integer"}},
                                        "line2_in": {"type": "array", "items": {"type": "integer"}},
                                        "lines_out": {"type": "array", "items": {"type": "integer"}},
                                        "rise_px": {"type": "integer"},
                                        "exit_px": {"type": "integer"}
                                    }
                                }
                            }
                        },
                        "watermark": {"type": "object"},
                        "music_genre": {"type": "array", "items": {"type": "string"}},
                        "target_duration_seconds": {"type": "object"}
                    }
                }
            },
            "required": ["series_id"]
        }
