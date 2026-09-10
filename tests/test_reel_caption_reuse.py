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
                project_folder=""):
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


def test_reused_segments_pair_back_to_the_same_files(reel_run):
    """Reuse pairs; it does not just skip.

    A reused segment must point at the file the first build rendered -
    the same overlay path - or the timeline would be paired to nothing.
    """
    run, _engine, _seen = reel_run
    first = run()
    second = run()
    assert ([s["overlay_path"] for s in second]
            == [s["overlay_path"] for s in first])
    for segment in second:
        assert os.path.isfile(segment["overlay_path"])
