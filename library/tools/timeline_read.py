"""The structural timeline read path.

Normal reads come from the newest verified generation in
`timeline_shadow`. A caller that needs the live timeline must say so by
calling `refresh` with the project's CURRENT Resolve timeline; that
records a generation before returning it. Every request is counted so
`ren resolved kpi` can report the shadow-hit rate instead of only a hit
count.

A timeline QUESTION is answered from its recorded GENERATION; a write is
an EditPatch on one.

This module is deliberately structural. Transform questions are answered
from the shadow too: the stored transform is in canonical form (the form
read when the timeline IS current), so `read_transform` needs no live
read and no cursor move. Writes still validate their preconditions
against Resolve inside the edit lease (`edit_patch`), and live-only
measurements such as the timeline oracle keep their own explicit live
read when they need to observe unrecorded hand edits.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from library.tools import timeline_shadow
from library.tools.timeline_shadow import Generation, ShadowError, ShadowStore


@dataclass(frozen=True)
class TimelineIdentity:
    """Stable identity shared by recorded and live timeline reads."""

    project: str
    timeline_id: str
    name: str


@dataclass(frozen=True)
class TimelineClip:
    """Canonical structural fields for one clip in a timeline row."""

    unique_id: str | None
    name: str
    record_in: Any
    record_out: Any
    duration: Any
    source_in_frame: Any
    source_out_frame: Any


@dataclass(frozen=True)
class TimelineTrack:
    """A typed timeline row, in Resolve track and clip order."""

    media_type: str
    index: int
    name: str
    clips: tuple[TimelineClip, ...]


@dataclass(frozen=True)
class TimelineMarker:
    """One marker note with its plane retained as part of its identity."""

    level: str
    frame: Any
    color: str
    name: str
    note: str
    clip_unique_id: str | None = None


@dataclass(frozen=True)
class TimelineRead:
    """One stored generation and its plain-data structural snapshot."""

    generation: Generation
    snapshot: dict
    source: str = "recorded_generation"

    @property
    def identity(self) -> TimelineIdentity:
        return TimelineIdentity(self.generation.project,
                                self.generation.timeline_id,
                                self.generation.timeline_name)

    @property
    def rows(self) -> tuple[TimelineTrack, ...]:
        """Typed row schema; the legacy snapshot remains available."""
        tracks = self.snapshot.get("tracks", ())
        result = []
        for row in tracks:
            clips = tuple(TimelineClip(
                unique_id=clip.get("unique_id"),
                name=clip.get("name", ""),
                record_in=clip.get("record_in"),
                record_out=clip.get("record_out"),
                duration=clip.get("duration"),
                source_in_frame=clip.get("source_in_frame"),
                source_out_frame=clip.get("source_out_frame"),
            ) for clip in row.get("clips", ()))
            result.append(TimelineTrack(
                media_type=row.get("type", ""),
                index=int(row.get("index", 0)),
                name=row.get("name", ""),
                clips=clips,
            ))
        return tuple(result)

    @property
    def markers(self) -> tuple[TimelineMarker, ...]:
        """Timeline, clip, and pool notes in one stable list."""
        return tuple(TimelineMarker(
            level=marker.get("level", "timeline"),
            frame=marker.get("frame"),
            color=marker.get("color", ""),
            name=marker.get("name", ""),
            note=marker.get("note", ""),
            clip_unique_id=marker.get("clip_unique_id"),
        ) for marker in marker_records(self.snapshot))

    @property
    def clip_details(self) -> tuple[dict, ...]:
        """Full clip records for compatibility consumers, owned here."""
        return tuple(clip_records(self.snapshot))

    @property
    def marker_details(self) -> tuple[dict, ...]:
        """Full marker records for compatibility consumers, owned here."""
        return tuple(marker_records(self.snapshot))

    def summary(self) -> dict:
        return self.generation.summary()


def _choose_head(heads: list[Generation], project: str, name: str,
                 allow_prefix: bool) -> Generation:
    by_id = [head for head in heads if head.timeline_id == name]
    if by_id:
        return by_id[0]
    exact = [head for head in heads if head.timeline_name == name]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ShadowError(
            f"{len(exact)} timelines in project {project!r} are named "
            f"{name!r}; name one by its id")
    if allow_prefix:
        matches = [head for head in heads
                   if head.timeline_name.startswith(name)]
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            names = [head.timeline_name for head in matches]
            raise ShadowError(
                f"no timeline named exactly {name!r}; it is a prefix of "
                f"{len(matches)} recorded timelines: {names} - pass the "
                "full name")
    names = [head.timeline_name for head in heads]
    if not heads:
        raise ShadowError(
            f"no recorded timeline generations for project {project!r}; "
            "refresh a timeline first with `ren resolved submit "
            "timeline.snapshot`")
    raise ShadowError(
        f"no recorded timeline named exactly {name!r} in project "
        f"{project!r}. Recorded timelines: {names}. Refresh the timeline "
        "to record a new or renamed timeline.")


def timeline_identity(timeline, project: str = "") -> TimelineIdentity:
    """Read a live handle's canonical identity at the shared API boundary."""
    try:
        value = timeline.GetUniqueId()
    except Exception:
        value = None
    if not value:
        try:
            value = timeline.GetName()
        except Exception as exc:
            raise ShadowError(f"timeline identity could not be read ({exc})") from exc
    try:
        name = timeline.GetName()
    except Exception as exc:
        raise ShadowError(f"timeline name could not be read ({exc})") from exc
    return TimelineIdentity(project, str(value), str(name))


def clip_records(snapshot: dict) -> list[dict]:
    """Flatten the canonical track/clip snapshot in timeline order."""
    return [clip for track in snapshot.get("tracks", ())
            for clip in track.get("clips", ())]


def marker_records(snapshot: dict) -> list[dict]:
    """Return every marker note, or timeline-plane markers in old snapshots."""
    markers = snapshot.get("markers", {})
    if "notes" in markers:
        return [dict(marker) for marker in markers["notes"]]
    return [dict(marker, level="timeline")
            for marker in markers.get("timeline", ())]


def read(project: str, timeline: str, *, generation: int | None = None,
         allow_prefix: bool = False, caller: str = "timeline_read.read",
         store: ShadowStore | None = None) -> TimelineRead:
    """Read a recorded generation, never Resolve.

    Prefix lookup is opt-in for commands that already promise that
    spelling. The result names and timestamps its source generation so a
    hand edit made afterward is not presented as current state.
    """
    store = store or ShadowStore()
    timeline_id = None
    try:
        current = store.current_snapshots(project)
        heads = [generation for generation, _snapshot in current]
        head = _choose_head(heads, project, timeline, allow_prefix)
        timeline_id = head.timeline_id
        if generation is None:
            selected, snapshot = next(
                (item, body) for item, body in current
                if item.timeline_id == timeline_id)
        else:
            selected = store.get(project, timeline_id, generation)
            snapshot = store.snapshot(selected)
        result = TimelineRead(selected, snapshot)
    except ShadowError:
        store.record_read_request(caller, project, timeline_id, "miss")
        raise
    store.answered(caller, project, timeline_id)
    store.record_read_request(caller, project, timeline_id, "shadow_hit")
    return result


def read_generation(project: str, timeline_id: str,
                    generation: int | None = None, *,
                    store: ShadowStore | None = None) -> TimelineRead:
    """Read one exact recorded timeline generation by canonical identity."""
    store = store or ShadowStore()
    selected = store.head(project, timeline_id) if generation is None else \
        store.get(project, timeline_id, generation)
    if selected is None:
        raise ShadowError(
            f"no recorded timeline generations for {timeline_id!r} in "
            f"project {project!r}; refresh a timeline first")
    return TimelineRead(selected, store.snapshot(selected))


def read_transform(project: str, timeline: str, unique_id: str, *,
                   generation: int | None = None,
                   allow_prefix: bool = False,
                   caller: str = "timeline_read.read_transform",
                   store: ShadowStore | None = None) -> dict:
    """Answer a transform question from the shadow, never from Resolve.

    The shadow stores transforms in canonical form - the form read when
    the timeline IS current (`read_reel` refuses a non-current read
    because a transform read through a non-current handle is scaled) -
    so a transform question is answered from the recorded generation
    without a live read and without moving the cursor, even when the
    timeline is not the project's current one.

    Raises `ShadowError` when the timeline has no recorded generation or
    the clip is not on it.
    """
    view = read(project, timeline, generation=generation,
                allow_prefix=allow_prefix, caller=caller, store=store)
    for clip in clip_records(view.snapshot):
        if clip.get("unique_id") == unique_id:
            return dict(clip.get("transform") or {})
    raise ShadowError(
        f"clip {unique_id!r} is not on timeline {timeline!r} in project "
        f"{project!r} at generation {view.generation.generation}")


def read_any(timeline: str, *, allow_prefix: bool = False,
             caller: str = "timeline_read.read_any",
             store: ShadowStore | None = None) -> TimelineRead:
    """Read by name when it identifies one timeline across known projects."""
    store = store or ShadowStore()
    current = store.current_snapshots()
    heads = [generation for generation, _snapshot in current]
    by_id = [head for head in heads if head.timeline_id == timeline]
    exact = [head for head in heads if head.timeline_name == timeline]
    matches = by_id or exact
    if not matches and allow_prefix:
        matches = [head for head in heads
                   if head.timeline_name.startswith(timeline)]
    projects = {head.project for head in matches}
    project = (next(iter(projects)) if len(projects) == 1 else
               "(multiple projects)" if projects else "(unspecified)")
    timeline_id = None
    try:
        if len(matches) > 1:
            if len(projects) == 1:
                names = [head.timeline_name for head in matches]
                if any(head.timeline_name == timeline for head in matches):
                    raise ShadowError(
                        f"timeline name {timeline!r} is duplicated in "
                        f"project {project!r}: {names}; pass its timeline "
                        "id")
                raise ShadowError(
                    f"no timeline named exactly {timeline!r}; it is a "
                    f"prefix of {len(matches)} recorded timelines: "
                    f"{names} - pass the full name")
            candidates = [f"{head.project}/{head.timeline_name}"
                          for head in matches]
            raise ShadowError(
                f"timeline {timeline!r} is recorded in multiple projects: "
                f"{candidates}; pass --project to select one")
        if not matches:
            raise ShadowError(
                f"no recorded timeline name or id {timeline!r}; pass "
                "--project and refresh the timeline first")
        head = matches[0]
        timeline_id = head.timeline_id
        snapshot = next(body for generation, body in current
                       if generation.project == project
                       and generation.timeline_id == timeline_id)
        result = TimelineRead(head, snapshot)
    except ShadowError:
        store.record_read_request(caller, project, timeline_id, "miss")
        raise
    store.answered(caller, project, timeline_id)
    store.record_read_request(caller, project, timeline_id, "shadow_hit")
    return result


def list_timelines(project: str | None = None, *,
                   caller: str = "timeline_read.list",
                   store: ShadowStore | None = None) -> list[TimelineRead]:
    """List newest recorded generations, optionally for one project."""
    store = store or ShadowStore()
    current = store.current_snapshots(project)
    if not current:
        store.record_read_request(caller, project or "(all projects)", None,
                                  "miss")
        raise ShadowError(
            "no recorded timeline generations"
            + (f" for project {project!r}" if project else "") + "; "
            "refresh a timeline first with `ren resolved submit "
            "timeline.snapshot`")
    store.record_read_request(caller, project or "(all projects)", None,
                              "shadow_hit")
    store.answered(caller, project or "(all projects)",
                   "(project timeline list)")
    return [TimelineRead(head, snapshot) for head, snapshot in current]


def refresh(resolve_project, timeline, *,
            store: ShadowStore | None = None,
            project_folder=None) -> TimelineRead:
    """Explicitly refresh from the CURRENT live timeline, then return it."""
    store = store or ShadowStore()
    generation = timeline_shadow.observe(
        resolve_project, timeline, store, project_folder=project_folder)
    return TimelineRead(generation, store.snapshot(generation),
                        "live_refresh")
