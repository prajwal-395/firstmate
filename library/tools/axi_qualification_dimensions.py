"""The one enumeration of AXI qualification DIMENSIONS.

A qualification predicate answers one question: "in what STATE may this
operation run?" A dimension is a property of that state - the media kind,
the linked-audio arrangement, the track layout, how full the timeline
is, the project's frame rate and geometry, the Resolve version, and the
effects the target items already carry.

It is NOT a property of a project. A predicate that named the fixture
project said "this operation may run in the one place the evidence was
earned", which is not a qualification boundary at all: it is a pointer
to the test. Three of the six records shipped in
`library/tools/axi_qualification_records.json` carried such predicates,
and two of them named values the measurement could never produce - a
predicate set that can never match is a route that is closed while
reading as open.

So the vocabulary here is defined by the STATE, never by a project, a
timeline, or a fixture:

    media_type                  the kind of media the request touches
    clip_composition           what the request's item IS
    linked_audio_configuration  whether that item carries linked audio
    track_layout               which track types the timeline has
    track_occupancy            whether those tracks are empty or populated
    timeline_density           how much the timeline holds
    timeline_structure         empty, one item, or several
    project_settings           the project's geometry and orientation
    frame_rate                 the project's frame rate
    existing_effects           what the request's items already carry
    resolve_version            the Resolve that was measured

Every dimension has a CLOSED vocabulary, except `resolve_version` (any
Resolve string is a different environment). A predicate whose value is
outside a dimension's vocabulary is refused at import
(`library/tools/axi_qualification.py`): a claim the measurement cannot
produce is a claim that can never be satisfied, and the registry must
say so at the door rather than carry it quietly.

The measurement lives here too, and is pure: it takes plain rows and
plain settings and answers with vocabulary values. The live read is
`library/tools/axi_qualification_state.py`; this module decides what
that read MEANS.

`tests/unit/resolve/test_axi_qualification_dimensions.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import combinations

#: Every dimension a recorded qualification predicate may name.
DIMENSIONS: tuple[str, ...] = (
    "resolve_version",
    "media_type",
    "clip_composition",
    "linked_audio_configuration",
    "track_layout",
    "track_occupancy",
    "timeline_density",
    "timeline_structure",
    "project_settings",
    "frame_rate",
    "existing_effects",
)

#: A dimension is a property of a STATE. These name an IDENTITY - which
#: project, which timeline - and are therefore never a dimension: a
#: predicate that names one restricts an operation to the place the
#: evidence was earned, which is not a qualification boundary.
IDENTITY_FIELDS: frozenset[str] = frozenset({"project_name", "timeline_name"})

#: The media kinds a request may touch. `mixed` is a request that spans
#: kinds - the evidence was never earned on that - and `none` is a
#: request that touches no existing item (a row it adds).
MEDIA_TYPES: tuple[str, ...] = (
    "video", "audio", "still", "mixed", "none", "unknown")

#: What the request's item IS.
CLIP_COMPOSITIONS: tuple[str, ...] = (
    "single_video_clip",
    "linked_video_clip",
    "still_clip",
    "audio_clip",
    "linked_audio_clip",
    "unknown_item",
    "none",
    "mixed",
)

#: Whether the request's item carries LINKED AUDIO.
LINKED_AUDIO_CONFIGURATIONS: tuple[str, ...] = (
    "no_linked_audio", "linked_audio_present")

#: Which track types the timeline has. The rows a request can touch live
#: on a video or an audio row: Ren's caption layer is rendered cards on
#: a video row named "Subtitles", and a Resolve subtitle track holds
#: caption text rather than clips.
TRACK_LAYOUTS: tuple[str, ...] = (
    "none",
    "video",
    "audio",
    "video+audio",
)

#: Whether those tracks are empty or populated. A qualification run must
#: cover BOTH: an operation proven only on a populated track says
#: nothing about an empty one, and the other way round.
TRACK_OCCUPANCIES: tuple[str, ...] = (
    "all_empty", "mixed", "all_populated")

#: How much the timeline holds, in bands. The bands are counted in ITEMS
#: on every row - the same count the structure dimension reports.
DENSITY_BANDS: tuple[tuple[int, str], ...] = (
    (0, "empty"),
    (1, "light"),
    (5, "moderate"),
    (20, "dense"),
)

TIMELINE_DENSITIES: tuple[str, ...] = tuple(
    name for _count, name in DENSITY_BANDS)

#: The shape of the timeline: nothing on it, one item on it, or several.
TIMELINE_STRUCTURES: tuple[str, ...] = (
    "empty_timeline", "single_item_timeline", "multi_item_timeline")

#: The effects a clip may already carry. `linked audio` is not here: it
#: has its own dimension, because a linked audio twin is an ARRANGEMENT
#: rather than an effect on the item.
EFFECT_KINDS: tuple[str, ...] = (
    "transitions", "transforms", "grades", "retiming", "fusion")

#: The project geometry: orientation and resolution. The frame rate is
#: its own dimension, because a rate is chosen for a reason the geometry
#: does not carry.
PROJECT_SETTINGS: tuple[str, ...] = (
    "unknown",
    "square_1920x1920",
    "landscape_1280x720",
    "landscape_1920x1080",
    "landscape_2560x1440",
    "landscape_3840x2160",
    "portrait_1080x1920",
    "portrait_2160x3840",
)

#: The frame rates a project is commonly set to. A rate outside this set
#: is reported as `other`, which matches no recorded predicate - a
#: qualification run has to name the rate it actually used.
FRAME_RATES: tuple[str, ...] = (
    "other",
    "23.976",
    "24",
    "25",
    "29.97",
    "30",
    "48",
    "50",
    "59.94",
    "60",
)

#: The measured rate spelling for the NTSC fractional rates, which
#: Resolve reports as 23.976 and computes at 24000/1001.
_RATE_SPELLINGS: tuple[tuple[float, str], ...] = (
    (23.976, "23.976"),
    (24.0, "24"),
    (25.0, "25"),
    (29.97, "29.97"),
    (30.0, "30"),
    (48.0, "48"),
    (50.0, "50"),
    (59.94, "59.94"),
    (60.0, "60"),
)


def _effect_values() -> tuple[str, ...]:
    """Every `existing_effects` value: none, and every subset."""
    values = {"none"}
    for size in range(1, len(EFFECT_KINDS) + 1):
        for group in combinations(EFFECT_KINDS, size):
            values.add("+".join(group))
    return tuple(sorted(values))


#: Every `existing_effects` value, sorted.
EXISTING_EFFECTS: tuple[str, ...] = _effect_values()

#: The CLOSED vocabulary of every dimension but `resolve_version`.
DIMENSION_VOCABULARIES: dict[str, tuple[str, ...]] = {
    "media_type": MEDIA_TYPES,
    "clip_composition": CLIP_COMPOSITIONS,
    "linked_audio_configuration": LINKED_AUDIO_CONFIGURATIONS,
    "track_layout": TRACK_LAYOUTS,
    "track_occupancy": TRACK_OCCUPANCIES,
    "timeline_density": TIMELINE_DENSITIES,
    "timeline_structure": TIMELINE_STRUCTURES,
    "project_settings": PROJECT_SETTINGS,
    "frame_rate": FRAME_RATES,
    "existing_effects": EXISTING_EFFECTS,
}


def is_dimension(field: str) -> bool:
    """Whether a predicate field names a dimension."""
    return str(field) in DIMENSIONS


def is_identity_field(field: str) -> bool:
    """Whether a field names a project's IDENTITY rather than a state."""
    return str(field) in IDENTITY_FIELDS


def vocabulary(field: str) -> tuple[str, ...] | None:
    """The values a dimension may take, or None when it is open."""
    return DIMENSION_VOCABULARIES.get(str(field))


def value_in_vocabulary(field: str, value) -> bool:
    """Whether a value is one a dimension can measure.

    `resolve_version` is open: every Resolve string is a different
    environment, and a predicate may name any of them.
    """
    allowed = vocabulary(field)
    if allowed is None:
        return field == "resolve_version"
    return isinstance(value, str) and value in allowed


# ── The measurement ──────────────────────────────────────────────────
#
# Pure functions over plain rows and plain settings. The live read that
# produces the rows is `library/tools/axi_qualification_state.py`; this
# is what the read MEANS, and it is testable without Resolve.


def media_kind(row: Mapping) -> str:
    """One item's media kind, in the vocabulary the dimensions use."""
    raw = str(row.get("media_type") or "").strip().casefold()
    if raw in ("video", "audio", "still"):
        return raw
    if raw in ("image sequence", "imagesequence"):
        return "still"
    return raw or "unknown"


def media_type(chosen: Sequence[Mapping],
               operations: Sequence[Mapping]) -> str:
    """The media kind the request touches.

    An item the request addresses answers for itself. A request that
    addresses no item (a row it adds) declares the kind it touches.
    A request that touches more than one kind answers "mixed", which
    matches no recorded predicate - the evidence was never earned on a
    request that spans kinds.
    """
    kinds = {media_kind(row) for row in chosen}
    if len(kinds) == 1:
        return kinds.pop()
    if len(kinds) > 1:
        return "mixed"
    declared = set()
    for operation in operations or ():
        if not isinstance(operation, Mapping):
            continue
        for key in ("media_type", "track_type"):
            value = str(operation.get(key) or "").strip().casefold()
            if value in ("video", "audio"):
                declared.add(value)
    if len(declared) == 1:
        return declared.pop()
    if len(declared) > 1:
        return "mixed"
    return "none"


def clip_composition(chosen: Sequence[Mapping]) -> str:
    """What the request's item IS, or "none" when it addresses no item."""
    if not chosen:
        return "none"
    compositions = {_composition(row) for row in chosen}
    return compositions.pop() if len(compositions) == 1 else "mixed"


def _composition(row: Mapping) -> str:
    """One item's composition, in the dimension vocabulary."""
    kind = media_kind(row)
    linked = bool(row.get("linked_unique_ids"))
    track_type = str(row.get("track_type") or "")
    if kind == "unknown":
        # A generator, a title, a compound clip, a transition: real, but
        # it plays no footage the pool can name. The measurement says so
        # rather than calling it a video clip.
        return "unknown_item"
    if track_type == "audio" or kind == "audio":
        return "linked_audio_clip" if linked else "audio_clip"
    if kind == "still":
        return "still_clip"
    return "linked_video_clip" if linked else "single_video_clip"


def linked_audio_configuration(chosen: Sequence[Mapping],
                                by_id: Mapping[str, Mapping]) -> str:
    """Whether the request's item carries LINKED AUDIO.

    A linked item the row read did not resolve counts as linked audio:
    the measurement cannot claim the qualified condition, so it refuses
    rather than asserts it.
    """
    for row in chosen:
        for uid in row.get("linked_unique_ids") or ():
            linked = by_id.get(str(uid))
            if linked is None:
                return "linked_audio_present"
            if str(linked.get("track_type") or "") == "audio":
                return "linked_audio_present"
    return "no_linked_audio"


def track_layout(tracks: Sequence[Mapping]) -> str:
    """Which track types the timeline has."""
    types = {str(track.get("type") or "") for track in tracks or ()}
    types.discard("")
    if not types:
        return "none"
    ordered = [name for name in ("video", "audio") if name in types]
    ordered += sorted(types - set(ordered))
    return "+".join(ordered)


def track_occupancy(rows: Sequence[Mapping]) -> str:
    """Whether the timeline's tracks are empty, populated, or mixed."""
    if not rows:
        return "all_empty"
    occupied = [bool(row.get("clips")) for row in rows]
    if not any(occupied):
        return "all_empty"
    if all(occupied):
        return "all_populated"
    return "mixed"


def item_count(rows: Sequence[Mapping]) -> int:
    """How many items the timeline holds, across every row."""
    return sum(len(row.get("clips") or ()) for row in rows or ())


def timeline_density(rows: Sequence[Mapping]) -> str:
    """How much the timeline holds, in bands."""
    count = item_count(rows)
    name = DENSITY_BANDS[0][1]
    for threshold, band in DENSITY_BANDS:
        if count >= threshold:
            name = band
    return name


def timeline_structure(rows: Sequence[Mapping]) -> str:
    """The shape of the timeline: empty, one item, or several."""
    count = item_count(rows)
    if count == 0:
        return "empty_timeline"
    if count == 1:
        return "single_item_timeline"
    return "multi_item_timeline"


def orientation(width: int, height: int) -> str:
    """Which way the project's frame is turned."""
    if width <= 0 or height <= 0:
        return "unknown"
    if width > height:
        return "landscape"
    if height > width:
        return "portrait"
    return "square"


def project_settings(width: int, height: int) -> str:
    """The project's geometry, in the dimension vocabulary."""
    if width <= 0 or height <= 0:
        return "unknown"
    return f"{orientation(width, height)}_{width}x{height}"


def frame_rate(reported) -> str:
    """The project's frame rate, in the dimension vocabulary."""
    try:
        value = round(float(reported), 3)
    except (TypeError, ValueError):
        return "other"
    for measured, spelling in _RATE_SPELLINGS:
        if abs(value - measured) <= 0.01:
            return spelling
    return "other"


def existing_effects(chosen: Sequence[Mapping]) -> str:
    """What the request's items already carry.

    The kinds are the ones an operation can collide with: a transition
    at the cut, a transform, a grade, a retime, a Fusion composition.
    Linked audio is not here - it has its own dimension.
    """
    kinds: set[str] = set()
    for row in chosen or ():
        kinds |= _effects_of(row)
    if not kinds:
        return "none"
    return "+".join(name for name in EFFECT_KINDS if name in kinds)


def _effects_of(row: Mapping) -> set[str]:
    """One item's effects."""
    found: set[str] = set()
    if str(row.get("item_type") or "") == "transition":
        found.add("transitions")
    if _has_transform(row):
        found.add("transforms")
    if _has_grade(row):
        found.add("grades")
    if _has_retiming(row):
        found.add("retiming")
    if _has_fusion(row):
        found.add("fusion")
    return found


def _has_transform(row: Mapping) -> bool:
    """Whether the item carries a transform that is not the identity."""
    transform = row.get("transform")
    if not isinstance(transform, Mapping) or not transform:
        return False
    for key in ("Pan", "Tilt", "ZoomX", "ZoomY"):
        try:
            value = float(transform.get(key, 0) or 0)
        except (TypeError, ValueError):
            continue
        if abs(value) > 1e-6:
            return True
    return False


def _has_grade(row: Mapping) -> bool:
    """Whether the item carries color state."""
    color = row.get("color")
    if not isinstance(color, Mapping):
        return False
    if color.get("cdl"):
        return True
    if str(color.get("color_group") or ""):
        return True
    luts = color.get("luts")
    return bool(luts)


def _has_retiming(row: Mapping) -> bool:
    """Whether the item is retimed."""
    speed = row.get("speed")
    if speed is not None:
        try:
            if abs(float(speed) - 100.0) > 1e-6:
                return True
        except (TypeError, ValueError):
            pass
    for key in ("left_offset", "right_offset"):
        value = row.get(key)
        if value in (None, 0, 0.0):
            continue
        try:
            if abs(float(value)) > 1e-6:
                return True
        except (TypeError, ValueError):
            continue
    return False


def _has_fusion(row: Mapping) -> bool:
    """Whether the item carries a Fusion composition that DRAWS.

    Resolve gives every timeline item a pass-through comp; a comp whose
    tools are only the source, the output and the waveform is not a
    treatment, and treating it as one would refuse every clip.
    """
    fusion = row.get("fusion")
    if not isinstance(fusion, Mapping):
        return False
    names = fusion.get("comp_names")
    if isinstance(names, (list, tuple)):
        for name in names:
            if str(name) and str(name) != "Fusion Composition":
                return True
    windows = fusion.get("media_windows")
    if isinstance(windows, (list, tuple)):
        for window in windows:
            if not isinstance(window, Mapping):
                continue
            tools = window.get("tools")
            if not isinstance(tools, (list, tuple)):
                continue
            if any(str(tool) not in ("MediaIn", "MediaOut", "AudioDisplay")
                   for tool in tools):
                return True
    return False


# ── The predicate vocabulary ─────────────────────────────────────────


def predicate(field: str, values: Sequence[str]) -> dict:
    """The predicate that admits exactly these values of a dimension."""
    unique = list(dict.fromkeys(str(value) for value in values))
    if len(unique) == 1:
        return {"field": field, "operator": "equals", "value": unique[0]}
    return {"field": field, "operator": "one_of", "value": unique}


def predicates_for(coverage: Mapping[str, Sequence[str]]) -> list[dict]:
    """The predicate set a coverage map declares.

    A coverage map says which values of each dimension the evidence
    exercised; the predicate set is that claim, one predicate per
    dimension. A dimension the coverage does not mention is NOT
    constrained - and the coverage is required to mention every one, so
    that cannot happen by accident.
    """
    return [predicate(field, list(coverage.get(field) or ()))
            for field in DIMENSIONS if coverage.get(field)]
