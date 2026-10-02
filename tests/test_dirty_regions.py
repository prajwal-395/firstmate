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
