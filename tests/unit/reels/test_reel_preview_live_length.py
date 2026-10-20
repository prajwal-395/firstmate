"""The defect the break test showed: a reel Ren did not build.

The break test ran `ren preview <project> --reel 13` on a scratch reel
that had been IMPORTED into Resolve rather than built by Ren, and got

    the Project Model has no rows for Reel 13 - the-accounting-firm-ai-called-healthcare

The preview read the reel's length off the Project Model - the model a
BUILD maintains - and a reel Ren never built has no rows in it. So a
timeline Ren did not build could not be previewed at all: the review
loop stopped there, with no file to watch and nothing to check a
render against.

The length is a property of the TIMELINE, so it is read off the live
timeline through AXI when the model has no rows. The live read is the
whole fix; the model still answers whenever it has rows.
"""

from __future__ import annotations

import contextlib
from pathlib import Path

import pytest

from tests import resolve_double

REEL_NAME = "Reel 13 - the-accounting-firm-ai-called-healthcare"
RESOLVE_PROJECT = "Ren Scratch - breaktest"


def _project_folder(tmp_path: Path) -> str:
    """A Ren project that binds to the scratch Resolve project."""
    folder = tmp_path / "ren-scratch-breaktest"
    folder.mkdir()
    (folder / "project.yaml").write_text(
        "name: ren-scratch-breaktest\n"
        "slug: ren-scratch-breaktest\n"
        "source:\n"
        "  footage: /tmp/footage\n"
        "resolve:\n"
        f"  project_name: {RESOLVE_PROJECT}\n",
        encoding="utf-8")
    return str(folder)


def _live_world(monkeypatch, tmp_path):
    """The scratch Resolve project, with its reel the only timeline."""
    from library.tools import resolve_lock, timeline_read, timeline_shadow

    timeline = resolve_double.FakeTimeline(REEL_NAME, start_frame=0,
                                           end_frame=1907)
    project = resolve_double.make_project(
        name=RESOLVE_PROJECT, timelines=[timeline], current=timeline,
        frame_rate=23.976)
    resolve = resolve_double.FakeResolve(project)

    # No instance lease and no cursor move in a test: the read is what
    # is under test, not the guard around it.
    monkeypatch.setattr(resolve_lock, "resolve_lease",
                        lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr(resolve_lock, "cursor_excursion",
                        lambda *_a, **_k: contextlib.nullcontext())
    monkeypatch.setattr("library.tools.marker_feedback.connect_resolve",
                        lambda: resolve)
    store = timeline_shadow.ShadowStore(tmp_path / "shadow.db")
    monkeypatch.setattr(timeline_read, "ShadowStore", lambda: store)
    return project, timeline


def test_a_reel_with_no_project_model_rows_reads_its_length_live(
        tmp_path, monkeypatch):
    """The length comes from the live timeline, not from a model that
    has no rows.

    The reel was imported, so the Project Model says nothing about it.
    The preview reads the length off the timeline AXI reads, and says
    where it read it.
    """
    from library.tools import reel_preview

    _live_world(monkeypatch, tmp_path)
    folder = _project_folder(tmp_path)
    target = {"number": 13, "timeline": REEL_NAME, "slug": "reel-13",
              "built": True}

    length = reel_preview.expected_length(folder, target, None)

    assert length["frames"] == 1907
    assert length["source"] == "live timeline through AXI"
    assert length["fps"] == pytest.approx(24000 / 1001)
    assert length["seconds"] == pytest.approx(1907 * 1001 / 24000)


def test_a_model_less_reel_is_no_longer_refused_by_the_preview(
        tmp_path, monkeypatch):
    """`ren preview` reaches the render for a reel Ren did not build.

    Before the fix the verb refused here, with the model's own words
    ("the Project Model has no rows for ..."), so nothing could be
    previewed for a timeline Ren did not build.
    """
    from library.tools import reel_preview

    _live_world(monkeypatch, tmp_path)
    folder = _project_folder(tmp_path)
    rendered = {}

    def fake_render(_project, _target, workdir):
        rendered["engine"] = "resolve"
        path = workdir / "engine.mp4"
        path.write_bytes(b"not really a video")
        return {"path": str(path), "losses": []}

    monkeypatch.setattr(reel_preview, "render_resolve", fake_render)
    monkeypatch.setattr(reel_preview, "encode_proxy",
                        lambda source, out: (out.parent.mkdir(
                            parents=True, exist_ok=True),
                            out.write_bytes(b"proxy")))
    monkeypatch.setattr(reel_preview, "verify_preview",
                        lambda path, expected: {
                            "playable": True, "passed": True,
                            "measured_frames": expected["frames"],
                            "expected_frames": expected["frames"],
                            "expected_seconds": expected["seconds"],
                            "measured_seconds": expected["seconds"]})
    target = {"number": 13, "timeline": REEL_NAME, "slug": "reel-13",
              "built": True}

    result = reel_preview.preview_reel(folder, target, engine="auto")

    assert rendered["engine"] == "resolve"
    assert result["outcome"] == "rendered"
    assert result["engine"] == "resolve"
    assert result["expected"]["source"] == "live timeline through AXI"
    assert result["expected"]["frames"] == 1907
    # The headless engine cannot draw a reel the model says nothing
    # about, and the preview says so rather than hiding it.
    assert "Project Model has no rows" in result["headless_refused"]


def test_the_model_still_answers_when_it_has_rows(tmp_path, monkeypatch):
    """The live read is a FALLBACK, not a replacement.

    A reel Ren built has model rows, and the model remains the record
    the length is checked against - the live timeline is not consulted
    for a reel the build already described.
    """
    from library.tools import reel_preview

    def refuse(*_args, **_kwargs):
        raise AssertionError("the live timeline was read for a built reel")

    monkeypatch.setattr(reel_preview, "live_timeline_length", refuse)
    monkeypatch.setattr(reel_preview, "_build_record",
                        lambda _project: {"reel_fps": 24.0})
    sliced = {"clip_uses": [{
        "timeline_interval": {"start": 0, "end": 120000,
                              "clock_id": "clock:reel:reel-13"}}]}

    length = reel_preview.expected_length(
        str(tmp_path), {"timeline": REEL_NAME}, sliced)

    assert length["frames"] == 24
    assert length["source"] == "project model clip uses, build reel_fps"
