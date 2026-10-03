"""The structural timeline read path.

Normal reads come from the newest verified generation in
`timeline_shadow`. A caller that needs the live timeline must say so by
calling `refresh` with the project's CURRENT Resolve timeline; that
records a generation before returning it. Every request is counted so
`ren resolved kpi` can report the shadow-hit rate instead of only a hit
count.

A timeline QUESTION is answered from its recorded GENERATION; a write is
an EditPatch on one.

This module is deliberately structural. Writes still validate their
preconditions against Resolve inside the edit lease (`edit_patch`), and
live-only measurements such as the timeline oracle keep their own
explicit live read when they need to observe unrecorded hand edits.
"""

from __future__ import annotations

from dataclasses import dataclass

from library.tools import timeline_shadow
from library.tools.timeline_shadow import Generation, ShadowError, ShadowStore


@dataclass(frozen=True)
class TimelineRead:
    """One stored generation and its plain-data structural snapshot."""

    generation: Generation
    snapshot: dict

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
            f"no recorded timeline generations"
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
    return TimelineRead(generation, store.snapshot(generation))
