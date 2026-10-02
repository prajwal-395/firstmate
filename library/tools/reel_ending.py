"""Where a reel ENDS, and what plays over its tail.

This module is the `ending` row of `library/tools/edit_depth.py`: "this
reel ends at the end of shot X" and "element Y plays over the tail" are
not properties of a keep range, of the look, or of whichever clip sorts
last.  Extending a keep range to give an ending room crosses the master's
own cut and admits the NEXT shot, and `reel_look.power_effects` then arms
the tail element on that shot - so neither decision is expressed that way.

A declared ending TRUNCATES, never extends
------------------------------------------
`apply_ending` drops every range after the last played span that speaks
the anchor, and that range's new end is `min(its current end, the ending
shot's own end)`.  A reel cannot acquire a shot it did not plan by asking
for breathing room.  It returns `(ranges, report)` with `applied`, `held`
and `stale`; with no ending the ranges pass
through untouched and nothing is reported.

What a declaration says
-----------------------
`<project>/external/reel_ending.json` (`ENDING_FILENAME`), checked and
never asserted (`validate_endings`, raising `ReelEndingError`)::

    {"version": 1,
     "endings": [{"reel": "Reel 13 - the-accounting-firm-ai-called",
                  "ends_on": {"anchor_phrase": "the link's in our bio"},
                  "tail_element": "tv_power_tail",
                  "reason": "captain 2026-09-11 marker @1909"}]}

`reel` is matched against the reel's timeline name by prefix, so a
staging suffix names the same reel as the promoted timeline.

`ends_on.anchor_phrase` is the SPOKEN WORDS the reel ends on, anchored
into the master transcript the way `span_retime` anchors a trim
(`library/tools/captain_edits.py`).  The shot carrying those words is the
ending shot.

`tail_element` names what DRAWS over the tail, from `TAIL_ELEMENTS`
(`tv_power_tail`, `none`).  It is a name, never a magnitude: how long a
switch-off takes is `library/tools/tv_power.py`'s declaration and a
project's own `tv_frame` override (AGENTS.md 10.5).  `"none"` is the
absence of decoration, not a choice of it.

The room the element needs is CHECKED.  `assert_tail_fits` raises
`TailElementHasNoRoom` when the ending shot is shorter than its declared
tail element, naming both counts, and returns its measurement on a
passing build too.  `treatment_verify` quietly undoes what will not draw
at comp time; a declared element that will not draw stops the build
instead.

Holding the last frame
----------------------
`tail_hold`, from `TAIL_HOLDS`, decides WHERE the tail element draws.
`"none"` draws it over the live tail of the ending shot.  `"freeze"`
HOLDS the ending shot's last frame for exactly as long as the element
needs (`tail_room_frames`) and draws it over the held frames, so the
animation begins after the last word.  The hold's length is the
element's own, so a freeze always fits and `assert_tail_fits` says so.
`plan_freeze`, `render_freeze` (a movie of one repeated frame at the
SOURCE's resolution, named `FREEZE_PREFIX`, rendered once per source,
frame and length) and `freeze_placement` build it.  The freeze is
PICTURE ONLY: audio is not held.

The freeze belongs to the CALL TO ACTION, not to a list of reels
--------------------------------------------------------------
The captain's instruction covers every reel that uses a CTA "or will be
using this CTA" (2026-09-11).  So the ending is INHERITED:
`cta_default_ending` gives any reel whose plan closes on a
`reel_proposal.CallToAction` the tail element `CTA_TAIL_ELEMENT` with
hold `CTA_TAIL_HOLD`, anchored on the CTA's closing words.  It hangs on
the TYPE, not on a passage or a speaker - reels close on different CTA
passages and speakers.  `resolve_ending` returns the declared ending if
there is one, else the inherited one: a per-reel entry OVERRIDES the
inheritance, never supplies it.  A reel whose plan declares no call to
action inherits nothing and builds as before.

An inherited ending also plays the closing BREATH: `closing_breath_end`
moves the end OUT into the CTA's trailing silence, because an aligner's
word boundary is not where the word stops being heard.  It is bounded by
the ending shot's own end and by the next spoken word, so it can admit
neither the next shot nor the next word.  A declaration stays
truncate-only.  `tests/test_reel_ending_cta_default.py` fails the moment
a newly planned reel stops inheriting the freeze.

What this module deliberately cannot say
----------------------------------------
It cannot hold a shot PAST the master's own cut to make tail room: those
frames exist but nothing in the pipeline has judged them.  The freeze
uses only a frame the reel already plays.  A tail that does not fit
inside the shot and declares no freeze is a refusal here.

`tests/test_reel_ending.py`, `tests/test_orphan_wiring.py`.

The incidents and rulings behind these rules (Reel 13's extended keep
range and lost switch-off, the freeze ruling, the four CTA reels and the
clipped "bio."): docs/evidence/reel_ending.md.
"""

from __future__ import annotations

import dataclasses
import json
import os
from dataclasses import dataclass
from typing import Optional

#: Schema version this reader honours.
ENDING_VERSION = 1

#: The file basename, under the project's external-inputs area.
ENDING_FILENAME = "reel_ending.json"

#: What a rendered hold is CALLED. The owner names its own artefact, so
#: a reader that must recognise one asks here rather than spelling the
#: convention itself (`freeze_items` below is that reader).
FREEZE_PREFIX = "reel_freeze_"

#: What may draw over a reel's tail. A NAME, never a magnitude - the
#: timings live in `library/tools/tv_power.py` and in whatever the
#: project's own `tv_frame` declaration overrides there. `none` is the
#: absence of decoration (AGENTS.md 10.5), which is why it may sit in a
#: vocabulary that otherwise states no taste.
TAIL_ELEMENTS = ("tv_power_tail", "none")

#: How the tail element is given room. `none` draws it over the live
#: tail of the ending shot; `freeze` holds that shot's last frame for
#: the element's own length and draws it over the held frames. Neither
#: is a magnitude and neither is taste: one is the absence of a hold,
#: the other takes its length from the element it serves.
TAIL_HOLDS = ("none", "freeze")

#: What a reel INHERITS from the call to action it closes on, and the
#: only place these two values are stated. A per-reel declaration in
#: `external/reel_ending.json` OVERRIDES them; it does not supply them.
CTA_TAIL_ELEMENT = "tv_power_tail"
CTA_TAIL_HOLD = "freeze"

#: How many of the call to action's closing words anchor the inherited
#: ending. Long enough to name one moment in a 44-minute episode, short
#: enough to survive a re-cut of the words before it. Eleven is what the
#: captain's own Reel 13 declaration wrote by hand.
CTA_ANCHOR_WORDS = 11

#: The fewest words an inherited anchor may be built from. Below this
#: the phrase names too little to be sure which saying of it the reel
#: closes on, and the CTA is not used as an anchor at all.
CTA_ANCHOR_MINIMUM = 3

#: Why every reel closing on a call to action inherits the freeze.
#: Recorded in the ending itself, the same way a hand-written one
#: records its reason, so a reader of a build has the ruling in front of
#: them rather than a bare `source: call_to_action`.
CTA_DEFAULT_REASON = (
    "captain 2026-09-11, on Reels 01, 23, 31 and 28: \"the ending here "
    "comes in too early. the last bit of akshita's audio is cut off and "
    "also the tv off animation occurs while she is still talking, not "
    "after, so you need to fix that\" - and, on Reel 13 where the freeze "
    "was already in: \"the way you fixed the CTA here is correct and how "
    "i want it, can you extrapolate similar fixes to all of the other "
    "places where i have said the ending cuts in too early\". The "
    "instruction that makes this a default rather than four entries is "
    "his own: \"this change needs to be applied to all other reels that "
    "currently also use this CTA OR WILL BE USING THIS CTA\". So the "
    "freeze is a property of closing on a call to action, inherited by "
    "every reel that does - including reels nobody has planned yet - "
    "and a per-reel declaration exists to override it."
)


class ReelEndingError(ValueError):
    """The declared ending cannot be honoured as written."""


class TailElementHasNoRoom(ReelEndingError):
    """The ending shot is shorter than its declared tail element."""


# ── Recording: validation ──────────────────────────────────────────

def validate_endings(value) -> list:
    """Structural check. Raises `ReelEndingError` naming what is wrong.

    Whether the words are really spoken is decided at APPLY time
    against the transcript, not here: a declaration may be recorded
    before the reel it names is planned, and a phrase that matches
    nothing reports STALE on the run rather than refusing the read.
    """
    if not isinstance(value, list) or not value:
        raise ReelEndingError(
            "reel_ending endings must be a non-empty list. An empty one "
            "is the absence of a declared ending - leave the file out "
            "instead, and every reel ends where its plan ends.")
    seen = set()
    for index, ending in enumerate(value):
        label = f"endings[{index}]"
        if not isinstance(ending, dict):
            raise ReelEndingError(f"{label} is not an object")
        reel = ending.get("reel")
        if not isinstance(reel, str) or not reel.strip():
            raise ReelEndingError(
                f"{label} names no reel. An ending is a decision about "
                f"ONE reel; a declaration that names none would end "
                f"every reel in the project.")
        if reel in seen:
            raise ReelEndingError(
                f"{label} is a second ending for {reel!r}. Two endings "
                f"for one reel is two answers to one question - record "
                f"the one that is true.")
        seen.add(reel)
        ends_on = ending.get("ends_on")
        if not isinstance(ends_on, dict):
            raise ReelEndingError(
                f"{label} has no ends_on object. An ending must say "
                f"WHERE it ends.")
        phrase = ends_on.get("anchor_phrase")
        if not isinstance(phrase, str) or not phrase.strip():
            raise ReelEndingError(
                f"{label} ends_on names no anchor_phrase. An ending is "
                f"anchored to the WORDS the reel ends on: a frame "
                f"number does not survive a re-cut and the words do.")
        element = ending.get("tail_element", "none")
        if element not in TAIL_ELEMENTS:
            raise ReelEndingError(
                f"{label} names tail_element {element!r}: one of "
                f"{', '.join(TAIL_ELEMENTS)}. An element this engine "
                f"cannot draw would be a declaration nothing places.")
        hold = ending.get("tail_hold", "none")
        if hold not in TAIL_HOLDS:
            raise ReelEndingError(
                f"{label} names tail_hold {hold!r}: one of "
                f"{', '.join(TAIL_HOLDS)}. A hold this engine cannot "
                f"place would be a declaration nothing honours.")
        if hold == "freeze" and element == "none":
            raise ReelEndingError(
                f"{label} declares a freeze with no tail element. A hold "
                f"exists to give an element room, so holding the last "
                f"frame for nothing is a still nobody asked for - "
                f"declare the element, or drop the hold.")
        reason = ending.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ReelEndingError(
                f"{label} records no reason. An ending outlives the "
                f"conversation that produced it; a pin nobody can date "
                f"or attribute is a pin nobody dares remove.")
    return list(value)


# ── Reading: the declaration ───────────────────────────────────────

def endings_path(project_folder: str) -> str:
    """Where a project's declared endings live."""
    return os.path.join(project_folder, "external", ENDING_FILENAME)


def load_endings(project_folder: str) -> list:
    """Declared endings, or `[]` where the project declares none.

    A file that exists and cannot be read RAISES: a declaration the
    build cannot parse must refuse, never build silently past it -
    which is how Reel 13's ending was lost the first two times.
    """
    path = endings_path(project_folder)
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError) as unreadable:
        raise ReelEndingError(
            f"{path} cannot be read ({unreadable}). A declared ending "
            f"the build cannot parse is refused, never ignored.") \
        from unreadable
    version = document.get("version")
    if version != ENDING_VERSION:
        raise ReelEndingError(
            f"{path} declares version {version!r}; this reader honours "
            f"{ENDING_VERSION}. A schema nobody wrote a reader for is "
            f"refused rather than read optimistically.")
    return validate_endings(document.get("endings"))


def declared_ending(project_folder: str, reel_name: str):
    """The HAND-WRITTEN ending for one reel, or None.

    Matched by PREFIX so a staging container
    (`... (scratch x) (rebuild staging)`) resolves to the same
    declaration its promoted name does: one declaration, every build
    of that reel, which is what makes an ending survive a rebuild.
    """
    if not reel_name:
        return None
    for ending in load_endings(project_folder):
        if reel_name == ending["reel"] or reel_name.startswith(
                ending["reel"]):
            return ending
    return None


# ── The ending a reel inherits from its call to action ─────────────

def _cta_anchor_words(cta, transcript) -> list:
    """The call to action's own CLOSING words, as anchor tokens.

    Read off the TRANSCRIPT over the CTA's own span wherever there is
    one, and only off `cta.text` when there is not.  The two are not
    always the same string: measured on this episode, the CTA at
    1168.34-1177.58s reads "the Lucy visibility system" in `text` and
    "the lucie visibility system" in the transcript the anchor is
    matched against, so a phrase taken from `text` would have named
    words no span speaks.  `text` is the fallback rather than the
    source for exactly that reason.
    """
    from library.tools.captain_edits import _span_word_tokens, _tokens

    spoken = []
    if transcript:
        try:
            spoken = _span_word_tokens(transcript,
                                       float(cta["timeline_start"]),
                                       float(cta["timeline_end"]))
        except (KeyError, TypeError, ValueError):
            spoken = []
    if len(spoken) < CTA_ANCHOR_MINIMUM:
        spoken = _tokens(str(cta.get("text") or ""))
    if len(spoken) < CTA_ANCHOR_MINIMUM:
        return []
    return spoken[-CTA_ANCHOR_WORDS:]


def call_to_action_of(moment) -> Optional[dict]:
    """One reel plan moment's call to action as a plain mapping, or None.

    `library/tools/reel_proposal.CallToAction` is where a CTA is
    defined, and its own docstring is why this has a single home:
    *"the episode's CTAs are where they are: six of them, scattered,
    and every reel has to end on one"*.  A moment carrying one closes
    on it; `None` is a reel that ends where its body ends.

    Takes the dataclass or the dict it serialises to, because the
    build reads `ReelMoment` objects and the verifier and the tests
    read the recorded plan.
    """
    if moment is None:
        return None
    cta = (moment.get("call_to_action") if isinstance(moment, dict)
           else getattr(moment, "call_to_action", None))
    if cta is None:
        return None
    if not isinstance(cta, dict):
        cta = {"timeline_start": getattr(cta, "timeline_start", None),
               "timeline_end": getattr(cta, "timeline_end", None),
               "text": getattr(cta, "text", "") or "",
               "speaker": getattr(cta, "speaker", "") or ""}
    # The span must be REAL NUMBERS, not merely present. A stand-in
    # object answers every attribute with something truthy, and an
    # ending built from one anchors on the repr of a mock and then
    # freezes a reel that places no picture. `is None` is not the
    # question; "is this a span" is.
    try:
        start = float(cta["timeline_start"])
        end = float(cta["timeline_end"])
    except (KeyError, TypeError, ValueError):
        return None
    if not end > start:
        return None
    text = cta.get("text")
    return {"timeline_start": start, "timeline_end": end,
            "text": text if isinstance(text, str) else "",
            "speaker": (cta.get("speaker")
                        if isinstance(cta.get("speaker"), str) else "")}


def cta_default_ending(reel_name: str, moment, transcript=None):
    """The ending a reel INHERITS from the call to action it closes on.

    This is the shape the captain asked for on 2026-09-11 and the
    reason this is not four entries in `external/reel_ending.json`:
    *"this change needs to be applied to all other reels that
    currently also use this CTA or will be using this CTA"*.  A reel
    that is planned tomorrow, on a CTA nobody has recorded a pin for,
    closes the same way - because what it inherits from is the CTA it
    closes on, not a list of reel names.

    It is deliberately NOT keyed to a passage, a speaker or a span.
    Measured across this episode's plan: the four reels the captain
    complained about close on THREE DIFFERENT call-to-action passages
    (Reels 01 and 23 on Akshita's 333.80-341.27s, Reel 31 on her
    1855.84-1865.28s, Reel 28 on CRAIG's 809.69-819.13s) and the
    accepted reference, Reel 13, on a fourth.  There is no one span
    to hang this on; what they share is that each closes on a
    `CallToAction`, which is the thing that has a single home.

    Returns None for a reel whose plan declares no call to action -
    three of this episode's thirty-one moments - and such a reel
    builds exactly as it did before this default existed.
    """
    cta = call_to_action_of(moment)
    if cta is None:
        return None
    anchor = _cta_anchor_words(cta, transcript)
    if not anchor:
        return None
    speaker = str(cta.get("speaker") or "").strip()
    ending = {
        "reel": reel_name,
        "ends_on": {"anchor_phrase": " ".join(anchor)},
        "tail_element": CTA_TAIL_ELEMENT,
        "tail_hold": CTA_TAIL_HOLD,
        "reason": CTA_DEFAULT_REASON,
        # INHERITED, and it says so: a build that prints its endings
        # must be able to tell the captain which of them he wrote.
        "source": "call_to_action",
        "cta": {"timeline_start": float(cta["timeline_start"]),
                "timeline_end": float(cta["timeline_end"]),
                # Recorded, never READ, by anything that decides the
                # hold: the mechanism must not know who closes. Reel
                # 28 closes on Craig and takes the same path Reel 13's
                # Akshita does.
                "speaker": speaker},
    }
    # Through the same structural check a hand-written one passes, so a
    # default that is malformed fails here rather than at the seam.
    validate_endings([ending])
    return ending


def is_inherited(ending) -> bool:
    """Whether this ending came from the CTA rather than from a pin."""
    return (ending or {}).get("source") == "call_to_action"


def resolve_ending(project_folder: str, reel_name: str, moment=None,
                   transcript=None):
    """The ending for one reel: the DECLARED one, else the INHERITED one.

    A per-reel entry in `external/reel_ending.json` wins outright.
    That is the whole relationship between the two: the declaration
    exists to OVERRIDE what closing on a call to action already gives
    a reel, never to supply it.  Reel 13's entry is the example - it
    is still needed, because its plan's last range reaches past the
    shot that speaks its closing words and only a pin can say where
    that reel ends.

    Called without `moment` this is the old behaviour exactly:
    declarations only.  Every caller that has a plan moment passes it,
    and a caller that does not is reading something other than a reel
    build.
    """
    declared = declared_ending(project_folder, reel_name)
    if declared is not None:
        return declared
    return cta_default_ending(reel_name, moment, transcript)


# ── The tail element ───────────────────────────────────────────────

def tail_room_frames(ending, look=None) -> int:
    """How many frames of CLIP the declared tail element needs to draw.

    Read out of the element's OWN declaration - `tv_power.py`, with
    whatever the project's `tv_frame` `power.switch_off` overrides on
    top - so this module carries no timing of its own and a project
    that redeclares the animation redeclares the room it needs in the
    same breath.
    """
    element = (ending or {}).get("tail_element", "none")
    if element != "tv_power_tail":
        return 0
    from library.tools.fusion.played_window import frames_for_ramp
    from library.tools.tv_power import switch_shape

    timing = dict(switch_shape())
    timing.update((look or {}).get("power", {}) or {})
    # Frames of CLIP, not frames of RAMP: a ramp needs the frame it
    # starts neutral on as well as every frame it moves over, and that
    # arithmetic belongs to the check that enforces it
    # (`played_window.frames_for_ramp`). An 18-frame switch-off on an
    # 18-frame hold is undone as `never_settles`; 19 draws.
    return frames_for_ramp(
        timing["collapse_frames"] + timing["dot_frames"]
        + timing["decay_frames"])


def ending_tail_frames(ending, look=None) -> int:
    """How many frames the ENDING occupies after the last keep range.

    A `freeze` holds the ending shot's last frame for exactly as long as
    its tail element needs and draws the element over the held frames,
    so those frames are picture the reel plays and anything placed after
    the body starts after them.  `none` holds nothing and returns 0.

    WHO NEEDS THIS: a closing element declared for every reel
    (`full_frame_element.full_frame_clip` at the tail).  The captain's
    logo card on Reel 09 sits on the frame after the picture ends, and
    the ordering is stated rather than inferred:

        picture live -> held frame with the switch-off over it -> card

    The switch-off ENDS AT BLACK - gain 0.0, the set off - so the card
    is what the brand shows once the picture is gone, and it never draws
    over a frame that is still collapsing to a dot underneath it.
    Ordering it the other way would put two animations on the same
    frames and leave the switch-off finishing underneath the logo.
    The captain has not ruled on this; it is flagged as theirs to judge.
    """
    if tail_hold(ending) != "freeze":
        return 0
    return tail_room_frames(ending, look)


def tail_effects(ending, look=None) -> dict:
    """The per-clip Fusion keys the declared tail element contributes.

    The same two keys `reel_look.power_effects` and `compile_manifest`
    set, resolved the same way - but reached because a declaration
    NAMES this element, rather than because a clip happened to sort
    last. `{}` for `none`, and `{}` for no declaration at all: a
    project that declares no ending keeps exactly the behaviour it
    had.
    """
    element = (ending or {}).get("tail_element", "none")
    if element != "tv_power_tail":
        return {}
    from library.tools.tv_power import switch_shape

    timing = dict(switch_shape())
    timing.update((look or {}).get("power", {}) or {})
    # DECLARED, so the renderer's own treatment check refuses rather
    # than undoing it: an element the captain asked for by name that
    # silently does not draw is the whole defect this owner exists for
    # (`execution/apply_fusion_comps`). `build_effect_comp` dispatches
    # on parameter NAMES and reads no key it was not taught, so this
    # travels as a fact about the plan and draws nothing itself.
    return {"tv_power_tail": True, "tv_power_tail_timing": timing,
            "tv_power_tail_declared": True}


def tail_hold(ending) -> str:
    """How the declared tail element is given room: `none` or `freeze`."""
    return (ending or {}).get("tail_hold", "none")


@dataclass(frozen=True)
class FreezeTail:
    """The held frame a declared freeze appends to the ending shot.

    `held_from` / `held_source_seconds` say WHICH frame of WHICH source
    is held - the last one the reel plays - so the artefact can be
    re-derived, and so a reader can check the freeze against the shot
    rather than taking the builder's word for it.
    """

    held_from: str
    """The source file the held frame comes from."""
    held_source_seconds: float
    """Where in that source the held frame sits, in seconds."""
    reel_start_frame: int
    """The reel frame the hold begins on - the frame after the live tail."""
    duration_frames: int
    """How long it is held: the tail element's own length."""
    element: str
    """The element that draws over it."""
    track_index: int
    """The master row of the shot being held, so the placer finds its angle."""
    held_master: tuple = (0.0, 0.0)
    """The master-transcript range the held frame was taken from - the
    tail placement's own span. A held frame speaks no words, so the
    placement keeps an empty `master` (no word-anchored pass may claim
    it as a shot); this names the words it HOLDS, so a hand-declared
    speaker value reaches the freeze built from that clip directly
    instead of only through the timeline-adjacency copy at build
    time."""
    speaker: str = ""
    label: str = "reel_freeze_tail"
    rendered_path: str = ""
    """Filled in by `render_freeze`; empty until the artefact exists."""

    @property
    def end_frame(self) -> int:
        """EXCLUSIVE, the convention every reel placement uses."""
        return self.reel_start_frame + self.duration_frames


def plan_freeze(picture_placements, ending, fps: float,
                look=None) -> Optional["FreezeTail"]:
    """The freeze a declared ending asks for, or None.

    `picture_placements` are the reel's picture placements in play
    order - `reel_build.placements()` entries. The hold begins on the
    frame AFTER the last one that plays and holds the last frame that
    plays, which is the only frame a freeze may use: the reel already
    shows it, so nothing unreviewed reaches the timeline.

    Returns None for no declaration and for `tail_hold: none`, so a
    project that declares neither builds exactly what it built before.
    """
    if not ending or tail_hold(ending) != "freeze":
        return None
    frames = tail_room_frames(ending, look)
    if frames <= 0:
        return None
    tail = list(picture_placements or [])[-1] if picture_placements else None
    if tail is None:
        raise ReelEndingError(
            "a freeze was declared for a reel that places no picture: "
            "there is no last frame to hold.")
    played = int(round((float(tail["source_out"])
                        - float(tail["source_in"])) * fps))
    start = int(tail["snapped_record"]) + played
    # The LAST frame that plays, not the first one that does not: the
    # placement's source_out is exclusive, the same reading every
    # `endFrame` in this builder takes.
    held_seconds = float(tail["source_out"]) - (1.0 / fps)
    clip = tail["clip"]
    try:
        held_master = tuple(tail.get("master") or (0.0, 0.0))
    except (AttributeError, TypeError, ValueError):
        held_master = (0.0, 0.0)
    return FreezeTail(
        held_from=getattr(clip, "source_file", ""),
        held_source_seconds=held_seconds,
        reel_start_frame=start,
        duration_frames=frames,
        element=(ending or {}).get("tail_element", "none"),
        track_index=int(getattr(clip, "track_index", 1)),
        held_master=held_master,
        speaker=str(getattr(clip, "speaker", "") or ""))


def render_freeze(freeze: "FreezeTail", out_dir: str,
                  fps: float) -> "FreezeTail":
    """Render the held frame as a clip, and return the freeze carrying it.

    A still cannot be placed for an arbitrary length through Resolve's
    scripting API - `reel_look.frame_overlay_segments` measured that and
    says why - so the hold is a MOVIE of one repeated frame, exactly as
    the TV frame overlay is.

    Rendered at the SOURCE's own resolution, never the delivery frame:
    the held item then takes the same punch-in transform as the shot it
    holds and lands on the same pixels. Rendered ONCE per (source,
    frame, length) and re-used, keyed by a digest of those three.
    """
    import hashlib
    import subprocess

    os.makedirs(out_dir, exist_ok=True)
    stamp = hashlib.sha1(
        f"{freeze.held_from}|{freeze.held_source_seconds:.6f}|"
        f"{freeze.duration_frames}|{fps:.6f}".encode("utf-8")
    ).hexdigest()[:10]
    path = os.path.join(out_dir, f"reel_freeze_{stamp}.mov")
    if not os.path.isfile(path):
        still = os.path.join(out_dir, f"reel_freeze_{stamp}.png")
        grab = subprocess.run([
            "ffmpeg", "-y", "-v", "error",
            "-ss", f"{max(freeze.held_source_seconds, 0.0):.6f}",
            "-i", freeze.held_from, "-frames:v", "1", still,
        ], capture_output=True, encoding="utf-8", check=False)
        if grab.returncode != 0 or not os.path.isfile(still):
            raise ReelEndingError(
                f"the frame to hold could not be read from "
                f"{freeze.held_from!r} at "
                f"{freeze.held_source_seconds:.3f}s: ffmpeg exited "
                f"{grab.returncode}. "
                f"{(grab.stderr or '').strip()[-400:]}")
        # ProRes 422 HQ at the still's own size. No alpha: a held
        # picture frame is opaque, and the switch-off that draws over
        # it is a comp on this clip rather than a layer above it.
        render = subprocess.run([
            "ffmpeg", "-y", "-v", "error", "-loop", "1", "-i", still,
            "-t", f"{freeze.duration_frames / fps:.6f}",
            "-r", f"{fps:.6f}",
            "-c:v", "prores_ks", "-profile:v", "3",
            "-pix_fmt", "yuv422p10le", path,
        ], capture_output=True, encoding="utf-8", check=False)
        os.path.isfile(still) and os.remove(still)
        if render.returncode != 0 or not os.path.isfile(path):
            raise ReelEndingError(
                f"the held frame could not be rendered to "
                f"{freeze.duration_frames} frames: ffmpeg exited "
                f"{render.returncode}. "
                f"{(render.stderr or '').strip()[-400:]}")
    return dataclasses.replace(freeze, rendered_path=path)


def freeze_placement(freeze: "FreezeTail", fps: float) -> dict:
    """The freeze as a picture PLACEMENT, for the Fusion manifest.

    The same shape `reel_build.placements()` emits, so the hold is the
    last picture clip of its row and `reel_look.power_effects` arms the
    declared tail element on it without knowing a freeze exists. The
    synthetic clip carries only what the manifest reads.
    """
    from types import SimpleNamespace

    return {
        "clip": SimpleNamespace(
            source_file=freeze.rendered_path,
            track_index=freeze.track_index,
            speaker=freeze.speaker,
            track_type="video",
            timeline_start=0.0,
            timeline_end=freeze.duration_frames / fps),
        "source_in": 0.0,
        "source_out": freeze.duration_frames / fps,
        "record": freeze.reel_start_frame / fps,
        "snapped_record": freeze.reel_start_frame,
        "track_index": freeze.track_index,
        "speaker": freeze.speaker,
        # A held frame speaks no words. An empty master span is what
        # stops every word-anchored pass (mix pins, caption timing)
        # from claiming it as a shot - the freeze inherits its
        # treatment from the shot it holds instead, which is what a
        # freeze IS. Transform overrides are the exception: `held_master`
        # names the words the held frame shows, so a hand-declared
        # speaker value names the freeze directly
        # (`captain_edits.match_transform_overrides`) as well as
        # reaching it through the build-time copy.
        "master": (0.0, 0.0),
        "held_master": tuple(freeze.held_master or (0.0, 0.0)),
        "freeze": True,
    }


def freeze_items(video_items) -> dict:
    """`{id(item): name}` for every timeline item that is a held frame.

    The same shape `reel_look.frame_overlay_items` takes, and for the
    same reason: a check over a reel's picture has to be able to tell
    the FOOTAGE from the things placed beside it. A hold is a copy of a
    frame the reel already plays, graded and framed by inheriting the
    shot it holds, so a framing check that grades it against the
    project's intent grades the same frame twice - once correctly as
    the shot, once against a catalog entry no hold has.
    """
    out = {}
    for item in video_items or ():
        source = str(getattr(item, "source_file", "") or "")
        if is_freeze_path(source):
            out[id(item)] = source.rsplit("/", 1)[-1]
    return out


def is_freeze_path(source_file) -> bool:
    """Whether this source path is a held frame this module rendered.

    The one place the `FREEZE_PREFIX` convention is READ, so a caller
    holding a path rather than a `.source_file` object asks here
    instead of respelling `startswith` - two spellings of one
    convention are two chances to stop recognising a hold. `freeze_items`
    is the same question asked of a list of objects.
    """
    name = str(source_file or "").rsplit("/", 1)[-1]
    return bool(name) and name.startswith(FREEZE_PREFIX)


def assert_tail_fits(picture_placements, ending, fps: float,
                     look=None) -> dict:
    """Refuse an ending whose last shot cannot hold its tail element.

    The check that was missing.  `treatment_verify` measures the same
    thing at COMP time and quietly undoes what will not draw, which is
    right for a treatment nobody declared and wrong for one the captain
    asked for by name.  Here the declaration exists, so a shot too
    short to carry it stops the build and says both counts.

    Returns the measurement (`{"tail_frames", "shot_frames", ...}`) so
    a caller can print what it checked on a passing build too - a gate
    that only speaks when it fails reads as absent.
    """
    room = tail_room_frames(ending, look)
    tail = list(picture_placements or [])[-1] if picture_placements else None
    if tail is None:
        raise ReelEndingError(
            "the reel has no picture to end on: an ending was declared "
            "for a reel that places no footage.")
    shot_frames = int(round(
        (float(tail["source_out"]) - float(tail["source_in"])) * fps))
    hold = tail_hold(ending)
    measured = {
        "element": (ending or {}).get("tail_element", "none"),
        "tail_frames": room,
        "shot_frames": shot_frames,
        "hold": hold,
        # A freeze mints the room from the element's own length, so it
        # fits BY CONSTRUCTION. Said rather than skipped: a check that
        # goes quiet where it cannot fail reads as a check that ran.
        "fits": True if hold == "freeze" else room <= shot_frames,
    }
    if not measured["fits"]:
        raise TailElementHasNoRoom(
            f"REFUSING to build: the declared tail element "
            f"{measured['element']!r} needs {room} frames and this "
            f"reel's ending shot plays {shot_frames}. A tail animation "
            f"longer than the shot it ends never reaches black, so it "
            f"would be dropped silently and the reel would ship without "
            f"the thing that was asked for. Either the ending shot is "
            f"the wrong one (check ends_on.anchor_phrase), or declare "
            f"tail_hold: freeze and the shot's last frame is held for "
            f"the {room} frames the element needs. The engine will not "
            f"read past the master's own cut to make room, because "
            f"nothing has judged that footage.")
    return measured


# ── The breath after the closing words ─────────────────────────────

def _next_word_start(transcript: dict, after: float):
    """When the next timed word is SPOKEN after `after`, or None.

    Timed words only, the rule `captain_edits._span_word_tokens`
    states: an untimed word cannot place anything.
    """
    soonest = None
    for segment in (transcript or {}).get("segments") or ():
        for word in segment.get("words") or ():
            if not word.get("timed"):
                continue
            try:
                start = float(word["start"])
            except (KeyError, TypeError, ValueError):
                continue
            if start > after + 1e-6 and (soonest is None or start < soonest):
                soonest = start
    return soonest


def closing_breath_end(transcript: dict, words_end: float,
                       shot_end: float) -> float:
    """Where a reel closing on its call to action stops: the CTA's BREATH.

    The captain, 2026-09-11, on Reels 01, 23 and 31: *"the last bit of
    akshita's audio is cut off"*.  Measured on the footage and he is
    literally right - and it is a SECOND fault, not the switch-off
    wearing another description.  Reels 01, 23 and 30 close on
    `call_to_action.timeline_end` 341.270s, which is exactly where
    WhisperX labels the end of "bio.", and the sound of that word is
    still at 1307 RMS on the last frame they play.  It takes four more
    frames to reach the noise floor.  The accepted reference, Reel 13,
    escaped it only by accident: its last range had been over-extended
    and was truncated back to the ending SHOT's end, which happens to
    sit seven frames later.

    So an aligner's word boundary is not where the word stops being
    heard, and a reel that ends on one clips its own closer.  The
    breath is the silence AFTER the closing words, and it is bounded
    twice - by the ending shot's own end, and by the next thing
    anybody says.  Neither bound is a number anybody tuned, neither
    can admit the next shot, and neither can admit the next word.

    This is the view the proposal layer already takes of the same
    seconds: `reel_proposal._widen_to_segments` keeps a boundary that
    sits in clean silence because pulling it back "deletes a tail
    breath the proposer meant (a switch-off animation's room, a word's
    last frame)".

    Where the episode has nothing after the closing words at all, the
    shot's own end is the only bound there is, and it is the answer.
    """
    following = _next_word_start(transcript, words_end)
    if following is None:
        return float(shot_end)
    return min(float(shot_end), float(following))


# ── Applying: the ranges seam ──────────────────────────────────────

def _range_for_span(ranges, master) -> int | None:
    """The played range that owns one placed span, or None if unmapped."""
    try:
        span_start, span_end = map(float, master)
    except (TypeError, ValueError):
        return None
    overlaps = []
    for index, (start, end) in enumerate(ranges):
        overlap = min(float(end), span_end) - max(float(start), span_start)
        if overlap > 0:
            overlaps.append((overlap, index))
    if not overlaps:
        return None
    # Equal overlaps can occur on repeated ranges; the later playback
    # occurrence owns the ending, just as the last matching spoken span
    # does below.
    return max(overlaps)[1]


def apply_ending(ranges, spans, transcript: dict, ending,
                 fps: float) -> tuple:
    """Truncate playback at the last played span that speaks the anchor.

    `ranges` are the reel's master keep ranges in play order and
    `spans` are `reel_build.placements()` entries probed from them,
    each carrying `span["master"]` and `span["clip"]` - the same join
    `captain_edits.match_span_retimes` reads, so an ending and a trim
    are anchored to the words the same way.

    Returns `(ranges, report)` where report is
    `{"applied": [...], "held": [...], "stale": [...]}`.  No
    declaration passes the ranges through untouched and reports
    nothing.

    A DECLARED ending only ever REMOVES playback. Ranges after the last
    played span that speaks the anchor are dropped, then that range's new
    end is `min(its current end, the ending shot's own end)`. This handles
    a borrowed CTA whose source seconds precede the body: a declaration
    ending on the body removes the CTA even though its master time is
    numerically earlier.

    An INHERITED ending may also move that end OUT, into the call to
    action's own trailing silence (`closing_breath_end`), and is
    bounded by the same shot end plus the next spoken word.  The bound
    is what matters, not the direction: neither reading can admit the
    next shot and the outward one cannot admit the next word either.
    A declaration is still truncate-only, because a pin is somebody
    saying where a reel ends and taking them at their word is the
    point of writing one.
    """
    empty = {"applied": [], "held": [], "stale": []}
    if not ending or not ranges:
        return list(ranges), empty
    from library.tools.captain_edits import (_contains_run, _span_word_tokens,
                                             _tokens)

    phrase = ending["ends_on"]["anchor_phrase"]
    anchor_tokens = _tokens(phrase)
    shot_end = None
    ending_range = None
    for span in spans or []:
        master = span.get("master") if isinstance(span, dict) else None
        clip = span.get("clip") if isinstance(span, dict) else None
        if not master or clip is None:
            continue
        tokens = _span_word_tokens(transcript, float(master[0]),
                                   float(master[1]))
        if _contains_run(tokens, anchor_tokens):
            range_index = _range_for_span(ranges, master)
            if range_index is None:
                continue
            # The LAST played span speaking the anchor owns the ending:
            # a phrase said twice ends on the later telling.
            ending_range = range_index
            shot_end = float(getattr(clip, "timeline_end", master[1]))
    inherited = is_inherited(ending)
    if shot_end is None:
        empty["stale"].append({
            "kind": "ending", "reel": ending["reel"],
            "anchor_phrase": phrase, "source": ending.get("source", ""),
            "reason": (
                (f"The ending {ending['reel']!r} inherits from its call "
                 f"to action anchors on {phrase!r} and those words are "
                 f"in no placed span, so the reel ends where its plan "
                 f"ends. The FREEZE still applies - the hold is planned "
                 f"from the shot the reel closes on, not from the "
                 f"anchor.")
                if inherited else
                (f"STALE: the declared ending for {ending['reel']!r} "
                 f"anchors on {phrase!r} and those words are in no "
                 f"placed span. The passage was reworded or re-cut out "
                 f"of this reel, so the reel ends where its plan ends "
                 f"and this declaration changed nothing. Original "
                 f"request: {ending.get('reason', '')}").strip())})
        return list(ranges), empty

    out = [tuple(r) for r in ranges[:ending_range + 1]]
    dropped_ranges = [
        [round(float(start), 3), round(float(end), 3)]
        for start, end in ranges[ending_range + 1:]]
    start, end = out[-1]
    # TRUNCATION first, and it is unconditional: the range carrying the
    # closing words may never reach past their shot, which is the defect
    # this owner exists for.
    new_end = min(float(end), shot_end)
    breath = None
    if inherited:
        # Then the BREATH. A reel that closes on a call to action plays
        # that call to action's own trailing silence, because an
        # aligner's word boundary is not where the word stops being
        # heard (`closing_breath_end`). Bounded by the shot's own end
        # and by the next thing anybody says, so this can no more
        # admit the next shot than the truncation above can - it is
        # the same bound, read the other way.
        breath = closing_breath_end(transcript, new_end, shot_end)
        new_end = max(new_end, breath)
    record = {"kind": "ending", "reel": ending["reel"],
              "anchor_phrase": phrase,
              "source": ending.get("source", ""),
              "tail_element": ending.get("tail_element", "none"),
              "tail_hold": tail_hold(ending),
              "shot_end": round(float(shot_end), 3),
              "breath_end": (None if breath is None
                             else round(float(breath), 3)),
              "was": [round(float(start), 3), round(float(end), 3)],
              "now": [round(float(start), 3), round(new_end, 3)],
              "dropped_ranges": dropped_ranges,
              "reason": ending.get("reason", "")}
    if abs(new_end - float(end)) < 1e-6 and not dropped_ranges:
        empty["held"].append(record)
        return out, empty
    if new_end - float(start) <= 0:
        raise ReelEndingError(
            f"REFUSING to build: the declared ending for "
            f"{ending['reel']!r} would truncate the range carrying its "
            f"ending "
            f"({start:.3f}-{end:.3f}s) to nothing. The anchor "
            f"{phrase!r} resolves to a shot that ends at "
            f"{shot_end:.3f}s, before this range begins - the ending "
            f"names words the reel plays somewhere other than its "
            f"close.")
    out[-1] = (float(start), new_end)
    empty["applied"].append(record)
    return out, empty


def report(record: dict) -> None:
    """Print one ending verdict the way the pin owners print theirs.

    An INHERITED ending says so on every line.  A build that prints
    "Ending: ..." for a reel nobody wrote a pin for would otherwise
    read as a declaration the captain has forgotten making, and the
    first thing he would do is go looking for it in a file that does
    not name that reel.
    """
    def _origin(row) -> str:
        return ("inherited from the call to action it closes on"
                if row.get("source") == "call_to_action"
                else "declared")

    def _why(row) -> str:
        # A HAND-WRITTEN reason is per reel and is the whole point of
        # printing it. The inherited one is the same paragraph for
        # every reel that closes on a CTA, and a build of nineteen
        # would print it nineteen times - which is how an operator
        # learns to skip the ending lines.
        if row.get("source") == "call_to_action":
            return ""
        return f" - {row.get('reason', '')}"

    for row in record.get("applied", ()):
        now = f"{row['now'][0]:.3f}-{row['now'][1]:.3f}s"
        if row.get("dropped_ranges"):
            count = len(row["dropped_ranges"])
            unit = "range" if count == 1 else "ranges"
            moved = (f"removed {count} {unit} after the ending in playback "
                     f"order; ending range is now {now}")
        else:
            moved = (f"truncated to {now}"
                     if row["now"][1] < row["was"][1]
                     # An inherited ending plays the call to action's own
                     # trailing silence, so it can move the end OUT as well
                     # as in - never past the shot, never into the next
                     # word. Said in the verb, so a reader is not told
                     # "truncated to" a larger number.
                     else f"extended into the closing breath to {now}")
        print(f"  Ending: {row['reel']} ends on {row['anchor_phrase']!r} "
              f"- ending range {row['was'][0]:.3f}-"
              f"{row['was'][1]:.3f}s; {moved} "
              f"(ending shot ends {row.get('shot_end')}s), "
              f"tail element {row['tail_element']}, hold "
              f"{row.get('tail_hold', 'none')} "
              f"({_origin(row)}){_why(row)}",
              flush=True)
    for row in record.get("held", ()):
        print(f"  Ending: {row['reel']} already ends on "
              f"{row['anchor_phrase']!r} - {_origin(row)}, tail element "
              f"{row['tail_element']}, hold "
              f"{row.get('tail_hold', 'none')}", flush=True)
    import sys
    for row in record.get("stale", ()):
        # An inherited ending whose anchor found nothing changed
        # nothing and REMOVED nothing: the freeze is still planned.
        # That is an ordinary outcome, and printing it beside real
        # failures on stderr would teach a reader to skim both.
        if row.get("source") == "call_to_action":
            print(f"  Ending: {row['reason']}", flush=True)
        else:
            print(f"  {row['reason']}", file=sys.stderr, flush=True)
