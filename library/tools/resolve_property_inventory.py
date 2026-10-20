"""Every Resolve property an edit can touch, and what a read can prove.

Three questions, answered once, in one enumeration, so that no consumer
has to answer them again:

1. **Which properties exist.** ``PROPERTIES`` names every Resolve property
   an edit can modify directly or indirectly, the API surface that reads
   it, the operations that can change it, and how well a read can prove
   it.  This is the inventory PRES-001 asks for: an inventory of
   PROPERTIES, not of operations - ``edit_algebra`` already enumerates the
   operations, and ``docs/RESOLVE_WRITE_INVENTORY.md`` already renders
   them.
2. **How well it can be read.** ``support`` is one of three classes, and
   the third one is the point of the whole module:

   - ``readable`` - a read returns the property and the read can be
     trusted.
   - ``partially_readable`` - a read returns SOME of the property.  The
     read does not claim the rest.  Pan and Tilt are the standing
     example: they are scaled by the CURRENT timeline's dimensions
     (``reel_read.assert_timeline_current``), so a read off a
     non-current handle is a measurement of the wrong number.
   - ``unreadable`` - Resolve's scripting API exposes no getter, so the
     property cannot be observed at all.  Transitions and an item's
     effect list are the standing examples: ``TimelineItem.AddTransition``
     exists and there is no transition getter.

   An unreadable property is a fact about Resolve, and it is recorded as
   one rather than as an empty value (PRES-004).
3. **What a missing value means.** ``measure`` never collapses "Resolve
   did not say" into "the value is zero".  A property that reads as its
   confirmed default is a DIFFERENT claim from a property that could not
   be read, and a preservation check that treats them as the same thing
   will restore a default over an approved value it never saw (PRES-005).

The surface
-----------
``clip_surface`` projects one clip read into the per-property surface:
``{property_id: {"status", "value", "reason"}}`` where ``status`` is
``measured``, ``default`` or ``unavailable``.  ``timeline_surface`` does
the same for a whole timeline and reports what could not be read at all.

The surface is DERIVED from a read, never a second read.  It exists so
that "Resolve did not say" travels with the state instead of being
flattened into it by the first consumer that wants a number.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

# ── Support classes (PRES-004) ───────────────────────────────────────
#
# How well a READ can prove a property.  Not how well a write can change
# it: an unreadable property can still be written (Resolve's UI can set
# an effect), and "we cannot see it" is a statement about the read.

READABLE = "readable"
PARTIALLY_READABLE = "partially_readable"
UNREADABLE = "unreadable"

SUPPORT_CLASSES = (READABLE, PARTIALLY_READABLE, UNREADABLE)

# ── Measurement statuses (PRES-005) ──────────────────────────────────
#
# What ONE reading of one property establishes.

MEASURED = "measured"
DEFAULT = "default"
UNAVAILABLE = "unavailable"

STATUSES = (MEASURED, DEFAULT, UNAVAILABLE)

#: How far two numeric readings may differ and still be one value.  The
#: same tolerance the replace guard uses for a preserved value
#: (``reel_replace_guard.VALUE_TOLERANCE``).
VALUE_TOLERANCE = 1e-6


@dataclass(frozen=True)
class Property:
    """One Resolve property, and everything a reader needs to judge it.

    ``default`` is the property's CONFIRMED Resolve default, or ``None``
    where no default is established.  ``None`` never means "the default
    is None": it means no default is claimed, so a reading equal to it
    is a measurement and not a confirmed default.
    """

    property_id: str
    group: str
    surface: str
    support: str
    default: Any = None
    modified_by: tuple[str, ...] = ()
    note: str = ""


# ── The groups, and the operations that can change them ───────────────
#
# Every operation in `edit_algebra.ALGEBRA` appears in at least one
# group.  `tests/unit/resolve/test_resolve_property_inventory.py` fails
# when a new operation is added without a group that says what it can
# change: an operation nobody inventoried is an operation whose state
# coverage nobody promised.

_MUTATIONS = (
    # identity, placement and source ranges move together: a Resolve
    # item is re-identified whenever its placement moves.
    "clip.insert", "clip.replace", "clip.trim", "clip.split", "clip.join",
    "clip.roll", "clip.slip", "clip.slide", "clip.move", "clip.ripple",
    "clip.delete", "clip.remove", "compound.create", "compound.flatten",
)

_PLACEMENT = _MUTATIONS + (
    "track.add", "track.delete", "track.reorder", "broll.insert",
    "drop_fragment", "reel.opening", "plan_change", "shot_selection",
    "speech_selection", "story_pacing", "redraw_closer", "caption_fix",
    "caption.style",
)

_GROUP_OPS: dict[str, tuple[str, ...]] = {
    "identity": _MUTATIONS,
    "placement": _PLACEMENT,
    "source": (
        "clip.trim", "clip.split", "clip.join", "clip.roll", "clip.slip",
        "clip.replace", "clip.insert", "retime", "retime.segments",
        "span_retime",
    ),
    "track": (
        "track.add", "track.delete", "track.rename", "track.reorder",
        "clip.move", "clip.insert", "clip.remove", "clip.delete",
    ),
    "transform": (
        "clip.set_property", "clip.smart_reframe", "transform_override",
        "picture.keyframe", "clip.insert", "clip.replace", "angle_plan",
        "multicam.angle", "multicam.select_angle",
    ),
    "crop": (
        "clip.set_property", "clip.smart_reframe", "transform_override",
        "clip.insert", "clip.replace",
    ),
    "composite": ("clip.set_property", "clip.insert", "clip.replace"),
    "retime": (
        "retime", "retime.segments", "span_retime", "clip.set_property",
        "clip.insert", "clip.replace",
    ),
    "enablement": ("clip.set_enabled", "clip.insert", "clip.replace"),
    "markers": (
        "marker.add", "marker.delete", "clip_marker.add", "clip_marker.delete",
        "clip.insert", "clip.replace", "clip.delete", "clip.remove",
    ),
    "linked": (
        "clip.link", "clip.unlink", "clip.insert", "clip.replace",
        "clip.delete",
    ),
    "fades": ("audio.fade", "audio_mix", "clip.insert", "clip.replace"),
    "keyframes": (
        "picture.keyframe", "clip.smart_reframe", "transform_override",
    ),
    "transitions": ("transition", "clip.insert", "clip.replace"),
    "effects": ("look", "visual_effect", "clip.set_property"),
    "color": (
        "clip_lut", "grade", "grade.node_add", "grade.node_remove",
        "grade.node_reorder", "grade.node_set", "color.window.create",
        "color.window.delete", "color.window.modify", "color.window.track",
        "color.qualifier.sample", "color.qualifier.adjust", "look",
        "clip.insert", "clip.replace",
    ),
    "fusion": (
        "mask.create", "mask.delete", "mask.edit", "mask.track",
        "compound.create", "compound.flatten", "hold.repair_window",
        "visual_effect", "subject_effect", "motion_graphic", "brand_asset",
        "end_card", "overlay.add", "overlay.remove", "overlay.replace",
        "overlay.resize", "overlay.position", "overlay.entry_motion",
        "overlay.swap_pixels", "clip.insert", "clip.replace",
    ),
    "audio": (
        "audio.gain", "audio.pan", "audio.automation", "audio.fade",
        "voice_isolation", "audio_mix", "music_selection", "sound_effect",
        "clip.insert", "clip.replace",
    ),
    "timeline": _PLACEMENT,
}

GROUPS = tuple(_GROUP_OPS)


def _ops(group: str) -> tuple[str, ...]:
    return _GROUP_OPS[group]


# ── The enumeration ──────────────────────────────────────────────────
#
# `surface` names the Resolve API that reads the property.  `support`
# says how far that read can be trusted.  `default` is the CONFIRMED
# Resolve default, taken from the values the repository already writes
# as a default - `timeline_serializer.serialize_timeline_state` and
# `edit_state_preservation.PICTURE_PROPERTIES` - so the inventory and
# the code that restores state cannot disagree about what "default"
# means.

PROPERTIES: tuple[Property, ...] = (
    # ── identity ──
    Property("clip.native_id", "identity", "TimelineItem.GetUniqueId()",
             READABLE, modified_by=_ops("identity"),
             note="Resolve's own object id. It is NOT an editorial "
                  "identity: a re-placed clip gets a new one."),
    Property("clip.name", "identity", "TimelineItem.GetName()", READABLE,
             modified_by=_ops("identity")),
    Property("clip.media_pool_item_id", "identity",
             "MediaPoolItem.GetMediaId()", READABLE,
             modified_by=_ops("identity"),
             note="The media's own id. Stable across re-places; this is "
                  "what the source identity is built on."),
    Property("clip.source_file", "identity",
             "MediaPoolItem.GetClipProperty('File Path')", READABLE,
             modified_by=_ops("identity")),
    Property("clip.source_frames", "identity",
             "MediaPoolItem.GetClipProperty('Frames')", READABLE,
             modified_by=_ops("identity")),

    # ── placement (timeline range) ──
    Property("clip.record_in", "placement", "TimelineItem.GetStart()",
             READABLE, modified_by=_ops("placement")),
    Property("clip.record_out", "placement", "TimelineItem.GetEnd()",
             READABLE, modified_by=_ops("placement")),
    Property("clip.duration", "placement", "TimelineItem.GetDuration()",
             READABLE, modified_by=_ops("placement")),

    # ── source range ──
    Property("clip.source_in_frame", "source",
             "TimelineItem.GetSourceStartFrame()", READABLE,
             modified_by=_ops("source")),
    Property("clip.source_out_frame", "source",
             "TimelineItem.GetSourceEndFrame()", READABLE,
             modified_by=_ops("source")),
    Property("clip.left_offset", "source", "TimelineItem.GetLeftOffset()",
             READABLE, modified_by=_ops("source")),
    Property("clip.right_offset", "source", "TimelineItem.GetRightOffset()",
             READABLE, modified_by=_ops("source")),

    # ── track ──
    Property("clip.track_type", "track",
             "TimelineItem.GetTrackTypeAndIndex()", READABLE,
             modified_by=_ops("track")),
    Property("clip.track_index", "track",
             "TimelineItem.GetTrackTypeAndIndex()", READABLE,
             modified_by=_ops("track")),
    Property("clip.track_name", "track", "Timeline.GetTrackName()",
             READABLE, modified_by=_ops("track")),

    # ── transform ──
    Property("transform.Pan", "transform", "TimelineItem.GetProperty()",
             PARTIALLY_READABLE, 0.0, _ops("transform"),
             "Scaled by the CURRENT timeline's width over this timeline's "
             "width. Refuse the read off a non-current handle "
             "(`reel_read.assert_timeline_current`)."),
    Property("transform.Tilt", "transform", "TimelineItem.GetProperty()",
             PARTIALLY_READABLE, 0.0, _ops("transform"),
             "Scaled by the CURRENT timeline's height over this "
             "timeline's height."),
    Property("transform.ZoomX", "transform", "TimelineItem.GetProperty()",
             READABLE, 1.0, _ops("transform")),
    Property("transform.ZoomY", "transform", "TimelineItem.GetProperty()",
             READABLE, 1.0, _ops("transform")),
    Property("transform.ZoomGang", "transform", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("transform")),
    Property("transform.RotationAngle", "transform",
             "TimelineItem.GetProperty()", READABLE, 0.0, _ops("transform")),
    Property("transform.AnchorPointX", "transform",
             "TimelineItem.GetProperty()", READABLE, 0.0, _ops("transform")),
    Property("transform.AnchorPointY", "transform",
             "TimelineItem.GetProperty()", READABLE, 0.0, _ops("transform")),
    Property("transform.Pitch", "transform", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("transform")),
    Property("transform.Yaw", "transform", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("transform")),
    Property("transform.FlipX", "transform", "TimelineItem.GetProperty()",
             READABLE, False, _ops("transform")),
    Property("transform.FlipY", "transform", "TimelineItem.GetProperty()",
             READABLE, False, _ops("transform")),
    Property("transform.Scaling", "transform", "TimelineItem.GetProperty()",
             READABLE, None, _ops("transform"),
             "The scaling MODE, not a magnification. No default is "
             "established: the tight-box geometry assumes Crop, so an "
             "unset mode is a decision, not a zero."),
    Property("transform.CropRetain", "transform",
             "TimelineItem.GetProperty()", READABLE, None,
             _ops("transform")),

    # ── crop ──
    Property("crop.CropLeft", "crop", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("crop")),
    Property("crop.CropRight", "crop", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("crop")),
    Property("crop.CropTop", "crop", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("crop")),
    Property("crop.CropBottom", "crop", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("crop")),
    Property("crop.CropSoftness", "crop", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("crop")),

    # ── composite ──
    Property("composite.Opacity", "composite", "TimelineItem.GetProperty()",
             READABLE, 100.0, _ops("composite")),
    Property("composite.CompositeMode", "composite",
             "TimelineItem.GetProperty()", READABLE, "Normal",
             _ops("composite")),

    # ── retime ──
    Property("retime.speed_percent", "retime", "TimelineItem.GetSpeed()",
             READABLE, 100.0, _ops("retime"),
             "Resolve reports a percentage; 100.0 is normal speed."),
    Property("retime.RetimeProcess", "retime",
             "TimelineItem.GetProperty()", READABLE, None, _ops("retime")),
    Property("retime.MotionEstimation", "retime",
             "TimelineItem.GetProperty()", READABLE, None, _ops("retime")),

    # ── enablement ──
    Property("enablement.enabled", "enablement",
             "TimelineItem.GetClipEnabled()", READABLE, True,
             _ops("enablement"),
             "A failed read is NOT `True`: a rebuild that assumes the "
             "default restores a state nobody approved."),

    # ── markers ──
    Property("markers.clip", "markers", "TimelineItem.GetMarkers()",
             READABLE, modified_by=_ops("markers")),
    Property("markers.timeline", "markers", "Timeline.GetMarkers()",
             READABLE, modified_by=_ops("markers")),
    Property("markers.media_pool", "markers", "MediaPoolItem.GetMarkers()",
             READABLE, modified_by=_ops("markers")),

    # ── linked ──
    Property("linked.item_ids", "linked", "TimelineItem.GetLinkedItems()",
             READABLE, modified_by=_ops("linked"),
             note="The link GROUP's members. The linked partner's own "
                  "state is read on the partner, not here."),
    Property("linked.audio_state", "linked",
             "TimelineItem.GetLinkedItems()", PARTIALLY_READABLE, None,
             _ops("linked"),
             note="A linked audio item's own gain, fades and enablement. "
                  "Readable item by item; there is no link-group getter "
                  "that reports the group's routing."),

    # ── fades ──
    Property("fades.item_in", "fades", "TimelineItem.GetFades()",
             PARTIALLY_READABLE, 0, _ops("fades"),
             "The documented API is Resolve 21.1's `GetFades`. Where the "
             "method is absent the fade state is UNAVAILABLE, not zero. "
             "On an AUDIO item this is the audio fade: measured, "
             "`reel_touchup._audio_fades` reads it there."),
    Property("fades.item_out", "fades", "TimelineItem.GetFades()",
             PARTIALLY_READABLE, 0, _ops("fades")),
    Property("fades.audio_in", "fades", "TimelineItem.GetFades()",
             PARTIALLY_READABLE, 0, _ops("fades"),
             "The audio fade of a VIDEO item, under its own key."),
    Property("fades.audio_out", "fades", "TimelineItem.GetFades()",
             PARTIALLY_READABLE, 0, _ops("fades")),

    # ── keyframes ──
    Property("keyframes.Pan", "keyframes", "TimelineItem.GetKeyframes()",
             PARTIALLY_READABLE, None, _ops("keyframes"),
             "Only Pan, Tilt, ZoomX and ZoomY have a keyframe getter."),
    Property("keyframes.Tilt", "keyframes", "TimelineItem.GetKeyframes()",
             PARTIALLY_READABLE, None, _ops("keyframes")),
    Property("keyframes.ZoomX", "keyframes", "TimelineItem.GetKeyframes()",
             PARTIALLY_READABLE, None, _ops("keyframes")),
    Property("keyframes.ZoomY", "keyframes", "TimelineItem.GetKeyframes()",
             PARTIALLY_READABLE, None, _ops("keyframes")),

    # ── transitions ──
    Property("transitions.in", "transitions",
             "TimelineItem.AddTransition()", UNREADABLE, None,
             _ops("transitions"),
             "Resolve's scripting API can PLACE a transition and returns "
             "the transition item; it exposes no getter that reports the "
             "transition on a clip. A clip's transition state cannot be "
             "observed, so it cannot be proven preserved."),
    Property("transitions.out", "transitions",
             "TimelineItem.AddTransition()", UNREADABLE, None,
             _ops("transitions")),

    # ── effects ──
    Property("effects.list", "effects", "no getter", UNREADABLE, None,
             _ops("effects"),
             "Resolve's scripting API exposes no effect list on a "
             "timeline item. An item's ResolveFX and OFX effects are "
             "invisible to a read."),
    Property("effects.lut", "effects", "TimelineItem.GetLUT(node)",
             PARTIALLY_READABLE, None, _ops("effects"),
             "The assigned LUT path for one node. The node's other "
             "parameters are not readable."),

    # ── color ──
    Property("color.cdl", "color", "TimelineItem.GetCDL()", READABLE,
             modified_by=_ops("color"),
             note="The CDL summary. It is not the grade graph."),
    Property("color.group", "color", "TimelineItem.GetColorGroup()",
             READABLE, modified_by=_ops("color")),
    Property("color.node_count", "color",
             "TimelineItem.GetNodeGraph().GetNumNodes()", READABLE,
             modified_by=_ops("color")),
    Property("color.node_luts", "color",
             "TimelineItem.GetNodeGraph().GetLUT(node)",
             PARTIALLY_READABLE, None, _ops("color"),
             "The assigned LUT paths. Node parameters, curves and "
             "qualifiers are not readable through this surface."),

    # ── fusion ──
    Property("fusion.comp_count", "fusion",
             "TimelineItem.GetFusionCompCount()", READABLE,
             modified_by=_ops("fusion")),
    Property("fusion.comp_names", "fusion",
             "TimelineItem.GetFusionCompNameList()", READABLE,
             modified_by=_ops("fusion")),
    Property("fusion.media_windows", "fusion",
             "FusionComp.GetInput('MediaIn')", PARTIALLY_READABLE, None,
             _ops("fusion"),
             "Where each comp's MediaIn reads from. A comp's tools and "
             "animation are not fully captured."),
    Property("fusion.mattes", "fusion", "FusionComp.GetToolList()",
             PARTIALLY_READABLE, None, _ops("fusion"),
             note="Each matte comp's geometry and keyframes, read only for "
                  "comps whose name marks them as mattes. A comp's other "
                  "tools are not captured."),

    # ── audio ──
    Property("audio.volume", "audio", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("audio"),
             "Resolve's decibel gain, so the default is silence."),
    Property("audio.pan", "audio", "TimelineItem.GetProperty()",
             READABLE, 0.0, _ops("audio")),
    Property("audio.effects", "audio", "no getter", UNREADABLE, None,
             _ops("audio"),
             "An audio item's effects, EQ and dynamics are not exposed "
             "to a read."),
    Property("audio.automation", "audio", "no getter", UNREADABLE, None,
             _ops("audio"),
             "Fairlight automation curves are not exposed to a read."),

    # ── timeline ──
    Property("timeline.start_frame", "timeline", "Timeline.GetStartFrame()",
             READABLE, modified_by=_ops("timeline")),
    Property("timeline.end_frame", "timeline", "Timeline.GetEndFrame()",
             READABLE, modified_by=_ops("timeline")),
    Property("timeline.frame_rate", "timeline",
             "Timeline.GetSetting('timelineFrameRate')", READABLE,
             modified_by=_ops("timeline")),
    Property("timeline.width", "timeline",
             "Timeline.GetSetting('timelineResolutionWidth')", READABLE,
             modified_by=_ops("timeline")),
    Property("timeline.height", "timeline",
             "Timeline.GetSetting('timelineResolutionHeight')", READABLE,
             modified_by=_ops("timeline")),
)

PROPERTY_BY_ID: dict[str, Property] = {
    prop.property_id: prop for prop in PROPERTIES
}

#: Properties a read can never establish.  A preservation claim that
#: covers one of these is a claim about a value nobody could read.
UNREADABLE_PROPERTIES: tuple[str, ...] = tuple(
    prop.property_id for prop in PROPERTIES if prop.support == UNREADABLE
)

#: Properties a read can only partly establish.
PARTIALLY_READABLE_PROPERTIES: tuple[str, ...] = tuple(
    prop.property_id for prop in PROPERTIES
    if prop.support == PARTIALLY_READABLE
)

#: Properties that describe the TIMELINE, not one clip.  They are read
#: once per timeline, never once per clip.
TIMELINE_PROPERTIES: tuple[str, ...] = tuple(
    prop.property_id for prop in PROPERTIES if prop.group == "timeline"
)


def property_rows() -> list[dict[str, Any]]:
    """The inventory as plain data, for a document or a CLI answer."""
    return [
        {
            "property_id": prop.property_id,
            "group": prop.group,
            "surface": prop.surface,
            "support": prop.support,
            "default": prop.default,
            "modified_by": list(prop.modified_by),
            "note": prop.note,
        }
        for prop in PROPERTIES
    ]


def operations() -> tuple[str, ...]:
    """Every operation that can change some property, in one list."""
    seen: set[str] = set()
    for prop in PROPERTIES:
        seen.update(prop.modified_by)
    return tuple(sorted(seen))


# ── The document ─────────────────────────────────────────────────────
#
# The inventory is only useful if a reader can check it, so the document
# is GENERATED from the enumeration and a test fails when the two
# disagree.  A hand-maintained table beside the code would drift, and a
# drifted inventory is worse than none: it would promise coverage that
# nobody checks.

DOC_PATH = "docs/RESOLVE_PROPERTY_INVENTORY.md"
DOC_START = "<!-- BEGIN GENERATED RESOLVE PROPERTY INVENTORY -->"
DOC_END = "<!-- END GENERATED RESOLVE PROPERTY INVENTORY -->"


def render_inventory() -> str:
    """The inventory as a markdown document body (no section markers)."""
    lines = [
        "## Resolve property inventory (generated)",
        "",
        ("Generated from `library/tools/resolve_property_inventory.py` by "
         "`python3 -m library.tools.resolve_property_inventory --write`. "
         "`readable` means a read returns the property and can be trusted; "
         "`partially_readable` means a read returns SOME of it and claims "
         "no more; `unreadable` means Resolve's scripting API exposes no "
         "getter, so the property cannot be observed at all."),
        "",
        ("An unreadable property is a fact about Resolve. A preservation "
         "claim that covers one is a claim about a value nobody could "
         "read."),
        "",
        ("| property | group | read by | support | confirmed default | "
         "modified by |"),
        "|---|---|---|---|---|---|",
    ]
    for prop in PROPERTIES:
        default = ("none established" if prop.default is None
                   else f"`{prop.default!r}`")
        modified = ", ".join(f"`{op}`" for op in prop.modified_by) or "none"
        lines.append(
            f"| `{prop.property_id}` | {prop.group} | `{prop.surface}` | "
            f"{prop.support} | {default} | {modified} |")
    lines.append("")
    lines.append("### The properties Resolve cannot report")
    lines.append("")
    for prop in PROPERTIES:
        if prop.support != UNREADABLE:
            continue
        lines.append(f"- **`{prop.property_id}`** - {prop.note}")
    lines.append("")
    lines.append("### The properties a read only partly establishes")
    lines.append("")
    for prop in PROPERTIES:
        if prop.support != PARTIALLY_READABLE:
            continue
        lines.append(f"- **`{prop.property_id}`** - {prop.note}")
    return "\n".join(lines)


def write_inventory(path=None) -> None:
    """Rewrite the document's generated section in place."""
    import argparse
    from pathlib import Path

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true",
                        help="update the inventory document")
    parser.add_argument("--path", default=DOC_PATH)
    args = parser.parse_args()
    target = Path(args.path)
    if not args.write:
        print(render_inventory())
        return
    text = target.read_text(encoding="utf-8")
    generated = render_inventory()
    if DOC_START in text and DOC_END in text:
        before, rest = text.split(DOC_START, 1)
        _old, after = rest.split(DOC_END, 1)
        text = f"{before}{DOC_START}\n{generated}\n{DOC_END}{after}"
    else:
        text = (text.rstrip() + f"\n\n{DOC_START}\n{generated}\n{DOC_END}\n")
    target.write_text(text, encoding="utf-8")
    print(f"wrote {target}")


# ── The measurement vocabulary (PRES-005) ────────────────────────────


def _same(left: Any, right: Any) -> bool:
    """One value, read twice."""
    if (isinstance(left, (int, float)) and not isinstance(left, bool)
            and isinstance(right, (int, float))
            and not isinstance(right, bool)):
        return abs(float(left) - float(right)) <= VALUE_TOLERANCE
    return left == right


def unavailable(reason: str) -> dict[str, Any]:
    """A property that could not be read, with the reason it could not."""
    return {"status": UNAVAILABLE, "value": None, "reason": str(reason)}


def measure(value: Any, *, default: Any = None,
            reason: str | None = None) -> dict[str, Any]:
    """One reading of one property, with what it establishes.

    ``None`` means the read did not establish the property, and the
    result says so.  A value equal to the CONFIRMED default reads as
    ``default``, which is a different claim from ``measured``: a default
    is a value Resolve itself chose, and a measurement is a value
    somebody set.
    """
    if value is None:
        return unavailable(reason or "Resolve did not report this property")
    if default is not None and _same(value, default):
        return {"status": DEFAULT, "value": value, "reason": None}
    return {"status": MEASURED, "value": value, "reason": None}


def is_available(reading: Mapping[str, Any]) -> bool:
    """Whether a reading established anything at all."""
    return reading.get("status") in (MEASURED, DEFAULT)


# ── The surface ──────────────────────────────────────────────────────
#
# Projected from a read, never a second read.

#: The keys Resolve reports through `GetProperty()`, by the group that
#: owns them.  The value is the key in the property dictionary.
_PROPERTY_KEYS: dict[str, tuple[tuple[str, str], ...]] = {
    "transform": (
        ("transform.Pan", "Pan"), ("transform.Tilt", "Tilt"),
        ("transform.ZoomX", "ZoomX"), ("transform.ZoomY", "ZoomY"),
        ("transform.ZoomGang", "ZoomGang"),
        ("transform.RotationAngle", "RotationAngle"),
        ("transform.AnchorPointX", "AnchorPointX"),
        ("transform.AnchorPointY", "AnchorPointY"),
        ("transform.Pitch", "Pitch"), ("transform.Yaw", "Yaw"),
        ("transform.FlipX", "FlipX"), ("transform.FlipY", "FlipY"),
        ("transform.Scaling", "Scaling"),
        ("transform.CropRetain", "CropRetain"),
    ),
    "crop": (
        ("crop.CropLeft", "CropLeft"), ("crop.CropRight", "CropRight"),
        ("crop.CropTop", "CropTop"), ("crop.CropBottom", "CropBottom"),
        ("crop.CropSoftness", "CropSoftness"),
    ),
    "composite": (
        ("composite.Opacity", "Opacity"),
        ("composite.CompositeMode", "CompositeMode"),
    ),
    "retime": (
        ("retime.RetimeProcess", "RetimeProcess"),
        ("retime.MotionEstimation", "MotionEstimation"),
    ),
    "audio": (
        ("audio.volume", "Volume"), ("audio.pan", "Pan"),
    ),
}

#: Why a property cannot be read, where the reason is a fact about
#: Resolve rather than about this read.
_UNREADABLE_REASONS = {
    "transitions.in": (
        "Resolve's scripting API exposes no getter for a clip's "
        "transition"),
    "transitions.out": (
        "Resolve's scripting API exposes no getter for a clip's "
        "transition"),
    "effects.list": (
        "Resolve's scripting API exposes no effect list on a timeline "
        "item"),
    "audio.effects": (
        "Resolve's scripting API exposes no effect list on an audio item"),
    "audio.automation": (
        "Resolve's scripting API exposes no automation curve on an audio "
        "item"),
}

#: The clip read's own keys, by property.
_CLIP_KEYS = {
    "clip.native_id": "unique_id",
    "clip.name": "name",
    "clip.media_pool_item_id": "media_pool_item_id",
    "clip.source_file": "source_file",
    "clip.source_frames": "source_frames",
    "clip.record_in": "record_in",
    "clip.record_out": "record_out",
    "clip.duration": "duration",
    "clip.source_in_frame": "source_in_frame",
    "clip.source_out_frame": "source_out_frame",
    "clip.left_offset": "left_offset",
    "clip.right_offset": "right_offset",
    "clip.track_type": "track_type",
    "clip.track_index": "track_index",
    "clip.track_name": "track_name",
    "retime.speed_percent": "speed",
    "enablement.enabled": "enabled",
    "markers.clip": "markers",
    "linked.item_ids": "linked_unique_ids",
    "linked.audio_state": "linked_clips",
}

#: The fade keys `GetFades` reports, by property.
_FADE_KEYS = {
    "fades.item_in": "FadeIn",
    "fades.item_out": "FadeOut",
    "fades.audio_in": "A_FadeIn",
    "fades.audio_out": "A_FadeOut",
}

#: The color keys the clip read carries, by property.
_COLOR_KEYS = {
    "color.cdl": "cdl",
    "color.group": "color_group",
    "color.node_count": "node_count",
    "color.node_luts": "luts",
}

#: The fusion keys the clip read carries, by property.
_FUSION_KEYS = {
    "fusion.comp_count": "comp_count",
    "fusion.comp_names": "comp_names",
    "fusion.media_windows": "media_windows",
    "fusion.mattes": "mattes",
}


def _property_reading(clip: Mapping[str, Any], prop: Property,
                      transform: Mapping[str, Any]) -> dict[str, Any]:
    """One property's reading, from the clip read."""
    if prop.support == UNREADABLE:
        return unavailable(_UNREADABLE_REASONS.get(
            prop.property_id,
            f"Resolve's scripting API exposes no getter for "
            f"{prop.property_id}"))
    for group, pairs in _PROPERTY_KEYS.items():
        if prop.group != group:
            continue
        for property_id, key in pairs:
            if property_id != prop.property_id:
                continue
            if not transform:
                return unavailable(
                    "Resolve did not report this item's properties")
            return measure(transform.get(key), default=prop.default)
    if prop.property_id in _CLIP_KEYS:
        return measure(clip.get(_CLIP_KEYS[prop.property_id]),
                       default=prop.default)
    if prop.property_id in _FADE_KEYS:
        fades = clip.get("fades")
        if not isinstance(fades, Mapping):
            return unavailable(
                "Resolve does not expose TimelineItem.GetFades on this "
                "item")
        key = _FADE_KEYS[prop.property_id]
        if key not in fades:
            return unavailable(f"GetFades reported no {key} on this item")
        return measure(fades.get(key), default=prop.default)
    if prop.property_id.startswith("keyframes."):
        keyframes = clip.get("keyframes")
        if not isinstance(keyframes, Mapping):
            return unavailable("this item reports no keyframe surface")
        return measure(keyframes.get(prop.property_id.split(".", 1)[1]),
                       default=prop.default)
    if prop.property_id == "effects.lut":
        return unavailable(
            "the assigned LUT is read per node, not as an item property")
    if prop.property_id in _COLOR_KEYS:
        color = clip.get("color")
        if not isinstance(color, Mapping):
            return unavailable("this item reports no color surface")
        return measure(color.get(_COLOR_KEYS[prop.property_id]),
                       default=prop.default)
    if prop.property_id in _FUSION_KEYS:
        fusion = clip.get("fusion")
        if not isinstance(fusion, Mapping):
            return unavailable("this item reports no Fusion surface")
        return measure(fusion.get(_FUSION_KEYS[prop.property_id]),
                       default=prop.default)
    return unavailable(f"{prop.property_id} has no declared reading")


def clip_surface(clip: Mapping[str, Any]) -> dict[str, Any]:
    """One clip read, projected into its per-property surface.

    Every CLIP property in the inventory appears, whether it was read or
    not.  A property that is absent from the surface is a property
    nobody promised, which is a different thing from a property that
    could not be read.
    """
    transform = clip.get("transform")
    transform = transform if isinstance(transform, Mapping) else {}
    return {
        prop.property_id: _property_reading(clip, prop, transform)
        for prop in PROPERTIES
        if prop.group != "timeline"
    }


#: The snapshot keys the timeline's own properties are read from.
_TIMELINE_KEYS = {
    "timeline.start_frame": "start_frame",
    "timeline.end_frame": "end_frame",
    "timeline.frame_rate": "fps",
    "timeline.width": "width",
    "timeline.height": "height",
}


def timeline_readings(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """The TIMELINE's own properties, read once for the whole timeline."""
    readings: dict[str, Any] = {}
    for prop in PROPERTIES:
        if prop.group != "timeline":
            continue
        key = _TIMELINE_KEYS.get(prop.property_id)
        readings[prop.property_id] = measure(
            snapshot.get(key) if key else None, default=prop.default)
    markers = snapshot.get("markers")
    markers = markers if isinstance(markers, Mapping) else {}
    readings["markers.timeline"] = measure(markers.get("timeline"),
                                            default=None)
    return readings


def timeline_surface(snapshot: Mapping[str, Any]) -> dict[str, Any]:
    """A whole timeline read, projected into its per-property surface.

    Returns one surface per clip, the timeline's own readings, and the
    counts that say what the read established and what it did not.
    """
    clips = []
    for track in snapshot.get("tracks", []) or []:
        for clip in track.get("clips", []) or []:
            clips.append({
                "unique_id": str(clip.get("unique_id") or ""),
                "name": str(clip.get("name") or ""),
                "track_type": str(clip.get("track_type") or ""),
                "track_index": clip.get("track_index"),
                "surface": clip_surface(clip),
            })
    counts = {"measured": 0, "default": 0, "unavailable": 0}
    unavailable_properties: set[str] = set()
    for entry in clips:
        for property_id, reading in entry["surface"].items():
            status = reading.get("status")
            if status == UNAVAILABLE:
                counts["unavailable"] += 1
                unavailable_properties.add(property_id)
            elif status == DEFAULT:
                counts["default"] += 1
            else:
                counts["measured"] += 1
    timeline = timeline_readings(snapshot)
    for property_id, reading in timeline.items():
        if reading.get("status") == UNAVAILABLE:
            unavailable_properties.add(property_id)
    return {
        "clips": clips,
        "timeline": timeline,
        "counts": counts,
        "unavailable_properties": sorted(unavailable_properties),
    }


if __name__ == "__main__":
    raise SystemExit(write_inventory())
