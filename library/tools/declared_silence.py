"""Where a delivered file's silence is DECLARED. One enumeration.

Why this module exists
-----------------------
The delivery path judges a delivered file's audio (QA-004, 2026-10-20):
its loudness against the spec, the digital silence under picture that
catches MISSING DIALOGUE, and its channel presence. The silence half
needs one thing the other two do not: the seconds where silence is
DELIBERATE. A reel that ends on a silent end card is correct, and a
reel whose dialogue went missing is not - and the difference is a
declaration, not a number.

So this module answers exactly one question: **which seconds of this
delivered file are declared silent?** It reads the declarations that
already exist and adds nothing of its own:

* the compiled manifest's ending - `render_qa.declared_ending_spans`
  reads the TV-switch-off window, the silent end card and the logo
  closer off the manifest the renderer itself consumes. The main edit's
  own render gate (step 6.02) already reads that same vocabulary, so a
  reel that ends on a silent card is excused by one gate and condemned
  by the other no more;
* a reel's own declared ending - `library/tools/reel_ending.py`: a
  `tv_power_tail` ending held with `tail_hold: freeze` plays a HELD
  frame, and the audio is not held, so the hold's seconds carry
  picture and no sound;
* the project's full-frame TAIL cards - `library/tools/
  full_frame_element.py`: a card carries no audio onto the timeline, so
  the card's seconds are silent too. A card's length is its own
  declaration (or, for a clip, its own file's measured length).

What it does not do
-------------------
It does not measure anything, and it does not decide whether a silence
is acceptable - `render_qa.measure_silence_under_picture` owns that
rule, and the delivery path calls it with the spans this module
returns. It does not invent a silence nobody declared: a project that
declares nothing gets `[]`, and every digital-zero run under picture
is then undeclared, which is the honest reading.

The reels route
---------------
A reel's declared silence is ONE window at its END: the freeze hold
ends exactly where the first tail card starts (`reel_ending.
plan_cards` places the cards after the hold), so the hold and the
cards are contiguous and the declared silence runs from the start of
the hold to the end of the reel.
"""

from __future__ import annotations

import json
import os
from typing import List, Optional, Sequence, Tuple

Span = Tuple[float, float]


def _manifest_spans(project_folder: str) -> List[Span]:
    """The compiled manifest's declared ending, when the project has one."""
    try:
        from library.tools.project_layout import ProjectLayout

        path = (ProjectLayout(project_folder).step_dir("compile_manifest")
                / "assembly_manifest.json")
    except Exception:  # noqa: BLE001 - an unreadable layout declares nothing
        return []
    if not path.is_file():
        return []
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    if not isinstance(manifest, dict):
        return []
    from library.tools import render_qa

    spans = render_qa.declared_ending_spans(manifest)
    return [tuple(span) for span in (spans.get("silence") or ())]


def _tail_card_seconds(project_folder: str) -> float:
    """How long the project's TAIL cards play, in seconds.

    A card is the copy the viewer reads, and it carries no audio onto
    the timeline, so its seconds are silent. A card's length is its own
    declaration; a `full_frame_clip` lasts as long as its own file, and
    that file is measured (`full_frame_element.measure_clip`).
    """
    from library.tools import reel_build

    try:
        declarations = reel_build.declared_cards(project_folder)
    except Exception:  # noqa: BLE001 - a malformed declaration raises
        # at the build; here it declares nothing.
        return 0.0
    seconds = 0.0
    for declaration in declarations or ():
        if declaration.get("placement") != "tail":
            continue
        if declaration.get("element") == "full_frame_clip":
            seconds += _clip_seconds(declaration, project_folder)
            continue
        try:
            seconds += float(declaration.get("duration_seconds") or 0.0)
        except (TypeError, ValueError):
            continue
    return seconds


def _clip_seconds(declaration: dict, project_folder: str) -> float:
    """How long a clip card plays: as long as its own file."""
    from library.tools.full_frame_element import (
        measure_clip,
        resolve_clip_asset,
    )

    named = declaration.get("asset") or ""
    try:
        path = resolve_clip_asset(named, project_folder)
        measured = measure_clip(path)
    except Exception:  # noqa: BLE001 - an unreadable asset declares nothing
        return 0.0
    try:
        return float(measured.duration_seconds)
    except (AttributeError, TypeError, ValueError):
        return 0.0


def _reel_hold_seconds(project_folder: str, reel_name: str,
                       fps: float) -> float:
    """How long the reel's declared ending holds a frame, in seconds.

    The reel's own declaration (`external/declarations/reel_ending.json`)
    wins; a reel that declares none declares no silence.
    """
    if not reel_name or fps <= 0:
        return 0.0
    from library.tools import reel_ending

    try:
        ending = reel_ending.declared_ending(project_folder, reel_name)
    except Exception:  # noqa: BLE001 - a malformed declaration raises at
        # the build; here it declares nothing.
        return 0.0
    if not ending:
        return 0.0
    try:
        frames = reel_ending.ending_tail_frames(ending)
    except Exception:  # noqa: BLE001 - see above
        return 0.0
    return float(frames) / float(fps)


def declared_silence_spans(project_folder: str,
                           reel_name: str = "",
                           *,
                           duration_seconds: Optional[float] = None,
                           fps: float = 0.0) -> List[Span]:
    """The seconds of a delivered file where silence is DELIBERATE.

    `reel_name` is the delivered timeline's name - the reels route's
    reel, or "" for a timeline that is not a reel. `duration_seconds`
    is the delivered file's own length, which is where the reels
    route's declared silence sits (at its end).

    Returns `(start, end)` seconds, merged and sorted. Empty when
    nothing is declared silent - and empty means EVERY digital-zero run
    under picture is undeclared, which is the honest reading of a
    project that declared nothing.
    """
    spans: List[Span] = []
    spans.extend(_manifest_spans(project_folder))

    hold = _reel_hold_seconds(project_folder, reel_name, fps)
    cards = _tail_card_seconds(project_folder)
    silent = hold + cards
    if silent > 0 and duration_seconds:
        end = float(duration_seconds)
        start = max(0.0, end - silent)
        if end > start:
            spans.append((start, end))

    return _merge(spans)


def _merge(spans: Sequence[Span]) -> List[Span]:
    """Merge overlapping spans into sorted, non-overlapping windows."""
    ordered = sorted((float(start), float(end)) for start, end in spans
                     if float(end) > float(start))
    merged: List[Span] = []
    for start, end in ordered:
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            continue
        merged.append((start, end))
    return merged


__all__ = ["declared_silence_spans"]
