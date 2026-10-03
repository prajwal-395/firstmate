"""An EditPatch commits against the generation it was planned on, or not at all.
"""
from __future__ import annotations
from dataclasses import replace
import pytest
from library.tools import capabilities, edit_patch, patch_algebra
from library.tools import timeline_shadow as shadow
from library.tools.resolve_lock import assume_sole_writer
from tests.resolve_double import (
    FakeResolve,
    FakeTimeline,
    make_pool_clip,
    make_project,
    place_clip,
)
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from library.processes.edit_video import run_pipeline as runner
from library.tools import edit_input_digest as digest
from library.tools import run_control
import shutil
import subprocess
from library.tools import dirty_regions as d
from library.tools import render_qa as q


@pytest.fixture
def world(tmp_path):
    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    item = place_clip(timeline, make_pool_clip("a.mov"), 0, 47)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp_path / "shadow.db")
    with assume_sole_writer("canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
        yield project, timeline, item, store, base


def _patch(pid, base, operations, domains, spans, **extra):
    return {"id": pid, "project": "Podcast", "timeline": "Reel 09",
            "base_generation": base, "capability": "reel.touchup",
            "affected_spans": spans, "conflict_domains": domains,
            "operations": operations, **extra}


def _apply(patch, project, timeline, store):
    return edit_patch.apply_patch(patch, resolve=FakeResolve(project),
                                  project=project, timeline=timeline,
                                  store=store)


def test_a_patch_commits_and_becomes_the_next_generation(world):
    project, timeline, item, store, base = world
    patch = _patch("p1", base.generation, [
        {"op": "marker.add", "frame": 12, "color": "Blue", "name": "beat"},
        {"op": "clip.set_enabled", "unique_id": item.GetUniqueId(),
         "enabled": False},
    ], ["markers", "timeline_structure"], [[0, 48]],
        preconditions=[{"kind": "marker_absent", "frame": 12}],
        postconditions=[{"kind": "duration_unchanged"}])
    receipt = _apply(patch, project, timeline, store)
    assert receipt["status"] == "committed", receipt
    assert receipt["generation"] == base.generation + 1
    head = store.head("Podcast", base.timeline_id)
    assert head.source == shadow.PATCH and head.patch["id"] == "p1"
    assert head.receipt["status"] == "committed"
    assert item.GetClipEnabled() is False
    # A retry of the same id is answered from the record, not re-applied.
    assert _apply(patch, project, timeline, store) == receipt
    assert store.head("Podcast", base.timeline_id).generation == \
        receipt["generation"]


def test_reusing_a_patch_id_with_different_contents_refuses(world):
    project, timeline, _item, store, base = world
    first = _patch("stable-id", base.generation, [
        {"op": "marker.add", "frame": 12, "color": "Blue", "name": "beat"}],
        ["markers"], [[12, 13]])
    _apply(first, project, timeline, store)

    changed = _patch("stable-id", base.generation, [
        {"op": "marker.add", "frame": 13, "color": "Blue", "name": "beat"}],
        ["markers"], [[13, 14]])
    with pytest.raises(edit_patch.PatchRefused,
                       match="already committed with different contents"):
        _apply(changed, project, timeline, store)
    assert 12 in timeline.markers and 13 not in timeline.markers


def test_a_write_that_answers_true_and_changes_nothing_fails_by_readback(
        world, monkeypatch):
    project, timeline, item, store, base = world
    monkeypatch.setattr(type(item), "SetProperty", lambda self, k, v: True)
    receipt = _apply(_patch("p1", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.4}], ["picture_transform"], [[0, 48]]),
        project, timeline, store)
    assert receipt["status"] == "verification_failed"
    assert "ZoomX=1.0" in receipt["operations"][0]["failure"]
    # The shadow records what Resolve holds, not what was planned.
    assert receipt["generation"] == base.generation + 1


def test_a_hand_edit_since_the_base_refuses_and_is_recorded(world):
    project, timeline, _item, store, base = world
    timeline.AddMarker(30, "Red", "captain", "", 1)
    with pytest.raises(edit_patch.StalePatch) as stale:
        _apply(_patch("p1", base.generation, [
            {"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
            ["markers"], [[0, 10]]), project, timeline, store)
    assert stale.value.observed and not stale.value.rebase_possible
    head = store.head("Podcast", base.timeline_id)
    assert head.source == shadow.OBSERVED
    assert 5 not in timeline.markers


def test_a_stale_patch_rebases_past_patches_whose_writes_do_not_meet(world):
    project, timeline, _item, store, base = world
    _apply(_patch("markers", base.generation, [
        {"op": "marker.add", "frame": 40, "color": "Blue", "name": "m"}],
        ["markers"], [[40, 41]]), project, timeline, store)

    # Same domain, overlapping spans, another frame: it commutes, where
    # "both touch markers here" used to refuse it.
    commuting = _patch("marker-20", base.generation, [
        {"op": "marker.add", "frame": 20, "color": "Red", "name": "n"}],
        ["markers"], [[0, 48]])
    with pytest.raises(edit_patch.StalePatch) as stale:
        _apply(commuting, project, timeline, store)
    assert stale.value.rebase_possible
    moved = edit_patch.rebase(commuting, store)
    assert _apply(moved.to_dict(), project, timeline,
                  store)["status"] == "committed"

    meeting = _patch("marker-too", base.generation, [
        {"op": "marker.delete", "frame": 40}], ["markers"], [[40, 41]])
    with pytest.raises(edit_patch.StalePatch,
                       match=r"both write \['marker', 40\]"):
        edit_patch.rebase(meeting, store)


def test_the_same_write_merges_and_a_different_one_is_a_lost_update(world):
    project, timeline, item, store, base = world

    def zoom(pid, value):
        return _patch(pid, base.generation, [
            {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
             "key": "ZoomX", "value": value}], ["picture_transform"],
            [[0, 48]], capability="reel.set_properties")

    _apply(zoom("first", 1.2), project, timeline, store)
    assert _apply(edit_patch.rebase(zoom("same", 1.2), store).to_dict(),
                  project, timeline, store)["status"] == "committed"
    with pytest.raises(edit_patch.StalePatch, match="lost update"):
        edit_patch.rebase(zoom("other", 1.4), store)


def test_a_frame_addressed_write_past_a_ripple_conflicts(world, monkeypatch):
    _project, _timeline, item, store, base = world
    snap = store.snapshot(base)
    specs = tuple(
        replace(spec, execution=replace(
            spec.execution,
            patch=replace(spec.execution.patch, temporal_effect="ripple")))
        if spec.id == "reel.touchup" else spec
        for spec in capabilities.all())
    monkeypatch.setattr(capabilities, "all", lambda: specs)
    ripple = _patch("cut", base.generation, [
        {"op": "marker.add", "frame": 10, "color": "Blue", "name": "c"}],
        ["markers"], [[10, 20]])
    marker = _patch("m", base.generation, [
        {"op": "marker.add", "frame": 30, "color": "Red", "name": "m"}],
        ["markers"], [[30, 31]])
    by_id = _patch("z", base.generation, [
        {"op": "clip.set_property", "unique_id": item.GetUniqueId(),
         "key": "ZoomX", "value": 1.2}], ["picture_transform"], [[0, 48]])
    assert patch_algebra.compose(ripple, marker, snap).verdict == \
        patch_algebra.CONFLICT
    assert patch_algebra.compose(ripple, by_id, snap).verdict == \
        patch_algebra.REBASE


def test_a_declaration_looser_than_its_operations_is_caught():
    loose = {"reel.set_properties": capabilities.PatchSemantics(
        operations=("clip.set_property", "marker.add"),
        conflict_domains=("picture_transform",),
        temporal_effect="local", merge_semantics="commutative")}
    found = "\n".join(patch_algebra.problems(
        loose, capability_ids={"reel.set_properties"}))
    assert "lost update" in found
    assert "writes 'markers', which it does not declare" in found


@pytest.mark.parametrize("operations,domains,spans,why", [
    ([{"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
     ["picture_transform"], [[0, 10]], "does not declare"),
    ([{"op": "marker.add", "frame": 50, "color": "Blue", "name": "x"}],
     ["markers"], [[0, 10]], "outside the declared spans"),
    ([{"op": "clip.delete", "unique_id": "nobody"}],
     ["timeline_structure"], [[0, 48]], "not exactly once"),
])
def test_a_patch_that_says_less_than_it_does_is_refused_before_resolve(
        world, operations, domains, spans, why):
    project, timeline, _item, store, base = world
    with pytest.raises(edit_patch.PatchRefused, match=why):
        _apply(_patch("p", base.generation, operations, domains, spans),
               project, timeline, store)
    assert store.head("Podcast", base.timeline_id).generation == \
        base.generation


@pytest.mark.parametrize("capability,why", [
    ("reel.set_properties", "beyond what"),
    ("footage.scan", "declares no patch semantics"),
])
def test_a_patch_may_not_say_more_than_its_capability_declares(
        world, capability, why):
    project, timeline, _item, store, base = world
    with pytest.raises(edit_patch.PatchRefused, match=why):
        _apply(_patch("p", base.generation, [
            {"op": "marker.add", "frame": 5, "color": "Blue", "name": "x"}],
            ["markers"], [[0, 10]], capability=capability),
            project, timeline, store)


# --------------------------------------------------------------------------
# From test_edit_input_digest.py
#
# Edit-step input digests: declared, stamped, and reported - never skipped.
#
# History: docs/evidence/resolve_test_history.md#test_edit_input_digest.

PILOT_ROOT = Path(__file__).resolve().parents[3]
STEPS_ROOT = PILOT_ROOT / "library" / "steps"


def _manifest(step_dir_name):
    with open(STEPS_ROOT / step_dir_name / "manifest.json",
              encoding="utf-8") as f:
        return json.load(f)


def _inputs(**overrides):
    base = {
        "timeline_transcript": {
            "measurement": {"segments": [
                {"speaker": "A", "text": "hello", "start": 0.0, "end": 1.0},
                {"speaker": "B", "text": "hi", "start": 1.0, "end": 2.0},
            ]},
        },
        "creative_direction": {"narrative_theme": "craft"},
        "creative_brief": "make three reels",
        "project_context": "context map",
        "brand_constraints": {"palette": ["#000000"]},
    }
    base.update(overrides)
    return base


def _select_reels_output():
    return {
        "turns": [{"speaker": "A", "start": 0.0, "end": 1.0}],
        "reel_candidates": [{"start": 0.0, "end": 45.0}],
        "length_guidance_seconds": [45, 90],
        "picture_holes": [],
        "lead_speaker": "A",
        "answering_speaker": "B",
        "who_leads_was_inferred": "A asks more",
        "reel_selection": {"moments": [], "considered": []},
    }


# ── The declaration ─────────────────────────────────────────────────


def test_select_reels_declares_what_it_reads():
    """19 of geo-podcast's last 20 run_history entries re-ran select_reels.

    The digest narrows the manifest's inputs to what reaches the bridge
    or the prompt. `audio_spine` is deliberately NOT among them: the
    manifest's own input description says nothing reads it - the DAG edge
    that carries it only orders this step after the spine - and hashing
    it would report "changed" on runs the model saw identically.
    """
    spec = digest.digest_spec(
        _manifest("step_3_04_select_reels"), "select_reels")
    assert spec == {
        "inputs": ["timeline_transcript", "creative_direction",
                   "creative_brief", "project_context",
                   "brand_constraints"],
        "context": ["turns", "reel_candidates", "length_guidance_seconds",
                    "picture_holes", "lead_speaker", "answering_speaker",
                    "who_leads_was_inferred"],
        "include_code": True,
    }


# ── The digest ──────────────────────────────────────────────────────

def _spec():
    return digest.digest_spec(
        _manifest("step_3_04_select_reels"), "select_reels")


def _step_dir():
    return STEPS_ROOT / "step_3_04_select_reels"


def test_digest_moves_when_an_upstream_value_moves():
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    changed = _inputs()
    changed["timeline_transcript"]["measurement"]["segments"][0]["text"] = \
        "hello, edited"
    after = digest.compute_digest(
        spec, changed, _select_reels_output(), _step_dir())
    assert before["digest"] != after["digest"]
    assert before["inputs_digest"] != after["inputs_digest"]
    assert before["code_digest"] == after["code_digest"]


def test_digest_moves_when_the_prompt_moves_but_code_identity_would_not():
    """The departure from code_identity, pinned: the handoff prompt is
    executable for a model step, so editing it moves the digest even
    though code_identity (which excludes .md as prose) would not see it."""
    import shutil
    import tempfile

    from library.tools import code_identity
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "step_3_04_select_reels"
        shutil.copytree(_step_dir(), clone,
                        ignore=shutil.ignore_patterns("__pycache__"))
        spec = _spec()
        before = digest.compute_digest(
            spec, _inputs(), _select_reels_output(), clone)
        code_before = code_identity.step_code_hash(str(clone))
        (clone / "handoff.md").write_text(
            (clone / "handoff.md").read_text(encoding="utf-8")
            + "\n\nAlso prefer owls.\n")
        after = digest.compute_digest(
            spec, _inputs(), _select_reels_output(), clone)
        assert code_before == code_identity.step_code_hash(str(clone))
        assert before["digest"] != after["digest"]
        assert before["code_digest"] != after["code_digest"]
        assert before["inputs_digest"] == after["inputs_digest"]


def test_a_model_answer_is_not_an_input():
    """Changing only the model's own answer must NOT move the digest -
    hashing one would report 'changed' on every re-run by construction."""
    spec = _spec()
    before = digest.compute_digest(
        spec, _inputs(), _select_reels_output(), _step_dir())
    output = _select_reels_output()
    output["reel_selection"] = {"moments": [{"slug": "new-answer"}],
                                "considered": []}
    after = digest.compute_digest(spec, _inputs(), output, _step_dir())
    assert before["digest"] == after["digest"]


# ── The comparison: report, never act ───────────────────────────────


# ── The stamp in the run record ─────────────────────────────────────

def _begin(tmp_path, previous=None):
    project = tmp_path / "proj"
    project.mkdir()
    if previous is not None:
        (project / run_control.RUN_STATUS_FILE).write_text(
            json.dumps(previous), encoding="utf-8")
    run_control.begin_run_status(
        str(project), "single-step select_reels, manual LLM",
        ["select_reels"], argv=["--step", "select_reels"],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})
    return project


def test_stamp_helper_reports_and_never_skips(tmp_path, capsys):
    """The runner helper stamps, prints the comparison, and returns None -
    there is no skip path because none was built. The second identical run
    prints 'identical' while stamping again."""
    project = _begin(tmp_path)
    impl = {"manifest": _manifest("step_3_04_select_reels"),
            "step_dir": _step_dir(), "type": "hybrid"}
    runner._stamp_step_input_digest(
        str(project), "select_reels", impl, _inputs(),
        _select_reels_output())
    first_out = capsys.readouterr().err
    assert "no prior stamp" in first_out

    # A second run carries the first run's stamp as its baseline.
    run_control.begin_run_status(
        str(project), "single-step select_reels, manual LLM",
        ["select_reels"], argv=["--step", "select_reels"],
        profile=SimpleNamespace(name="", path="", source="",
                                adopted=False, description=""),
        breakpoints={}, state={})
    assert runner._stamp_step_input_digest(
        str(project), "select_reels", impl, _inputs(),
        _select_reels_output()) is None
    second_out = capsys.readouterr().err
    assert "identical inputs" in second_out
    record = run_control.read_run_status(str(project))
    assert record["step_input_digests"]["select_reels"]["digest"]


# --------------------------------------------------------------------------
# From test_dirty_regions.py
#
# A touch's dirty block, and the scoped render QA that reads it.
#
# Two defects this pins: a span stated wrong for an op (a move that dirties
# only one end re-checks the wrong seconds), and a scoped read whose findings
# differ from the whole-file read over the same frames - the scoped verdict
# must be the whole verdict, restricted.

F0, FPS = 86400, 30.0


def _clip(a, b):
    return {"record_in": F0 + round(a * FPS), "duration": round((b - a) * FPS)}


TRACKS = [
    {"type": "video", "index": 4,
     "clips": [_clip(8.2, 9.0), _clip(14.5, 15.0), _clip(19.0, 19.5)]},
    {"type": "audio", "index": 1, "clips": [_clip(3.2, 3.6)]},
]


def _dirty(*edits, end=600):
    return d.dirty_from_spec({"edits": list(edits)}, TRACKS,
                             first_frame=F0, end_frame=F0 + end, fps=FPS)


def _cores(dirty):
    return [(s["domain"], s["core_start_seconds"], s["core_end_seconds"])
            for s in dirty["dirty_spans"]]


def test_each_op_dirties_its_own_span_and_domain():
    dirty = _dirty({"op": "swap_pixels", "row": "V4", "item": 1},
                   {"op": "set_properties", "row": "A1", "item": 0})
    assert not dirty["whole_reel"]
    assert dirty["dirty_domains"] == ["picture", "audio"]
    assert _cores(dirty) == [("audio", 3.2, 3.6), ("picture", 14.5, 15.0)]
    span = dirty["dirty_spans"][1]
    assert span["start_seconds"] == 14.5 - d.HANDLE_SECONDS
    assert span["end_seconds"] == 15.0 + d.HANDLE_SECONDS
    # A move dirties where it left and where it landed.
    dirty = _dirty({"op": "move", "row": "V4", "item": 0,
                    "to_record": F0 + 360})
    assert _cores(dirty) == [("picture", 8.2, 9.0), ("picture", 12.0, 12.8)]
    # A retime dirties from the cut to the end on both domains: 15f
    # grown to 45f, the reel ends a second later than it did.
    dirty = _dirty({"op": "retime", "row": "V4", "item": 1,
                    "duration": 45})
    assert _cores(dirty) == [("audio", 14.5, 21.0), ("picture", 14.5, 21.0)]


def test_what_cannot_be_bounded_is_the_whole_reel():
    assert _dirty({"op": "mystery", "row": "V4", "item": 0})["whole_reel"]
    assert _dirty({"op": "swap_pixels", "row": "V4", "item": 9})["whole_reel"]
    assert d.dirty_from_spec({"edits": []}, TRACKS, first_frame=None,
                             end_frame=None, fps="")["whole_reel"]
    # A receipt from before the dirty block is never "nothing changed".
    assert d.read_dirty([{"final": "Reel 01"}])["whole_reel"]


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_scoped_findings_equal_the_whole_file_read_over_the_same_frames(
        tmp_path):
    # Black 8.0-9.5s, a freeze 14.0-16.0s and digital silence 3.0-4.0s.
    video = str(tmp_path / "synth.mp4")
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", "testsrc2=s=180x320:r=30:d=20",
         "-f", "lavfi", "-i", "sine=f=440:r=48000:d=20",
         "-filter_complex",
         ("[0:v]drawbox=c=black:t=fill:enable='between(t,8,9.5)',split[p][q];"
          "[p][q]freezeframes=first=420:last=480:replace=420[v];"
          "[1:a]volume=enable='between(t,3,4)':volume=0[a]"),
         "-map", "[v]", "-map", "[a]", "-c:v", "libx264",
         "-pix_fmt", "yuv420p", "-c:a", "aac", "-shortest", video],
        check=True, capture_output=True, encoding="utf-8")
    dirty = _dirty({"op": "set_properties", "row": "V4", "item": 0},
                   {"op": "swap_pixels", "row": "V4", "item": 1},
                   {"op": "set_properties", "row": "A1", "item": 0})
    scoped, skipped = q.run_scoped_render_qa(video, dirty)
    assert skipped == []
    got = {r.metric: [(round(f["start"], 3), round(f["end"], 3))
                      for f in r.value] for r in scoped}

    silence = q.measure_silence_under_picture(video).value
    whole = {
        "black_frames": q.detect_black_frames(video).value,
        "freeze_frames": q.detect_freeze_frames(video).value,
        "silence_under_picture": [
            r for r in silence["by_level"]["digital_zero"]["where"]
            if r["seconds_under_picture"] >= silence["minimum_run_seconds"]],
    }
    for metric, domains in q.SCOPED_DETECTORS.items():
        cores = [(s[2], s[3]) for s in d.spans_for(dirty, domains)]
        want = [(round(r["start"], 3), round(r["end"], 3))
                for r in whole[metric]
                if any(r["start"] < hi and r["end"] > lo for lo, hi in cores)]
        assert len(got[metric]) == len(want), (metric, got, want)
        for (gs, ge), (ws, we) in zip(got[metric], want):
            assert gs == ws, (metric, got, want)
            # A freeze running past the span's end is reported up to it:
            # it runs AT LEAST that far.
            assert ge == we or (metric == "freeze_frames" and ge <= we), (
                metric, got, want)
    assert got["black_frames"] and got["freeze_frames"] \
        and got["silence_under_picture"], json.dumps(got)


def _encode(path, colour, seconds):
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
         f"color={colour}:s=64x112:r=30:d={seconds}",
         "-c:v", "libx264", "-pix_fmt", "yuv420p", str(path)],
        check=True, capture_output=True, encoding="utf-8")


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_re_delivered_file_is_watched_afresh(tmp_path):
    """`deliver-reel` writes a reel to the same name every time: keyed by
    the stem alone, the re-delivered reel was shown the previous render's
    strips."""
    from library.tools import render_watch as rw
    video = tmp_path / "Reel 01.mp4"

    def drawn():
        rw.watch_video(str(video), str(tmp_path / "f"),
                       str(tmp_path / "w.json"), "t")
        record = json.loads((tmp_path / "w.json").read_text("utf-8"))
        return {(tmp_path / "f" / row["file"]).read_bytes()
                for row in rw.draw_watch_strips(
                    str(video), str(tmp_path / "f"))["rows"]} \
            if record["watched"] else set()

    _encode(video, "red", 3)
    before = drawn()
    _encode(video, "blue", 3)
    assert drawn().isdisjoint(before)


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="needs ffmpeg")
def test_a_scoped_watch_is_the_whole_watch_restricted(tmp_path):
    from library.tools import render_watch as rw
    video = tmp_path / "reel.mp4"
    _encode(video, "green", 20)
    receipt = tmp_path / "touch.json"
    receipt.write_text(json.dumps({
        "final": "Reel 01", "started": "2000-01-01T00:00:00+00:00",
        "dirty": _dirty({"op": "set_properties", "row": "V4", "item": 1})}),
        "utf-8")
    whole = rw.watch_video(str(video), str(tmp_path / "f"),
                           str(tmp_path / "a.json"), "t")["record"]
    scoped = rw.watch_video(str(video), str(tmp_path / "f"),
                            str(tmp_path / "b.json"), "t",
                            dirty_receipts=[str(receipt)])["record"]
    assert (whole["strips"], scoped["strips"]) == (3, 1)
    assert scoped["not_rewatched"] == ["0.000-8.000s", "16.000-20.000s"]

    receipt.write_text(json.dumps({
        "final": "Reel 01", "started": "2000-01-01T00:00:00+00:00",
        "dirty": _dirty({"op": "set_properties", "row": "A1", "item": 0})}),
        "utf-8")
    with pytest.raises(rw.NoPictureChanged):
        rw.watch_video(str(video), str(tmp_path / "f"),
                       str(tmp_path / "c.json"), "t",
                       dirty_receipts=[str(receipt)])
