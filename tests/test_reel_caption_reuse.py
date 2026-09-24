"""Rebuilds reuse unchanged captions instead of re-rendering them.

vep-caption-assets-cache-and-cleanup, Part 2. Every rebuild regenerated
every caption clip and orphaned the previous set (gigabytes in one
night). ``caption_content_hash`` cannot key a per-card cache: it digests
the whole card list into ONE per-reel hash
(``library/tools/plan_provenance.py``). The per-card identity is the
segment name (speaker + timeline + source span,
``library/tools/subtitle_segment_id.py``) plus the recorded reuse key
(props digest + renderer fingerprint, step 4.05) - and the reel caption
path opts into it with ``reuse=True``.

These tests drive the real ``reel_subtitle_segments`` with the real
``render_one_segment`` behind a stub renderer, so what is pinned is the
wiring, not either half alone. Reverting the ``reuse=True`` opt-in turns
the first two tests red; the third names the pairing the cache stands
on.
"""

from __future__ import annotations

import os
import unittest.mock as mock

import pytest

from library.steps.step_4_05_render_subtitles.step import render_one_segment
from library.tools import operations
from library.tools import reel_spine
from library.tools.reel_build import reel_subtitle_segments

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTION = os.path.join(REPO, "remotion-subtitles")
"""The real composition tree. The reuse key fingerprints it; a directory
without one yields no fingerprint and reuse is refused - which would
make the tests below render twice and fail for the wrong reason."""

FPS = 24000 / 1001


class _Renderer:
    """A renderer that writes bytes and counts its own calls."""

    def __init__(self):
        self.calls = 0

    def render(self, props_path, overlay_path, sequence=False):
        self.calls += 1
        with open(overlay_path, "wb") as handle:
            handle.write(b"pixels")
        return True, ""


def _entries(texts=("alpha bravo", "charlie delta")):
    entries = []
    for i, text in enumerate(texts):
        start = 20.0 + i * 5.0
        entries.append({
            "timeline_start": start,
            "timeline_end": start + 2.5,
            "text": text,
            "spine_block_position": f"body_{i + 1}",
            "speaker": "Craig",
            "words": [],
        })
    return entries


@pytest.fixture
def reel_run(monkeypatch, tmp_path):
    """The real reel caption path, plan stubbed, render REAL.

    Returns ``(run, engine, seen)`` where ``run(entries)`` builds the
    reel's captions for the given plan entries, ``engine`` is the stub
    renderer counting real renders, and ``seen`` records the ``reuse``
    flag each render call carried.
    """
    engine = _Renderer()
    seen = []
    entries_holder = {"entries": _entries()}

    def fake_spine(moment, transcript, keep_ranges, lead_seconds=0.0):
        return {}

    class FakePlan:
        def run(self, spine, **kwargs):
            return {"subtitle_plan": {
                "subtitle_entries": entries_holder["entries"],
                "style": {"fontFamily": "Montserrat"},
            }}

    class FakeRender:
        def run(self, props, out_dir, name, progress="", reuse=False,
                overlay_geometry=None, overlay_container=None,
                project_folder="", draw_gain=None):
            seen.append(reuse)
            # These tests pin the reuse wiring, not the carrying: the
            # byte stub cannot feed the tight probe (it writes no
            # decodable video), so the geometry is held at full while
            # the default is tight. Geometry itself is pinned in
            # test_overlay_mode.py and test_subtitle_overlay_modes.py.
            return render_one_segment(
                props, out_dir, name, remotion_dir=REMOTION,
                progress=progress, reuse=reuse, renderer=engine,
                overlay_geometry="full",
                overlay_container=overlay_container,
                project_folder=project_folder)

    def fake_get(name):
        if name == "subtitles.plan":
            return FakePlan()
        if name == "subtitles.render_segment":
            return FakeRender()
        raise AssertionError(f"unexpected operation {name!r}")

    monkeypatch.setattr(reel_spine, "spine_for_reel", fake_spine)
    monkeypatch.setattr(operations, "get", fake_get)

    def run(entries=None):
        if entries is not None:
            entries_holder["entries"] = entries
        return reel_subtitle_segments(
            mock.MagicMock(), {"segments": []}, [(0.0, 60.0)],
            str(tmp_path), FPS, 1080, 1920, timeline_name="Reel 01")

    return run, engine, seen


def test_reel_rebuild_reuses_unchanged_captions(reel_run):
    """An identical rebuild renders nothing twice.

    Fails while the reel path calls the render operation without
    ``reuse=True``: the plain-run default re-renders, the second build
    reports RENDERED, and the engine is called for every segment again.
    """
    run, engine, seen = reel_run
    first = run()
    second = run()
    assert [s["provenance"] for s in first] == ["rendered", "rendered"]
    assert [s["provenance"] for s in second] == ["reused", "reused"]
    assert engine.calls == 2, (
        f"the rebuild must not have rendered: {engine.calls} renders "
        f"for 2 unchanged segments built twice")
    assert seen == [True] * 4


def test_reel_rebuild_rerenders_a_changed_caption_only(reel_run):
    """The cache is per-card: a text correction re-renders its own card.

    The segment name carries no caption content, so the corrected card
    keeps its filename - skipping on presence would serve the stale
    overlay. The reuse key carries the props digest, so only the changed
    card re-renders and its neighbour is still paired back to disk.
    """
    run, engine, _seen = reel_run
    run()
    calls_after_first = engine.calls
    second = run(_entries(texts=("alpha bravo", "charlie DELTA")))
    provenances = sorted(s["provenance"] for s in second)
    assert provenances == ["rendered", "reused"], (
        f"exactly the changed card re-renders; got {provenances}")
    assert engine.calls == calls_after_first + 1


def test_tight_reuse_without_sidecar_falls_through_to_measured(tmp_path):
    """A key hit with no box sidecar re-measures instead of crashing.

    Measured 2026-09-10: Reel 26 reuses a Reel 09 render (same words,
    same drawing digest) whose box sidecar predates the sidecar
    mechanism. The open failed BEFORE the `TightBoxMismatch` import
    inside the try ran, so the except naming it raised
    UnboundLocalError - the "falls through to a fresh measured
    render" path had never been exercised (every reuse test above
    holds the geometry at full, so the tight branch never runs).

    The stub renderer writes bytes no render can decode, so the
    fall-through honestly FAILS the segment here; in production the
    fresh render decodes and the segment ships tight. What is pinned
    is the fall-through itself: no exception escapes, and the entry
    says failed rather than reused.
    """
    from library.steps.step_4_05_render_subtitles import step as seg_step
    from library.tools.subtitle_segment_id import (
        segment_binding,
        segment_identifier,
    )

    engine = _Renderer()
    out_dir = str(tmp_path)
    props = {
        "durationInFrames": 60,
        "fps": 30,
        "width": 1080,
        "height": 1920,
        "style": {
            "fontFamily": "Montserrat",
            "fontSize": 58,
            "fontWeight": 800,
            "position": "bottom",
            "safeArea": {"top": 120, "right": 120,
                         "bottom": 320, "left": 90},
            "captionMaxWidth": 840,
        },
        "subtitles": [{"text": "alpha bravo"}],
        "fontFamily": "Montserrat",
        "_block_position": "body_1",
        "_timeline_start": 20.0,
        "_timeline_end": 22.5,
        "_speaker": "Craig",
        "_source_clip_id": "clip_001",
        "_source_start": 100.0,
        "_source_end": 102.5,
    }
    binding = segment_binding(
        timeline="Reel 01",
        speaker=props["_speaker"],
        block_position=props["_block_position"],
        source_clip_id=props["_source_clip_id"],
        source_start=props["_source_start"],
        source_end=props["_source_end"])
    name = segment_identifier(
        binding, seg_step._drawing_digest(props, "tight", "video"))
    key = seg_step._reuse_key(props, REMOTION, "tight", "video")
    assert key, "the real remotion tree must fingerprint for this test"
    with open(os.path.join(out_dir, f"{name}.mov"), "wb") as handle:
        handle.write(b"pixels")
    with open(os.path.join(out_dir, f"{name}_reuse_key.txt"), "w",
              encoding="utf-8") as handle:
        handle.write(key)
    assert not os.path.exists(
        os.path.join(out_dir, f"{name}_box.json")), (
        "the sidecar must be absent: its absence is the case under test")

    entry = render_one_segment(
        dict(props), out_dir, "Reel 01", remotion_dir=REMOTION,
        reuse=True, renderer=engine,
        overlay_geometry="tight", overlay_container="video",
        project_folder="")

    assert entry["provenance"] == "failed", (
        f"the stub probe cannot decode, so the fall-through must "
        f"report failed, not {entry['provenance']!r}")
    assert "UnboundLocalError" not in str(entry.get("failure", ""))
    assert engine.calls >= 1, (
        "the fall-through must attempt a fresh measured render")
