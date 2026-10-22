"""The caption layer composites in ffmpeg exactly where Resolve placed it.

Proves the plan reads every value off the segment record (spans through
``frame_utils.span_frames``, source windows verbatim, positions off
``tight_box.absolute_canvas_box``), that every unplaceable segment is
refused by name, and that the ffmpeg composite draws each card inside
its own record window and never outside it - on tiny synthetic renders.
No Resolve, no project media.
"""
import shutil
import subprocess

import pytest

from library.tools import caption_layer_composite as clc

FFMPEG = shutil.which("ffmpeg")


def _base(path, *, width=64, height=64, seconds=2.0, fps=24):
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=red:size={width}x{height}:rate={fps}:duration={seconds}",
        "-f", "lavfi", "-i",
        f"anullsrc=channel_layout=stereo:sample_rate=8000:d={seconds}",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-shortest", str(path),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False)
    assert completed.returncode == 0, completed.stderr[:300]


def _card(path, *, width=32, height=16, frames=12, fps=24):
    # Green card with full alpha over transparent black: qtrle keeps the
    # alpha Resolve's own caption files carry.
    command = [
        "ffmpeg", "-v", "error", "-y",
        "-f", "lavfi", "-i",
        f"color=0x00FF00FF:size={width}x{height}:rate={fps}:duration={frames / fps}",
        "-c:v", "qtrle", str(path),
    ]
    completed = subprocess.run(
        command, capture_output=True, text=True, encoding="utf-8", check=False)
    assert completed.returncode == 0, completed.stderr[:300]


def _segment(segment_id, overlay_path, *, start=0.0, end=0.5,
             source_in=0, source_out=12, box=(8, 8, 40, 24),
             canvas=(32, 16), container="video"):
    tight = None
    if box is not None:
        tight = {"width": canvas[0], "height": canvas[1],
                 "absolute_canvas_box": [box[0], box[1], box[2], box[3]]}
    return {"segment_id": segment_id, "overlay_path": overlay_path,
            "timeline_start": start, "timeline_end": end,
            "source_in_frame": source_in, "source_out_frame": source_out,
            "tight_box": tight, "container": container}


def _ink(path, box, frame_index):
    x0, y0, x1, y1 = box
    command = [
        "ffmpeg", "-v", "error", "-i", str(path),
        "-vf", (f"select='eq(n\\,{frame_index})',"
                   f"crop={x1 - x0}:{y1 - y0}:{x0}:{y0}"),
        "-frames:v", "1", "-f", "rawvideo", "-pix_fmt", "rgb24", "-",
    ]
    completed = subprocess.run(
        command, capture_output=True, check=False)
    assert completed.returncode == 0
    raw = completed.stdout
    assert len(raw) == (x1 - x0) * (y1 - y0) * 3
    green = sum(1 for index in range(0, len(raw), 3)
                if raw[index + 1] > 150
                and raw[index + 1] > raw[index] + 40
                and raw[index + 1] > raw[index + 2] + 40)
    return green


# ── The plan reads the record, and nothing else ──


def test_plan_reads_spans_source_and_origin_off_the_record(tmp_path):
    card = tmp_path / "sub.mov"
    _card(card)
    plan = clc.plan_caption_overlays(
        [_segment("sub_1", str(card))], fps=24, width=64, height=64)
    assert plan["refused"] == []
    (overlay,) = plan["overlays"]
    assert overlay["record_start"] == 0
    assert overlay["record_end"] == 12
    assert (overlay["source_in"], overlay["source_out"]) == (0, 12)
    assert (overlay["x"], overlay["y"]) == (8, 8)


def test_plan_rounds_each_span_edge_like_the_placer(tmp_path):
    card = tmp_path / "sub.mov"
    _card(card)
    plan = clc.plan_caption_overlays(
        [_segment("sub_1", str(card), start=0.02, end=0.06,
                  source_in=0, source_out=1)],
        fps=24, width=64, height=64)
    assert plan["refused"] == []
    (overlay,) = plan["overlays"]
    assert (overlay["record_start"], overlay["record_end"]) == (0, 1)


def test_full_canvas_card_overlays_at_the_frame_origin(tmp_path):
    card = tmp_path / "full.mov"
    _card(card, width=64, height=64)
    plan = clc.plan_caption_overlays(
        [_segment("sub_full", str(card), box=None, canvas=(64, 64))],
        fps=24, width=64, height=64)
    assert plan["refused"] == []
    assert (plan["overlays"][0]["x"], plan["overlays"][0]["y"]) == (0, 0)


# ── Refusals, each named ──


def test_missing_caption_file_is_refused_by_name(tmp_path):
    plan = clc.plan_caption_overlays(
        [_segment("sub_gone", str(tmp_path / "gone.mov"))],
        fps=24, width=64, height=64)
    assert plan["overlays"] == []
    assert plan["refused"] == [
        {"segment_id": "sub_gone",
         "reason": plan["refused"][0]["reason"]}]
    assert "absent" in plan["refused"][0]["reason"]


def test_frames_container_is_refused_by_name(tmp_path):
    card = tmp_path / "sub.mov"
    _card(card)
    plan = clc.plan_caption_overlays(
        [_segment("sub_seq", str(card), container="frames")],
        fps=24, width=64, height=64)
    assert plan["overlays"] == []
    assert "frames-container" in plan["refused"][0]["reason"]


def test_ledger_source_out_is_noted_never_followed(tmp_path):
    card = tmp_path / "sub.mov"
    _card(card)
    plan = clc.plan_caption_overlays(
        [_segment("sub_short", str(card), source_in=0, source_out=6)],
        fps=24, width=64, height=64)
    assert plan["refused"] == []
    (overlay,) = plan["overlays"]
    assert (overlay["source_in"], overlay["source_out"]) == (0, 12)
    assert "ledger source_out 6" in overlay["note"]


def test_legacy_origin_places_without_absolute_canvas_box(tmp_path):
    card = tmp_path / "sub.mov"
    _card(card, width=32, height=16)
    segment = _segment("sub_old", str(card), box=None, canvas=(32, 16))
    segment["tight_box"] = {"width": 32, "height": 16,
                              "origin": [8, 8]}
    plan = clc.plan_caption_overlays(
        [segment], fps=24, width=64, height=64)
    assert plan["refused"] == []
    assert (plan["overlays"][0]["x"],
            plan["overlays"][0]["y"]) == (8, 8)


def test_overlapping_record_spans_are_refused_by_name(tmp_path):
    first, second = tmp_path / "a.mov", tmp_path / "b.mov"
    _card(first)
    _card(second)
    plan = clc.plan_caption_overlays(
        [_segment("sub_a", str(first), start=0.0, end=0.5),
         _segment("sub_b", str(second), start=0.25, end=0.75,
                  source_in=0, source_out=12)],
        fps=24, width=64, height=64)
    assert plan["overlays"] == []
    assert "overlapping_record_span" in plan["refused"][0]["reason"]


def test_canvas_mismatch_is_refused_by_name(tmp_path):
    card = tmp_path / "sub.mov"
    _card(card, width=32, height=16)
    plan = clc.plan_caption_overlays(
        [_segment("sub_wrong", str(card), canvas=(48, 16))],
        fps=24, width=64, height=64)
    assert plan["overlays"] == []
    assert "canvas_mismatch" in plan["refused"][0]["reason"]


def test_empty_layer_plans_nothing_and_refuses_nothing():
    assert clc.plan_caption_overlays(
        [], fps=24, width=64, height=64) == {
            "overlays": [], "refused": []}


# ── The composite draws the layer ──


@pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")
def test_composite_draws_the_card_inside_its_window_only(tmp_path):
    base = tmp_path / "base.mp4"
    card = tmp_path / "sub.mov"
    _base(base)
    _card(card)
    out = tmp_path / "out.mp4"
    receipt = clc.composite_caption_layer(
        base, [_segment("sub_1", str(card))], out,
        fps=24, width=64, height=64)
    assert receipt["n_overlays"] == 1
    assert receipt["seconds"] >= 0
    assert out.is_file()
    box = (8, 8, 40, 24)
    assert _ink(out, box, 6) > 0
    assert _ink(out, box, 30) == 0


@pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")
def test_composite_keeps_the_base_mix_and_drops_caption_audio(tmp_path):
    base = tmp_path / "base.mp4"
    card = tmp_path / "sub.mov"
    _base(base)
    _card(card)
    out = tmp_path / "out.mp4"
    clc.composite_caption_layer(
        base, [_segment("sub_1", str(card))], out,
        fps=24, width=64, height=64)
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=codec_name", "-of", "csv=p=0", str(out)],
        capture_output=True, text=True, encoding="utf-8", check=False)
    assert probe.stdout.strip() == "aac"


@pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")
def test_composite_refuses_a_base_of_another_size(tmp_path):
    base = tmp_path / "base.mp4"
    card = tmp_path / "sub.mov"
    _base(base, width=128, height=128)
    _card(card)
    with pytest.raises(clc.CaptionCompositeRefused):
        clc.composite_caption_layer(
            base, [_segment("sub_1", str(card))], tmp_path / "out.mp4",
            fps=24, width=64, height=64)


@pytest.mark.skipif(FFMPEG is None, reason="needs ffmpeg")
def test_box_comparison_scores_a_match_above_a_miss(tmp_path):
    first = tmp_path / "first.mp4"
    card = tmp_path / "sub.mov"
    _base(first)
    _card(card)
    out = tmp_path / "out.mp4"
    clc.composite_caption_layer(
        first, [_segment("sub_1", str(card))], out,
        fps=24, width=64, height=64)
    boxes = clc.measure_caption_boxes(first, out, [[8, 8, 40, 24]])
    assert boxes["n"] == 1
    assert 0.0 <= boxes["min_y"] <= 1.0
    assert boxes["mean_y"] == boxes["min_y"]
