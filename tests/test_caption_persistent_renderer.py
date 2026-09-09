"""The caption step can use the bundle-once renderer through its own seam.

`library/tools/remotion_batch.py` ships `PersistentRenderer` - one node
process, one bundle, many cards - with zero callers, while step 4.05's
`SubprocessRenderer` pays one `npx remotion render` per card and names
this exact swap as its intended design. This file pins the wiring:

* the step offers the persistent renderer BESIDE the subprocess one,
  and the default is still the subprocess one;
* the persistent renderer builds LAZILY - a pass that draws nothing
  pays nothing;
* the two failure kinds stay distinct: a bad card returns
  `(False, error)` and the renderer stays up, while a dead renderer
  RAISES and the pass stops rather than marching every remaining card
  into a closed pipe and reporting each as failed.
"""

import importlib.util
import os
import sys

import pytest

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTION = os.path.join(REPO, "remotion-subtitles")


def _load_405():
    """4.05 imports a sibling by bare name; the step directory is owned
    by tests/conftest.py, so this loader adds nothing to sys.path."""
    step_dir = os.path.join(REPO, "library/steps/step_4_05_render_subtitles")
    spec = importlib.util.spec_from_file_location(
        "s405_persistent_under_test", os.path.join(step_dir, "step.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r405 = _load_405()


def _block(position, tl_start, duration, words, clip="clip_001", src=0.0):
    stamps = [{"word": w,
               "source_start": round(src + i * duration / len(words), 3),
               "source_end": round(src + (i + 0.8) * duration / len(words), 3)}
              for i, w in enumerate(words)]
    return {
        "position": position, "block_type": "speech", "clip_id": clip,
        "source_start": src, "source_end": round(src + duration, 3),
        "timeline_start": tl_start,
        "timeline_end": round(tl_start + duration, 3),
        "duration_seconds": duration,
        "word_timestamps": stamps, "alignment_method": "whisperx",
        "content": {"text": " ".join(words), "word_timestamps": stamps},
    }


def _spine_and_plan():
    from library.steps.step_4_01_plan_subtitles.step import (
        generate_subtitles,
    )
    spine = {"structure": [
        _block(1, 0.0, 3.0, ["alpha", "bravo"], src=10.0),
        _block(2, 3.0, 3.0, ["charlie", "delta"], src=20.0),
    ], "frame_rate": 30.0}
    return spine, generate_subtitles(spine)["subtitle_plan"]


class _NoPixelQA:
    """The wiring tests use stub renderers writing bytes, not ProRes, so
    the real pixel QA would correctly refuse them. That is the QA doing
    its job on a stub and is not what is pinned here."""

    def __init__(self, monkeypatch):
        import types
        module = types.ModuleType("tools.qa.subtitle_qa")
        module.run_subtitle_qa = lambda *a, **k: None
        monkeypatch.setitem(sys.modules, "tools.qa.subtitle_qa", module)


# ── The default is unchanged ──────────────────────────────────────────

def test_the_default_renderer_is_still_the_subprocess_one(monkeypatch,
                                                          tmp_path):
    """The captain's ruling: the existing version is retained until the
    new one is proven. A change that silently switches the renderer
    under approved reels is the one outcome to avoid - so the default
    is pinned here, and any future switch turns this red."""
    import inspect
    _NoPixelQA(monkeypatch)
    spine, plan = _spine_and_plan()
    params = inspect.signature(r405.render_subtitle_overlays).parameters
    assert params["renderer_kind"].default == "subprocess"

    built = []

    class _Stub:
        def __init__(self, remotion_dir):
            built.append(remotion_dir)

        def render(self, props_path, overlay_path, sequence=False):
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
            return True, ""

        def close(self):
            pass

    monkeypatch.setattr(r405, "SubprocessRenderer", _Stub)
    r405.render_subtitle_overlays(plan, spine, project_folder=str(tmp_path),
                                  remotion_dir=REMOTION)
    assert len(built) == 1 and built[0] == REMOTION


def test_an_unknown_renderer_kind_is_refused(tmp_path):
    """A misspelled kind must fail loudly, never fall back to a
    renderer the caller did not ask for - a silent fallback is the
    vacuous-gate shape this repository keeps removing."""
    spine, plan = _spine_and_plan()
    with pytest.raises(ValueError, match="renderer_kind"):
        r405.render_subtitle_overlays(plan, spine,
                                      project_folder=str(tmp_path),
                                      remotion_dir=REMOTION,
                                      renderer_kind="turbo")


# ── Lazy: a pass that draws nothing pays nothing ──────────────────────

def test_the_persistent_renderer_builds_lazily():
    """Construction must not spawn anything. `render_one_segment` can
    return without rendering - a region-scoped pass skips most cards -
    so an eager bundle would pay its whole cost to draw one card and
    make that path SLOWER than what it replaced."""
    import subprocess as sp

    def running():
        return int(sp.run("pgrep -f render-batch.mjs | wc -l", shell=True,
                          capture_output=True, text=True,
                          check=False).stdout.strip() or 0)

    before = running()
    engine = r405.PersistentCaptionRenderer(REMOTION)
    assert running() == before, "constructing the renderer started work"
    engine.close()  # never started: must be a no-op, never a raise
    assert running() == before


def test_a_persistent_renderer_that_never_started_closes_quietly():
    engine = r405.PersistentCaptionRenderer(REMOTION)
    engine.close()
    engine.close()


def test_the_adapter_is_usable_as_a_context_manager_without_starting():
    """The orchestrator closes what it built via `close()`; callers
    holding one renderer across passes will use `with`. Entering must
    not start the bundle - that stays lazy to the first real card."""
    import subprocess as sp

    def running():
        return int(sp.run("pgrep -f render-batch.mjs | wc -l", shell=True,
                          capture_output=True, text=True,
                          check=False).stdout.strip() or 0)

    before = running()
    with r405.PersistentCaptionRenderer(REMOTION):
        assert running() == before
    assert running() == before


# ── The two failure kinds are not the same ────────────────────────────

class _Inner:
    """Stands in for `PersistentRenderer` behind the adapter."""

    def __init__(self, behaviour):
        self.behaviour = behaviour
        self.calls = 0
        self.closed = 0

    def render(self, props_path, overlay_path, sequence=False):
        self.calls += 1
        return self.behaviour(props_path, overlay_path)

    def close(self):
        self.closed += 1


def test_a_card_failure_is_returned_and_the_renderer_stays_up(tmp_path):
    """One bad card is `(False, error)`, not an exception - the bundle
    is the expensive thing and one bad card must not cost it."""
    from library.tools.remotion_batch import RendererUnavailable
    inner = _Inner(lambda p, o: (False, "bad font"))
    engine = r405.PersistentCaptionRenderer.__new__(
        r405.PersistentCaptionRenderer)
    engine._inner = inner
    props = tmp_path / "p.json"
    props.write_text("{}")
    ok, error = engine.render(str(props), str(tmp_path / "o.mov"))
    assert ok is False and "bad font" in error
    assert inner.calls == 1
    # And the renderer is still usable: the next card goes out too.
    ok2, _ = engine.render(str(props), str(tmp_path / "o2.mov"))
    assert ok2 is False
    assert inner.calls == 2
    assert not isinstance(ok2, type(RendererUnavailable("x")))


def test_a_dead_renderer_raises_rather_than_returning_failure(tmp_path):
    """Conflating these marches 762 more cards into a closed pipe and
    reports 762 failures instead of one fault."""
    from library.tools.remotion_batch import RendererUnavailable
    inner = _Inner(lambda p, o: (_ for _ in ()).throw(
        RendererUnavailable("the renderer process exited (code -9)")))
    engine = r405.PersistentCaptionRenderer.__new__(
        r405.PersistentCaptionRenderer)
    engine._inner = inner
    props = tmp_path / "p.json"
    props.write_text("{}")
    with pytest.raises(RendererUnavailable):
        engine.render(str(props), str(tmp_path / "o.mov"))


def test_the_pass_stops_on_a_dead_renderer_and_reports_one_fault(
        monkeypatch, tmp_path):
    """The orchestrator keeps the distinction: on `RendererUnavailable`
    it refuses the pass ONCE, carrying the cards rendered so far - it
    does not record a FAILED segment per remaining card."""
    _NoPixelQA(monkeypatch)
    from library.tools.remotion_batch import RendererUnavailable
    spine, plan = _spine_and_plan()

    calls = []

    class _Dying:
        def render(self, props_path, overlay_path, sequence=False):
            calls.append(overlay_path)
            raise RendererUnavailable("the renderer process exited")

        def close(self):
            calls.append("close")

    engine = _Dying()
    with pytest.raises(r405.SubtitleRenderRefused) as caught:
        r405.render_subtitle_overlays(plan, spine,
                                      project_folder=str(tmp_path),
                                      remotion_dir=REMOTION,
                                      renderer=engine)
    assert len(calls) == 1, (
        f"the second card must never be attempted: {calls}")
    payload = caught.value.payload["subtitle_overlay"]
    assert payload["available"] is False
    assert "renderer" in payload["error"].lower()


def test_a_sequence_through_the_persistent_renderer_refuses_loudly(
        tmp_path):
    """The persistent renderer stitches video; a frames container it
    cannot draw must refuse before rendering, not report success."""
    spine, plan = _spine_and_plan()
    with pytest.raises(ValueError, match="sequence"):
        r405.render_subtitle_overlays(
            plan, spine, project_folder=str(tmp_path),
            remotion_dir=REMOTION, renderer_kind="persistent",
            overlay_container="frames")


# ── The codec path carries ────────────────────────────────────────────

def test_the_serve_path_renders_prores_4444_like_the_cli_path():
    """Bite 3 from the brief: the current call passes `--codec prores
    --prores-profile 4444`. The persistent path must carry the same
    codec and profile, or the finding is that it cannot - not a quiet
    format change. Pinned off the script both modes share."""
    script = (REMOTION and open(
        os.path.join(REMOTION, "render-batch.mjs")).read())
    assert 'codec: "prores"' in script
    assert 'proResProfile: "4444"' in script
