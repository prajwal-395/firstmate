"""Who is speaking, named on screen the first time they appear.

The current contract
--------------------
**Every reel, planned fresh** (captain, 2026-09-12: "for all reels, not
just this one").  This is a capability of the reel build, planned from
the reel's own words on every reel, never a graphic pinned onto one
timeline.  A reel nobody has planned yet gets one too, the same
inheritance ``full_frame_elements`` has.

**On the first appearance** - once per speaker per reel, on the first
line that speaker really SAYS in that reel (:func:`first_appearances`).
Derived from ``reel_quality_bar.played_speech`` - the reel's lines after
the bad takes are cut, in play order, the same list the caption pass and
the conformance verifier read - never from the master timeline.

A declared speaker who APPEARS in the reel - the proposal's own cast
list, ``moment.speakers`` - but says no attributed line in it still gets
the card (captain, 2026-09-30: the card identifies the person, not the
sentence).  The card opens the reel (:func:`opening_appearances`): the
first such speaker at 0.0s and each further one where the previous
card's hold ends, in the proposal's speaker order.  Where no appearance
is known the skips stand - :data:`NO_LINES_IN_THE_REEL` and
:data:`NO_DECLARED_SPEAKER_SPOKE` - because a card with no anchor would
be taste (AGENTS.md 10.5).

**A motion graphic, not animated words.**  The drawing is
``remotion-subtitles/src/compositions/MotionGraphics``'s ``lower_third``
arm.  This module owns only WHEN it plays, WHO it names and WHERE it
sits; how it is drawn is the composition's.

**The names and titles are DATA.**  Nothing in this file knows any
project's names - not in a constant, a docstring or an example
(AGENTS.md 14).  They are declared at ``effect.speaker_lower_thirds`` in
the project's own ``project.yaml``, the same slot and precedence
``effect.timed_text_overlay`` and ``effect.full_frame_elements`` take
(:func:`project_declaration`, :func:`resolve_declaration`).

**A project that declares none gets none.**  :func:`plan_for_reel`
returns an empty plan with a basis saying WHICH kind of nothing it is
(:data:`BASES`).  Nothing here has a default name, title, colour or hold.

Where the colour comes from
---------------------------
There are no house looks (AGENTS.md 12).  Two declarations answer, in
order (:func:`speaker_colour`):

1. the speaker's own ``colour`` under ``effect.speaker_lower_thirds``;
2. failing that, the accent the project already declares for that
   speaker's captions
   (``pipeline.speaker_subtitle_styles.<speaker>.accentColor``).

Where neither answers, the entry is DROPPED with
:data:`NO_COLOUR_DECLARED`.  Everything else the plan states - the
anchor, the hold, the entrance and exit characters - is declared too,
and a declaration missing one is refused by name in
:func:`declared_speakers` (:class:`SpeakerIdentityError`), so
``resolve_plan``'s ``cut`` fallback never reaches a frame.

Where it sits
-------------
:func:`placement_box` is the safe area with its BOTTOM RAISED so the
graphic cannot land on the caption row.  The strictest safe area governs
(``safe_area.resolve_safe_area``, read rather than restated).  The
caption row is the project's (``subtitle_style.project_caption_row``) or
the engine's own, and the box's bottom is that row lifted by the height
of the tallest caption card this reel really rendered -
:func:`measured_caption_height`, a MEASUREMENT off the rendered cards,
never a prediction from the font size.  No room reads
:data:`NO_ROOM_ABOVE_THE_CAPTIONS`.

The graphic renders FULL CANVAS as its probe and is then BOUND tightly
from its own measured pixels across every frame
(``reel_build._bind_lower_third_tight``) - never from ``mg_tight_box``'s
prediction.  No re-layout, so the copy cannot re-wrap.
:func:`render_findings` then MEASURES the drawn ink back off the
rendered file and refuses a segment whose ink left the box
(:data:`INK_LEFT_THE_BOX`).

``tests/unit/audio/test_speaker_identity.py``.

The captain's note that started this and the measurements behind the
tight binding: docs/evidence/speaker_identity.md.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

#: The slot a project declares its speakers in.  Under ``effect`` beside
#: ``timed_text_overlay`` and ``full_frame_elements``, because all three
#: are copy or artwork the project owns (AGENTS.md 14).
DECLARATION_KEY = "speaker_lower_thirds"

#: What the declaration may carry at its top level.  Complete, and
#: checked on read: a key nothing reads is a look the editor believes
#: shipped.
DECLARATION_KEYS = ("speakers", "anchor", "hold_seconds", "entrance",
                    "exit", "reason")

#: What ONE speaker's entry may carry.  ``name`` and ``title`` are the
#: copy; ``colour`` is this speaker's own, and its absence falls to the
#: caption accent rather than to anything the engine chose.
SPEAKER_KEYS = ("name", "title", "colour", "color")

#: The roster element this plans.  Not a new one: ``lower_third``'s own
#: ``earns_its_place`` already reads "The first time a speaker or a
#: source appears and the audio does not name them", which is this ask
#: written down before it was asked.  A second entry beside it would be
#: two roster rows for one idea, which
#: ``motion_graphics_vocabulary.OUT_OF_VOCABULARY`` exists to prevent.
ELEMENT = "lower_third"

# ── What kind of nothing a reel got ──────────────────────────────────

NOT_DECLARED = "not_declared"
"""The project declares no ``effect.speaker_lower_thirds`` at all."""

NO_DECLARED_SPEAKER_SPOKE = "no_declared_speaker_spoke"
"""It declares speakers and none of them says a word in THIS reel."""

NO_LINES_IN_THE_REEL = "no_lines_in_the_reel"
"""The reel plays no attributed speech, so nobody appears first."""

SPEAKERS_INTRODUCED = "speakers_introduced"
"""Entries were planned."""

BASES = (NOT_DECLARED, NO_DECLARED_SPEAKER_SPOKE, NO_LINES_IN_THE_REEL,
         SPEAKERS_INTRODUCED)

#: Why one speaker's entry was not planned although they were declared.
NO_COLOUR_DECLARED = "no_colour_declared"
NO_ROOM_ABOVE_THE_CAPTIONS = "no_room_above_the_captions"
OUTSIDE_THE_REEL = "outside_the_reel"
INK_LEFT_THE_BOX = "ink_left_the_box"
TRUNCATED_BELOW_READABLE = "truncated_below_readable"

REFUSALS = (NO_COLOUR_DECLARED, NO_ROOM_ABOVE_THE_CAPTIONS,
            OUTSIDE_THE_REEL, INK_LEFT_THE_BOX,
            TRUNCATED_BELOW_READABLE)


class SpeakerIdentityError(ValueError):
    """A declaration that cannot be read as written.

    RAISED rather than dropped, the same choice
    ``full_frame_element`` and ``timed_text_overlay`` make: a lower
    third silently ignored is a name the editor believes the viewer
    saw.
    """


@dataclass
class Introduction:
    """One speaker, named once, at the second they first speak.

    ``line_less`` is the appearance-anchored case: the speaker is in
    the reel's cast list but says no attributed line in it, so the
    card opens the reel instead of landing on a line.  ``says`` is
    then empty, and the record says so rather than carrying a line
    nobody spoke.
    """

    speaker: str
    name: str
    title: str
    colour: str
    colour_basis: str
    at_seconds: float
    says: str
    line_less: bool = False


@dataclass
class SpeakerPlan:
    """What one reel's speaker lower-thirds were, INCLUDING the empty ones.

    Returned even when nothing is drawn, for the reason
    ``explainer_plan.ExplainerPlan`` is: a reel with no lower thirds
    must be able to say WHICH kind of nothing it has.
    """

    reel_name: str
    declared: bool = False
    basis: str = NOT_DECLARED
    introductions: List[Introduction] = field(default_factory=list)
    entries: List[dict] = field(default_factory=list)
    refused: List[dict] = field(default_factory=list)
    box: Optional[Dict[str, int]] = None
    segments: List[dict] = field(default_factory=list)
    # What `motion_graphics_plan.overlapping_pairs` reported on the
    # resolved moments, as the build read it. Set by the caller that
    # resolves - `plan_for_reel` plans in seconds and an overlap is a
    # fact about frames - so a reel whose cards collide says so in its
    # own record rather than nowhere.
    overlaps: List[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "reel": self.reel_name,
            "declared": bool(self.declared),
            "basis": self.basis,
            "box": dict(self.box) if self.box else None,
            "introductions": [
                {"speaker": i.speaker, "name": i.name, "title": i.title,
                 "colour": i.colour, "colour_basis": i.colour_basis,
                 "at_seconds": i.at_seconds, "says": i.says,
                 "line_less": bool(i.line_less)}
                for i in self.introductions],
            "entries": list(self.entries),
            "refused": list(self.refused),
            # Pairs of resolved moments drawn through each other, as
            # `motion_graphics_plan.overlapping_pairs` reported them on
            # the run that built this reel. Empty is the common case;
            # a pair here is the detector having caught one.
            "overlaps": [dict(pair) for pair in self.overlaps],
            # What was really RENDERED and placed, in the shape the
            # explainer's record takes, because this is what a check
            # grades the built timeline against. A segment the renderer
            # or the measurement refused never appears here, so nothing
            # looks for an item the build did not place.
            "segments": [
                {"overlay_path": s.get("overlay_path"),
                 "segment_id": s.get("segment_id"),
                 "placement_label": s.get("placement_label"),
                 "timeline_start": s.get("timeline_start"),
                 "timeline_end": s.get("timeline_end"),
                 "total_frames": s.get("total_frames"),
                 "measured_box": s.get("measured_box"),
                 "elements": list(s.get("elements") or []),
                 "geometry": s.get("geometry"),
                 "tight_box": s.get("tight_box"),
                 "tight_fallback": s.get("tight_fallback") or ""}
                for s in self.segments
            ],
        }


# ── The declaration ──────────────────────────────────────────────────

def project_declaration(project_folder: Optional[str]):
    """``effect.speaker_lower_thirds`` off a project.yaml, or None.

    None means the project declared nothing, which is different from an
    empty mapping: a project may deliberately declare an empty speaker
    table to say "this series names nobody", and that is a statement
    rather than an absence.
    """
    if not project_folder:
        return None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return None
    import yaml
    with open(project_yaml, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    effect = config.get("effect") or {}
    if not isinstance(effect, dict):
        raise SpeakerIdentityError(
            f"{project_yaml}: `effect` must be a mapping, got "
            f"{type(effect).__name__}")
    if DECLARATION_KEY not in effect:
        return None
    return effect[DECLARATION_KEY]


def resolve_declaration(brand_effect: Optional[dict],
                        project_folder: Optional[str]):
    """The declaration to plan from: the PROJECT's speakers win.

    Same precedence and same reason as
    ``full_frame_element.resolve_declaration``.  The WHOLE slot is
    replaced rather than merged key by key: half a speaker table from a
    brand template and half from a project is a cast nobody assembled.

    Returns None where neither declares one.
    """
    declared = project_declaration(project_folder)
    if declared is not None:
        return declared
    from_template = (brand_effect or {}).get(DECLARATION_KEY)
    return from_template


def declared_speakers(declaration: Any,
                      project_folder: Optional[str] = None) -> dict:
    """Normalise a declaration into ``{speaker_label: {...}}`` plus timing.

    Returns ``{"speakers": {...}, "anchor": str, "hold_seconds": float,
    "entrance": str, "exit": str}``.

    Every one of those four timing and motion values is REQUIRED.  They
    are not defaulted here because a hold the engine picked is the
    engine deciding how long a client's name is on screen, and an
    entrance the engine picked is the engine choosing the gesture - both
    are taste (AGENTS.md 10.5).  A declaration missing one is refused by
    name rather than completed.

    Anything malformed RAISES.
    """
    from library.tools import motion_graphics_plan as mg

    if declaration is None:
        raise SpeakerIdentityError(
            "declared_speakers called with no declaration; the caller "
            "should have taken the not_declared path.")
    if not isinstance(declaration, dict):
        raise SpeakerIdentityError(
            f"effect.{DECLARATION_KEY} must be a mapping, got "
            f"{type(declaration).__name__}")
    unknown = sorted(set(declaration) - set(DECLARATION_KEYS))
    if unknown:
        raise SpeakerIdentityError(
            f"effect.{DECLARATION_KEY} declares {unknown}, which nothing "
            f"reads. It takes {list(DECLARATION_KEYS)}.")

    for required in ("anchor", "hold_seconds", "entrance", "exit"):
        if declaration.get(required) in (None, ""):
            raise SpeakerIdentityError(
                f"effect.{DECLARATION_KEY} declares no {required!r}. All "
                f"of anchor, hold_seconds, entrance and exit are required: "
                f"a hold or a gesture the ENGINE picked would be the engine "
                f"deciding how a client's name arrives (AGENTS.md 10.5).")

    anchor = str(declaration["anchor"]).strip().lower()
    if anchor not in mg.ANCHORS:
        raise SpeakerIdentityError(
            f"effect.{DECLARATION_KEY}.anchor is {anchor!r}, which is not "
            f"one of the vocabulary's positions: {list(mg.ANCHORS)}.")
    for key in ("entrance", "exit"):
        character = str(declaration[key]).strip().lower()
        if character not in mg.MOTION_CHARACTERS:
            raise SpeakerIdentityError(
                f"effect.{DECLARATION_KEY}.{key} is {character!r}, which is "
                f"not one of {list(mg.MOTION_CHARACTERS)}.")
    try:
        hold = float(declaration["hold_seconds"])
    except (TypeError, ValueError):
        raise SpeakerIdentityError(
            f"effect.{DECLARATION_KEY}.hold_seconds must be a number, got "
            f"{declaration['hold_seconds']!r}")
    if hold <= 0:
        raise SpeakerIdentityError(
            f"effect.{DECLARATION_KEY}.hold_seconds must be positive, got "
            f"{hold!r}")

    raw = declaration.get("speakers")
    if raw is None:
        raw = {}
    if not isinstance(raw, dict):
        raise SpeakerIdentityError(
            f"effect.{DECLARATION_KEY}.speakers must be a mapping of the "
            f"transcript's own speaker label to that speaker's name and "
            f"title, got {type(raw).__name__}")

    speakers: Dict[str, dict] = {}
    for label, entry in raw.items():
        if not isinstance(entry, dict):
            raise SpeakerIdentityError(
                f"effect.{DECLARATION_KEY}.speakers[{label!r}] must be a "
                f"mapping, got {type(entry).__name__}")
        unknown = sorted(set(entry) - set(SPEAKER_KEYS))
        if unknown:
            raise SpeakerIdentityError(
                f"effect.{DECLARATION_KEY}.speakers[{label!r}] declares "
                f"{unknown}, which nothing reads. It takes "
                f"{list(SPEAKER_KEYS)}.")
        name = str(entry.get("name") or "").strip()
        if not name:
            raise SpeakerIdentityError(
                f"effect.{DECLARATION_KEY}.speakers[{label!r}] declares no "
                f"`name`. A lower third with nothing to say names nobody.")
        speakers[str(label)] = {
            "name": name,
            "title": str(entry.get("title") or "").strip(),
            "colour": str(entry.get("colour")
                          or entry.get("color") or "").strip(),
        }
    return {
        "speakers": speakers,
        "anchor": anchor,
        "hold_seconds": hold,
        "entrance": str(declaration["entrance"]).strip().lower(),
        "exit": str(declaration["exit"]).strip().lower(),
    }


def speaker_colour(label: str, declared: dict,
                   project_folder: Optional[str]) -> Tuple[str, str]:
    """The colour this speaker's lower third draws in, and where from.

    Two project declarations answer, in order, and NOTHING else does:
    the speaker's own ``colour`` under ``effect.speaker_lower_thirds``,
    then the accent this project already declared for that speaker's
    captions.  ``("", "")`` where neither did - the caller drops the
    entry, because a colour the engine chose is a house look and there
    are none (AGENTS.md 12).
    """
    stated = (declared.get("colour") or "").strip()
    if stated:
        return stated, f"effect.{DECLARATION_KEY}.speakers[{label!r}].colour"
    from library.tools.subtitle_style import speaker_style_overrides
    accent = str((speaker_style_overrides(project_folder, label) or {})
                 .get("accentColor") or "").strip()
    if accent:
        return accent, (f"pipeline.speaker_subtitle_styles[{label!r}]"
                        f".accentColor")
    return "", ""


# ── First appearance ─────────────────────────────────────────────────

def first_appearances(lines: Sequence[dict],
                      speakers: Sequence[str]) -> List[dict]:
    """The first line each declared speaker says, in the order they arrive.

    ``lines`` is ``reel_quality_bar.played_speech``'s shape - the reel's
    own lines in the order the reel plays them, each carrying
    ``speaker`` and ``reel_start``.

    ONCE per speaker, which is the whole of "on the first appearance":
    a speaker who comes back six times is introduced on the first of
    the six and on none of the others.  A speaker the project did not
    declare is not introduced at all - the declaration is the cast
    list, so an unlabelled or third voice is passed over in silence
    rather than named from a guess.
    """
    wanted = {str(s) for s in speakers}
    seen = set()
    out: List[dict] = []
    for line in sorted(lines or [],
                       key=lambda l: float(l.get("reel_start") or 0.0)):
        label = line.get("speaker")
        if label is None:
            continue
        label = str(label)
        if label not in wanted or label in seen:
            continue
        seen.add(label)
        out.append({
            "speaker": label,
            "at_seconds": round(float(line.get("reel_start") or 0.0), 3),
            "says": str(line.get("text") or "").strip(),
        })
    return out


def opening_appearances(appearing: Sequence[str],
                        speakers: Sequence[str],
                        already_introduced: Sequence[str],
                        hold_seconds: float) -> List[dict]:
    """The declared speakers who appear but say nothing, and when.

    ``appearing`` is the reel's own cast list - ``moment.speakers`` at
    the call site - in the proposal's order.  A label is kept only
    where the project declared it (the declaration is the cast list,
    so an undeclared voice is passed over in silence rather than
    named from a guess) and where ``already_introduced`` does not
    carry it (speech anchors first; one card per speaker per reel).

    The timing is the reel's opening, hold-spaced: the first such
    speaker at 0.0s, each further one where the previous card's hold
    ends.  The hold is the project's declared number, the order is
    the proposal's and 0.0s is the reel's own start - nothing here is
    chosen.  ``says`` is empty: there is no line, and the record says
    so rather than borrowing one.
    """
    declared = {str(s) for s in speakers}
    introduced = {str(s) for s in already_introduced}
    hold = max(0.0, float(hold_seconds or 0.0))
    out: List[dict] = []
    seen = set()
    for label in (str(s) for s in (appearing or ())):
        if label in seen or label in introduced or label not in declared:
            continue
        seen.add(label)
        out.append({
            "speaker": label,
            "at_seconds": round(len(out) * hold, 3),
            "says": "",
            "line_less": True,
        })
    return out


# ── Where it sits ────────────────────────────────────────────────────

def measured_caption_height(subtitle_segments: Sequence[dict]) -> Optional[int]:
    """How tall this reel's tallest caption card really rendered, in px.

    Read off the caption segments' OWN measured tight boxes.  Those
    boxes are measured from a decoded probe render rather than predicted
    from the font metrics (step 4.05, "the tight canvas is MEASURED off
    a decoded probe render, never predicted"), so this is a measurement
    of the pixels a viewer of THIS reel will see, not an estimate of
    what two lines of 58px type ought to come to.

    None where no caption carried a measured box - a run that skipped
    captions, or a project rendering them full canvas.  The caller then
    has nothing to raise the box by and SAYS so rather than assuming a
    height.
    """
    heights = [int(((segment or {}).get("tight_box") or {}).get("height") or 0)
               for segment in (subtitle_segments or [])]
    heights = [h for h in heights if h > 0]
    return max(heights) if heights else None


def placement_box(project_folder: Optional[str], width: int, height: int,
                  caption_height: Optional[int],
                  reel_name: Optional[str] = None) -> Dict[str, Any]:
    """The insets the graphic is positioned in, and what made them.

    The strictest safe area governs (one master render serves Reels,
    TikTok and Shorts), so the left, top and right are
    ``safe_area.resolve_safe_area``'s and are not restated here.

    The BOTTOM is raised to clear the captions: to the caption row this
    project declared - or the engine's row where it declared none -
    lifted by the tallest caption card this reel measured.  Where no
    caption was measured the row itself is the floor and the reason
    says so, which is a narrower box than the truth rather than a
    wider one.

    ``reel_name`` prefers that reel's declared caption row
    (``external/reel_caption_row.json``) over the project value, so a
    reel whose captions moved clears its own row rather than the
    project's.  None reads today's answer exactly.

    Returns ``{"insets": {...}, "caption_row": int, "basis": str}``.
    The insets are the shape ``MotionGraphics``'s ``safeArea`` prop
    takes, so the composition's own nine-position anchor grid resolves
    against THIS box with no second positioning mechanism - the same
    trick ``explainer_plan.band_insets`` plays.
    """
    from library.tools.safe_area import resolve_safe_area
    from library.tools.subtitle_style import (
        CAPTION_LIFT_PX,
        caption_row_px,
        project_caption_row,
    )

    safe = resolve_safe_area(project_folder=project_folder,
                             width=width, height=height)
    insets = safe.as_props()

    declared_row = project_caption_row(project_folder,
                                         reel_name=reel_name)
    if declared_row is None:
        row = int(height) - int(insets["bottom"]) - CAPTION_LIFT_PX
        row_basis = "the engine's caption row"
    else:
        row = caption_row_px(declared_row, height)
        if reel_name is not None:
            row_basis = (f"reel {reel_name!r}'s declared caption row "
                         f"{declared_row!r}")
        else:
            row_basis = (f"the project's declared caption row "
                         f"{declared_row!r}")

    if caption_height:
        floor = row - int(caption_height)
        basis = (f"{row_basis} ({row}px) lifted by this reel's tallest "
                 f"measured caption card ({int(caption_height)}px)")
    else:
        floor = row
        basis = (f"{row_basis} ({row}px); no caption card carried a "
                 f"measured box on this reel, so the row itself is the "
                 f"floor")

    insets["bottom"] = max(0, int(height) - int(floor))
    return {"insets": insets, "caption_row": int(row), "basis": basis,
            "floor": int(floor)}


def box_has_room(insets: Dict[str, int], width: int, height: int) -> bool:
    """Is there any rectangle left to draw in?"""
    return (int(width) - int(insets.get("left", 0))
            - int(insets.get("right", 0)) > 0
            and int(height) - int(insets.get("top", 0))
            - int(insets.get("bottom", 0)) > 0)


# ── The plan ─────────────────────────────────────────────────────────

def plan_for_reel(reel_name: str, lines: Sequence[dict],
                  reel_seconds: float,
                  project_folder: Optional[str],
                  brand_effect: Optional[dict] = None,
                   *,
                   width: int, height: int,
                   subtitle_segments: Sequence[dict] = (),
                   appearing_speakers: Sequence[str] = ()) -> SpeakerPlan:
    """One reel's speaker lower-thirds, as plan entries and a record.

    The entries are ``motion_graphics_plan.resolve_plan``'s own shape -
    this module plans, it does not draw and it does not resolve.  The
    caller hands them straight to that resolver, so a lower third takes
    exactly the path every other motion graphic takes.

    Every card holds the declared hold UNLESS the next speaker's card
    begins first, in which case it is truncated to end there - two cards
    cannot occupy one row at one time (:func:`truncate_to_next`).

    ``appearing_speakers`` is the reel's own cast list - the
    proposal's ``moment.speakers`` at the call site.  A declared
    speaker in it who says no attributed line in the reel still gets
    a card opening the reel (:func:`opening_appearances`); without
    that list there is no appearance to anchor to and the old skips
    stand.  A project declaring nothing returns a plan with no
    entries and ``basis == NOT_DECLARED``.  That is the whole of "a
    project that declares none gets no lower-thirds": there is no
    crash and no placeholder, and the reel builds as it built before
    this existed.
    """
    plan = SpeakerPlan(reel_name=reel_name)

    declaration = resolve_declaration(brand_effect, project_folder)
    if declaration is None:
        return plan

    declared = declared_speakers(declaration, project_folder)
    plan.declared = True
    appearances = first_appearances(lines, declared["speakers"].keys())
    openings = opening_appearances(
        appearing_speakers, declared["speakers"].keys(),
        [a["speaker"] for a in appearances], declared["hold_seconds"])
    if not appearances and not openings:
        plan.basis = NO_LINES_IN_THE_REEL if not lines \
            else NO_DECLARED_SPEAKER_SPOKE
        return plan

    box = placement_box(project_folder, width, height,
                        measured_caption_height(subtitle_segments),
                        reel_name=reel_name)
    plan.box = dict(box["insets"])
    plan.box["_basis"] = box["basis"]
    if not box_has_room(box["insets"], width, height):
        for appearance in list(openings) + list(appearances):
            plan.refused.append({
                "speaker": appearance["speaker"],
                "reason": NO_ROOM_ABOVE_THE_CAPTIONS,
                "detail": box["basis"]})
        plan.basis = NO_DECLARED_SPEAKER_SPOKE
        return plan

    # The openings go first, so a card anchored to speech wins a tie
    # against one anchored to appearance: `truncate_to_next` is stable,
    # and the earlier of two cards starting on the same second is the
    # one it shortens away.
    for appearance in list(openings) + list(appearances):
        label = appearance["speaker"]
        entry = declared["speakers"][label]
        colour, colour_basis = speaker_colour(label, entry, project_folder)
        if not colour:
            plan.refused.append({
                "speaker": label,
                "reason": NO_COLOUR_DECLARED,
                "detail": (f"{label!r} declares no `colour` and this "
                           f"project declares no "
                           f"pipeline.speaker_subtitle_styles[{label!r}]"
                           f".accentColor. The engine has no colour to "
                           f"offer (AGENTS.md 12).")})
            continue
        start = float(appearance["at_seconds"])
        if start >= float(reel_seconds or 0.0):
            if appearance.get("line_less"):
                detail = (f"appears in this reel but says no attributed "
                          f"line in it, so the card would open at "
                          f"{start}s of a {reel_seconds}s reel")
            else:
                detail = (f"first heard at {start}s of a "
                          f"{reel_seconds}s reel")
            plan.refused.append({
                "speaker": label,
                "reason": OUTSIDE_THE_REEL,
                "detail": detail})
            continue
        plan.introductions.append(Introduction(
            speaker=label, name=entry["name"], title=entry["title"],
            colour=colour, colour_basis=colour_basis,
            at_seconds=start, says=appearance["says"],
            line_less=bool(appearance.get("line_less"))))
        plan.entries.append(entry_for(
            introduction=plan.introductions[-1], declared=declared))

    # Two cards, one row, one moment: the earlier card ends where the
    # next speaker's card begins. Mechanical, not taste - two things
    # cannot occupy one row at one time - and truncation is the
    # conservative act `edit_depth` states for an ending.
    truncate_to_next(plan)

    plan.basis = SPEAKERS_INTRODUCED if plan.entries \
        else NO_DECLARED_SPEAKER_SPOKE
    return plan


def entry_for(introduction: Introduction, declared: dict) -> dict:
    """One ``lower_third`` plan entry, in ``resolve_plan``'s own shape.

    The copy travels as RUNS with type roles, which is how every other
    copy-carrying element states its hierarchy: the name is ``display``
    and the title ``supporting``.  A speaker with no title declared
    sends one run, and the composition builds one line - the second
    part of the construction is simply not there, rather than there and
    empty.

    ``data.construction`` is what turns this from a text block into the
    staged build the captain asked for; the composition owns every
    number in it and this module states none.
    """
    runs = [{"text": introduction.name, "type_role": "display"}]
    if introduction.title:
        runs.append({"text": introduction.title,
                     "type_role": "supporting"})
    if introduction.line_less:
        why = (f"{introduction.speaker!r} appears in this reel but says "
               f"no attributed line in it, so the card opens the reel "
               f"at {introduction.at_seconds}s")
    else:
        why = (f"first appearance of {introduction.speaker!r} in this "
               f"reel, at {introduction.at_seconds}s")
    return {
        "element": ELEMENT,
        "anchor": declared["anchor"],
        "row": 0,
        "copy": runs,
        "color": introduction.colour,
        "entrance": declared["entrance"],
        "exit": declared["exit"],
        "start_seconds": introduction.at_seconds,
        "duration_seconds": declared["hold_seconds"],
        "why": why,
        "data": {
            # WHAT to build, not how. The composition's `lower_third`
            # arm reads this and draws the staged construction; without
            # it the arm keeps the flat attribution block it drew
            # before, so every existing caller's graphic is unchanged.
            "construction": "staged_rule",
            "speaker": introduction.speaker,
            "colour_basis": introduction.colour_basis,
        },
    }


# ── What was drawn ───────────────────────────────────────────────────

def truncate_to_next(plan: SpeakerPlan) -> None:
    """End each card where the next speaker's card begins, in place.

    Every entry shares one anchor and one row (see :func:`entry_for`),
    so two cards whose spans overlap are on screen together in one
    layout slot: the composition stacks them, then the earlier clears
    and the later jumps to the edge.  A card therefore ends at the
    earlier of its declared hold and the next card's start.  The hold
    itself is never shortened - that number is the project's, and only
    the project changes it.

    A truncation that would leave less than the pipeline's own
    readability floor for timed on-screen text
    (``manifest_validator.MIN_CAPTION_DISPLAY_SECONDS``) is REFUSED
    with :data:`TRUNCATED_BELOW_READABLE` rather than shipped: a card
    nobody can read is not a name the viewer saw.  The refusal SAYS the
    gap, the hold and the floor, so the run that made it also says
    which line the project has to move.  That floor is reused, not
    chosen: it is the same number the manifest enforces on caption
    cards and the conformance verifier grades placed items against.

    ``plan.introductions`` and ``plan.entries`` are parallel - each
    loop of :func:`plan_for_reel` appends one of each or neither - so a
    refused entry removes its introduction at the same index.
    """
    from library.tools.manifest_validator import MIN_CAPTION_DISPLAY_SECONDS

    order = sorted(range(len(plan.entries)),
                   key=lambda i: float(plan.entries[i]["start_seconds"]))
    doomed: List[int] = []
    for position, index in enumerate(order):
        if position + 1 >= len(order):
            continue
        entry = plan.entries[index]
        following = plan.entries[order[position + 1]]
        start = float(entry["start_seconds"])
        hold = float(entry["duration_seconds"])
        next_start = float(following["start_seconds"])
        if start + hold <= next_start:
            continue
        shortened = round(next_start - start, 3)
        speaker = str((entry.get("data") or {}).get("speaker") or "?")
        if shortened < MIN_CAPTION_DISPLAY_SECONDS:
            doomed.append(index)
            plan.refused.append({
                "speaker": speaker,
                "reason": TRUNCATED_BELOW_READABLE,
                "detail": (
                    f"first heard at {start}s and the next introduction "
                    f"starts at {next_start}s, so the {hold}s hold would "
                    f"overlap it by "
                    f"{round(start + hold - next_start, 3)}s; ending it "
                    f"there leaves {shortened}s, under the "
                    f"{MIN_CAPTION_DISPLAY_SECONDS}s readability floor "
                    f"for timed on-screen text "
                    f"(manifest_validator.MIN_CAPTION_DISPLAY_SECONDS). "
                    f"Refusing rather than shipping a card nobody can "
                    f"read; shortening the declared hold is the "
                    f"project's line to change.")})
            continue
        entry["duration_seconds"] = shortened
        entry["why"] = (
            f"{entry.get('why') or ''} Truncated to {shortened}s from "
            f"{hold}s: ends where the next speaker's card begins at "
            f"{next_start}s, because two cards cannot occupy one row "
            f"at one time.")
        entry.setdefault("data", {})["truncated_for_next"] = {
            "hold_seconds": hold,
            "duration_seconds": shortened,
            "next_starts_at": next_start,
        }
    for index in sorted(doomed, reverse=True):
        del plan.entries[index]
        del plan.introductions[index]


def render_findings(measured: dict, insets: Dict[str, int],
                    width: int, height: int) -> List[dict]:
    """Did the ink land in the box?  Read off the RENDER, not the plan.

    ``measured`` is ``explainer_plan.measure_render``'s reading of the
    rendered overlay - the alpha channel's row and column extents.
    Reused rather than respelled, so the one measurement of "where is
    the ink" in this engine stays one.

    The segment renders FULL CANVAS, so those extents are already
    delivery-frame coordinates and no transform stands between the
    reading and the placement.  That is why this check means something:
    a tight-boxed graphic would be measured on its own small canvas and
    the answer would depend on a predicted Pan/Tilt.

    ``error`` REFUSES the segment; ``warning`` is said and placed.
    """
    findings: List[dict] = []
    rows = (measured or {}).get("rows")
    cols = (measured or {}).get("cols")
    if not rows or not cols:
        findings.append({
            "severity": "error", "code": "nothing_drawn",
            "message": "the rendered overlay has no ink on it at all"})
        return findings

    frame = (measured or {}).get("frame") or [width, height]
    if list(frame) != [int(width), int(height)]:
        findings.append({
            "severity": "warning", "code": "not_the_delivery_frame",
            "message": (f"measured on a {frame[0]}x{frame[1]} canvas, not "
                        f"the {width}x{height} delivery frame, so these "
                        f"extents are not frame coordinates")})
        return findings

    top, bottom = int(rows[0]), int(rows[1])
    left, right = int(cols[0]), int(cols[1])
    floor = int(height) - int(insets.get("bottom", 0))
    ceiling = int(insets.get("top", 0))
    left_edge = int(insets.get("left", 0))
    right_edge = int(width) - int(insets.get("right", 0))

    if bottom > floor:
        findings.append({
            "severity": "error", "code": INK_LEFT_THE_BOX,
            "message": (f"ink reaches row {bottom}, below the box floor "
                        f"{floor} - it would sit on the caption row")})
    if top < ceiling:
        findings.append({
            "severity": "error", "code": INK_LEFT_THE_BOX,
            "message": (f"ink reaches row {top}, above the safe-area top "
                        f"{ceiling}")})
    if left < left_edge or right > right_edge:
        findings.append({
            "severity": "error", "code": INK_LEFT_THE_BOX,
            "message": (f"ink spans columns {left}..{right}, outside the "
                        f"safe columns {left_edge}..{right_edge}")})
    return findings


def measured_box(measured: dict) -> Optional[Tuple[int, int, int, int]]:
    """The drawn ink as ``(left, top, right, bottom)``, or None."""
    rows = (measured or {}).get("rows")
    cols = (measured or {}).get("cols")
    if not rows or not cols:
        return None
    return (int(cols[0]), int(rows[0]), int(cols[1]), int(rows[1]))


# ── The record ───────────────────────────────────────────────────────

PLANS_FILE = "speaker_lower_thirds.json"

#: What the reel build calls the row these land on, and what the
#: conformance verifier files items by. The NAME, not an index: a reel's
#: row index depends on how many angles, layers and caption rows it has,
#: and `timeline_layout` is what decides it
#: (`timeline_layout.MOTION_GRAPHICS`). Named here once so the placement
#: and the check cannot disagree about it.
TRACK_NAME = "Motion Graphics"

#: The stem `reel_build` gives a rendered lower-third's PLACEMENT. The
#: file itself is content-keyed (`render_cache`), so this is what a
#: reader sees on the timeline rather than what is on disk.
RENDER_PREFIX = "lt_"


def segment_name(reel_name: str, index: int) -> str:
    """The placement label one reel's lower-third render is placed under.

    The canonical spelling of the `lt_<reel-slug>_<index>` name
    `reel_build` passes as `segment_name=` to the renderer: which
    placing the file serves, never the file's identity. Kept equal to
    `reel_build._reel_slug`'s rendering by
    `tests/unit/captions/test_overlay_intent.py` - two spellings of one name is how
    a re-key maps a stale digest onto the wrong placing, so the
    equality is pinned rather than trusted.
    """
    import re

    slug = re.sub(r"[^A-Za-z0-9]+", "_",
                  str(reel_name or "reel")).strip("_").lower()[:48]
    return f"{RENDER_PREFIX}{slug or 'reel'}_{int(index):02d}"


def write_plans(project_folder: str, plans: Sequence[SpeakerPlan]) -> str:
    """Record what every reel's lower thirds were, including the empty ones.

    Written to ``pipeline_output/review/`` beside the explainer plans and
    for the same reason: a reader grading a built timeline needs the plan
    it was built from, and a reel that drew nothing must be able to say
    which kind of nothing (:data:`BASES`).

    MERGED, not overwritten, exactly as ``explainer_plan.write_plans`` is:
    a partial (`only`) build replaces the reels it touched and leaves
    every other reel's record standing, because overwriting would delete
    the record for timelines this build never looked at.
    """
    import json

    from library.tools.project_layout import Area, ProjectLayout

    out_dir = ProjectLayout(project_folder).write_dir(Area.REVIEW)
    path = os.path.join(str(out_dir), PLANS_FILE)
    stored: dict = {"format": "speaker_lower_thirds/1", "plans": []}
    if os.path.exists(path):
        try:
            with open(path, "r", encoding="utf-8") as handle:
                stored = json.load(handle) or stored
        except (OSError, ValueError):
            stored = {"format": "speaker_lower_thirds/1", "plans": []}
    touched = {plan.reel_name for plan in (plans or ())}
    kept = [p for p in (stored.get("plans") or [])
            if str(p.get("reel")) not in touched]
    kept.extend(plan.as_dict() for plan in (plans or ()))
    stored["plans"] = kept
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)
    return path


def plans_path(project_folder: str) -> str:
    """Where :func:`write_plans` puts the record."""
    from library.tools.project_layout import Area, ProjectLayout
    return os.path.join(
        str(ProjectLayout(project_folder).write_dir(Area.REVIEW)), PLANS_FILE)


def read_plans(project_folder: str) -> dict:
    """What the build recorded, or ``{}`` when it recorded nothing."""
    import json
    path = plans_path(project_folder)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return json.load(handle) or {}
    except (OSError, ValueError):
        return {}


def rename_plan_reels(project_folder: str, mapping: dict) -> None:
    """Rename ``plans[].reel`` in the recorded lower-third plans.

    The staging half of promotion, and the exact mirror of
    ``explainer_plan.rename_plan_reels``: a staged build records its
    lower thirds under the STAGING container so F24 grades the
    staging, and promotion renames the claim to the final timeline
    name.  Without it the record is orphaned under a container that no
    longer exists and :func:`plan_for` answers ``None`` for a reel that
    really has a plan - which reads to F24 as items nothing accounts
    for.  Measured 2026-09-12 on the first beside build of Reel 01:
    two lower thirds placed on V7, F24 ERROR *"2 item(s) on the
    lower-third row (V7) and this reel has no recorded speaker
    lower-third plan at all"*.

    A plan the previous build left under the final name is REPLACED,
    never kept beside the renamed one.  Reels outside ``mapping`` are
    untouched, and no file yet is a no-op.
    """
    import json

    from library.tools.project_layout import Area, ProjectLayout
    if not mapping:
        return
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)), PLANS_FILE)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    finals = set(mapping.values())
    plans = [plan for plan in (payload.get("plans") or [])
             if plan.get("reel") not in finals]
    for plan in plans:
        if plan.get("reel") in mapping:
            plan["reel"] = mapping[plan["reel"]]
    payload["plans"] = plans
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def drop_plan_reels(project_folder: str, names) -> None:
    """Remove recorded lower-third plans for the named reels.

    The gate-fail half of a refused staging, mirroring
    ``explainer_plan.drop_plan_reels``: no record may survive for a
    container that is about to be deleted.  Absent file or absent
    names are no-ops.
    """
    import json

    from library.tools.project_layout import Area, ProjectLayout
    drop = set(names or ())
    if not drop:
        return
    path = os.path.join(
        str(ProjectLayout(project_folder).read_dir(Area.REVIEW)), PLANS_FILE)
    if not os.path.isfile(path):
        return
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle)
    payload["plans"] = [plan for plan in (payload.get("plans") or [])
                        if plan.get("reel") not in drop]
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, indent=2)


def plan_for(plans: Optional[dict], reel_name: str) -> Optional[dict]:
    """The recorded plan for one reel BY NAME, or None.

    None means this build recorded nothing for this reel - which is what
    a reel built before speaker lower thirds existed looks like, and the
    conformance check returns nothing rather than grading a correct reel
    against an absence.
    """
    for record in ((plans or {}).get("plans") or []):
        if str(record.get("reel")) == str(reel_name):
            return record
    return None
