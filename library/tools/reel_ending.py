"""Where a reel ENDS, and what plays over its tail.

The defect this closes
----------------------
Reel 13, 2026-09-11.  The captain typed it onto the timeline himself,
at the frame where it goes wrong::

    "it cuts to craig here at the end which is a little bit too much,
     it should just end at the end of the clip of akshita. and it
     should also do the tv off animation which was reomved for some
     reason"

Two complaints, ONE cause.  An earlier lane wanted the closing line to
finish before the television switched off, and the only vocabulary it
had for "give the ending room" was the reel's last KEEP RANGE - so it
extended it, 341.27s -> 342.03s.  A keep range is master-timeline
seconds and `reel_build.placements` cuts one range against every master
clip it overlaps, so the extra 18 frames did not lengthen the shot that
was playing: they crossed the master's own cut and admitted the NEXT
shot.  Twelve frames of Craig appeared at the end of the reel.

That cut then removed the switch-off as well.  `reel_look.power_effects`
arms `tv_power_tail` on the LAST picture clip, whatever that clip turns
out to be, and the last picture clip was now Craig's 12 frames.  The
switch-off is 18 frames (`library/tools/tv_power.py`), `treatment_verify`
measured that it could not draw inside 12, and undid it - correctly, and
to stderr in a build log nobody read.  So the reel gained a cut nobody
asked for and lost an animation everybody wanted, from one edit, and
nothing said either had happened.

Why a new owner
---------------
Neither decision had anywhere to live.  "This reel ends at the end of
shot X" and "element Y plays over the tail" are not properties of a keep
range, not properties of the look, and not properties of whichever clip
happens to sort last.  `library/tools/edit_depth.py` is the map of which
layer owns which edit; this module is the `ending` row of it.

An ending TRUNCATES, never extends.  That is the whole lesson of the
defect and it is enforced here rather than documented: `apply_ending`
takes `min(current_end, shot_end)` and a declaration that would reach
past the shot is refused by name.  A reel cannot acquire a shot it did
not plan by asking for breathing room.

What a declaration says
-----------------------
`<project>/external/reel_ending.json`, checked and never asserted (the
`overlay_intent.json` / `placed_assets.json` precedent)::

    {"version": 1,
     "endings": [{"reel": "Reel 13 - the-accounting-firm-ai-called",
                  "ends_on": {"anchor_phrase": "the link's in our bio"},
                  "tail_element": "tv_power_tail",
                  "reason": "captain 2026-09-11 marker @1909"}]}

`reel` is matched against the reel's timeline name by prefix, so a
staging suffix (`(scratch ...) (rebuild staging)`) names the same reel
as the promoted timeline - one declaration serves the build and every
rebuild of it.

`ends_on.anchor_phrase` is the SPOKEN WORDS the reel ends on, anchored
into the master transcript exactly the way `span_retime` anchors a trim
(`library/tools/captain_edits.py`): words survive a re-cut, a
renumbering and a re-plan; a frame number survives none of them.  The
shot carrying those words is the ending shot, and the reel's last keep
range is truncated to that shot's own end.

`tail_element` names what DRAWS over the tail, from `TAIL_ELEMENTS`.  It
is a name, never a magnitude: how long a switch-off takes is
`library/tools/tv_power.py`'s declaration and a project's own `tv_frame`
override, and this module states no timing of its own (AGENTS.md 10.5).
`"none"` is the absence of decoration, not a choice of it, and is the
only other member.

The room the element needs is CHECKED, not hoped for.  `assert_tail_fits`
refuses a build whose ending shot is shorter than its declared tail
element, naming both counts - so the silent `treatment_verify` undo
that removed Reel 13's switch-off cannot happen behind a declaration
again.  An element that will not draw is a build that stops, because a
reel missing the thing the captain asked for is not a reel with a minor
omission.

Holding the last frame
----------------------
`tail_hold` decides WHERE the tail element draws.  `"none"` draws it
over the live tail of the ending shot, which is what an undeclared
reel has always done.  `"freeze"` HOLDS the ending shot's last frame
for exactly as long as the element needs and draws it over the held
frames, so the animation begins after the last word rather than over
it.

The captain chose the freeze for Reel 13 on 2026-09-11, over the two
alternatives, after seeing the switch-off play across the whole of
"The link's in our bio."  His words: *"do the freeze"*.

The hold's LENGTH is not a number this module states: it is the
element's own (`tail_room_frames`), so "the element plays entirely
after the words" is true by construction rather than by a value
somebody tuned.  A freeze therefore always fits, and `assert_tail_fits`
says so rather than skipping quietly.

The freeze is PICTURE ONLY.  Audio is not held: the speaker's voice
plays to its natural end and the held frames carry whatever silence
follows, because a stretched voice is a different edit and nobody
asked for one.

What this module deliberately cannot say
----------------------------------------
It cannot hold a shot PAST the master's own cut to make tail room.  The
footage is there - a synced podcast's camera keeps rolling after the
rough cut leaves it - but nothing in the pipeline has judged those
frames, and reading into them would put unreviewed picture on a
delivered reel.  That is the alternative the captain declined; the
freeze is the one he chose, and it uses only a frame the reel already
plays.  A tail that does not fit inside the shot and declares no
freeze is a refusal here.

`tests/test_reel_ending.py`, `tests/test_orphan_wiring.py`.
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


def resolve_ending(project_folder: str, reel_name: str):
    """The declared ending for one reel, or None.

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
    from library.tools.tv_power import switch_off_frames

    timing = dict(switch_off_frames())
    timing.update(((look or {}).get("power", {}) or {}).get(
        "switch_off", {}) or {})
    # Frames of CLIP, not frames of RAMP: a ramp needs the frame it
    # starts neutral on as well as every frame it moves over, and that
    # arithmetic belongs to the check that enforces it
    # (`played_window.frames_for_ramp`). An 18-frame switch-off on an
    # 18-frame hold is undone as `never_settles`; 19 draws.
    return frames_for_ramp(
        timing["collapse_frames"] + timing["dot_frames"]
        + timing["decay_frames"])


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
    from library.tools.tv_power import switch_off_frames

    timing = dict(switch_off_frames())
    timing.update(((look or {}).get("power", {}) or {}).get(
        "switch_off", {}) or {})
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
    return FreezeTail(
        held_from=getattr(clip, "source_file", ""),
        held_source_seconds=held_seconds,
        reel_start_frame=start,
        duration_frames=frames,
        element=(ending or {}).get("tail_element", "none"),
        track_index=int(getattr(clip, "track_index", 1)),
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
        # stops every word-anchored pass (transform overrides, mix
        # pins, caption timing) from claiming it as a shot - the freeze
        # inherits its treatment from the shot it holds instead, which
        # is what a freeze IS.
        "master": (0.0, 0.0),
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
        name = source.rsplit("/", 1)[-1]
        if name.startswith(FREEZE_PREFIX):
            out[id(item)] = name
    return out


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


# ── Applying: the ranges seam ──────────────────────────────────────

def apply_ending(ranges, spans, transcript: dict, ending,
                 fps: float) -> tuple:
    """Truncate the reel's last keep range to its declared ending shot.

    `ranges` are the reel's master keep ranges in play order and
    `spans` are `reel_build.placements()` entries probed from them,
    each carrying `span["master"]` and `span["clip"]` - the same join
    `captain_edits.match_span_retimes` reads, so an ending and a trim
    are anchored to the words the same way.

    Returns `(ranges, report)` where report is
    `{"applied": [...], "held": [...], "stale": [...]}`.  No
    declaration passes the ranges through untouched and reports
    nothing.

    An ending only ever REMOVES seconds.  The last range's new end is
    `min(its current end, the ending shot's own end)`, so a
    declaration can never admit the next shot - which is the whole
    defect this owner exists for.
    """
    empty = {"applied": [], "held": [], "stale": []}
    if not ending or not ranges:
        return list(ranges), empty
    from library.tools.captain_edits import (_contains_run, _span_word_tokens,
                                             _tokens)

    phrase = ending["ends_on"]["anchor_phrase"]
    anchor_tokens = _tokens(phrase)
    shot_end = None
    for span in spans or []:
        master = span.get("master") if isinstance(span, dict) else None
        clip = span.get("clip") if isinstance(span, dict) else None
        if not master or clip is None:
            continue
        tokens = _span_word_tokens(transcript, float(master[0]),
                                   float(master[1]))
        if _contains_run(tokens, anchor_tokens):
            # The LAST span speaking the anchor owns the ending: a
            # phrase said twice ends the reel on the later saying,
            # which is the one the reel was cut to close on.
            shot_end = float(getattr(clip, "timeline_end", master[1]))
    if shot_end is None:
        empty["stale"].append({
            "kind": "ending", "reel": ending["reel"],
            "anchor_phrase": phrase,
            "reason": (
                f"STALE: the declared ending for {ending['reel']!r} "
                f"anchors on {phrase!r} and those words are in no "
                f"placed span. The passage was reworded or re-cut out "
                f"of this reel, so the reel ends where its plan ends "
                f"and this declaration changed nothing. Original "
                f"request: {ending.get('reason', '')}".strip())})
        return list(ranges), empty

    out = [tuple(r) for r in ranges]
    start, end = out[-1]
    new_end = min(float(end), shot_end)
    record = {"kind": "ending", "reel": ending["reel"],
              "anchor_phrase": phrase,
              "tail_element": ending.get("tail_element", "none"),
              "was": [round(float(start), 3), round(float(end), 3)],
              "now": [round(float(start), 3), round(new_end, 3)],
              "reason": ending.get("reason", "")}
    if abs(new_end - float(end)) < 1e-6:
        empty["held"].append(record)
        return out, empty
    if new_end - float(start) <= 0:
        raise ReelEndingError(
            f"REFUSING to build: the declared ending for "
            f"{ending['reel']!r} would truncate its last range "
            f"({start:.3f}-{end:.3f}s) to nothing. The anchor "
            f"{phrase!r} resolves to a shot that ends at "
            f"{shot_end:.3f}s, before this range begins - the ending "
            f"names words the reel plays somewhere other than its "
            f"close.")
    out[-1] = (float(start), new_end)
    empty["applied"].append(record)
    return out, empty


def report(record: dict) -> None:
    """Print one ending verdict the way the pin owners print theirs."""
    for row in record.get("applied", ()):
        print(f"  Ending: {row['reel']} ends on {row['anchor_phrase']!r} "
              f"- last range {row['was'][0]:.3f}-{row['was'][1]:.3f}s "
              f"truncated to {row['now'][0]:.3f}-{row['now'][1]:.3f}s, "
              f"tail element {row['tail_element']} - {row['reason']}",
              flush=True)
    for row in record.get("held", ()):
        print(f"  Ending: {row['reel']} already ends on "
              f"{row['anchor_phrase']!r} - declaration held, tail "
              f"element {row['tail_element']}", flush=True)
    import sys
    for row in record.get("stale", ()):
        print(f"  {row['reason']}", file=sys.stderr, flush=True)
