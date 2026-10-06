"""The transformation tier's behaviour: room tone from the source's own
ambience, a freeze rendered at the boundary, and a substitute chosen
from the project's catalog."""
from __future__ import annotations

import json
import math
import os
import struct
import subprocess
import wave

import pytest

from library.tools import asset_routing, asset_transformations
from library.tools.asset_routing import Need
from library.tools.ren_refusal import RenRefusal


def _write_wav(path: str, seconds: float, rate: int = 8000,
               tone: float = 0.0) -> None:
    """A WAV fixture: silence, or a quiet tone for measurable room."""
    frames = int(seconds * rate)
    with wave.open(path, "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(rate)
        samples = []
        for i in range(frames):
            value = tone * math.sin(2 * math.pi * 220 * i / rate)
            samples.append(int(value * 32767))
        handle.writeframes(struct.pack(f"<{len(samples)}h", *samples))


def _write_video(path: str, seconds: float = 1.0, fps: int = 10) -> None:
    """A small video fixture, rendered by ffmpeg's testsrc.

    The freeze handler's whole behaviour is rendering pixels, so a test
    that did not encode would leave its ffmpeg path untested.
    """
    subprocess.run(
        ["ffmpeg", "-v", "error", "-y",
         "-f", "lavfi", "-i", f"testsrc=duration={seconds}:"
                           f"size=64x64:rate={fps}",
         "-pix_fmt", "yuv420p", path],
        capture_output=True, timeout=120, encoding="utf-8", check=True,
    )


@pytest.fixture(autouse=True)
def _clear_handlers():
    saved = dict(asset_routing.registered_handlers("transformation"))
    try:
        yield
    finally:
        handlers = asset_routing.registered_handlers("transformation")
        handlers.clear()
        handlers.update(saved)


def _need(kind, params):
    return Need(kind, params)


def test_room_tone_fill_stages_the_sources_own_room(tmp_path):
    source = str(tmp_path / "source.wav")
    _write_wav(source, 2.0, tone=0.02)
    context = {"project_folder": str(tmp_path), "step_id": "s"}

    outcome = asset_transformations.room_tone_fill(
        _need("room_tone", {"source_file": source, "fill_seconds": 1.0}),
        context)

    assert outcome["solved"] is True
    assert outcome["artifact"]["media_type"] == "audio"
    assert os.path.isfile(outcome["artifact"]["path"])
    assert outcome["provenance"]["derived_from"] == [source]
    assert "loop" in outcome["provenance"]["detail"]


def test_room_tone_fill_refuses_a_source_with_no_measurable_room(tmp_path):
    source = str(tmp_path / "silent.wav")
    _write_wav(source, 1.0, tone=0.0)
    context = {"project_folder": str(tmp_path), "step_id": "s"}

    with pytest.raises(RenRefusal) as excinfo:
        asset_transformations.room_tone_fill(
            _need("room_tone", {"source_file": source,
                                "fill_seconds": 0.5}),
            context)
    assert "room tone cannot be measured" in excinfo.value.what


def test_room_tone_fill_refuses_a_need_with_no_source(tmp_path):
    with pytest.raises(RenRefusal) as excinfo:
        asset_transformations.room_tone_fill(
            _need("room_tone", {"fill_seconds": 1.0}),
            {"project_folder": str(tmp_path), "step_id": "s"})
    assert "no source_file" in excinfo.value.what


def test_freeze_frame_holds_the_boundary_frame(tmp_path):
    source = str(tmp_path / "clip.mp4")
    _write_video(source, seconds=1.0, fps=10)
    context = {"project_folder": str(tmp_path), "step_id": "s"}

    outcome = asset_transformations.freeze_frame(
        _need("missing_frames",
              {"source_file": source, "boundary_seconds": 0.5,
               "frames": 5, "fps": 10}),
        context)

    assert outcome["solved"] is True
    assert outcome["artifact"]["media_type"] == "video"
    assert outcome["artifact"]["frames"] == 5
    assert os.path.isfile(outcome["artifact"]["path"])
    assert outcome["provenance"]["derived_from"] == [source]
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "format=duration", "-of", "default=nw=1:nk=1",
         outcome["artifact"]["path"]],
        capture_output=True, timeout=60, encoding="utf-8", check=True,
    )
    assert abs(float(probe.stdout.strip()) - 0.5) < 0.1


def test_freeze_frame_refuses_a_source_that_is_not_on_disk(tmp_path):
    with pytest.raises(RenRefusal) as excinfo:
        asset_transformations.freeze_frame(
            _need("missing_frames",
                  {"source_file": str(tmp_path / "absent.mp4"),
                   "boundary_seconds": 0.5, "frames": 5, "fps": 10}),
            {"project_folder": str(tmp_path), "step_id": "s"})
    assert "is not on disk" in excinfo.value.what


def test_freeze_frame_refuses_a_non_positive_frame_count(tmp_path):
    source = str(tmp_path / "clip.mp4")
    _write_video(source, seconds=0.5, fps=10)
    with pytest.raises(RenRefusal) as excinfo:
        asset_transformations.freeze_frame(
            _need("missing_frames",
                  {"source_file": source, "boundary_seconds": 0.1,
                   "frames": 0, "fps": 10}),
            {"project_folder": str(tmp_path), "step_id": "s"})
    assert "not a positive whole number" in excinfo.value.what


def _project_with_catalog(tmp_path, clips, semantic_docs=None):
    """A project whose pipeline_data.json carries a catalog."""
    state = {"capability_outputs": {"footage.catalog": {
        "clip_catalog": clips,
    }}}
    if semantic_docs is not None:
        state["capability_outputs"]["semantics.analyse"] = {
            "semantic_analysis_documents": semantic_docs,
        }
    out = tmp_path / "pipeline_data"
    out.mkdir()
    with open(out / "pipeline_data.json", "w", encoding="utf-8") as handle:
        json.dump(state, handle)


def test_broll_substitute_selects_from_the_catalog(tmp_path):
    video = str(tmp_path / "broll.mp4")
    _write_video(video, seconds=2.0, fps=10)
    _project_with_catalog(tmp_path, [
        {"clip_id": "clip_001", "path": video, "filename": "broll.mp4",
         "duration_seconds": 2.0},
        {"clip_id": "clip_002", "path": str(tmp_path / "absent.mp4"),
         "filename": "absent.mp4", "duration_seconds": 3.0},
    ])
    context = {"project_folder": str(tmp_path), "step_id": "s"}

    outcome = asset_transformations.broll_substitute(
        _need("b_roll", {"subject": "harbor at dawn"}), context)

    assert outcome["solved"] is True
    assert outcome["artifact"]["clip_id"] == "clip_001"
    assert outcome["provenance"]["derived_from"] == [video]


def test_broll_substitute_ranks_by_semantic_prose(tmp_path):
    video_a = str(tmp_path / "a.mp4")
    video_b = str(tmp_path / "b.mp4")
    _write_video(video_a, seconds=2.0, fps=10)
    _write_video(video_b, seconds=2.0, fps=10)
    _project_with_catalog(tmp_path, [
        {"clip_id": "clip_001", "path": video_a, "filename": "a.mp4",
         "duration_seconds": 2.0},
        {"clip_id": "clip_002", "path": video_b, "filename": "b.mp4",
         "duration_seconds": 2.0},
    ], semantic_docs=[
        {"file_path": video_b, "scene": "harbor at dawn"},
    ])
    context = {"project_folder": str(tmp_path), "step_id": "s"}

    outcome = asset_transformations.broll_substitute(
        _need("b_roll", {"subject": "harbor at dawn"}), context)

    assert outcome["artifact"]["clip_id"] == "clip_002"
    assert "semantic analysis" in outcome["provenance"]["detail"]


def test_broll_substitute_refuses_a_project_with_no_catalog(tmp_path):
    context = {"project_folder": str(tmp_path), "step_id": "s"}
    with pytest.raises(RenRefusal) as excinfo:
        asset_transformations.broll_substitute(
            _need("b_roll", {"subject": "harbor"}), context)
    assert "no catalog" in excinfo.value.what


def test_broll_substitute_refuses_when_no_clip_meets_constraints(tmp_path):
    video = str(tmp_path / "broll.mp4")
    _write_video(video, seconds=2.0, fps=10)
    _project_with_catalog(tmp_path, [
        {"clip_id": "clip_001", "path": video, "filename": "broll.mp4",
         "duration_seconds": 2.0},
    ])
    context = {"project_folder": str(tmp_path), "step_id": "s"}
    with pytest.raises(RenRefusal) as excinfo:
        asset_transformations.broll_substitute(
            _need("b_roll", {"subject": "harbor",
                            "exclude_clip_ids": ["clip_001"]}),
            context)
    assert "no catalog clip meets" in excinfo.value.what


def test_register_transformations_registers_every_handler():
    asset_transformations.register_transformations()
    handlers = asset_routing.registered_handlers("transformation")
    assert set(handlers) == {"room_tone_fill", "freeze_frame",
                             "broll_substitute"}


def _jl_clips_and_spine(source_b):
    """Two V1 clips and spine blocks; clip B's source is real audio."""
    clips = [
        {"source_file": "/footage/camA.mov",
         "source_in": 100.0, "source_out": 105.0,
         "timeline_in": 0.0, "timeline_out": 5.0,
         "timeline_in_frame": 0, "timeline_out_frame": 120,
         "label": "speech_1", "spine_position": 1},
        {"source_file": source_b,
         "source_in": 200.0, "source_out": 205.0,
         "timeline_in": 5.0, "timeline_out": 10.0,
         "timeline_in_frame": 120, "timeline_out_frame": 240,
         "label": "speech_2", "spine_position": 2},
    ]
    spine = [
        {"position": 1, "block_type": "speech", "clip_id": "clip_001",
         "source_start": 100.0, "source_end": 105.0,
         "timeline_start": 0.0, "timeline_end": 5.0,
         "word_timestamps": [
             {"word": "first", "source_start": 100.0,
              "source_end": 100.5},
             {"word": "last", "source_start": 103.5,
              "source_end": 104.0}],
         "alignment_method": "mfa"},
        {"position": 2, "block_type": "speech", "clip_id": "clip_002",
         "source_start": 200.0, "source_end": 205.0,
         "timeline_start": 5.0, "timeline_end": 10.0,
         "word_timestamps": [
             {"word": "next", "source_start": 201.0,
              "source_end": 201.5},
             {"word": "final", "source_start": 203.5,
              "source_end": 204.0}],
         "alignment_method": "mfa"},
    ]
    return clips, spine


def _j_cut_entry():
    return [{"kind": "j_cut", "outgoing_position": 1,
             "incoming_position": 2, "picture_cut_timeline": 5.005,
             "picture_cut_frame": 120, "audio_cut_timeline": 4.504,
             "audio_cut_frame": 108, "lead_seconds": 0.5,
             "method": "x"}]


def test_the_jl_cut_path_writes_a_routing_decision_record(tmp_path):
    """The J/L room-tone fill carries a routing decision record.

    On origin/main the fill is staged with no record of why it exists;
    routed through the transformation tier, the same fill lands in the
    project's routing ledger with its provenance.
    """
    from library.tools.jl_cut import apply_jl_cuts

    source = str(tmp_path / "camB.wav")
    _write_wav(source, 2.0, tone=0.02)
    clips, spine = _jl_clips_and_spine(source)

    def route_fn(source_file, speech_spans, fill_seconds, out_path,
                 project_folder):
        return asset_transformations.route_room_tone_fill(
            source_file, speech_spans, fill_seconds, project_folder)

    out = apply_jl_cuts(clips, _j_cut_entry(), spine, 23.976,
                        None, None, str(tmp_path), route_fn=route_fn,
                        project_folder=str(tmp_path))
    (fill,) = out["fills"]
    assert fill["room_tone_fill"] is True
    assert os.path.isfile(fill["source_file"])

    records = asset_routing.read_decisions(str(tmp_path))
    assert len(records) == 1
    assert records[0]["need"]["kind"] == "room_tone"
    assert records[0]["answered_by"] == "transformation"
    assert records[0]["provenance"]["producer"] == "room_tone_fill"
    assert records[0]["provenance"]["derived_from"] == [source]


def test_the_unrouted_jl_cut_path_writes_no_routing_record(tmp_path):
    """The measure/stage path stages the fill with no routing record.

    This is the origin/main behavior: the fill is produced, but no
    decision record is written, so the asset's provenance is whatever
    the caller remembers - which is the defect the routing layer exists
    to remove.
    """
    from library.tools.jl_cut import apply_jl_cuts

    source = str(tmp_path / "camB.wav")
    _write_wav(source, 2.0, tone=0.02)
    clips, spine = _jl_clips_and_spine(source)

    def measure(source_file, speech_spans):
        return {"source_file": source_file, "segment_start": 0.0,
                "segment_end": 1.0, "level_dbfs": -50.0,
                "spectrum_db": [-100.0] * 8,
                "spectrum_edges_hz": [20.0] * 9, "gaps_considered": 1,
                "method": "fake"}

    def stage(source_file, record, fill_seconds, out_path):
        os.makedirs(os.path.dirname(os.path.abspath(out_path)),
                    exist_ok=True)
        with open(out_path, "wb") as handle:
            handle.write(b"fake-wav")
        return {"path": out_path, "fill_seconds": round(fill_seconds, 3),
                "loops": 0, "level_dbfs": -50.0}

    out = apply_jl_cuts(clips, _j_cut_entry(), spine, 23.976,
                        measure, stage, str(tmp_path))
    (fill,) = out["fills"]
    assert fill["room_tone_fill"] is True
    assert os.path.isfile(fill["source_file"])
    assert asset_routing.read_decisions(str(tmp_path)) == []
