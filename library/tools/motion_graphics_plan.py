"""A model plans the motion-graphics layer, and a brand template refines it.

**The defect this closes.**  On project 001's run of record step 4.06
wrote::

    all 8 resolved motion graphics props draw nothing: the brand template
    declares neither effect.motion_accents nor effect.motion_progress_bar,
    and the creative direction supplies no title or subtitle

Read as a report that is honest; read as a design it is the bug.  No
model was ever ASKED whether this video wanted a title, an accent or a
progress bar.  The capability was switched off by two absent booleans
before any creative reasoning happened, and a missing OPTIONAL input had
silently disabled a whole layer.

The captain's ruling, 2026-09-02: *"it does not matter, the LLM was still
meant to plan these things and implement them properly, the brand
template is only a secondary, we are still trying to get the LLM to
produce well reasoned outputs on its own."*

So **the template REFINES and it may not GATE.**  A project with no brand
template at all gets a planned motion-graphics layer; what the template
adds, when there is one, is a palette to resolve a colour role against
and a typeface to resolve a type role against.  A project without one
does not get a reduced layer - the plan states its own colour, and only
a plan that states NEITHER a role the template can resolve NOR a colour
of its own is dropped, by name, with the reason recorded.

**The overlay layer carries its own timebase.**  The captain, same
ruling: *"why is it that it tries to line up the motion graphics with the
clip segments in the video, the motion graphics can be seperate and on
their own timescale if they need to be, and also they are allowed to use
multiple rows in order to have various motion graphics."*

`generate_motion_props` used to emit one props dict per spine block, with
`durationInFrames` equal to the block's length and the element's timing
therefore equal to the block's timing by construction.  Here an entry
declares `start_seconds` and `duration_seconds` in TIMELINE seconds and
nothing consults a block boundary: the spine reaches the prompt as
CONTEXT - where the speech is, where the cutaways are - and never as a
grid the plan has to land on.

**And several elements may be on screen at once, in rows.**
`anchor` is the vocabulary's nine-position grid; `row` is which line
within that anchor an element occupies, so two elements anchored
`bottom_centre` at the same moment stack rather than collide.  Rows are
an ON-SCREEN layout, not a second Resolve video track: overlapping
entries are composited into ONE overlay segment by :func:`plan_segments`,
which is the shape `timed_text_overlay.plan_timed_text_segments` already
uses and the reason `manifest_validator`'s non-overlap rule for
`motion_graphics_overlay` stays true.  A second V-track would have had to
sit above the generator lane (V5) and the timed-text lane (V6), which
would silently change what composites over what - a stacking order
nobody chose.

**What is refused, and why refusing is the honest answer.**
`motion_graphics_vocabulary.ROSTER` is fifteen elements and the
composition draws four of them.  An entry naming one of the other eleven
is DROPPED with `renderer_cannot_draw_it_yet` and the drop is RECORDED -
never quietly rendered as nothing, and never silently substituted with a
neighbouring element.  The whole roster still reaches the prompt, because
whatever selects a shortlist becomes the chooser (AGENTS.md 10.5) and a
roster written around today's renderer would bake the defect in
permanently (`motion_graphics_vocabulary`'s own argument).

**This module states no magnitude.**  There is no default colour, no
default duration, no default footprint, no minimum count and no maximum.
`DROP_REASONS` is the whole of what a drop can be for and a reason
outside it is refused by name, so a new drop branch has to say what it is
before it can go quiet - the shape `vfx_plan_basis.py` established.

`tests/test_motion_graphics_plan.py`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from library.tools import motion_graphics_vocabulary as vocabulary

#: What the model's answer is called.  One spelling, here.
PLAN_KEY = "motion_graphics_plan"

#: The elements the composition can put on a frame today.  DERIVED from
#: the roster's own reachability flag rather than listed, so an element
#: that becomes drawable is drawable here the moment the roster says so
#: and a stale second list cannot exist.
DRAWABLE = frozenset(
    element.key for element in vocabulary.ROSTER
    if element.reachable == vocabulary.REACHABLE_NOW
)

#: Where in the frame an element may sit.  The vocabulary's own axis.
ANCHORS = tuple(vocabulary.AXES_BY_NAME["anchor"].positions)

#: `tracked` follows a subject and needs a per-frame position nothing
#: measures for the overlay layer - `motion_graphics_vocabulary` files
#: `tracked_label` under NEEDS_MEASUREMENT for the same reason.  An entry
#: anchored here is dropped rather than pinned to a fixed point, because
#: pinning it would be this module choosing a position.
ANCHOR_NEEDS_MEASUREMENT = "tracked"

#: How an element arrives and leaves.  The vocabulary's own axis.
MOTION_CHARACTERS = tuple(vocabulary.AXES_BY_NAME["entrance"].positions)

#: Which role of a declaring palette draws the element.
COLOUR_ROLES = tuple(vocabulary.AXES_BY_NAME["colour_role"].positions)

#: The typographic weight of a run of copy.
TYPE_ROLES = tuple(vocabulary.AXES_BY_NAME["type_role"].positions)


# ── What a drop can be for ───────────────────────────────────────────
#
# The whole of it.  `resolve_plan` refuses a reason outside this table by
# name, so a new branch that discards an entry has to say what it is
# before it can go quiet.  Same shape as `vfx_plan_basis.DROP_REASONS`.

DROP_REASONS: Dict[str, str] = {
    "not_in_the_vocabulary": (
        "The entry names something that is not a motion-graphics element "
        "this pipeline knows. motion_graphics_vocabulary.refusal_reason "
        "carries the words, including the enumeration that owns a near "
        "miss."
    ),
    "renderer_cannot_draw_it_yet": (
        "The element is in the roster and the composition has no node "
        "for it. Recorded rather than rendered as nothing, and never "
        "swapped for a neighbouring element - a substitution here would "
        "be the engine choosing which graphic the video gets."
    ),
    "anchor_needs_a_measurement_nothing_takes": (
        "The entry anchors `tracked`, which follows a subject frame by "
        "frame, and no measurement of that reaches the overlay layer. "
        "Pinning it to a fixed point would be choosing a position."
    ),
    "unknown_anchor": (
        "The anchor is not one of the vocabulary's positions. Nothing is "
        "snapped to the nearest one: a near match is a chooser."
    ),
    "no_timing_declared": (
        "The entry declares no start or no duration. The overlay layer "
        "carries its own timebase, so there is no block boundary to fall "
        "back to and inventing one would put the coupling back."
    ),
    "outside_the_timeline": (
        "The declared span starts at or after the end of the timeline, "
        "or runs for no frames. Not clamped: moving a start is choosing "
        "when the graphic plays."
    ),
    "no_copy_for_an_element_that_needs_one": (
        "The roster entry declares copy `required` and the plan carries "
        "none. What a graphic SAYS has no producer in this engine "
        "(motion_graphics_vocabulary.COPY_SOURCE_IS_UNSET), so an empty "
        "run cannot be filled in from anywhere."
    ),
    "no_colour_to_draw_it_in": (
        "Neither a brand palette role nor a colour stated by the plan "
        "itself resolves to a colour. There is no fallback: drawing in a "
        "constant is what PR #310 emptied house_look.py to stop."
    ),
}


class MotionPlanError(ValueError):
    """A plan this module refuses outright, rather than dropping an entry."""


# ── The three bases ──────────────────────────────────────────────────
#
# `no_elements_planned` and `every_entry_dropped` are spelled
# differently on purpose: the first is a decision the model took and the
# second is the absence of one.  `vfx_plan_basis` draws the same line and
# for the same reason - `{"visual_effects": []}` read identically whether
# the planner chose stillness or named four effects the post-bridge threw
# away.

NO_ELEMENTS_PLANNED = "no_elements_planned"
EVERY_ENTRY_DROPPED = "every_entry_dropped"
ELEMENTS_PLANNED = "elements_planned"

#: The plan was never asked for.  Distinct from an empty plan, the same
#: way `undetermined.NOT_DECLARED` is distinct from `NOTHING_MISSING`.
NOT_PLANNED = "not_planned"

BASES = (NOT_PLANNED, NO_ELEMENTS_PLANNED, EVERY_ENTRY_DROPPED,
         ELEMENTS_PLANNED)


@dataclass
class Dropped:
    """One entry the resolver discarded, and why."""

    element: str
    reason: str
    detail: str = ""
    entry: Dict[str, Any] = field(default_factory=dict)

    def as_record(self) -> dict:
        if self.reason not in DROP_REASONS:
            raise MotionPlanError(
                f"{self.reason!r} is not a reason an entry may be dropped "
                f"for. DROP_REASONS is the whole of it: "
                f"{sorted(DROP_REASONS)}.")
        row = {"element": self.element, "reason": self.reason,
               "what_the_reason_means": DROP_REASONS[self.reason]}
        if self.detail:
            row["detail"] = self.detail
        return row


@dataclass
class ResolvedPlan:
    """What `resolve_plan` returns: the drawable moments and the basis."""

    moments: List[dict] = field(default_factory=list)
    basis: str = NOT_PLANNED
    proposed: int = 0
    dropped: List[Dropped] = field(default_factory=list)

    def basis_record(self) -> dict:
        """The account that travels onto the step's own output.

        Every casualty is named. An empty layer that SAYS which absence
        it is cannot be misread as a clean one.
        """
        return {
            "basis": self.basis,
            "what_the_basis_means": {
                NOT_PLANNED: "no plan was asked for on this run",
                NO_ELEMENTS_PLANNED: (
                    "the model was asked and planned no element - a "
                    "decision, not an absence"),
                EVERY_ENTRY_DROPPED: (
                    "the model planned elements and every one of them "
                    "was dropped - the absence of a decision surviving, "
                    "not a decision to draw nothing"),
                ELEMENTS_PLANNED: "the model planned elements that draw",
            }[self.basis],
            "proposed": self.proposed,
            "resolved": len(self.moments),
            "dropped": [d.as_record() for d in self.dropped],
        }


# ── Resolution ───────────────────────────────────────────────────────

def _number(raw) -> Optional[float]:
    if isinstance(raw, bool):
        return None
    if isinstance(raw, (int, float)):
        return float(raw)
    if isinstance(raw, str):
        try:
            return float(raw.strip())
        except ValueError:
            return None
    return None


def _text(raw) -> str:
    return str(raw).strip() if isinstance(raw, (str, int, float)) else ""


def _copy_runs(entry: Dict[str, Any]) -> List[dict]:
    """The runs of copy an entry carries, in the order it wrote them.

    A run is `{text, type_role}`. `type_role` is the vocabulary's axis
    and carries no size: what `display` and `micro` MEASURE is the
    composition's business, and what they MEAN is the vocabulary's.
    Nothing here supplies a run the plan did not write.
    """
    raw = entry.get("copy")
    runs: List[dict] = []
    if isinstance(raw, str):
        text = raw.strip()
        if text:
            runs.append({"text": text, "type_role": "display"})
        return runs
    if isinstance(raw, dict):
        # A mapping of type_role -> text, in the roles' own order so a
        # display run is never printed under its supporting run because
        # a dict happened to be built the other way round.
        for role in TYPE_ROLES:
            text = _text(raw.get(role))
            if text:
                runs.append({"text": text, "type_role": role})
        for key, value in raw.items():
            if key in TYPE_ROLES:
                continue
            text = _text(value)
            if text:
                runs.append({"text": text, "type_role": "supporting"})
        return runs
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    runs.append({"text": text, "type_role": "display"})
            elif isinstance(item, dict):
                text = _text(item.get("text"))
                if not text:
                    continue
                role = _text(item.get("type_role"))
                runs.append({
                    "text": text,
                    "type_role": role if role in TYPE_ROLES else "supporting",
                })
    return runs


def resolve_colour(entry: Dict[str, Any], palette_roles: Dict[str, str]
                   ) -> Tuple[str, str]:
    """The colour an element draws in, and where it came from.

    **This is the whole of "the template refines and does not gate".**
    A brand palette, when the project named a template that has one,
    resolves the entry's `colour_role`. A project with no template
    resolves nothing here - and the plan's own `color` then answers,
    because the model is allowed taste and the engine is not.

    Returns `(colour, basis)` with an empty colour when neither
    answers. There is no third source: a constant here is a house look
    (AGENTS.md section 12).
    """
    role = _text(entry.get("colour_role")).lower()
    if role in COLOUR_ROLES:
        from_palette = _text(palette_roles.get(role))
        if from_palette:
            return from_palette, f"brand palette role {role!r}"
    stated = _text(entry.get("color") or entry.get("colour"))
    if stated:
        return stated, "stated by the plan"
    return "", ""


def resolve_plan(plan: Any, *, timeline_duration: float, fps: float,
                 palette_roles: Optional[Dict[str, str]] = None,
                 asked: bool = True) -> ResolvedPlan:
    """Turn the model's plan into drawable moments, naming every casualty.

    `timeline_duration` bounds a span and nothing else times one: the
    layer carries its own timebase and a spine block is never consulted.

    `palette_roles` is what a brand template's palette resolved to, or
    `{}` when the project named no template. It REFINES - see
    :func:`resolve_colour` - and its absence drops nothing on its own.
    """
    palette_roles = palette_roles or {}
    if not asked:
        return ResolvedPlan(basis=NOT_PLANNED)
    if plan is None:
        return ResolvedPlan(basis=NO_ELEMENTS_PLANNED)
    if isinstance(plan, dict):
        plan = plan.get("elements", plan.get(PLAN_KEY, []))
    if not isinstance(plan, list):
        raise MotionPlanError(
            f"{PLAN_KEY} must be a list of entries, got "
            f"{type(plan).__name__}.")

    resolved = ResolvedPlan(proposed=len(plan))

    def drop(entry, key, reason, detail=""):
        if reason not in DROP_REASONS:
            raise MotionPlanError(
                f"{reason!r} is not a reason an entry may be dropped for. "
                f"DROP_REASONS is the whole of it: {sorted(DROP_REASONS)}.")
        resolved.dropped.append(
            Dropped(element=key or "(unnamed)", reason=reason,
                    detail=detail, entry=entry if isinstance(entry, dict) else {}))

    for raw in plan:
        entry = raw if isinstance(raw, dict) else {}
        named = _text(entry.get("element") or entry.get("element_key")
                      or (raw if isinstance(raw, str) else ""))
        key = vocabulary.canonical_key(named)
        if not key:
            drop(entry, named, "not_in_the_vocabulary",
                 vocabulary.refusal_reason(named))
            continue

        element = vocabulary.ELEMENTS_BY_KEY[key]
        if key not in DRAWABLE:
            drop(entry, key, "renderer_cannot_draw_it_yet",
                 element.reachability_note or
                 f"the roster records it as {element.reachable}")
            continue

        anchor = _text(entry.get("anchor")).lower()
        if anchor == ANCHOR_NEEDS_MEASUREMENT:
            drop(entry, key, "anchor_needs_a_measurement_nothing_takes")
            continue
        if anchor not in ANCHORS:
            drop(entry, key, "unknown_anchor",
                 f"{anchor!r} is not one of {list(ANCHORS)}")
            continue

        start = _number(entry.get("start_seconds"))
        duration = _number(entry.get("duration_seconds"))
        if start is None or duration is None:
            drop(entry, key, "no_timing_declared",
                 "start_seconds and duration_seconds are both required; "
                 f"got start={entry.get('start_seconds')!r} "
                 f"duration={entry.get('duration_seconds')!r}")
            continue
        start = max(0.0, start)
        if duration <= 0 or start >= timeline_duration:
            drop(entry, key, "outside_the_timeline",
                 f"start {start}s, duration {duration}s, timeline "
                 f"{timeline_duration}s")
            continue
        # The END is bounded by the timeline because a frame past the
        # last one cannot be rendered at all.  The START is never moved:
        # moving it would choose when the graphic plays.
        end = min(start + duration, timeline_duration)
        frames = max(1, int(round((end - start) * fps)))

        runs = _copy_runs(entry)
        if element.copy == "required" and not runs:
            drop(entry, key, "no_copy_for_an_element_that_needs_one")
            continue

        colour, colour_basis = resolve_colour(entry, palette_roles)
        if not colour:
            drop(entry, key, "no_colour_to_draw_it_in",
                 "the entry names no colour_role the brand palette "
                 "resolves and states no colour of its own")
            continue

        entrance = _text(entry.get("entrance")).lower()
        exit_ = _text(entry.get("exit")).lower()
        resolved.moments.append({
            "element": key,
            "anchor": anchor,
            # Which line within that anchor. Two elements anchored the
            # same way at the same moment stack instead of colliding.
            "row": max(0, int(_number(entry.get("row")) or 0)),
            "runs": runs,
            "color": colour,
            "colorBasis": colour_basis,
            "entrance": entrance if entrance in MOTION_CHARACTERS else "cut",
            "exit": exit_ if exit_ in MOTION_CHARACTERS else "cut",
            # Timeline seconds, kept beside the frame counts so a reader
            # of the props file never has to divide by the fps to see
            # what the plan actually said.
            "timeline_start": round(start, 3),
            "timeline_end": round(end, 3),
            # Where this span sits in the WHOLE piece, as fractions.
            # `progress_bar` is the element that draws them; every other
            # element carries them because a reader of the props file
            # should not have to recompute what the plan already knew.
            "timelineProgressStart": round(
                start / timeline_duration, 4) if timeline_duration else 0.0,
            "timelineProgressEnd": round(
                end / timeline_duration, 4) if timeline_duration else 0.0,
            "startFrame": int(round(start * fps)),
            "durationFrames": frames,
            "footprint": _number(entry.get("footprint")),
            "emphasis": _number(entry.get("emphasis")),
            "why": _text(entry.get("why") or entry.get("rationale")),
        })

    if resolved.moments:
        resolved.basis = ELEMENTS_PLANNED
    elif resolved.proposed:
        resolved.basis = EVERY_ENTRY_DROPPED
    else:
        resolved.basis = NO_ELEMENTS_PLANNED
    return resolved


# ── Segments ─────────────────────────────────────────────────────────

def plan_segments(moments: List[dict], *, fps: float, width: int,
                  height: int, safe_area: dict) -> List[dict]:
    """Cluster the moments into non-overlapping overlay segments.

    Moments whose spans touch or overlap go into ONE segment and have
    their `startFrame` rebased to that segment's start, so several
    graphics are on screen together on a single video lane.  That is
    what keeps `manifest_validator._check_overlay_segments_do_not_overlap`
    true for `motion_graphics_overlay` and what makes rows an on-screen
    layout rather than a second Resolve track.

    The same algorithm `timed_text_overlay.plan_timed_text_segments`
    uses.  Deliberately the same: a second clustering with its own
    rounding would be two answers to one question.
    """
    ordered = sorted(moments, key=lambda m: (m["startFrame"], m.get("row", 0)))
    clusters: List[List[dict]] = []
    for moment in ordered:
        if clusters and moment["startFrame"] <= _cluster_end(clusters[-1]):
            clusters[-1].append(moment)
        else:
            clusters.append([moment])

    segments = []
    for index, cluster in enumerate(clusters):
        start_frame = cluster[0]["startFrame"]
        end_frame = _cluster_end(cluster)
        total_frames = max(1, end_frame - start_frame)
        segments.append({
            "index": index,
            "timeline_start": round(start_frame / fps, 3),
            "timeline_end": round(end_frame / fps, 3),
            "total_frames": total_frames,
            "element_count": len(cluster),
            "elements": sorted({m["element"] for m in cluster}),
            "props": {
                "elements": [
                    {**m, "startFrame": m["startFrame"] - start_frame}
                    for m in cluster
                ],
                "fps": fps,
                "width": width,
                "height": height,
                "safeArea": safe_area,
                "durationInFrames": total_frames,
            },
        })
    return segments


def _cluster_end(cluster: List[dict]) -> int:
    return max(m["startFrame"] + m["durationFrames"] for m in cluster)


def props_draw_ink(props: dict) -> bool:
    """Would Remotion put a single pixel on this frame?

    Every pixel the composition draws belongs to an entry in
    `props["elements"]`, and `resolve_plan` has already dropped every
    entry that resolves to nothing - so a segment with an element draws,
    and one without does not.  That is a much shorter statement than the
    flag-per-element predicate it replaces, and it stays true when the
    composition grows a node, which the old one did not.
    """
    return bool(props.get("elements"))
