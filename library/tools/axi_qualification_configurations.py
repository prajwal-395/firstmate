"""The disposable timeline CONFIGURATIONS a qualification run covers.

A qualification earned on ONE timeline shape says nothing about another.
A delete proven on a lone clip is not a delete proven on a retimed clip
that sits under a grade, and neither is a proof on a portrait project
or at 29.97fps. The evidence is only worth what the states it covered
are worth - so the states are ENUMERATED here, built as disposable
timelines, and every candidate primitive is run against each of them.

This module is that enumeration and the builder that materializes one.
It is deliberately separate from the operations themselves: a
configuration is a SHAPE, and what an operation does inside that shape
is the operation's business.

What the enumeration covers
---------------------------

* AXI-027 - empty AND populated tracks. A qualification that only ever
  ran on a populated track says nothing about adding a clip to an empty
  one, and the two are different states.
* AXI-028 - clips that already carry linked audio, a transition, a
  transform, a grade, a retime or a Fusion composition. Those are the
  states where an edit can destroy work, so they are the states worth
  qualifying in.
* AXI-029 - the common frame rates, and portrait as well as landscape.

Every configuration DECLARES the dimension values it fixes, and they
are DERIVED from the recipe - the rows it builds and the items it
places - so a declaration cannot drift from the recipe that produced
it. The live run MEASURES the shape it built and compares, so a
configuration that does not build what it says is caught at the build
rather than believed.

Nothing here touches a captain's project: the builder works on the
disposable qualification project, and every timeline it creates is
named after its configuration so it can be found and removed.

`tests/unit/resolve/test_axi_qualification_configurations.py`.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from library.tools import axi_qualification_dimensions as dimensions

#: The prefix every disposable configuration timeline carries. A timeline
#: whose name starts with this is a qualification fixture, never a
#: captain's reel.
CONFIGURATION_PREFIX = "AXI "

#: The item kinds a configuration may place.
ITEM_KINDS: tuple[str, ...] = (
    "video", "linked_av", "still", "caption")


class ConfigurationError(RuntimeError):
    """A configuration could not be built as it declares itself."""


@dataclass(frozen=True)
class Row:
    """One track row of a configuration.

    `occupancy` is "empty" or "populated"; `items` names the item kinds
    placed on the row. A linked A/V clip also puts its twin on an audio
    row, and that twin is counted where it lands. `name` is the row's
    own name - Ren's caption cards live on a VIDEO row named
    "Subtitles" (`library/tools/caption_swap.py`), not on a Resolve
    subtitle track.
    """

    type: str
    occupancy: str
    items: tuple[str, ...] = ()
    name: str = ""

    @property
    def count(self) -> int:
        return len(self.items)


@dataclass(frozen=True)
class Configuration:
    """One disposable timeline shape."""

    id: str
    frame_rate: str
    width: int
    height: int
    rows: tuple[Row, ...]
    items: int
    media: tuple[tuple[str, str], ...] = ()
    effects: tuple[str, ...] = ()
    note: str = ""

    @property
    def timeline_name(self) -> str:
        """The name the disposable timeline carries."""
        return f"{CONFIGURATION_PREFIX}{self.id}"

    @property
    def project_settings(self) -> str:
        return dimensions.project_settings(self.width, self.height)

    def media_kinds(self) -> tuple[str, ...]:
        """The item kinds this configuration places."""
        return tuple(dict.fromkeys(kind for kind, _source in self.media))


def _rows(*specs: tuple[str, str, tuple[str, ...], str]) -> tuple[Row, ...]:
    return tuple(Row(kind, occupancy, items, name) for kind, occupancy, items,
                 name in specs)


#: The ONE enumeration of the configurations a qualification run covers.
CONFIGURATIONS: tuple[Configuration, ...] = (
    Configuration(
        id="empty_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "empty", (), "")),
        items=0,
        note="two empty video rows: the request adds what was not there",
    ),
    Configuration(
        id="populated_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="two clips on the first video row, an empty row beside them",
    ),
    Configuration(
        id="linked_av_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("linked_av",), ""),
                   ("audio", "populated", ("linked_audio",), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("linked_av", "qual_a.mp4"),),
        note="one clip with its linked audio twin, and an empty video row",
    ),
    Configuration(
        id="still_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("still",), ""),
                   ("video", "empty", (), "")),
        items=1,
        media=(("still", "fm-qual-logo-180x64.png"),),
        note="one still beside an empty row",
    ),
    Configuration(
        id="graded_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video",), ""),),
        items=1,
        media=(("video", "qual_a.mp4"),),
        effects=("grades",),
        note="one clip that already carries a grade",
    ),
    Configuration(
        id="transformed_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video",), ""),),
        items=1,
        media=(("video", "qual_a.mp4"),),
        effects=("transforms",),
        note="one clip that already carries a transform",
    ),
    Configuration(
        id="retimed_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video",), ""),),
        items=1,
        media=(("video", "qual_a.mp4"),),
        effects=("retiming",),
        note="one clip that is already retimed",
    ),
    Configuration(
        id="transition_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video", "video"), ""),),
        items=3,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        effects=("transitions",),
        note="two clips with a native transition across the cut",
    ),
    Configuration(
        id="fusion_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video",), ""),),
        items=1,
        media=(("video", "qual_a.mp4"),),
        effects=("fusion",),
        note="one clip that carries a Fusion composition",
    ),
    Configuration(
        id="caption_cards_1080p24",
        frame_rate="24", width=1920, height=1080,
        rows=_rows(("video", "empty", (), ""),
                   ("video", "populated", ("caption", "caption"), "Subtitles")),
        items=2,
        media=(("caption", "axc-caption-1.mov"),
               ("caption", "axc-caption-2.mov")),
        note="two rendered caption cards on the Subtitles video row",
    ),
    Configuration(
        id="populated_1080p2398",
        frame_rate="23.976", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut at 23.976fps",
    ),
    Configuration(
        id="populated_1080p25",
        frame_rate="25", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut at 25fps",
    ),
    Configuration(
        id="populated_1080p2997",
        frame_rate="29.97", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut at 29.97fps",
    ),
    Configuration(
        id="populated_1080p30",
        frame_rate="30", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut at 30fps",
    ),
    Configuration(
        id="populated_1080p60",
        frame_rate="60", width=1920, height=1080,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut at 60fps",
    ),
    Configuration(
        id="populated_4k25",
        frame_rate="25", width=3840, height=2160,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut at 4K",
    ),
    Configuration(
        id="populated_portrait_1080x1920_24",
        frame_rate="24", width=1080, height=1920,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut, turned on its side",
    ),
    Configuration(
        id="populated_portrait_1080x1920_30",
        frame_rate="30", width=1080, height=1920,
        rows=_rows(("video", "populated", ("video", "video"), ""),
                   ("video", "empty", (), "")),
        items=2,
        media=(("video", "qual_a.mp4"), ("video", "qual_b.mp4")),
        note="the same cut, turned on its side, at 30fps",
    ),
)

#: The frame rates the enumeration covers, in the dimension vocabulary.
COVERED_FRAME_RATES: tuple[str, ...] = tuple(dict.fromkeys(
    configuration.frame_rate for configuration in CONFIGURATIONS))

#: The project geometries the enumeration covers.
COVERED_PROJECT_SETTINGS: tuple[str, ...] = tuple(dict.fromkeys(
    configuration.project_settings for configuration in CONFIGURATIONS))


def configuration_by_id(identifier: str) -> Configuration:
    """The configuration with this id, or the refusal that names it."""
    for configuration in CONFIGURATIONS:
        if configuration.id == identifier:
            return configuration
    raise ConfigurationError(
        f"no qualification configuration is called {identifier!r}")


def track_layout(configuration: Configuration) -> str:
    """Which row types the configuration builds."""
    return dimensions.track_layout([
        {"type": row.type} for row in configuration.rows])


def track_occupancy(configuration: Configuration) -> str:
    """Whether the configuration's rows are empty, populated or mixed."""
    if not configuration.rows:
        return "all_empty"
    populated = [row.occupancy == "populated" for row in configuration.rows]
    if not any(populated):
        return "all_empty"
    if all(populated):
        return "all_populated"
    return "mixed"


def timeline_structure(configuration: Configuration) -> str:
    """The shape the configuration's item count gives."""
    return ("empty_timeline" if configuration.items == 0
            else "single_item_timeline" if configuration.items == 1
            else "multi_item_timeline")


def timeline_density(configuration: Configuration) -> str:
    """The band the configuration's item count falls in."""
    count = configuration.items
    name = dimensions.DENSITY_BANDS[0][1]
    for threshold, band in dimensions.DENSITY_BANDS:
        if count >= threshold:
            name = band
    return name


def shape_of(configuration: Configuration) -> dict:
    """Every dimension value the configuration fixes.

    The configuration's own frame rate and geometry are part of its
    shape, so a caller never has to remember which of the two it is.
    """
    return {
        "track_layout": track_layout(configuration),
        "track_occupancy": track_occupancy(configuration),
        "timeline_structure": timeline_structure(configuration),
        "timeline_density": timeline_density(configuration),
        "project_settings": configuration.project_settings,
        "frame_rate": configuration.frame_rate,
    }


def declared_values(configuration: Configuration) -> dict:
    """The dimension values the configuration declares, by dimension."""
    return {field: [value] for field, value
            in shape_of(configuration).items()}


def uncovered_values(covered: Mapping[str, Sequence[str]]) -> dict:
    """Every value of every closed dimension the coverage did not reach."""
    out = {}
    for field, vocabulary in dimensions.DIMENSION_VOCABULARIES.items():
        seen = set(covered.get(field, ()))
        missing = [value for value in vocabulary if value not in seen]
        if missing:
            out[field] = missing
    return out


# ── Building one ─────────────────────────────────────────────────────
#
# A configuration is built into the DISPOSABLE qualification project and
# nowhere else. The builder never touches a captain's project: it takes
# the project as an argument and refuses anything that is not the
# qualification one.

#: How many frames a placed clip occupies, when its source is long
#: enough. The frames matter for the record span, not for the shape.
PLACE_FRAMES = 48


def _pool_item(pool_items: Mapping[str, object], source: str):
    item = pool_items.get(source)
    if item is None:
        raise ConfigurationError(
            f"the pool holds no item called {source!r}")
    return item


def _delete_existing(project, name: str) -> None:
    """A rebuild is DELETE + CREATE, so a stale configuration never leaks.

    The qualification project is rebuilt before every run, but a
    configuration left behind by an interrupted run would otherwise be
    found by name and silently reused.
    """
    timeline = None
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(index)
        if candidate is not None and candidate.GetName() == name:
            timeline = candidate
            break
    if timeline is None:
        return
    if not project.DeleteTimelines([timeline]):
        raise ConfigurationError(
            f"the qualification project still holds a timeline called "
            f"{name!r}; refusing to build a configuration over it")


def _ensure_rows(timeline, track_type: str, wanted: int) -> int:
    """Make sure the timeline has `wanted` rows of this type."""
    index = 0
    while index < wanted:
        count = int(timeline.GetTrackCount(track_type) or 0)
        if index >= count:
            if not timeline.AddTrack(track_type):
                raise ConfigurationError(
                    f"Resolve refused to add a {track_type} row")
            count = int(timeline.GetTrackCount(track_type) or 0)
            if count <= index:
                raise ConfigurationError(
                    f"the {track_type} row {index + 1} did not appear after "
                    "AddTrack")
        index += 1
    return wanted


def _place(pool, item, *, track_index: int, record_frame: int,
           media_type: str, frames: int = PLACE_FRAMES) -> object:
    """Place one pool item on one row at one record frame."""
    payload: dict = {"mediaPoolItem": item,
                     "trackIndex": track_index,
                     "recordFrame": record_frame}
    if media_type == "linked_av":
        # No mediaType: Resolve places the clip with its linked audio.
        pass
    else:
        payload["mediaType"] = 1
        payload["startFrame"] = 0
        payload["endFrame"] = frames
    placed = pool.AppendToTimeline([payload])
    if not placed:
        raise ConfigurationError(
            f"Resolve placed nothing from the pool item on row "
            f"{track_index} at frame {record_frame}")
    return placed[0]


def _seed_effects(items, configuration: Configuration) -> list[dict]:
    """Seed the effects the configuration declares, on its first item."""
    seeded: list[dict] = []
    if not configuration.effects:
        return seeded
    target = items[0] if items else None
    if target is None:
        raise ConfigurationError(
            f"{configuration.id} declares the effects "
            f"{configuration.effects} but places no item to carry them")
    for effect in configuration.effects:
        if effect == "grades":
            written = bool(target.SetCDL({
                "NodeIndex": "1", "Slope": "1.08 1.0 0.94",
                "Offset": "0 0 0", "Power": "1 1 1",
                "Saturation": "1.05"}))
            if not written:
                raise ConfigurationError(
                    f"{configuration.id}: SetCDL answered false; the grade "
                    "did not land")
        elif effect == "transforms":
            if not target.SetProperty("Pan", 40.0):
                raise ConfigurationError(
                    f"{configuration.id}: SetProperty(Pan) answered false; "
                    "the transform did not land")
        elif effect == "retiming":
            if not target.SetSpeed({"Percentage": 50.0,
                                    "RippleTimeline": False}):
                raise ConfigurationError(
                    f"{configuration.id}: SetSpeed answered false; the retime "
                    "did not land")
        elif effect == "transitions":
            _seed_transition(target, configuration)
        elif effect == "fusion":
            _seed_fusion(target, configuration)
        else:
            raise ConfigurationError(
                f"{configuration.id} declares the unknown effect {effect!r}")
        seeded.append({"effect": effect, "item": str(target.GetName())})
    return seeded


def _seed_transition(target, configuration: Configuration) -> None:
    """Put a native transition across the cut after this item."""
    placed = target.AddTransition({
        "type": "Cross Dissolve", "category": "Video", "position": "end",
        "alignment": "center", "duration": 12})
    if not placed:
        raise ConfigurationError(
            f"{configuration.id}: AddTransition answered empty; the "
            "transition did not land")


def _seed_fusion(target, configuration: Configuration) -> None:
    """Give the item a Fusion composition that DRAWS.

    An empty comp does not count: Resolve gives every item a pass-through
    comp, so the dimension asks for a comp whose tools are more than the
    source and the output.
    """
    before = list(target.GetFusionCompNameList() or [])
    added = target.AddFusionComp()
    after = list(target.GetFusionCompNameList() or [])
    new_names = [name for name in after if name not in before]
    if not added or len(new_names) != 1:
        raise ConfigurationError(
            f"{configuration.id}: AddFusionComp returned {added!r} and the "
            f"comp names changed by {new_names!r}; expected exactly one new "
            "comp")
    comp = target.GetFusionCompByIndex(after.index(new_names[0]) + 1)
    if comp is None:
        raise ConfigurationError(
            f"{configuration.id}: the new Fusion comp did not read back")
    if comp.AddTool("BrightnessContrast", -32768, -32768) is None:
        raise ConfigurationError(
            f"{configuration.id}: the Fusion comp refused a "
            "BrightnessContrast node")


def build(project, configuration: Configuration, *,
          pool_items: Mapping[str, object]) -> dict:
    """Materialize one configuration as a disposable timeline.

    `pool_items` maps a source file name to its MediaPoolItem. The
    caller must have the project open and the timeline CURRENT before
    calling: Resolve's `AppendToTimeline` writes to the current
    timeline.

    The result records what was placed and the shape the configuration
    declared, so the run can compare the MEASURED shape against it.
    """
    from library.tools.resolve_lock import assert_current_timeline

    pool = project.GetMediaPool()
    _delete_existing(project, configuration.timeline_name)
    timeline = pool.CreateEmptyTimeline(configuration.timeline_name)
    if timeline is None:
        raise ConfigurationError(
            f"Resolve refused to create {configuration.timeline_name!r}")
    for key, value in (("timelineFrameRate", configuration.frame_rate),
                       ("timelineResolutionWidth", str(configuration.width)),
                       ("timelineResolutionHeight", str(configuration.height))):
        if not timeline.SetSetting(key, value):
            raise ConfigurationError(
                f"{configuration.id}: Resolve refused {key}={value}")
    if not project.SetCurrentTimeline(timeline):
        raise ConfigurationError(
            f"{configuration.id}: Resolve refused to make "
            f"{configuration.timeline_name!r} current")
    assert_current_timeline(project, timeline)

    wanted: dict[str, int] = {}
    for row in configuration.rows:
        wanted[row.type] = wanted.get(row.type, 0) + 1
    for track_type, count in wanted.items():
        _ensure_rows(timeline, track_type, count)

    counters: dict[str, int] = {}
    placed: list[object] = []
    for row in configuration.rows:
        counters[row.type] = counters.get(row.type, 0) + 1
        index = counters[row.type]
        if row.name and not timeline.SetTrackName(row.type, index, row.name):
            raise ConfigurationError(
                f"{configuration.id}: Resolve refused to name the "
                f"{row.type} row {index} {row.name!r}")
        record = 0
        for kind in row.items:
            source = _source_for(configuration, kind)
            item = _pool_item(pool_items, source)
            placed.append(_place(pool, item, track_index=index,
                                 record_frame=record, media_type=kind))
            record += PLACE_FRAMES
    seeded = _seed_effects(placed, configuration)
    return {"timeline": configuration.timeline_name,
            "placed": len(placed),
            "effects": seeded,
            "shape": shape_of(configuration)}


def _source_for(configuration: Configuration, kind: str) -> str:
    """The pool source an item kind is placed from."""
    for media_kind, source in configuration.media:
        if media_kind == kind:
            return source
    raise ConfigurationError(
        f"{configuration.id} places a {kind!r} but names no pool source "
        "for it")
