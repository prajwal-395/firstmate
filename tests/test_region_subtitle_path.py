"""The region-scoped caption path, and the honesty of what it reports.

Increment 5. The captain's worked example: *"Regenerate just this small
segment of subtitles ... and splice the refreshed subtitles back in."*

Most of what is asserted here is a REFUSAL, and every refusal is paired
with the case that must still PASS - a guard that cannot pass is not a
guard, and a check that only ever refuses is the other half of the
vacuous-gate problem this repository keeps removing.

The rows below map to the twelve mutations the design named. Each is
written so that reverting the behaviour it guards turns it red; the
comment on each says which mutation.
"""

import importlib.util
import json
import os
import sys

import pytest

from library.steps.step_1_04_temporal_index.step import splice_region_index
from library.steps.step_4_01_plan_subtitles.step import (
    generate_subtitles, splice_region_plan,
)
from library.tools import scope as scope_mod
from library.tools import state_splice
from library.tools.subtitle_splice import (
    SpliceRefused, assert_durations_preserved, outside_region, splice_plan,
    splice_report,
)

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMOTION = os.path.join(REPO, "remotion-subtitles")
"""The real composition tree. The reuse key hashes it, so a directory
without one yields no fingerprint and reuse is correctly refused - which
is what `test_an_unavailable_renderer_fingerprint_refuses_reuse` asserts
deliberately, and what every OTHER reuse test must avoid tripping over
accidentally."""


def _load_405():
    """4.05 imports a sibling by bare name, as every step body does.

    The step directory is owned by tests/conftest.py, so this loader
    adds nothing to sys.path itself.
    """
    step_dir = os.path.join(REPO, "library/steps/step_4_05_render_subtitles")
    spec = importlib.util.spec_from_file_location(
        "s405_under_test", os.path.join(step_dir, "step.py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


r405 = _load_405()


# ── Fixtures: a spine small enough to read, shaped like the contract ──

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


@pytest.fixture
def spine():
    return {"structure": [
        _block(1, 0.0, 3.0, ["alpha", "bravo", "charlie"], src=10.0),
        _block(2, 3.0, 3.0, ["delta", "echo", "foxtrot"], src=20.0),
        _block(3, 6.0, 3.0, ["golf", "hotel", "india"], src=30.0),
    ], "frame_rate": 30.0}


@pytest.fixture
def plan(spine):
    return generate_subtitles(spine)["subtitle_plan"]


# ── The splice is bounded, and says so from measurement ──────────────

def test_the_splice_leaves_every_other_entry_byte_identical(spine, plan):
    out = splice_region_plan(spine, plan, scope_mod.region("3.0-6.0"))
    assert out["splice"]["outside_unchanged"] is True
    assert (outside_region(plan["subtitle_entries"], [2])
            == outside_region(out["subtitle_plan"]["subtitle_entries"], [2]))


def test_outside_unchanged_is_measured_and_can_be_false():
    """MUTATION 1: report `outside_unchanged: True` without checking."""
    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0},
              {"id": "b", "spine_block_position": 2, "timeline_start": 1.0}]
    tampered = [{"id": "a", "spine_block_position": 1, "timeline_start": 9.9},
                {"id": "c", "spine_block_position": 2, "timeline_start": 1.0}]
    assert splice_report(stored, tampered, [2])["outside_unchanged"] is False
    honest = splice_plan(stored, [{"id": "c", "spine_block_position": 2,
                                   "timeline_start": 1.0}], [2])
    assert splice_report(stored, honest, [2])["outside_unchanged"] is True


def test_a_region_touching_no_block_is_refused_not_silently_empty(spine, plan):
    """MUTATION 2: return an empty plan for a typo'd region."""
    with pytest.raises(ValueError) as exc:
        splice_region_plan(spine, plan, scope_mod.region("100.0-110.0"))
    assert "touches no spine block" in str(exc.value)


def test_a_fresh_plan_may_not_carry_a_block_outside_the_region():
    """MUTATION 3: let a splice write blocks it was not asked for."""
    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0}]
    with pytest.raises(SpliceRefused) as exc:
        splice_plan(stored, [{"id": "x", "spine_block_position": 9,
                              "timeline_start": 5.0}], [1])
    assert "not in the region" in str(exc.value)


def test_a_region_whose_fresh_plan_is_empty_still_clears_its_block():
    """A re-index finding silence must REMOVE the captions, not keep them.

    MUTATION 4: infer the target blocks from the fresh entries instead of
    taking them explicitly - then an empty result silently keeps the old
    captions, which is the one case a caller cannot see.
    """
    stored = [{"id": "a", "spine_block_position": 1, "timeline_start": 0.0},
              {"id": "b", "spine_block_position": 2, "timeline_start": 1.0}]
    assert splice_plan(stored, [], [2]) == [stored[0]]


# ── The duration-preserving refusal ──────────────────────────────────

def test_a_duration_or_block_count_changing_splice_is_refused(spine):
    """MUTATION 5: drop the duration check.

    mesh_spine's post_bridge lays blocks end to end from a cumulative
    cursor, so a longer block moves every block after it.
    """
    stretched = [dict(b) for b in spine["structure"]]
    stretched[1] = {**stretched[1], "duration_seconds": 3.5}
    with pytest.raises(SpliceRefused) as exc:
        assert_durations_preserved(spine["structure"], stretched)
    assert "3.0s -> 3.5s" in str(exc.value)
    assert "every block after the change would move" in str(exc.value)

    # MUTATION 6: compare pairwise by index instead of by position. A
    # count change preserves total duration while renumbering every
    # position downstream - the join key for 4.01, 4.05 and 5.04.
    with pytest.raises(SpliceRefused) as exc:
        assert_durations_preserved(
            spine["structure"],
            spine["structure"] + [_block(99, 9.0, 1.0, ["extra"])])
    assert "would ADD a block" in str(exc.value)


# ── Three-valued provenance, both directions ─────────────────────────

class _Renderer:
    """A renderer that does what the test tells it to."""

    def __init__(self, ok=True, error=""):
        self.ok, self.error, self.calls = ok, error, 0

    def render(self, props_path, overlay_path, sequence=False):
        self.calls += 1
        if self.ok:
            with open(overlay_path, "wb") as handle:
                handle.write(b"pixels")
        return self.ok, self.error


def _props(block=1, text="alpha"):
    return {"_block_position": block, "_timeline_start": 0.0,
            "_timeline_end": 1.0, "_source_in_frame": 0,
            "_source_out_frame": 30, "_speaker": None,
            "_source_clip_id": "clip_001", "_source_start": 0.0,
            "_source_end": 1.0, "durationInFrames": 30, "fps": 30,
            "width": 1080, "height": 1920,
            "style": {"position": "bottom",
                      "safeArea": {"top": 120, "right": 120,
                                   "bottom": 320, "left": 90},
                      "captionMaxWidth": 840},
            "subtitles": [{"text": text}]}


def test_a_failed_render_is_REPORTED_not_dropped(tmp_path):
    """MUTATION 8: return None on failure again.

    A dropped segment is one the manifest never learns about, and 5.04
    then refuses the compile citing a missing block rather than the
    render that actually failed.
    """
    seg = r405.render_one_segment(_props(), str(tmp_path), "tl",
                                  remotion_dir=REPO,
                                  renderer=_Renderer(ok=False, error="boom"))
    assert seg is not None
    assert seg["provenance"] == r405.FAILED
    assert "boom" in seg["failure"]


def test_an_unchanged_segment_is_reused_and_a_changed_one_never_is(tmp_path):
    """MUTATION 9: mark everything `rendered`."""
    engine = _Renderer()
    first = r405.render_one_segment(_props(), str(tmp_path), "tl",
                                    remotion_dir=REMOTION, renderer=engine,
                                    reuse=True,
                                    overlay_geometry="full")
    second = r405.render_one_segment(_props(), str(tmp_path), "tl",
                                     remotion_dir=REMOTION, renderer=engine,
                                     reuse=True,
                                     overlay_geometry="full")
    assert first["provenance"] == r405.RENDERED
    assert second["provenance"] == r405.REUSED
    assert engine.calls == 1, "the second call must not have rendered"

    # MUTATION 10, the one that matters most: a CHANGED segment is never
    # skipped. Skipping on PRESENCE would skip a text-only correction
    # (on 001, changing every caption in a block changed 0 of 8 names).
    again = r405.render_one_segment(_props(text="AFTER"), str(tmp_path), "tl",
                                    remotion_dir=REMOTION, renderer=engine,
                                    reuse=True,
                                    overlay_geometry="full")
    assert again["provenance"] == r405.RENDERED
    assert engine.calls == 2


def test_a_plain_run_re_renders_even_when_the_key_matches(tmp_path):
    """MUTATION 11: flip the `reuse` default to on.

    Firstmate's ruling: a plain run always re-renders. Reuse is opt-in.
    Without this, a one-character change to a default would make every
    run reuse, every other test would still pass, and the first symptom
    would be a stale caption in a delivered video.
    """
    engine = _Renderer()
    r405.render_one_segment(_props(), str(tmp_path), "tl",
                            remotion_dir=REMOTION, renderer=engine, reuse=True,
                            overlay_geometry="full")
    plain = r405.render_one_segment(_props(), str(tmp_path), "tl",
                                    remotion_dir=REMOTION, renderer=engine,
                                    overlay_geometry="full")
    assert plain["provenance"] == r405.RENDERED
    assert engine.calls == 2


def test_an_unavailable_renderer_fingerprint_refuses_reuse(tmp_path):
    """MUTATION 12: treat "cannot hash" as "matches".

    Unavailable evidence must never read as matching evidence.
    """
    assert r405.renderer_fingerprint(str(tmp_path / "nothing-here")) == ""
    engine = _Renderer()
    for _ in range(2):
        seg = r405.render_one_segment(
            _props(), str(tmp_path), "tl",
            remotion_dir=str(tmp_path / "nothing-here"),
            renderer=engine, reuse=True,
            overlay_geometry="full")
        assert seg["provenance"] == r405.RENDERED
    assert engine.calls == 2


# ── The transcript half ──────────────────────────────────────────────

def test_the_transcript_splice_replaces_by_overlap_and_rederives_word_ends():
    doc = {"speech_regions": [
        {"start": 0.0, "end": 1.5, "text": "before", "words": []},
        {"start": 1.8, "end": 4.0, "text": "straddles", "words": []},
        {"start": 6.0, "end": 8.0, "text": "after", "words": []}]}
    out = splice_region_index(doc, [{"start": 2.5, "end": 3.5,
                                     "text": "new", "words": []}], 1.9, 5.0)
    # "straddles" goes even though it is not CONTAINED: it was partly
    # re-measured, so keeping it would leave two descriptions of 1.9-4.0s.
    assert [r["text"] for r in out["speech_regions"]] == \
        ["before", "new", "after"]

    # word_end_times is re-derived, not left stale.
    doc = {"speech_regions": [{"start": 0.0, "end": 2.0, "words": [
               {"word": "a", "start": 0.0, "end": 2.0}]}],
           "word_end_times": [2.0]}
    out = splice_region_index(doc, [{"start": 3.0, "end": 4.0, "words": [
        {"word": "b", "start": 3.0, "end": 4.0}]}], 2.5, 5.0)
    assert out["word_end_times"] == [2.0, 4.0]


def test_a_re_measured_region_outside_its_span_is_refused():
    """Leaked padding would overwrite speech that was never re-measured."""
    doc = {"speech_regions": []}
    with pytest.raises(ValueError) as exc:
        splice_region_index(doc, [{"start": 0.5, "end": 3.0, "words": []}],
                            1.0, 4.0)
    assert "padding leaked" in str(exc.value)


# ── The partial write ────────────────────────────────────────────────

def _project(tmp_path, outputs):
    (tmp_path / "pipeline_data.json").write_text(
        json.dumps({"step_outputs": outputs}), encoding="utf-8")
    return str(tmp_path)


def test_a_refused_splice_leaves_the_file_byte_identical(tmp_path):
    project = _project(tmp_path, {"plan_subtitles": {"n": 1}})
    before = (tmp_path / "pipeline_data.json").read_bytes()

    def boom(_after):
        raise ValueError("not on my watch")

    with pytest.raises(state_splice.StateSpliceRefused):
        state_splice.splice_step_output(
            project, "plan_subtitles", lambda cur: {"n": 2},
            label="t", verify=boom)
    assert (tmp_path / "pipeline_data.json").read_bytes() == before

    # Splicing into a step that never ran is refused.
    with pytest.raises(state_splice.StateSpliceRefused) as exc:
        state_splice.splice_step_output(
            project, "render_subtitles", lambda cur: {}, label="t")
    assert "nothing to splice into" in str(exc.value)


# ── The fifth --rerun form ───────────────────────────────────────────

def _runner():
    """The runner module, by absolute package path.

    Production (`operations.Operation`) reaches it through a bare
    ``import run_pipeline`` with the process directory on sys.path; the
    tests need the same file's behaviour, and the bare name is
    collection-order-sensitive, so this route spells the package out.
    """
    from library.processes.edit_video import run_pipeline
    return run_pipeline


def test_the_runner_refuses_a_region_rerun_and_names_what_honours_one():
    """`--rerun <step>@<span>` PARSES, and the runner cannot honour it:
    steps run as subprocesses and no `main()` reads an address, so it
    used to redo the WHOLE video while printing that the region decided.
    It refuses, naming the region operation DERIVED from the registry -
    or saying plainly that none exists - and half-does nothing."""
    from library.tools.step_ledger import LedgerError

    runner = _runner()
    state = {}
    with pytest.raises(LedgerError) as exc:
        runner.apply_rerun_requests(
            "/nonexistent", state, ["plan_subtitles@32.0-48.0"],
            {"plan_subtitles": "edit"}, {})
    message = str(exc.value)
    assert "cannot re-run part of a step" in message
    assert "operations subtitles.plan" in message
    assert "--region 32.0-48.0" in message
    assert state == {}

    with pytest.raises(LedgerError) as exc:
        runner.apply_rerun_requests(
            "/nonexistent", {}, ["color_grade@32.0-48.0"],
            {"color_grade": "edit"}, {})
    assert "No operation on color_grade runs at a region" in str(exc.value)
    assert "--rerun color_grade" in str(exc.value)

    with pytest.raises(LedgerError) as exc:
        runner.apply_rerun_requests(
            "/nonexistent", {}, ["plan_transitions@32.0-48.0"],
            {"plan_transitions": "edit"}, {})
    assert "transitions.splice" in str(exc.value)


# ── The line that joins the two halves of the seam ───────────────────
#
# vep-audit-contracts builds the renderer; this file builds the caller.
# Without the construction site AND the teardown site, both halves are
# individually correct and the feature is absent - the vacuous-gate shape
# in wiring rather than in checking.


@pytest.fixture
def no_pixel_qa(monkeypatch):
    """Stub the rendered-overlay QA for the WIRING tests.

    Those tests use a fake renderer that writes a few bytes rather than a
    real ProRes file, so `subtitle_qa` correctly reports that the overlay
    draws nothing. That is the QA doing its job on a stub, and it is not
    what these tests are about - they assert who CONSTRUCTS and who
    CLOSES the renderer. The real QA is exercised by the 001
    demonstration, against real renders.
    """
    import types
    module = types.ModuleType("tools.qa.subtitle_qa")
    module.run_subtitle_qa = lambda *a, **k: None
    monkeypatch.setitem(sys.modules, "tools.qa.subtitle_qa", module)
    return module


class _CountingRenderer:
    def __init__(self):
        self.rendered, self.closed = 0, 0

    def render(self, props_path, overlay_path, sequence=False):
        self.rendered += 1
        with open(overlay_path, "wb") as handle:
            handle.write(b"pixels")
        return True, ""

    def close(self):
        self.closed += 1


def _spine_and_plan_for_render():
    spine = {"structure": [_block(1, 0.0, 3.0, ["alpha", "bravo"], src=10.0)],
             "frame_rate": 30.0}
    return spine, generate_subtitles(spine)["subtitle_plan"]


def test_a_renderer_we_BUILT_is_closed_even_when_the_pass_raises(
        tmp_path, monkeypatch):
    """MUTATION: drop the `finally`.

    A renderer holding a bundle or a browser owns an OS resource; over
    nineteen reels a leak per pass is nineteen leaks. The default build
    is the persistent renderer, so the bomb is planted there.
    """
    spine, plan = _spine_and_plan_for_render()
    built = []

    class _Boom(_CountingRenderer):
        def render(self, props_path, overlay_path, sequence=False):
            raise RuntimeError("mid-pass explosion")

    def factory(remotion_dir):
        engine = _Boom()
        built.append(engine)
        return engine

    monkeypatch.setattr(r405, "PersistentCaptionRenderer", factory)
    with pytest.raises(RuntimeError):
        r405.render_subtitle_overlays(plan, spine,
                                      project_folder=str(tmp_path),
                                      remotion_dir=REMOTION)
    assert built and built[0].closed == 1, "we built it, so we close it"
