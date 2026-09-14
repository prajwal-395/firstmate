"""The caption step can use the bundle-once renderer through its own seam.

`library/tools/remotion_batch.py` ships `PersistentRenderer` - one node
process, one bundle, many cards - with zero callers, while step 4.05's
`SubprocessRenderer` pays one `npx remotion render` per card and names
this exact swap as its intended design. This file pins the wiring:

* the step offers the persistent renderer BESIDE the subprocess one,
  and the default is the persistent one (captain's ruling, 2026-09-09);
  the per-card subprocess path is the loud startup fallback;
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


# ── The default is the bundle-once renderer ───────────────────────────

def test_the_default_renderer_is_now_the_persistent_one(monkeypatch,
                                                       tmp_path):
    """The captain's ruling, 2026-09-09: the persistent renderer is the
    default and the per-card subprocess path is the fallback. The
    previous pin on `"subprocess"` turned red on that word, as it said
    it would - this is its replacement, pinning the new default both
    as the signature value and as the renderer actually built."""
    import inspect
    _NoPixelQA(monkeypatch)
    spine, plan = _spine_and_plan()
    params = inspect.signature(r405.render_subtitle_overlays).parameters
    assert params["renderer_kind"].default == "persistent"

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

    monkeypatch.setattr(r405, "PersistentCaptionRenderer", _Stub)
    # Full-canvas carrying: the stubs write undecodable bytes, and the
    # tight default would try to DECODE the probe off them. What is
    # pinned here is WHICH renderer is built, not the tight path - so
    # the carrying is explicit rather than left to the project default.
    out = r405.render_subtitle_overlays(
        plan, spine, project_folder=str(tmp_path),
        remotion_dir=REMOTION,
        overlay_geometry="full", overlay_container="video")
    assert len(built) == 1 and built[0] == REMOTION
    assert out["subtitle_overlay"]["renderer"] == "persistent"


def test_the_subprocess_path_stays_selectable(monkeypatch, tmp_path):
    """The old default is the explicit fallback: asking for it by name
    still builds exactly one per-card renderer."""
    _NoPixelQA(monkeypatch)
    spine, plan = _spine_and_plan()
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
    # Full-canvas carrying, as above: the stub draws bytes, not video,
    # and what is pinned here is that the old path stays selectable.
    out = r405.render_subtitle_overlays(
        plan, spine, project_folder=str(tmp_path),
        remotion_dir=REMOTION, renderer_kind="subprocess",
        overlay_geometry="full", overlay_container="video")
    assert len(built) == 1 and built[0] == REMOTION
    assert out["subtitle_overlay"]["renderer"] == "subprocess"
    assert "renderer_fallback" not in out["subtitle_overlay"]


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

def _no_spawn_recorder(calls):
    """The single spawn path, refused. `PersistentRenderer.start()` is
    the only caller of `Popen`, and only `render()` calls `start()`, so
    a laziness pin belongs here - not on a machine-global `pgrep`
    count, which a concurrent probe in another lane flips with nothing
    started here (measured 2026-09-14: the `pgrep -f render-batch.mjs`
    form of this pin failed the full-suite gate and fails ~5/25 beside
    tight probe loops). An eager adapter still turns these red: the
    first spawn raises out of construction or out of the block."""

    def _no_spawn(*args, **kwargs):
        calls.append(args)
        raise AssertionError(
            "a caption renderer spawned a child before any card asked "
            "to be drawn - the bundle is paid lazily, on the first "
            "render, not at construction")

    return _no_spawn


def test_the_persistent_renderer_builds_lazily(monkeypatch):
    """Construction must not spawn anything. `render_one_segment` can
    return without rendering - a region-scoped pass skips most cards -
    so an eager bundle would pay its whole cost to draw one card and
    make that path SLOWER than what it replaced."""
    import subprocess as sp

    calls = []
    monkeypatch.setattr(sp, "Popen", _no_spawn_recorder(calls))
    engine = r405.PersistentCaptionRenderer(REMOTION)
    assert engine._inner._proc is None, (
        "constructing the renderer started work")
    assert calls == [], (
        f"constructing the renderer spawned a child: {calls}")
    engine.close()  # never started: must be a no-op, never a raise
    assert calls == [], (
        f"closing an unstarted renderer spawned a child: {calls}")


def test_a_persistent_renderer_that_never_started_closes_quietly():
    engine = r405.PersistentCaptionRenderer(REMOTION)
    engine.close()
    engine.close()


def test_the_adapter_is_usable_as_a_context_manager_without_starting(
        monkeypatch):
    """The orchestrator closes what it built via `close()`; callers
    holding one renderer across passes will use `with`. Entering must
    not start the bundle - that stays lazy to the first real card."""
    import subprocess as sp

    calls = []
    monkeypatch.setattr(sp, "Popen", _no_spawn_recorder(calls))
    with r405.PersistentCaptionRenderer(REMOTION) as engine:
        assert engine._inner._proc is None, (
            "entering the block started a process before any card asked "
            "to be drawn")
        assert calls == [], (
            f"entering the block spawned a child: {calls}")
    assert calls == [], (
        f"leaving the block spawned a child: {calls}")


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


# ── The startup fallback: loud, once, never per card ─────────────────

def test_a_renderer_that_cannot_start_falls_back_loudly(monkeypatch,
                                                       tmp_path, capsys):
    """The fallback fires at STARTUP, not per card: the persistent
    renderer raises `RendererUnavailable` before a single card is drawn
    (no node, no bundle, bundling outran its budget), and the pass
    continues on the per-card subprocess renderer.

    It MUST SAY SO LOUDLY: the step's own stderr carries a FALLBACK
    banner, and the emitted payload records `renderer_fallback` with
    the reason - a pass that silently ran the slow way while reporting
    success is exactly what the design refuses."""
    _NoPixelQA(monkeypatch)
    from library.tools.remotion_batch import RendererUnavailable
    spine, plan = _spine_and_plan()

    dead = []

    class _CannotStart:
        def __init__(self, remotion_dir):
            dead.append(remotion_dir)

        def render(self, props_path, overlay_path, sequence=False):
            raise RendererUnavailable(
                "could not start the renderer: node is not on PATH")

        def close(self):
            dead.append("closed")

    drawn = []

    class _Subprocess:
        def __init__(self, remotion_dir):
            drawn.append(remotion_dir)

        def render(self, props_path, overlay_path, sequence=False):
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
            return True, ""

        def close(self):
            pass

    monkeypatch.setattr(r405, "PersistentCaptionRenderer", _CannotStart)
    monkeypatch.setattr(r405, "SubprocessRenderer", _Subprocess)
    # Full-canvas carrying: both stand-ins draw bytes, not video, and
    # the tight default would try to decode a probe off them. The
    # fallback being pinned is startup selection, not the tight path.
    out = r405.render_subtitle_overlays(
        plan, spine, project_folder=str(tmp_path),
        remotion_dir=REMOTION,
        overlay_geometry="full", overlay_container="video")

    overlay = out["subtitle_overlay"]
    assert overlay["available"] is True
    assert overlay["renderer"] == "subprocess"
    fallback = overlay["renderer_fallback"]
    assert fallback["requested"] == "persistent"
    assert "node is not on PATH" in fallback["reason"]
    assert len(drawn) == 1 and drawn[0] == REMOTION
    assert "closed" in dead, "the dead renderer is closed, not leaked"

    announcement = capsys.readouterr().err
    assert "FALLBACK" in announcement
    assert "persistent" in announcement.lower()
    assert "node is not on PATH" in announcement


def test_a_renderer_that_dies_mid_run_still_stops_the_pass(monkeypatch,
                                                           tmp_path):
    """The startup fallback must not become a per-card fallback: the
    first card draws, the renderer dies on the second, and the pass
    REFUSES with one fault rather than degrading quietly onto the slow
    path for the remaining cards."""
    _NoPixelQA(monkeypatch)
    from library.tools.remotion_batch import RendererUnavailable
    spine, plan = _spine_and_plan()

    class _DiesAfterOne:
        def __init__(self, remotion_dir):
            self.calls = 0

        def render(self, props_path, overlay_path, sequence=False):
            self.calls += 1
            if self.calls > 1:
                raise RendererUnavailable(
                    "the renderer process exited (code -9)")
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
            return True, ""

        def close(self):
            pass

    class _SubprocessMustNotRun:
        def __init__(self, remotion_dir):
            raise AssertionError(
                "mid-run death must not fall back to subprocess")

        def render(self, props_path, overlay_path, sequence=False):
            raise AssertionError("unreachable")

        def close(self):
            pass

    monkeypatch.setattr(r405, "PersistentCaptionRenderer", _DiesAfterOne)
    monkeypatch.setattr(r405, "SubprocessRenderer", _SubprocessMustNotRun)
    with pytest.raises(r405.SubtitleRenderRefused) as caught:
        r405.render_subtitle_overlays(
            plan, spine, project_folder=str(tmp_path),
            remotion_dir=REMOTION)
    payload = caught.value.payload["subtitle_overlay"]
    assert payload["available"] is False
    assert "renderer" in payload["error"].lower()
    assert len(payload["segments"]) == 1, (
        "one card drawn, then the fault - no FAILED entry per card "
        "that never had a chance")


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


# ── The unit-level default: one shared bundle-once renderer ──────────
#
# The pass-level default above is not where real caption work happens:
# region redos, reel fixes and agent-driven renders reach
# `render_one_segment` directly, and that unit built a fresh
# per-card subprocess renderer per call - the shape that rendered 136
# cards at 18.2s each on 2026-09-11 with no banner and no record. These
# pin that the unit shares one bundle-once engine per process instead.

def _unit_props(block=1, text="alpha", frames=30):
    return {"_block_position": block, "_timeline_start": 0.0,
            "_timeline_end": 1.0, "_source_in_frame": 0,
            "_source_out_frame": frames, "_speaker": None,
            "_source_clip_id": "clip_001", "_source_start": 0.0,
            "_source_end": 1.0, "durationInFrames": frames, "fps": 30,
            "width": 1080, "height": 1920, "style": {},
            "subtitles": [{"text": text}]}


def _unit_dirs(tmp_path):
    rdir = str(tmp_path / "remotion")
    os.makedirs(rdir)
    out_dir = str(tmp_path / "out")
    os.makedirs(out_dir)
    return rdir, out_dir


def test_unit_renders_without_a_renderer_share_one_engine(
        monkeypatch, tmp_path, capsys):
    """Two cards, no explicit renderer: ONE engine built, announced once.

    Reverting the unit default to a per-call subprocess turns this red:
    two cards would build two engines (or, with real node, draw real
    pixels through two bundles instead of one)."""
    r405._reset_shared_caption_renderers()
    try:
        built = []

        class _Shared:
            def __init__(self, remotion_dir):
                built.append(remotion_dir)

            def render(self, props_path, overlay_path, sequence=False):
                with open(overlay_path, "wb") as handle:
                    handle.write(b"pixels")
                return True, ""

            def close(self):
                pass

        monkeypatch.setattr(r405, "PersistentCaptionRenderer", _Shared)
        rdir, out_dir = _unit_dirs(tmp_path)
        first = r405.render_one_segment(
            _unit_props(1, "alpha"), out_dir, "tl", remotion_dir=rdir,
            overlay_geometry="full")
        second = r405.render_one_segment(
            _unit_props(2, "bravo"), out_dir, "tl", remotion_dir=rdir,
            overlay_geometry="full")
        assert first["provenance"] == "rendered"
        assert second["provenance"] == "rendered"
        assert len(built) == 1, (
            f"two cards must share one engine, built: {built}")
        assert "shared bundle-once" in capsys.readouterr().err
    finally:
        r405._reset_shared_caption_renderers()


def test_unit_shared_start_failure_falls_back_loudly_and_once(
        monkeypatch, tmp_path, capsys):
    """A shared engine that never started falls back per-card, loudly.

    In a process without node this is today's behavior exactly - and it
    must SAY SO once, not per card, and must not retry the dead engine
    on the second card."""
    r405._reset_shared_caption_renderers()
    try:
        from library.tools.remotion_batch import RendererUnavailable
        attempts = []

        class _CannotStart:
            def __init__(self, remotion_dir):
                pass

            def render(self, props_path, overlay_path, sequence=False):
                attempts.append(overlay_path)
                raise RendererUnavailable(
                    "could not start the renderer: boom")

            def close(self):
                pass

        drawn = []

        class _Sub:
            def __init__(self, remotion_dir):
                pass

            def render(self, props_path, overlay_path, sequence=False):
                drawn.append(overlay_path)
                with open(overlay_path, "wb") as handle:
                    handle.write(b"pixels")
                return True, ""

            def close(self):
                pass

        monkeypatch.setattr(r405, "PersistentCaptionRenderer", _CannotStart)
        monkeypatch.setattr(r405, "SubprocessRenderer", _Sub)
        rdir, out_dir = _unit_dirs(tmp_path)
        first = r405.render_one_segment(
            _unit_props(1, "alpha"), out_dir, "tl", remotion_dir=rdir,
            overlay_geometry="full")
        second = r405.render_one_segment(
            _unit_props(2, "bravo"), out_dir, "tl", remotion_dir=rdir,
            overlay_geometry="full")
        assert first["provenance"] == "rendered"
        assert second["provenance"] == "rendered"
        assert len(attempts) == 1, (
            "the dead shared engine is tried once, not per card")
        assert len(drawn) == 2
        err = capsys.readouterr().err
        assert "FALLBACK" in err and "shared bundle-once" in err
    finally:
        r405._reset_shared_caption_renderers()


def test_unit_shared_death_after_serving_raises(monkeypatch, tmp_path):
    """A shared engine that drew and then died is a mid-run death: it
    RAISES rather than degrading quietly onto the slow path."""
    r405._reset_shared_caption_renderers()
    try:
        from library.tools.remotion_batch import RendererUnavailable
        calls = []

        class _DiesSecond:
            def __init__(self, remotion_dir):
                pass

            def render(self, props_path, overlay_path, sequence=False):
                calls.append(overlay_path)
                if len(calls) > 1:
                    raise RendererUnavailable(
                        "the renderer process exited (code -9)")
                with open(overlay_path, "wb") as handle:
                    handle.write(b"pixels")
                return True, ""

            def close(self):
                pass

        monkeypatch.setattr(r405, "PersistentCaptionRenderer", _DiesSecond)
        rdir, out_dir = _unit_dirs(tmp_path)
        first = r405.render_one_segment(
            _unit_props(1, "alpha"), out_dir, "tl", remotion_dir=rdir,
            overlay_geometry="full")
        assert first["provenance"] == "rendered"
        with pytest.raises(RendererUnavailable):
            r405.render_one_segment(
                _unit_props(2, "bravo"), out_dir, "tl", remotion_dir=rdir,
                overlay_geometry="full")
    finally:
        r405._reset_shared_caption_renderers()


def test_unit_frames_carrying_stays_per_card_subprocess(
        monkeypatch, tmp_path, capsys):
    """A sequence the bundle-once renderer cannot draw stays per-card -
    and the persistent factory is never even built."""
    r405._reset_shared_caption_renderers()
    try:
        def _boom_factory(remotion_dir):
            raise AssertionError(
                "frames must never build the persistent renderer")

        drawn = []

        class _SeqSub:
            def __init__(self, remotion_dir):
                drawn.append(remotion_dir)

            def render(self, props_path, overlay_path, sequence=False):
                assert sequence is True
                os.makedirs(overlay_path, exist_ok=True)
                for i in range(30):
                    open(os.path.join(
                        overlay_path, f"frame-{i:02d}.png"), "wb").close()
                return True, ""

            def close(self):
                pass

        monkeypatch.setattr(r405, "PersistentCaptionRenderer", _boom_factory)
        monkeypatch.setattr(r405, "SubprocessRenderer", _SeqSub)
        rdir, out_dir = _unit_dirs(tmp_path)
        out = r405.render_one_segment(
            _unit_props(1, "alpha"), out_dir, "tl", remotion_dir=rdir,
            overlay_geometry="full", overlay_container="frames")
        assert out["provenance"] == "rendered"
        assert len(drawn) == 1
        assert "frame-sequence" in capsys.readouterr().err
    finally:
        r405._reset_shared_caption_renderers()
