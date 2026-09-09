"""A transition carried by an ELEMENT laid over the cut, and why nothing is keyed.

The captain named "chroma key transitions" as a capability the pipeline
should have.  Read plainly that is the classic form: a graphic or a piece
of footage shot on green, keyed out, sweeping across frame to hide a cut.
In short form it is the wipes, sweeps and shape transitions that carry a
cut without a hard jump.

This module is the mechanism.  It carries no keyer, and the rest of this
docstring is why that is the right answer rather than a shortcut.

Nothing is keyed, because nothing needs keying
----------------------------------------------
Measured 2026-09-07, over every asset library and every project on this
machine:

- **There is no green-screen source to key.**  The only motion elements
  any project owns are ``transition_bumper.mov`` and ``logo_reveal.mov``
  in the Lucie brand assets, and both are already ProRes 4444
  (``yuva444p12le``) - an ALPHA CHANNEL, authored, 1080x1920.  Nobody
  keyed them and nobody would.  They were rendered from the project's own
  Remotion compositions, which is the route the engine already runs for
  every caption (step 4.05 emits ProRes 4444 with alpha) and every motion
  graphic (4.06).  **An element that is born with alpha never needs
  keying at all.**
- **A keyer cannot recover an authored alpha, even in its best case.**
  The bumper's own frame 22 was flattened onto perfectly uniform
  ``0x00B140`` - no spill, no lighting variation, no compression, which
  is a cleaner plate than any real shoot produces - and keyed back with
  ``ffmpeg chromakey`` across the whole similarity range.  Against the
  authored alpha (31,871 ink pixels of 2,073,600):

      similarity   background left opaque   holes punched in the element
      0.01              2,041,729                        0
      0.05                  1,054                   10,426
      0.10                      2                   20,980
      0.20                      0                   24,729
      0.30                      0                   30,501

  There is no setting that returns the element.  Every one of them either
  leaves the plate or eats the artwork, and the soft glow the design
  actually carries is destroyed at every setting.  ``docs/CHROMA_KEY_TRANSITIONS_MEASURED.md``
  has the pictures.
- **Choosing between those rows is taste with no producer here.**  A key
  colour, a similarity and a blend are three numbers nobody in this
  pipeline is asked for, and ``if similarity < 0.x`` is precisely the
  invented threshold AGENTS.md 10.5 and ``tests/test_no_creative_floors.py``
  exist to keep out.  An authored alpha needs none of them: the softness
  is drawn, not derived.

So the engine REQUIRES alpha and does not key.  An element whose picture
carries no alpha plane is refused by name, with that reasoning, rather
than silently composited as an opaque rectangle - see
:func:`measure_element` and :data:`ALPHA_IS_REQUIRED_NOT_KEYED`.

Why an OVERLAY is buildable where a wipe is not
-----------------------------------------------
``transition_vocabulary.WITHDRAWN`` withdrew ``cross_dissolve``, ``wipe``
and ``whip_pan`` for one reason, and it is still true: a per-clip Fusion
comp sees only its own clip, so nothing on that route can MIX the
outgoing and incoming pictures.

An element laid over the cut does not mix them.  It HIDES the cut - it is
an additive overlay on its own track, and it reads neither neighbour.
That is the whole reason this capability exists when a wipe does not, and
it is why the mechanism belongs here rather than in
``library/tools/fusion/``.

The same distinction is already drawn in
``motion_graphics_vocabulary.OUT_OF_VOCABULARY``, which sends
``shape_wipe_transition`` to ``transition_vocabulary`` and notes the
per-clip limitation.  This module is the answer that entry was waiting
for, and ``transition_vocabulary.OVERLAY_TYPES`` is where it is named, so
there is still one enumeration of transition types and not two.

An overlay is ADDITIVE: it changes no duration
----------------------------------------------
:data:`TIMING_IS_ADDITIVE` is the ruling and this is the reasoning.

A transition occupies time, and the question is whose.  Three answers
were available and two of them cost something the captain measures:

1. **Consume frames from the shots either side.**  The reel gets shorter,
   which is a change to a delivered quality.  Worse, a reel is its
   ``keep_ranges`` laid end to end (``reel_build.reel_time``), so eating
   frames at a seam moves every caption after it AND changes every later
   block's ``timeline_start`` - which is one of the five fields
   ``plan_provenance.footage_binding_hash`` digests.  Every caption on
   the reel would have to be re-planned and re-rendered to stay bound to
   its footage.
2. **Extend the reel.**  Picture and sound come off the same ranges, so
   inserting picture frames the audio does not have desynchronises
   everything downstream of the seam.
3. **Lay the element OVER the cut.**  The hard cut underneath stays on
   exactly the frame it was on.  Duration is unchanged, ``keep_ranges``
   is unchanged, every caption's timing and binding is unchanged, and the
   element hides the jump - which is what the gesture is for.

Three is what an editor does with a bumper, and it is the only one of the
three that costs nothing.  ``tests/test_transition_overlay.py`` asserts
the binding hash is byte-identical either side of adding overlays, so the
claim is checked rather than asserted.

What it DOES cost is stated rather than assumed: an element over the cut
draws over whatever else is on frame, captions included.
:func:`captions_covered` measures which caption cards it covers and for
how long, and the reels conformance verifier reports it.  Whether that is
wanted is the captain's; that it happened is a fact, and a fact with a
reader (AGENTS.md 10.1).

The engine ships no element
---------------------------
An element is ARTWORK - copy or a graphic the viewer reads - so it lives
with the project, not the engine (AGENTS.md 14, ``docs/ASSET_LIBRARY_PLAN.md``).
The declaration is the same two-mode shape ``content.bookends`` uses and
for the same recorded reason::

    # <project>/project.yaml
    effect:
      transition_overlay:
        element:
          asset: brand_assets/transition_bumper.mov   # already rendered
          # -- or --
          composition: TransitionBumper               # rendered by Remotion
          source: compositions/TransitionBumper/index.tsx
          duration_seconds: 1.5
        anchor: centre
        on_cuts: [closer]

The key is ``on_cuts`` and not ``on`` because YAML 1.1 - which PyYAML
implements - parses a bare ``on`` as the BOOLEAN True.  Written as
``on:`` the declaration parses to ``{True: ['closer']}``, the lookup
finds nothing, and the reader is told they declared no seams while
looking straight at the line where they did.  Found by writing the
documented syntax and running it.

Nothing here has a default.  ``anchor`` is REQUIRED and comes from
:data:`ANCHORS` - naming a set is not choosing from it - because where an
element sits relative to the cut is the gesture, and an engine-supplied
default would be taste arriving one level up.  ``duration_seconds`` is
required only in ``composition`` mode, where there is no file to measure;
in ``asset`` mode it is MEASURED off the element and a declared value that
disagrees with the measurement is refused rather than believed.

Which renderer owns a full-frame element
----------------------------------------
Another lane owns the full-screen architecture and had not landed a
``docs/`` ruling when this was written (2026-09-07: no such document, no
open PR).  **This module renders nothing**, which is what makes it
reconcilable with whatever that lane decides: it consumes an element BY
PATH and places it.  ``asset`` mode needs no renderer at all, and
``composition`` mode names the composition and its project-owned source
so it goes wherever bookends go - and until a renderer exists on the
reels side it REFUSES by name rather than placing nothing, because the
reels process is two nodes and neither renders (see
:func:`resolve_element`).  If the full-screen lane names Remotion,
nothing here changes; if it names something else, nothing here changes
either.  Ruling 1 holds: no second path is built beside it.

Reachability
------------
``element_overlay`` is reachable when a declared element MEASURES
drawable, and that is derived rather than declared -
:func:`element_is_reachable` runs the measurement.  A roster entry that
claims reachability it does not have is the defect class this project
spent a week removing, so nothing here claims it.

    python3 -m library.tools.transition_overlay --measure <element.mov>

``tests/test_transition_overlay.py``.
"""
from __future__ import annotations

import os
import re
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Optional

from library.tools.project_asset import (
    ProjectAssetNotFoundError,
    resolve_project_asset,
)

# ── Recorded answers ─────────────────────────────────────────────────

ALPHA_IS_REQUIRED_NOT_KEYED = (
    "The engine requires an element that already carries alpha and does "
    "not key one out of a plate. Measured 2026-09-07: no project or asset "
    "library on this machine holds green-screen source, both motion "
    "elements that do exist are ProRes 4444 with an authored alpha, and a "
    "flatten-and-re-key of one of them recovered the authored alpha at no "
    "keyer setting - every setting either left the plate or ate the "
    "artwork. A keyer would also need a key colour, a similarity and a "
    "blend, which are three taste values with no producer in this "
    "pipeline (AGENTS.md 10.5). "
    "docs/CHROMA_KEY_TRANSITIONS_MEASURED.md has the numbers and the "
    "pictures."
)

TIMING_IS_ADDITIVE = True
"""An overlay transition changes no duration and moves no caption.

It is laid OVER the cut on its own track; the hard cut underneath stays
on the frame it was already on. Consuming frames from the neighbours
would shorten the reel and move every later caption's `timeline_start`,
which `plan_provenance.footage_binding_hash` digests - so every caption
after the seam would need re-planning. Extending the reel would insert
picture the audio does not have. See the module docstring.
"""

ANCHORS = ("centre", "before", "after")
"""Where the element sits relative to the cut it covers.

``centre`` straddles the cut, ``before`` ends on it, ``after`` starts on
it. Naming the set is not choosing from it: a declaration states which,
and there is no default, because which one it is IS the gesture.
"""

OVERLAY_TRACK = 4
"""The reel video track an overlay element is placed on - in the layout
without the TV-frame look (V1/V2 picture, V3 captions).

Rows pack and the plan owns the indices
(``reel_build.build_reel_timeline`` re-stamps placements onto the
plan's transitions row, which is V5 on a two-angle reel wearing the
look). This stays as the planner's default and the legacy fallback.
An element that hides a cut hides everything on that frame, captions
included - which is what a bumper over a cut does - so it goes above
them, and :func:`captions_covered` reports what it covered rather than
leaving it to be discovered.
"""

# Kinds of seam a reel has. A seam is a FACT about the built reel, not a
# recommendation: this module states one per cut and chooses none of them
# (the same shape `transition_carriers.cut_carriers` takes for the
# edit_video route).
SEAM_TAKE_REMOVED = "take_removed"
SEAM_CLOSER = "closer"
SEAM_REEL_HEAD = "reel_head"
SEAM_REEL_TAIL = "reel_tail"


class TransitionOverlayError(ValueError):
    """A declaration, an element or a placement this module refuses."""


class ElementHasNoAlpha(TransitionOverlayError):
    """The element's picture carries no alpha plane, so there is nothing to
    composite. The engine does not key one out - see
    :data:`ALPHA_IS_REQUIRED_NOT_KEYED`."""


class ElementDrawsNothing(TransitionOverlayError):
    """The element has an alpha plane and it is zero on every frame.

    Project 001 rendered 53.8 MB of motion-graphics ProRes in which
    ``max(alpha)`` was 0 on every frame and reported them as delivered
    (``motion_graphics_vocabulary``). An overlay that draws nothing is
    not rendered and not placed (AGENTS.md 10.2).
    """


class ElementUnmeasurable(TransitionOverlayError):
    """The element could not be measured at all - missing, unreadable, or
    ffmpeg is absent. Distinguished from :class:`ElementHasNoAlpha` on
    purpose: a measurement that did not happen is not a measurement of
    zero."""


class OverlayDoesNotFit(TransitionOverlayError):
    """The element, anchored as declared, would start before the reel or
    end after it. Not clamped - a clamped element is a different gesture
    from the one that was declared, delivered under its name."""


class OverlaysCollide(TransitionOverlayError):
    """Two elements would occupy the same frames of one track. Resolve
    places one and drops the other, so this is refused by name."""


class NotASeam(TransitionOverlayError):
    """An overlay was asked for at a position that is not a cut on this
    reel."""


# ── Measuring an element ─────────────────────────────────────────────

_FRAME_LINE = re.compile(r"^frame:(\d+)\b")
_STAT_LINE = re.compile(
    r"^lavfi\.signalstats\.(YMAX|YMIN|YAVG)=([0-9.]+)\s*$")


@dataclass(frozen=True)
class AlphaMeasurement:
    """What the element's alpha channel actually contains.

    ``frames_read`` is the anti-vacuity half and it is why this is a
    dataclass rather than a bare max. ``ffmpeg`` exits non-zero and emits
    NO frames when the input has no alpha plane; a reader that only
    looked at ``max_alpha`` would see 0 and call it "draws nothing",
    which is the wrong diagnosis and the wrong error message. Zero frames
    read means the channel is ABSENT; frames read and all zero means the
    channel is present and empty.
    """

    frames_read: int
    max_alpha: int
    """0..255, over every frame read. `format=gray` normalises whatever
    bit depth the source carries - the Lucie bumper is 12-bit, where the
    raw statistic tops out at 3760 of 4095 and would read as 92% of
    nothing in particular."""
    first_drawing_frame: Optional[int]
    last_drawing_frame: Optional[int]
    """The frames either side of the element's ink. An element that draws
    only in its middle has transparent handles, which is a legitimate
    thing to author and a fact a placer should be able to see."""
    fully_opaque_frames: int = 0
    """How many frames are opaque over the WHOLE frame (min alpha 255).

    This is what tells a covering element from a stamping one, and it is
    a definition rather than a threshold: a frame every pixel of which is
    fully opaque hides what is under it, and one with a single
    translucent pixel does not. Measured because the difference is
    invisible until it is on the timeline - the only element the field
    test owns is a brand lockup with 0.4% ink, which composites
    perfectly and leaves the cut underneath in plain view."""
    peak_mean_alpha: float = 0.0
    """The highest per-frame MEAN alpha, 0..255. How much of the frame the
    element covers at its fullest, averaged over pixels. Reported, never
    judged: how much cover a transition should have is the declaring
    author's decision (AGENTS.md 10.5)."""

    @property
    def has_channel(self) -> bool:
        return self.frames_read > 0

    @property
    def hides_the_frame(self) -> bool:
        """True when at least one frame covers the picture completely.

        An element that never reaches this STAMPS the cut - it draws over
        it and leaves it visible. Both are legitimate gestures and
        neither is refused; the point is that a declarer can find out
        which one they have before it is on nineteen timelines.
        """
        return self.fully_opaque_frames > 0

    @property
    def draws(self) -> bool:
        """True when some pixel of some frame is not fully transparent.

        This is a definition, not a threshold: `> 0` is what "draws
        something" means. No minimum coverage, opacity or duration is
        imposed - how much is enough belongs to whoever authored the
        element (AGENTS.md 10.5).
        """
        return self.frames_read > 0 and self.max_alpha > 0

    def as_dict(self) -> dict:
        return {
            "frames_read": self.frames_read,
            "max_alpha": self.max_alpha,
            "has_channel": self.has_channel,
            "draws": self.draws,
            "first_drawing_frame": self.first_drawing_frame,
            "last_drawing_frame": self.last_drawing_frame,
            "fully_opaque_frames": self.fully_opaque_frames,
            "peak_mean_alpha": round(self.peak_mean_alpha, 3),
            "hides_the_frame": self.hides_the_frame,
        }


@dataclass(frozen=True)
class Element:
    """A measured transition element."""

    path: str
    width: int
    height: int
    fps: float
    frame_count: int
    pix_fmt: str
    alpha: AlphaMeasurement

    @property
    def duration_seconds(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0

    def as_dict(self) -> dict:
        return {
            "path": self.path,
            "width": self.width,
            "height": self.height,
            "fps": self.fps,
            "frame_count": self.frame_count,
            "duration_seconds": round(self.duration_seconds, 6),
            "pix_fmt": self.pix_fmt,
            "alpha": self.alpha.as_dict(),
        }


def _ffprobe_stream(path: str, timeout: int = 30) -> dict:
    """The element's video stream properties, or raise."""
    cmd = [
        "ffprobe", "-v", "error", "-select_streams", "v:0",
        "-show_entries",
        "stream=width,height,pix_fmt,nb_frames,avg_frame_rate,r_frame_rate,duration",
        "-of", "default=noprint_wrappers=1", path,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise ElementUnmeasurable(
            f"ffprobe is not on PATH, so {path!r} cannot be measured. "
            f"An element is admitted on a measurement, never on its "
            f"filename or its extension.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ElementUnmeasurable(
            f"ffprobe timed out after {timeout}s on {path!r}") from exc
    if res.returncode != 0:
        raise ElementUnmeasurable(
            f"ffprobe could not read {path!r}: "
            f"{res.stderr.strip() or 'no stderr'}")

    fields: dict = {}
    for line in res.stdout.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            fields[k.strip()] = v.strip()
    if not fields.get("width"):
        raise ElementUnmeasurable(
            f"{path!r} has no video stream ffprobe could describe")
    return fields


def _rate(value: str) -> float:
    """A `num/den` frame rate as a float, or 0.0."""
    if not value or value == "0/0":
        return 0.0
    if "/" in value:
        num, _, den = value.partition("/")
        try:
            den_f = float(den)
            return float(num) / den_f if den_f else 0.0
        except ValueError:
            return 0.0
    try:
        return float(value)
    except ValueError:
        return 0.0


def measure_alpha(path: str, timeout: int = 300, *,
                  video_decoder: str | None = None) -> AlphaMeasurement:
    """Read the element's alpha channel, frame by frame.

    ``alphaextract`` fails outright when the input has no alpha plane -
    ffmpeg exits non-zero having written no frames - so ABSENT and EMPTY
    are two different results here and not one. The validation that says
    so is in ``tests/test_transition_overlay.py``, against a
    known-opaque, a known-empty and a known-partial control, because an
    instrument that reports zero has to be shown reporting non-zero on
    something known first.

    ``video_decoder`` names the ``-c:v`` decoder for the input, and is
    None (ffmpeg's default) unless the container needs otherwise.
    Measured 2026-09-08: the NATIVE ``vp9`` decoder drops WebM alpha
    (``alphaextract`` sees no plane on a file Chrome paints with alpha),
    while ``libvpx-vp9`` recovers every frame - all 45 of a 1.5s bumper
    mezzanine. So a WebM source is measured with ``libvpx-vp9`` (``libvpx``
    for VP8, same mechanism) and everything else with the default. The
    parameter exists so there stays ONE alpha instrument rather than two
    that could disagree.
    """
    cmd = [
        "ffmpeg", "-v", "error",
    ]
    if video_decoder:
        cmd += ["-c:v", video_decoder]
    cmd += [
        "-i", path,
        "-vf",
        ("alphaextract,format=gray,signalstats,"
         "metadata=print:file=-"),
        "-f", "null", "-",
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True,
                             encoding="utf-8", errors="replace",
                             timeout=timeout, check=False)
    except FileNotFoundError as exc:
        raise ElementUnmeasurable(
            f"ffmpeg is not on PATH, so the alpha channel of {path!r} "
            f"cannot be measured.") from exc
    except subprocess.TimeoutExpired as exc:
        raise ElementUnmeasurable(
            f"ffmpeg timed out after {timeout}s measuring {path!r}") from exc

    frames_read = 0
    max_alpha = 0
    fully_opaque = 0
    peak_mean = 0.0
    first_drawing: Optional[int] = None
    last_drawing: Optional[int] = None
    current: Optional[int] = None
    stats: dict = {}

    def close_frame():
        """Fold one frame's three statistics into the running answer."""
        nonlocal frames_read, max_alpha, fully_opaque, peak_mean
        nonlocal first_drawing, last_drawing
        if "YMAX" not in stats:
            return
        frames_read += 1
        ymax = int(round(stats["YMAX"]))
        max_alpha = max(max_alpha, ymax)
        peak_mean = max(peak_mean, stats.get("YAVG", 0.0))
        # A frame every pixel of which is fully opaque covers the picture.
        if int(round(stats.get("YMIN", 0.0))) >= 255:
            fully_opaque += 1
        if ymax > 0 and current is not None:
            if first_drawing is None:
                first_drawing = current
            last_drawing = current
        stats.clear()

    for line in res.stdout.splitlines():
        frame_match = _FRAME_LINE.match(line)
        if frame_match:
            close_frame()
            current = int(frame_match.group(1))
            continue
        stat_match = _STAT_LINE.match(line)
        if stat_match:
            stats[stat_match.group(1)] = float(stat_match.group(2))
    close_frame()

    if frames_read == 0:
        # Not "alpha is zero" - the plane is not there at all. The two
        # need different words because they need different fixes.
        raise ElementHasNoAlpha(
            f"{path!r} carries no alpha plane, so there is nothing to "
            f"composite over the cut. {ALPHA_IS_REQUIRED_NOT_KEYED} "
            f"(ffmpeg said: {res.stderr.strip().splitlines()[0] if res.stderr.strip() else 'no frames'})")

    return AlphaMeasurement(
        frames_read=frames_read,
        max_alpha=max_alpha,
        first_drawing_frame=first_drawing,
        last_drawing_frame=last_drawing,
        fully_opaque_frames=fully_opaque,
        peak_mean_alpha=peak_mean,
    )


def measure_element(path: str, *,
                    video_decoder: str | None = None) -> Element:
    """Measure a transition element, or refuse it by name.

    A file on disk is not a measurement (AGENTS.md 10.3): this opens the
    element and reads its pixels. Refuses when the alpha plane is absent
    (:class:`ElementHasNoAlpha`) and when it is present and empty
    (:class:`ElementDrawsNothing`). ``video_decoder`` is
    :func:`measure_alpha`'s - a WebM element measured with the default
    decoder would be refused as having no alpha when the plane is there
    and the decoder dropped it.
    """
    if not os.path.isfile(path):
        raise ElementUnmeasurable(f"element {path!r} is not a file")

    fields = _ffprobe_stream(path)
    fps = _rate(fields.get("avg_frame_rate", "")) or _rate(
        fields.get("r_frame_rate", ""))
    if not fps:
        raise ElementUnmeasurable(
            f"{path!r} reports no frame rate, so its length in frames "
            f"cannot be established")

    alpha = measure_alpha(path, video_decoder=video_decoder)
    if not alpha.draws:
        raise ElementDrawsNothing(
            f"{path!r} has an alpha plane and it is zero on all "
            f"{alpha.frames_read} frames - it would composite as nothing "
            f"at all. An overlay that draws nothing is not placed "
            f"(AGENTS.md 10.2).")

    declared_frames = fields.get("nb_frames")
    try:
        frame_count = int(declared_frames) if declared_frames else 0
    except ValueError:
        frame_count = 0
    # The alpha pass decoded every frame, so its own count is the one
    # that was actually read. A container's nb_frames is a claim.
    if alpha.frames_read:
        frame_count = alpha.frames_read

    return Element(
        path=path,
        width=int(fields["width"]),
        height=int(fields["height"]),
        fps=fps,
        frame_count=frame_count,
        pix_fmt=fields.get("pix_fmt", ""),
        alpha=alpha,
    )


def element_is_reachable(path: str) -> tuple[bool, str]:
    """Whether this element can really be placed, and why not if not.

    Derived, never declared. ``motion_graphics_vocabulary`` records
    reachability per entry because a roster written around a broken
    renderer bakes the defect in; the same rule applied here means the
    answer is a measurement taken now, not a flag somebody set once.
    """
    try:
        measure_element(path)
    except TransitionOverlayError as exc:
        return False, str(exc)
    return True, ""


# ── The declaration ──────────────────────────────────────────────────

DECLARATION_KEY = "transition_overlay"


def declared_overlay(effect: dict[str, Any] | None) -> dict | None:
    """The ``effect.transition_overlay`` slot, or None when nothing is
    declared. Declare nothing and get nothing - the opt-in shape
    ``content.bookends`` and ``effect.timed_text_overlay`` already take."""
    if not effect:
        return None
    raw = effect.get(DECLARATION_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise TransitionOverlayError(
            f"effect.{DECLARATION_KEY} must be a mapping, got "
            f"{type(raw).__name__}")
    return raw


def resolve_declaration(brand_effect: dict[str, Any] | None,
                        project_folder: str | None) -> dict[str, Any]:
    """The effect dict to plan from: the PROJECT's element wins.

    A transition element is artwork, and artwork belongs to the project
    (AGENTS.md 14). The precedence and the whole-slot replacement are
    ``timed_text_overlay.resolve_declaration``'s, for the identical
    reason: half a declaration from each of two sources is an element
    nobody designed.

    **Today the project is the ONLY declarer.**
    ``library/schemas/brand_template.EffectSlots`` carries no
    ``transition_overlay`` field, so a template cannot write one and
    ``reel_build`` passes ``{}`` here rather than reading a template for a
    key it cannot hold - a declaration that does not bind is the defect,
    whether or not today's value happens to be absent. Whether a template
    should gain the slot is a section 14 question about where the line
    between per-series PARAMETERS and ARTWORK falls, and it is not
    answered by implication: this signature takes the brand half so that
    adding the field is the whole change, and nothing here assumes it
    will be added.
    """
    effect = dict(brand_effect or {})
    declaration = _project_declaration(project_folder)
    if declaration is None:
        return effect
    effect[DECLARATION_KEY] = declaration
    return effect


def _project_declaration(project_folder: str | None) -> dict | None:
    """``effect.transition_overlay`` out of a project.yaml, or None."""
    if not project_folder:
        return None
    project_yaml = os.path.join(project_folder, "project.yaml")
    if not os.path.exists(project_yaml):
        return None

    import yaml

    with open(project_yaml, "r", encoding="utf-8") as f:
        config = yaml.safe_load(f) or {}
    if not isinstance(config, dict):
        raise TransitionOverlayError(
            f"{project_yaml} does not parse as a mapping")
    effect = config.get("effect") or {}
    if not isinstance(effect, dict):
        raise TransitionOverlayError(
            f"{project_yaml} has an `effect:` that is not a mapping")
    return declared_overlay(effect)


@dataclass(frozen=True)
class ResolvedElement:
    """A declaration resolved to a file, an anchor and a length."""

    gesture: str = field(default="", kw_only=True)
    """What this element DOES to the cut, measured off its own alpha.

    :data:`GESTURE_HIDES` when some frame covers the picture completely,
    :data:`GESTURE_STAMPS` when it draws over the cut and leaves it
    visible, and empty in ``composition`` mode where there is no file to
    measure yet. Both gestures are legitimate and neither is refused;
    what is refused is not knowing which one a declaration bought.

    Measured 2026-09-07 on the only two elements the field-test project
    owns: ``transition_bumper.mov`` peaks at a mean alpha of 1.358 of
    255 (0.53% of the frame) and ``logo_reveal.mov`` at 3.065 (1.2%).
    Neither has a single fully opaque frame, so on the captain's own
    reels this capability delivers a brand stamp over the cut, not a
    cover of it - and the picture agrees, which is what
    ``docs/CHROMA_KEY_TRANSITIONS_MEASURED.md`` shows.
    """

    anchor: str
    mode: str
    """``asset`` or ``composition``."""
    path: str
    """Where the element is, or where a composition WILL be rendered."""
    composition: Optional[str]
    source: Optional[str]
    duration_seconds: float
    declared_duration_seconds: Optional[float]

    def as_dict(self) -> dict:
        return {
            "anchor": self.anchor,
            "mode": self.mode,
            "path": self.path,
            "composition": self.composition,
            "source": self.source,
            "duration_seconds": round(self.duration_seconds, 6),
            "declared_duration_seconds": self.declared_duration_seconds,
            "gesture": self.gesture,
        }


GESTURE_HIDES = "hides_the_cut"
GESTURE_STAMPS = "stamps_the_cut"
"""What an element does to the cut it sits on.

`hides_the_cut` means at least one frame covers the picture completely,
so the shot change happens behind it - the classic sweep or bumper. 
`stamps_the_cut` means the element draws over the cut and the cut stays
visible underneath - a brand mark or a graphic accent.

Both are real transitions and this module refuses neither. The
distinction exists because it is invisible until the element is on a
timeline, and because the two are asked for in the same words.
"""


def gesture_of(alpha: AlphaMeasurement) -> str:
    return GESTURE_HIDES if alpha.hides_the_frame else GESTURE_STAMPS


OVERLAY_RENDER_DIRNAME = "transition_overlays"
"""Where a composition-mode element is rendered to, under the project's
own output. Named here so a renderer and a placer cannot disagree about
the filename - the reason ``bookends.BOOKEND_RENDER_DIRNAME`` exists."""


def overlay_render_path(project_folder: str, composition: str) -> str:
    return os.path.join(project_folder, "pipeline_output",
                        OVERLAY_RENDER_DIRNAME, f"{composition}.mov")


def resolve_element(declaration: dict[str, Any],
                    project_folder: str | None,
                    *,
                    measure: bool = True) -> ResolvedElement:
    """Turn a declaration into a file, an anchor and a length, or refuse.

    ``measure=False`` resolves the declaration without opening the file -
    for a composition that has not been rendered yet, and for tests that
    are checking the declaration rules rather than a picture.
    """
    if not isinstance(declaration, dict):
        raise TransitionOverlayError(
            f"a transition_overlay declaration must be a mapping, got "
            f"{type(declaration).__name__}")

    anchor = declaration.get("anchor")
    if anchor is None:
        raise TransitionOverlayError(
            f"transition_overlay declares no `anchor`. It is required and "
            f"has no default: where the element sits relative to the cut "
            f"IS the gesture, and an engine-supplied default would be a "
            f"creative choice nobody made (AGENTS.md 10.5). One of: "
            f"{', '.join(ANCHORS)}.")
    if anchor not in ANCHORS:
        raise TransitionOverlayError(
            f"transition_overlay anchor {anchor!r} is not one of "
            f"{', '.join(ANCHORS)}")

    element = declaration.get("element")
    if not isinstance(element, dict):
        raise TransitionOverlayError(
            "transition_overlay declares no `element` mapping. It names "
            "either an `asset:` that already exists or a `composition:` "
            "to render - the two modes `content.bookends` uses.")

    asset = element.get("asset")
    composition = element.get("composition")
    if bool(asset) == bool(composition):
        raise TransitionOverlayError(
            "transition_overlay.element names "
            + ("both `asset` and `composition`" if asset else
               "neither `asset` nor `composition`")
            + ". Exactly one, so it is unambiguous which one drew what "
              "reached the frame.")

    raw_duration = element.get("duration_seconds")
    declared_duration = (float(raw_duration)
                         if raw_duration is not None else None)
    if declared_duration is not None and declared_duration <= 0:
        raise TransitionOverlayError(
            f"transition_overlay duration_seconds is {declared_duration}, "
            f"which draws for no time at all")

    if composition:
        if declared_duration is None:
            raise TransitionOverlayError(
                f"transition_overlay names composition {composition!r} and "
                f"no duration_seconds. A composition has no file to "
                f"measure, so its length has to be declared.")
        source = element.get("source")
        if source:
            # Resolved for its side effect: a source that is not there is
            # a declaration that will render nothing, and it says so now
            # rather than at render time.
            try:
                source = resolve_project_asset(source, project_folder)
            except ProjectAssetNotFoundError as exc:
                raise TransitionOverlayError(
                    f"transition_overlay composition {composition!r} names "
                    f"source {element.get('source')!r}: {exc}") from exc
        if not project_folder:
            raise TransitionOverlayError(
                f"transition_overlay names composition {composition!r} and "
                f"no project folder was given, so there is nowhere for it "
                f"to have been rendered to")
        path = overlay_render_path(project_folder, composition)
        if not os.path.exists(path):
            # NOTHING ON THE REELS PROCESS RENDERS THIS, and saying so is
            # the whole point of the refusal. `content.bookends`'
            # composition mode works because step 4.06
            # (`render_motion_graphics`) exists on the edit_video graph
            # to render it. The reels process is two nodes -
            # `build_reels` and `verify_reels` - and neither renders
            # anything. Accepting the declaration and placing nothing
            # would be a vocabulary entry that does not draw, which is
            # the exact defect this module was written under.
            raise TransitionOverlayError(
                f"transition_overlay names composition {composition!r} and "
                f"nothing has rendered it to {path}. NO STEP OF THE REELS "
                f"PROCESS RENDERS A COMPOSITION: that process is "
                f"`build_reels` and `verify_reels`, and the renderer that "
                f"serves `content.bookends` lives on the edit_video graph "
                f"at step 4.06. Until a reels-side renderer exists, render "
                f"the composition yourself and declare the result with "
                f"`asset:` - or put the file at that path and this "
                f"declaration measures and places it like any other "
                f"element.")
        measured = measure_element(path)
        return ResolvedElement(
            anchor=anchor, mode="composition", path=path,
            composition=composition, source=source,
            duration_seconds=measured.duration_seconds,
            declared_duration_seconds=declared_duration,
            gesture=gesture_of(measured.alpha),
        )

    try:
        path = resolve_project_asset(asset, project_folder)
    except ProjectAssetNotFoundError as exc:
        raise TransitionOverlayError(
            f"transition_overlay asset {asset!r}: {exc}") from exc

    if not measure:
        return ResolvedElement(
            anchor=anchor, mode="asset", path=path, composition=None,
            source=None,
            duration_seconds=declared_duration or 0.0,
            declared_duration_seconds=declared_duration,
            gesture="",
        )

    measured = measure_element(path)
    duration = measured.duration_seconds
    if declared_duration is not None:
        # One frame of the element's own rate is the resolution of the
        # medium - mechanical, not editorial (AGENTS.md 10.5).
        tolerance = 1.0 / measured.fps if measured.fps else 0.0
        if abs(declared_duration - duration) > tolerance:
            raise TransitionOverlayError(
                f"transition_overlay declares duration_seconds="
                f"{declared_duration} for {os.path.basename(path)}, which "
                f"measures {duration:.3f}s ({measured.frame_count} frames "
                f"at {measured.fps:g}fps). A declaration that disagrees "
                f"with the file is believed by nobody: fix whichever is "
                f"wrong rather than letting the placement use one and the "
                f"reader the other.")
    return ResolvedElement(
        anchor=anchor, mode="asset", path=path, composition=None,
        source=None, duration_seconds=duration,
        declared_duration_seconds=declared_duration,
        gesture=gesture_of(measured.alpha),
    )


# ── The cuts a reel has ──────────────────────────────────────────────

@dataclass(frozen=True)
class Seam:
    """One boundary on a built reel, and whether it can carry an element.

    This module states a fact per seam and chooses none of them, the same
    shape ``transition_carriers.cut_carriers`` takes for the edit_video
    route. Which cut gets a transition is a creative decision and stays
    with whoever is making it (AGENTS.md 10.5).
    """

    index: int
    """Position in the seam list, so a caller can name one."""
    reel_frame: int
    """The frame of the reel the cut lands on. The first frame of the
    picture AFTER the cut, which is how `placements` lays ranges down."""
    kind: str
    carries: bool
    basis: str
    """Why it can or cannot carry an element, in words."""
    master_out: Optional[float] = None
    master_in: Optional[float] = None
    """The master seconds either side of the cut, when there are two."""

    def as_dict(self) -> dict:
        return {
            "index": self.index, "reel_frame": self.reel_frame,
            "kind": self.kind, "carries": self.carries, "basis": self.basis,
            "master_out": self.master_out, "master_in": self.master_in,
        }


def reel_seams(ranges: Sequence[tuple], fps: float,
               closer_seam_frame: Optional[int] = None) -> list[Seam]:
    """Every boundary on a reel built from ``ranges``, in reel frames.

    The arithmetic is ``reel_build.placements``' arithmetic, in frames and
    in the same order, because an element that lands one frame off the
    cut it is hiding is worse than no element: it exposes the jump AND
    covers a frame of the shot. Both walk the ranges in list order and
    accumulate ``round(end*fps) - round(start*fps)``.

    The head and the tail of the reel are reported as seams that do NOT
    carry, rather than omitted, because "there is no cut there" is an
    answer a caller may need and silence is not one.
    """
    seams: list[Seam] = []
    seams.append(Seam(
        index=0, reel_frame=0, kind=SEAM_REEL_HEAD, carries=False,
        basis=("the first frame of the reel - there is no outgoing shot "
               "for an element to hide the cut from"),
        master_out=None,
        master_in=float(ranges[0][0]) if ranges else None,
    ))

    cursor = 0
    for i in range(len(ranges)):
        start, end = float(ranges[i][0]), float(ranges[i][1])
        cursor += int(round(end * fps)) - int(round(start * fps))
        if i == len(ranges) - 1:
            seams.append(Seam(
                index=len(seams), reel_frame=cursor, kind=SEAM_REEL_TAIL,
                carries=False,
                basis=("the last frame of the reel - there is no incoming "
                       "shot to cut to"),
                master_out=end, master_in=None,
            ))
            break
        next_start = float(ranges[i + 1][0])
        is_closer = (closer_seam_frame is not None
                     and cursor == closer_seam_frame)
        seams.append(Seam(
            index=len(seams), reel_frame=cursor,
            kind=SEAM_CLOSER if is_closer else SEAM_TAKE_REMOVED,
            carries=True,
            basis=(f"the picture jumps from master {end:.3f}s to "
                   f"{next_start:.3f}s here"),
            master_out=end, master_in=next_start,
        ))
    return seams


def carrying_seams(seams: Sequence[Seam]) -> list[Seam]:
    """The seams an element can sit on. A filter the CALLER applies, not
    one this module applies to its own answer."""
    return [s for s in seams if s.carries]


# ── Placing an element ───────────────────────────────────────────────

@dataclass(frozen=True)
class OverlayPlacement:
    """One element, on one seam, at exact frames of the reel."""

    seam_index: int
    seam_kind: str
    record_frame: int
    duration_frames: int
    """How long the element occupies the reel, in TIMELINE frames. This is
    what `record_frame` is measured in and what F18 grades."""
    track_index: int
    element_path: str
    anchor: str
    element_seconds: float = 0.0
    """The element's own length in SECONDS, carried because Resolve's
    `AppendToTimeline` takes `startFrame`/`endFrame` in the POOL ITEM's
    own frames, not the timeline's (AGENTS.md 5). The Lucie bumper is
    30fps on a 23.976 timeline, so its 36 timeline frames are 45 of its
    own; converting through `duration_frames` instead would round twice
    and drift a frame at some rates."""

    @property
    def end_frame(self) -> int:
        return self.record_frame + self.duration_frames

    def as_dict(self) -> dict:
        return {
            "seam_index": self.seam_index, "seam_kind": self.seam_kind,
            "record_frame": self.record_frame,
            "duration_frames": self.duration_frames,
            "end_frame": self.end_frame,
            "track_index": self.track_index,
            "element_path": self.element_path, "anchor": self.anchor,
            "element_seconds": round(self.element_seconds, 6),
        }


def anchor_record_frame(seam_frame: int, duration_frames: int,
                        anchor: str) -> int:
    """Where the element starts, given the cut and the anchor.

    ``centre`` puts the cut inside the element. An odd number of frames
    cannot be halved exactly, and the extra frame goes to the OUTGOING
    side (`//2` of the duration is subtracted, so the longer half is
    before the cut) - which is arithmetic, not a preference: it has to go
    one way, and it is written down so a reader can see which.
    """
    if anchor == "before":
        return seam_frame - duration_frames
    if anchor == "after":
        return seam_frame
    if anchor == "centre":
        return seam_frame - (duration_frames + 1) // 2
    raise TransitionOverlayError(
        f"anchor {anchor!r} is not one of {', '.join(ANCHORS)}")


def place_overlays(seams: Sequence[Seam],
                   chosen: Sequence[int],
                   element: ResolvedElement,
                   fps: float,
                   reel_frames: int,
                   track_index: int = OVERLAY_TRACK,
                   ) -> list[OverlayPlacement]:
    """Place the element on each chosen seam, or refuse by name.

    ``chosen`` is a list of seam indexes. WHICH seams get an element is
    not decided here - this places what it is handed and says no to what
    it cannot place.

    Refuses on: a seam that is not a cut, an element that would run off
    either end of the reel, and two elements that would collide on the
    track. None of those is clamped or dropped, because a silently
    shortened or missing element is indistinguishable from one nobody
    asked for.
    """
    duration_frames = int(round(element.duration_seconds * fps))
    if duration_frames <= 0:
        raise TransitionOverlayError(
            f"the element measures {element.duration_seconds:.4f}s, which "
            f"is under one frame at {fps:g}fps - it would occupy no time "
            f"on the timeline")

    by_index = {s.index: s for s in seams}
    placements: list[OverlayPlacement] = []
    for seam_index in chosen:
        seam = by_index.get(seam_index)
        if seam is None:
            raise NotASeam(
                f"seam {seam_index} does not exist on this reel; it has "
                f"{len(seams)} seams (0..{len(seams) - 1})")
        if not seam.carries:
            raise NotASeam(
                f"seam {seam_index} is the {seam.kind} and carries no "
                f"transition: {seam.basis}")

        record = anchor_record_frame(seam.reel_frame, duration_frames,
                                     element.anchor)
        if record < 0:
            raise OverlayDoesNotFit(
                f"the element is {duration_frames} frames and anchored "
                f"{element.anchor!r} on the cut at frame "
                f"{seam.reel_frame}, so it would start at frame {record} - "
                f"before the reel begins. Nothing is clamped: a shortened "
                f"element is a different gesture delivered under the same "
                f"name.")
        if record + duration_frames > reel_frames:
            raise OverlayDoesNotFit(
                f"the element is {duration_frames} frames and anchored "
                f"{element.anchor!r} on the cut at frame "
                f"{seam.reel_frame}, so it would end at frame "
                f"{record + duration_frames} on a reel of {reel_frames} "
                f"frames.")
        placements.append(OverlayPlacement(
            seam_index=seam.index, seam_kind=seam.kind, record_frame=record,
            duration_frames=duration_frames, track_index=track_index,
            element_path=element.path, anchor=element.anchor,
            element_seconds=element.duration_seconds,
        ))

    assert_no_collisions(placements)
    return placements


def assert_no_collisions(placements: Sequence[OverlayPlacement]) -> None:
    """Two elements may not occupy the same frames of one track.

    Resolve's ``AppendToTimeline`` places one and silently drops the
    other - the F9 defect class in ``reel_conformance_verifier``, found
    when a placement loop produced items nobody could see. Refused here,
    by both seam numbers, so the two cuts that are too close together are
    named rather than one of them quietly losing its element.
    """
    ordered = sorted(placements, key=lambda p: (p.track_index,
                                                p.record_frame))
    for earlier, later in zip(ordered, ordered[1:]):
        if earlier.track_index != later.track_index:
            continue
        if later.record_frame < earlier.end_frame:
            raise OverlaysCollide(
                f"the elements on seams {earlier.seam_index} and "
                f"{later.seam_index} overlap on V{earlier.track_index}: "
                f"frames {earlier.record_frame}..{earlier.end_frame} and "
                f"{later.record_frame}..{later.end_frame}. Two clips "
                f"cannot occupy the same frames of one track.")


# ── Which seams a declaration asks for ───────────────────────────────

SELECTABLE_KINDS = (SEAM_TAKE_REMOVED, SEAM_CLOSER)
"""The seam kinds a declaration may name. The head and tail of the reel
are not here because they are not cuts."""


def select_seams(declaration: dict[str, Any],
                 seams: Sequence[Seam]) -> list[int]:
    """The seam indexes this declaration asks for.

    A declaration says WHERE it wants elements, by seam kind (``on_cuts:``) or
    by explicit index (``seams:``), and it must say one of them. There is
    no default and no "everywhere" fallback, because which cuts get a
    transition is the editorial decision this module must not take
    (AGENTS.md 10.5).

        effect:
          transition_overlay:
            on_cuts: [closer]          # every seam of that kind
            # -- and/or --
            seams: [3, 7]         # exactly these

    An ``on_cuts:`` that matches no seam on this reel returns nothing and says
    so to its caller by returning an empty list - which the caller
    reports. A reel with no cut of that kind is a real answer.
    """
    kinds = declaration.get("on_cuts")
    explicit = declaration.get("seams")
    if kinds is None and explicit is None:
        raise TransitionOverlayError(
            f"transition_overlay declares neither `on_cuts:` (seam kinds) nor "
            f"`seams:` (explicit indexes), so nothing says which cuts get "
            f"an element. There is no default: choosing where a transition "
            f"goes is an editorial decision (AGENTS.md 10.5). Kinds "
            f"available: {', '.join(SELECTABLE_KINDS)}.")

    chosen: list[int] = []
    if kinds is not None:
        if isinstance(kinds, str):
            kinds = [kinds]
        unknown = [k for k in kinds if k not in SELECTABLE_KINDS]
        if unknown:
            raise TransitionOverlayError(
                f"transition_overlay `on_cuts:` names {unknown!r}, which are not "
                f"seam kinds an element can sit on. One of: "
                f"{', '.join(SELECTABLE_KINDS)}.")
        chosen.extend(s.index for s in seams
                      if s.carries and s.kind in kinds)
    if explicit is not None:
        for raw in explicit:
            index = int(raw)
            if index not in {s.index for s in seams}:
                raise NotASeam(
                    f"transition_overlay `seams:` names {index}, and this "
                    f"reel has seams 0..{len(seams) - 1}")
            if index not in chosen:
                chosen.append(index)
    return sorted(set(chosen))


@dataclass(frozen=True)
class ReelOverlayPlan:
    """Everything one reel's overlay pass produced, including nothing."""

    seams: list[Seam]
    placements: list[OverlayPlacement]
    element: Optional[ResolvedElement]
    reel_frames: int
    reason_empty: str = ""
    """Why no element was placed, when none was. An empty plan that does
    not say why is indistinguishable from a declaration nobody wrote -
    the defect `vfx_plan_basis` exists for (AGENTS.md 10.2)."""

    def as_dict(self) -> dict:
        return {
            "reel_frames": self.reel_frames,
            "seams": [s.as_dict() for s in self.seams],
            "placements": [p.as_dict() for p in self.placements],
            "element": self.element.as_dict() if self.element else None,
            "reason_empty": self.reason_empty,
        }


def reel_frame_count(ranges: Sequence[tuple], fps: float) -> int:
    """How many frames a reel built from these ranges runs for.

    `reel_build.placements`' arithmetic, so the fit check and the
    placement agree about where the reel ends.
    """
    total = 0
    for start, end in ranges:
        total += int(round(float(end) * fps)) - int(round(float(start) * fps))
    return total


def plan_reel_overlays(effect: dict[str, Any] | None,
                       ranges: Sequence[tuple],
                       fps: float,
                       project_folder: str | None,
                       closer_seam_frame: Optional[int] = None,
                       track_index: int = OVERLAY_TRACK,
                       ) -> ReelOverlayPlan:
    """One reel's overlay transitions, from the declaration to frames.

    Returns a plan with an empty `placements` and a stated reason when
    nothing is declared or nothing matches - never a bare empty list.
    Raises when a declaration exists and cannot be honoured, because a
    declared element that silently does not appear is the defect this
    whole area keeps producing.
    """
    seams = reel_seams(ranges, fps, closer_seam_frame)
    reel_frames = reel_frame_count(ranges, fps)

    declaration = declared_overlay(effect)
    if declaration is None:
        return ReelOverlayPlan(
            seams=seams, placements=[], element=None,
            reel_frames=reel_frames,
            reason_empty=("no effect.transition_overlay is declared - "
                          "declare nothing and get nothing"))

    element = resolve_element(declaration, project_folder)
    chosen = select_seams(declaration, seams)
    if not chosen:
        carrying = carrying_seams(seams)
        return ReelOverlayPlan(
            seams=seams, placements=[], element=element,
            reel_frames=reel_frames,
            reason_empty=(
                f"the declaration asks for {declaration.get('on_cuts')!r} and "
                f"this reel has {len(carrying)} carrying seam(s), of kinds "
                f"{sorted({s.kind for s in carrying}) or 'none'}"))

    placements = place_overlays(seams, chosen, element, fps, reel_frames,
                                track_index=track_index)
    return ReelOverlayPlan(seams=seams, placements=placements,
                           element=element, reel_frames=reel_frames)


# ── What it costs the captions ───────────────────────────────────────

@dataclass(frozen=True)
class CaptionCoverage:
    """One caption card an element draws over, and for how long."""

    seam_index: int
    caption_start_frame: int
    caption_end_frame: int
    covered_frames: int
    covered_seconds: float
    caption_name: str = ""

    def as_dict(self) -> dict:
        return {
            "seam_index": self.seam_index,
            "caption_start_frame": self.caption_start_frame,
            "caption_end_frame": self.caption_end_frame,
            "covered_frames": self.covered_frames,
            "covered_seconds": round(self.covered_seconds, 4),
            "caption_name": self.caption_name,
        }


def captions_covered(placements: Sequence[OverlayPlacement],
                     caption_spans: Sequence[tuple],
                     fps: float) -> list[CaptionCoverage]:
    """Which caption cards an element draws over, and for how long.

    An element that hides a cut hides everything on that frame. That is
    what the gesture is, and whether it is wanted is the captain's -
    but it is a FACT about the delivered picture, so it is measured and
    reported rather than discovered on the timeline. No threshold: every
    overlap is returned, including a single frame, and the reader decides.

    ``caption_spans`` is ``(start_frame, end_frame[, name])`` per card,
    in reel frames - the shape ``reel_conformance_verifier.TimelineItem``
    already carries.
    """
    out: list[CaptionCoverage] = []
    for placement in placements:
        for span in caption_spans:
            start, end = int(span[0]), int(span[1])
            name = str(span[2]) if len(span) > 2 else ""
            overlap = (min(end, placement.end_frame)
                       - max(start, placement.record_frame))
            if overlap > 0:
                out.append(CaptionCoverage(
                    seam_index=placement.seam_index,
                    caption_start_frame=start, caption_end_frame=end,
                    covered_frames=overlap,
                    covered_seconds=overlap / fps if fps else 0.0,
                    caption_name=name,
                ))
    return out


# ── Reading it ───────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(
        description="Measure a transition element, or explain the route.")
    parser.add_argument("--measure", metavar="ELEMENT",
                        help="measure one element file's alpha and shape")
    args = parser.parse_args(argv)

    if not args.measure:
        print(ALPHA_IS_REQUIRED_NOT_KEYED)
        print()
        print(f"anchors: {', '.join(ANCHORS)}")
        print(f"overlay track on a reel without the look: V{OVERLAY_TRACK} "
              f"(the plan owns the index - V5 on a two-angle reel "
              f"wearing the TV-frame look)")
        print(f"timing is additive: {TIMING_IS_ADDITIVE}")
        return 0

    try:
        element = measure_element(args.measure)
    except TransitionOverlayError as exc:
        print(f"REFUSED: {exc}")
        return 1
    print(json.dumps(element.as_dict(), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
