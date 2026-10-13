"""`ren preview` on the golden conversation (plan item T12).

Build, then preview: a playable file as long as the reel's timeline
within one frame, named by the verb and recorded on the reel's
assignment. A second build that changes nothing previews from the
cache: nothing renders, and the hit is recorded.

The headless half answers the engine seam and proves no Resolve route
is reached; whether the pinned adapter can draw the golden reel is asked
of the adapter itself. The Resolve half renders through the canonical
double's render queue. Both run everywhere ffmpeg does.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest

from tests.scenarios import golden

pytestmark = pytest.mark.skipif(
    shutil.which("ffmpeg") is None,
    reason="golden media and previews are encoded with ffmpeg")


@pytest.fixture
def built(tmp_path, monkeypatch, stub_resolve_script):
    """The golden conversation, proposed through the assignment spine,
    approved and built on the double."""
    from library.tools import assignment_spine

    monkeypatch.setenv("REN_NOTIFY", "0")
    recipe = golden.CONVERSATION
    media = golden.ensure_media(recipe)
    resolve, project = golden.conversation_world(recipe, media)
    golden.install_offline_seams(monkeypatch, resolve, recipe)
    folder = golden.make_project_folder(tmp_path, recipe)
    golden.analyze(folder)
    proposal = golden.plan(folder, golden.conversation_answer(recipe))
    assignment_spine.on_propose(str(folder))
    golden.rule(proposal)
    assert golden.build(folder) == 0
    reel = golden.timeline(project, golden.REEL)
    frames = reel.GetEndFrame() - reel.GetStartFrame()
    return folder, project, frames


def _forbid_resolve(monkeypatch):
    """Every route to a Resolve process raises: the headless path must
    reach none of them."""
    from library.tools import reel_build, resolve_locale
    from library.tools.execution import resolve_render

    def refuse(*_args, **_kwargs):
        raise AssertionError("the headless preview reached Resolve")

    monkeypatch.setattr(resolve_locale, "scriptapp_preserving_locale",
                        refuse)
    monkeypatch.setattr(reel_build, "_connect_resolve", refuse)
    monkeypatch.setattr(resolve_render, "_connect", refuse)
    monkeypatch.setattr(resolve_render, "render_timeline", refuse)


def _assert_preview_matches_timeline(result, frames):
    from library.tools import reel_preview

    probe = reel_preview.probe_file(result["path"])
    verdict = result["verification"]
    assert verdict["playable"], verdict["decode_errors"]
    assert verdict["expected_frames"] == frames
    measured = round(probe["frames"] / probe["fps"] * 24000 / 1001)
    assert abs(measured - frames) <= 1, (measured, frames)
    assert probe["height"] == 1920 // 2 and probe["width"] == 1080 // 2
    assert probe["audio"]


def _assignment_reel(folder):
    from library.tools import assignment_lifecycle

    (record,) = assignment_lifecycle.list_records(str(folder))
    (reel,) = record["reels"].values()
    return record, reel


def _engine_file(frames):
    """The engine seam answered: a file of the reel's frame and length,
    what an export of the reel writes."""
    def render(_project, _target, _sliced, workdir):
        path = workdir / "engine.mp4"
        done = subprocess.run([
            "ffmpeg", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=24000/1001",
            "-f", "lavfi", "-i", "sine=frequency=330:sample_rate=48000",
            "-frames:v", str(frames), "-c:v", "libx264",
            "-pix_fmt", "yuv420p", "-c:a", "aac",
            "-t", f"{frames * 1001 / 24000:.6f}", str(path)],
            capture_output=True, encoding="utf-8", check=False)
        assert done.returncode == 0, done.stderr
        return {"path": str(path), "losses": []}
    return render


def test_headless_preview_then_unchanged_build_is_a_cache_hit(
        built, monkeypatch, capsys):
    """Build, preview headless, rebuild unchanged, preview again.

    The engine seam is answered (`_engine_file`): no reel-build model is
    projectable by the pinned FilmCraft adapter today (see
    `test_auto_previews_an_unprojectable_reel_through_resolve`), so what
    this proves is everything around the engine - the route reaches no
    Resolve, the file plays and is as long as the timeline, the record,
    and the cache.
    """
    from library.tools import reel_preview

    folder, _project, frames = built
    monkeypatch.setattr(reel_preview, "headless_refusal", lambda _s: "")
    monkeypatch.setattr(reel_preview, "_filmcraft_revision", lambda: "pin")
    monkeypatch.setattr(reel_preview, "render_filmcraft",
                        _engine_file(frames))

    # ── build, then preview: headless, no Resolve route reached ──
    with monkeypatch.context() as no_resolve:
        _forbid_resolve(no_resolve)
        assert reel_preview.main([str(folder)]) == 0
    out = capsys.readouterr().out
    index = json.loads((reel_preview.previews_dir(str(folder))
                        / reel_preview.INDEX_FILE).read_text("utf-8"))
    (first,) = index["events"]
    assert first["outcome"] == "rendered"
    assert first["engine"] == "filmcraft"
    assert out.strip().splitlines()[-1] == f"Watch: {first['path']}"
    _assert_preview_matches_timeline(first, frames)
    record, reel = _assignment_reel(folder)
    assert reel["preview"]["path"] == first["path"]
    assert record["history"][-1]["event"] == "preview"
    rendered_at = Path(first["path"]).stat().st_mtime_ns

    # ── a second build that changes nothing renders nothing ──
    assert golden.build(folder) == 0

    def no_render(*_args, **_kwargs):
        raise AssertionError("an unchanged reel was rendered again")

    monkeypatch.setattr(reel_preview, "render_filmcraft", no_render)
    monkeypatch.setattr(reel_preview, "encode_proxy", no_render)
    (again,) = reel_preview.preview(str(folder))
    assert again["outcome"] == "cache_hit"
    assert again["path"] == first["path"]
    assert again["content_key"] == first["content_key"]
    assert Path(again["path"]).stat().st_mtime_ns == rendered_at
    index = json.loads((reel_preview.previews_dir(str(folder))
                        / reel_preview.INDEX_FILE).read_text("utf-8"))
    assert [e["outcome"] for e in index["events"]] == [
        "rendered", "cache_hit"]
    _record, reel = _assignment_reel(folder)
    assert reel["preview"]["outcome"] == "cache_hit"


def test_auto_previews_an_unprojectable_reel_through_resolve(built):
    """The golden reel's model is not projectable headless today, and
    `auto` says so on the preview rather than failing or hiding it.

    Needs the pinned adapter on `ren_native`; the answer it gives is the
    adapter's own, read through its plan, so no engine process runs.
    """
    from library.tools import reel_preview

    if reel_preview.filmcraft_available():
        pytest.skip("the pinned FilmCraft engine is not installed: "
                    + reel_preview.filmcraft_available())
    folder, _project, frames = built
    (result,) = reel_preview.preview(str(folder))
    assert result["engine"] == "resolve"
    assert "FilmCraft projection refused" in result["headless_refused"]
    _assert_preview_matches_timeline(result, frames)
    with pytest.raises(reel_preview.PreviewRefused,
                       match="cannot draw this reel"):
        reel_preview.preview(str(folder), engine="filmcraft")


def test_resolve_preview_renders_the_timeline_and_caches(built, monkeypatch):
    from library.tools import reel_preview

    folder, project, frames = built
    (first,) = reel_preview.preview(str(folder), engine="resolve")
    assert first["outcome"] == "rendered"
    assert first["engine"] == "resolve"
    _assert_preview_matches_timeline(first, frames)
    assert project.render_jobs == [], "the preview leaves no queued job"

    # A changed reel is a different key: a touch records a new Ren
    # snapshot of the timeline, and the next preview renders again.
    (cached,) = reel_preview.preview(str(folder), engine="resolve")
    assert cached["outcome"] == "cache_hit"

    from library.tools import plan_provenance

    review = reel_preview._review_dir(str(folder))
    plan_provenance.record_timeline_snapshot(
        review, golden.REEL, {"changed": True}, action="touchup")
    (after_touch,) = reel_preview.preview(str(folder), engine="resolve")
    assert after_touch["outcome"] == "rendered"
    assert after_touch["content_key"] != first["content_key"]
