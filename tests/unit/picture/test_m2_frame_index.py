"""The M2 sample carries the SOURCE FRAME INDEX of every frame it took.

The coarse sample is the engine's map from a time to a frame of the
original, and until it carried an index a reader could only say
"somewhere near 33.2 seconds" - the ground-truth path needs "frame
997".  These name the defects:

* a sampled frame with no source frame index (the map has no frame
  numbers in it);
* a source whose rate was never measured given a SILENT index anyway;
* an index computed through a rounded second, which lands a frame
  early or late at the boundary.
"""
import json

import pytest

from library.tools import source_memory

STREAM = {"avg_frame_rate": "30/1", "r_frame_rate": "30/1",
          "time_base": "1/15360", "start_time": "0.000000",
          "nb_frames": "1440"}


@pytest.fixture
def sampled(monkeypatch, tmp_path):
    """An M2 sample of three keyframes at 0.0, 1.266667 and 2.833333."""
    source = tmp_path / "clip.MOV"
    source.write_bytes(b"not really a movie")

    monkeypatch.setattr(source_memory.footage_identity, "fingerprint",
                        lambda path: {"content_digest": "digest-1",
                                      "size_bytes": 18})
    monkeypatch.setattr(source_memory, "read_m0",
                        lambda digest, root=None: {
                            "video_streams": [STREAM],
                            "duration_seconds": 48.0})
    monkeypatch.setattr(source_memory, "read_m2",
                        lambda digest, root=None: None)

    def fake_extract(source_file, out_dir, width, use_hwaccel=True):
        times = [0.0, 1.266667, 2.833333]
        return [(f"{out_dir}/frame_{i:06d}.jpg", t)
                for i, t in enumerate(times, start=1)], True

    monkeypatch.setattr(source_memory, "extract_iframes", fake_extract)
    source_memory.build_frames(str(source), content_digest="digest-1",
                               root=tmp_path)
    index = tmp_path / "digest-1" / source_memory.SLOT_FRAMES_INDEX
    return json.loads(index.read_text(encoding="utf-8"))


def test_every_sampled_frame_carries_its_source_frame_index(sampled):
    assert [row["frame_index"] for row in sampled["frames"]] == [0, 38, 85]


def test_the_sample_carries_the_source_timebase(sampled):
    assert sampled["timebase"]["fps_num"] == 30
    assert sampled["timebase"]["fps_den"] == 1
    assert sampled["timebase"]["time_base_den"] == 15360
    assert sampled["frame_index_status"] == "measured"


def test_the_frame_index_is_computed_on_the_source_ticks(sampled):
    # 1.266667 s is frame 38 at 30 fps; a rounded second would say 37.
    row = sampled["frames"][1]
    assert row["pts"] == pytest.approx(1.266667, abs=1e-6)
    assert row["frame_index"] == 38
    assert row["t"] == 1.267


def test_a_source_with_no_measured_rate_gets_no_frame_index(        monkeypatch, tmp_path):
    source = tmp_path / "clip.MOV"
    source.write_bytes(b"not really a movie")
    monkeypatch.setattr(source_memory.footage_identity, "fingerprint",
                        lambda path: {"content_digest": "digest-2",
                                      "size_bytes": 18})
    monkeypatch.setattr(source_memory, "read_m0",
                        lambda digest, root=None: {"video_streams": [{}],
                                                   "duration_seconds": 1.0})
    monkeypatch.setattr(source_memory, "read_m2",
                        lambda digest, root=None: None)
    monkeypatch.setattr(
        source_memory, "extract_iframes",
        lambda source_file, out_dir, width, use_hwaccel=True: (
            [(f"{out_dir}/frame_000001.jpg", 0.0)], True))
    source_memory.build_frames(str(source), content_digest="digest-2",
                               root=tmp_path)
    index = tmp_path / "digest-2" / source_memory.SLOT_FRAMES_INDEX
    doc = json.loads(index.read_text(encoding="utf-8"))
    assert doc["timebase"] is None
    assert doc["frame_index_status"].startswith("unavailable:")
    assert "frame_index" not in doc["frames"][0]


def test_the_m3_record_carries_the_frame_index_it_measured():
    # The chain: the M2 sample knows the source frame, and the M3
    # measurement of that frame says which frame it measured.
    from library.tools import person_measurements
    doc = {"faces": [{"box": [0.1, 0.1, 0.2, 0.2], "confidence": 0.9}],
           "hands": [], "landmarks": [], "face_quality": []}
    record = person_measurements.frame_record(doc, [True], 1.267, 38)
    assert record["frame_index"] == 38
    assert record["t"] == 1.267
    assert len(record["faces"]) == 1


def test_an_m3_record_without_a_frame_index_says_none():
    from library.tools import person_measurements
    doc = {"faces": [{"box": [0.1, 0.1, 0.2, 0.2], "confidence": 0.9}],
           "hands": [], "landmarks": [], "face_quality": []}
    record = person_measurements.frame_record(doc, [True], 1.267)
    assert record["frame_index"] is None
