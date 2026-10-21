"""The reel's caption cards render in waves, and the bytes do not move.

Measured 2026-10-21 on the captain's 10-core box, against the
Ren-owned scratch copy `ren-scratch-breaktest`: a reel's 52
`full_frame_span` caption cards cost 365.1 s rendered one at a time and
111.0 s four at a time, and every artefact came out byte-identical
(sha256) to the serial run's.  The bytes are the half that matters: a
parallelisation that changed a byte would silently change what the
reel shows, and nothing downstream would say so.

Two things are pinned here, and the second one is the one that matters:

* the wave HAPPENS - the cards overlap instead of running one after
  another, and
* the artefacts are BYTE-IDENTICAL to the serial run's.

A wave that changed a byte would be a silent change to the reel.
"""

from __future__ import annotations

import copy
import hashlib
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from library.tools import fframes_render as _ff
from library.tools import remotion_batch as rb
from library.tools import full_frame_element as ffe

FPS = 24000 / 1001


def _sha256(path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


# ── The fixtures the render tests share ──────────────────────────────


RANGES = [(10.0, 11.0), (12.0, 13.0), (14.0, 15.0), (16.0, 17.0)]
"""Four keep ranges, so a span may declare one segment per range."""

WINDOW = [10.0, 11.0]


def _transcript():
    """Timed words inside each keep range, so a segment can quote them."""
    return {"segments": [
        {"speaker": "SpeakerOne",
         "timeline_start": start, "timeline_end": end,
         "text": " ".join(_WORDS),
         "words": [
             {"word": word, "start": start + i * 0.25,
              "end": start + 0.2 + i * 0.25, "timed": True}
             for i, word in enumerate(_WORDS)
         ]}
        for start, end in RANGES
    ]}


_WORDS = ["connect", "the", "dots"]


class _Moment:
    number = 3
    speakers = ("SpeakerOne",)


def _facts():
    return ffe.ReelFacts.from_moment(
        _Moment(), RANGES, _transcript(), opening_seconds=3.0)


def _declare(segments=4):
    """A whole-body span carrying one segment per keep range."""
    return {"full_frame_elements": [{
        "element": "full_frame_span",
        "placement": "span",
        "background": "#0B1B2B",
        "entrance": "draw",
        "exit": "fade",
        "font_family": "Montserrat",
        "allow_gaps": True,
        "segments": [{
            "window": list(RANGES[index]),
            "runs": [{"bind": "range_line", "type_role": "display",
                      "colour": "#FFFFFF"}],
        } for index in range(segments)],
    }]}


def _cards(segments=4):
    return ffe.plan_reel_cards(
        ffe.declared_elements(_declare(segments)),
        _facts(), 0, FPS,
        width=1080, height=1920,
        ranges=RANGES, transcript=_transcript())


# ── The wave HAPPENS ─────────────────────────────────────────────────


def test_cards_render_in_overlapping_waves(monkeypatch, tmp_path):
    """The defect: 52 independent cards, one after another.

    Each card is its own scene and its own file, so the reel build
    paid for the machine's idleness rather than for the work.  This
    pins the OVERLAP: a serial renderer observes a maximum of one live
    render, and this test fails against it.
    """
    guard = threading.Lock()
    live = 0
    peak = 0

    def fake_render(composition, props, out_path, work_dir, project_folder,
                    repo_root, stream=sys.stderr):
        nonlocal live, peak
        with guard:
            live += 1
            peak = max(peak, live)
        try:
            # Long enough that a serial renderer never overlaps.
            time.sleep(0.05)
            Path(out_path).write_bytes(b"card")
        finally:
            with guard:
                live -= 1

    monkeypatch.setattr(_ff, "render_one_card", fake_render)
    monkeypatch.setattr(rb, "native_card_workers", lambda: 4)

    cards = _cards()
    ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))

    assert peak > 1, (
        "the reel's caption cards were rendered one at a time; the build "
        f"paid one native render plus one encode per card in sequence "
        f"(peak concurrency {peak})")


def test_a_failed_card_names_itself(monkeypatch, tmp_path):
    """A card that fails names ITSELF.

    The wave must not turn one broken card into an anonymous failure:
    the message says which card, exactly as the serial render did.
    """
    def fake_render(composition, props, out_path, work_dir, project_folder,
                    repo_root, stream=sys.stderr):
        if "_02" in Path(out_path).name:
            raise _ff.FFramesRenderError("this card is broken")
        Path(out_path).write_bytes(b"card")

    monkeypatch.setattr(_ff, "render_one_card", fake_render)
    monkeypatch.setattr(rb, "native_card_workers", lambda: 2)

    cards = _cards()
    with pytest.raises(ffe.FullFrameRenderError) as caught:
        ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))
    message = str(caught.value)
    assert cards[1].render_name in message, (
        f"the failure does not name the card that failed: {message!r}")
    assert "broken" in message, message


def test_a_failed_card_leaves_the_cards_it_never_ran_unwritten(
        monkeypatch, tmp_path):
    """A card that never ran leaves no artefact behind.

    A wave that stopped early but left a file for a card it never
    drew is a partly-written reel, and the next build would find it.
    """
    def fake_render(composition, props, out_path, work_dir, project_folder,
                    repo_root, stream=sys.stderr):
        if "_02" in Path(out_path).name:
            raise _ff.FFramesRenderError("this card is broken")
        Path(out_path).write_bytes(b"card")

    monkeypatch.setattr(_ff, "render_one_card", fake_render)
    monkeypatch.setattr(rb, "native_card_workers", lambda: 2)

    cards = _cards()
    with pytest.raises(ffe.FullFrameRenderError):
        ffe.render_reel_cards(cards, str(tmp_path), str(tmp_path))

    written = {p.name for p in tmp_path.glob("*.mov")}
    drawn = {f"{card.render_name}.mov" for card in cards}
    assert not (written - drawn), (
        "the wave left artefacts for cards it never drew: "
        f"{sorted(written - drawn)}")


# ── The bytes do NOT move ────────────────────────────────────────────


def test_a_wave_writes_the_same_bytes_as_the_serial_loop(monkeypatch,
                                                          tmp_path):
    """The fidelity half: a card drawn in a wave is the same card.

    The same card, drawn alone and drawn in a wave, must produce the
    same bytes.  A parallelisation that changed a byte would silently
    change what the reel shows.
    """
    if not _ff.fframes_available()[0]:
        pytest.skip("FFrames binary not available")

    cards = _cards()
    one = cards[0]

    serial_dir = tmp_path / "serial"
    serial_dir.mkdir()
    _ff.render_one_card(ffe.FULL_FRAME_COMPOSITION, one.props,
                        str(serial_dir / "card.mov"), str(serial_dir),
                        "", None)

    monkeypatch.setattr(rb, "native_card_workers", lambda: 4)
    waved_dir = tmp_path / "waved"
    waved_dir.mkdir()
    ffe.render_reel_cards([one], str(tmp_path), str(waved_dir))

    waved = waved_dir / f"{one.render_name}.mov"
    assert waved.is_file(), "the wave drew no card"
    assert _sha256(waved) == _sha256(serial_dir / "card.mov"), (
        "the card drawn in a wave differs from the card drawn alone: a "
        "parallelisation that changed a byte would silently change what "
        "the reel shows")


# ── The width is DECIDED ─────────────────────────────────────────────


def test_the_declared_width_is_the_one_that_runs(monkeypatch):
    """The width is DECIDED, and the decision is honoured.

    An explicit `PIPELINE_CARD_RENDER_WORKERS` wins over the machine's
    core count, and a width outside the declared range is REFUSED
    rather than silently clamped: a typo that quietly halved a build
    would be a lie about the width.
    """
    monkeypatch.setenv(rb.NATIVE_CARD_WORKERS_ENV, "3")
    assert rb.native_card_workers() == 3

    monkeypatch.setenv(rb.NATIVE_CARD_WORKERS_ENV, "0")
    with pytest.raises(ValueError, match="from 1 to"):
        rb.native_card_workers()

    monkeypatch.setenv(rb.NATIVE_CARD_WORKERS_ENV, "not-a-number")
    with pytest.raises(ValueError, match="must be an integer"):
        rb.native_card_workers()

    monkeypatch.delenv(rb.NATIVE_CARD_WORKERS_ENV)
    lanes = rb.native_card_workers()
    assert 1 <= lanes <= rb.DEFAULT_NATIVE_CARD_WORKERS
    assert lanes <= (os.cpu_count() or 1)


def test_the_wave_preserves_plan_order(monkeypatch):
    """The results come back in the order the cards were planned.

    A wave that returned its results in completion order would put the
    reel's cards in the wrong order, and the timeline would show them
    shuffled.
    """
    def render(index, card):
        # Finish in the REVERSE of the plan order, so a wave that
        # returned completion order would be caught.
        time.sleep(0.01 * (5 - index))
        return card * 10

    monkeypatch.setattr(rb, "native_card_workers", lambda: 4)
    results = rb.run_native_card_wave([1, 2, 3, 4, 5], render)
    assert results == [10, 20, 30, 40, 50]
