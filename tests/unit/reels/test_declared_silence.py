"""Where a delivered file's silence is DECLARED, read from the project.

The defect this covers (QA-004, 2026-10-20): the delivery path judges
a delivered file's audio, and the silence half needs the seconds where
silence is DELIBERATE - Reel 13's silent end card, the TV-switch-off
tail. Without that vocabulary, a correct reel that ends on a silent
card is indistinguishable from a reel that lost its dialogue, and the
gate has to pick one: it would either fail a correct reel or pass a
broken one.

So the declarations that already exist are read, and nothing is
invented:

* a reel's own declared ending (`external/declarations/reel_ending.json`)
  - a `tv_power_tail` held with `tail_hold: freeze` plays a HELD
  frame, and the audio is not held, so the hold's seconds carry
  picture and no sound;
* the project's full-frame TAIL cards - a card carries no audio onto
  the timeline, so the card's seconds are silent too.

Both sit at the reel's END and they are contiguous, so the declared
silence is ONE window. A project that declares nothing gets `[]`, and
every digital-zero run under picture is then undeclared - the honest
reading.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

from library.tools.declared_silence import declared_silence_spans

FFMPEG = shutil.which("ffmpeg")
REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"


def _project(tmp_path, *, card_seconds: float = 2.0,
             ending: bool = True, card: bool = True) -> str:
    """A project with a tail card and (optionally) a declared ending."""
    effect = {}
    if card:
        effect = {"full_frame_elements": [{
            "element": "full_frame_clip", "placement": "tail",
            "asset": str(tmp_path / "closer.mov"),
            "reason": "captain 2026-09-11, Blue marker at source "
                      "frame 38: the logo reveal on every reel",
        }]}
    (tmp_path / "project.yaml").write_text(yaml.safe_dump({
        "name": "T", "slug": "t",
        "resolve": {"project_name": "Scratch"},
        "pipeline": {},
        "effect": effect,
    }), encoding="utf-8")
    if FFMPEG is not None:
        subprocess.run(
            ["ffmpeg", "-v", "error", "-y",
             "-f", "lavfi", "-i",
             f"color=c=0x101010:s=320x180:r=24:d={card_seconds}",
             "-c:v", "libx264", "-preset", "ultrafast",
             "-pix_fmt", "yuv420p", str(tmp_path / "closer.mov")],
            check=True, capture_output=True)
    if ending:
        declarations = tmp_path / "external" / "declarations"
        declarations.mkdir(parents=True, exist_ok=True)
        (declarations / "reel_ending.json").write_text(json.dumps({
            "version": 1,
            "endings": [{
                "reel": REEL,
                "ends_on": {"anchor_phrase": "the link's in our bio"},
                "tail_element": "tv_power_tail",
                "tail_hold": "freeze",
                "reason": "captain 2026-09-20 green marker @1712",
            }],
        }), encoding="utf-8")
    return str(tmp_path)


def test_a_declared_ending_and_a_tail_card_excuse_the_reels_tail(tmp_path):
    """The reel's declared silent tail: the freeze hold and the card
    after it, together, at the reel's end."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    folder = _project(tmp_path)

    spans = declared_silence_spans(
        folder, REEL, duration_seconds=20.0, fps=24.0)

    assert len(spans) == 1
    start, end = spans[0]
    assert end == pytest.approx(20.0)
    # The card is two seconds and the hold is the tail element's own
    # length, so the window opens before the card does.
    assert 17.0 < start < 18.5


def test_a_project_that_declares_nothing_declares_no_silence(tmp_path):
    """Nothing declared means nothing excused: every digital-zero run
    under picture is undeclared, and the silence check says so."""
    folder = _project(tmp_path, ending=False, card=False)

    assert declared_silence_spans(
        folder, REEL, duration_seconds=20.0, fps=24.0) == []


def test_a_reel_with_no_declared_ending_gets_no_hold(tmp_path):
    """The declaration is matched BY NAME: another reel's ending is
    not this reel's."""
    if FFMPEG is None:
        pytest.skip("ffmpeg not on PATH")
    folder = _project(tmp_path)

    other = declared_silence_spans(
        folder, "Reel 01 - another-reel", duration_seconds=20.0, fps=24.0)
    # The card is still declared for every reel this project builds, so
    # the card's window is there - but the HOLD belongs to Reel 13's
    # own declaration, and Reel 01 has none.
    assert len(other) == 1
    start, end = other[0]
    assert end == pytest.approx(20.0)
    assert start == pytest.approx(18.0, abs=0.2)
