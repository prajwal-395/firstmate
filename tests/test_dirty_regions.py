"""A touch's dirty block, and the scoped render QA that reads it.

Two defects this pins: a span stated wrong for an op (a move that dirties
only one end re-checks the wrong seconds), and a scoped read whose findings
differ from the whole-file read over the same frames - the scoped verdict
must be the whole verdict, restricted.
"""

import json
import shutil
import subprocess

import pytest

from library.tools import dirty_regions as d
from library.tools import render_qa as q

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


def test_a_move_dirties_where_it_left_and_where_it_landed():
    dirty = _dirty({"op": "move", "row": "V4", "item": 0,
                    "to_record": F0 + 360})
    assert _cores(dirty) == [("picture", 8.2, 9.0), ("picture", 12.0, 12.8)]


def test_a_retime_dirties_from_the_cut_to_the_end_on_both_domains():
    dirty = _dirty({"op": "retime", "row": "V4", "item": 1,
                    "duration": 45})
    # 15f grown to 45f: the reel ends a second later than it did.
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
