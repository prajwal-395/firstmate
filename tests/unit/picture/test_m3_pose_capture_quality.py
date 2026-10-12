"""M3 keeps head pose and face capture quality, and person queries order by it.

The defect each test names: the Vision helper already ran the
face-rectangles request (which returns yaw/pitch/roll) but never
serialized the pose, and never ran the capture-quality request at all -
so M3 faces carried no `pose` or `capture_quality`, M7 spans no median,
and a person query came back in time order with the best face buried.
On main every test here fails (the fields do not exist); on the branch
each passes.
"""
import json
import sys
from pathlib import Path

import pytest

from library.tools import event_spans, person_measurements
from library.tools import source_memory

FIXTURE_FACE = (Path(__file__).resolve().parents[2]
                / "fixtures" / "mu03" / "turned-face.jpg")


def _face(box=None, quality=None, pose=None):
    face = {"box": box or [0.4, 0.3, 0.6, 0.7]}
    if pose is not None:
        face["pose"] = pose
    if quality is not None:
        face["capture_quality"] = quality
    return face


def _helper_doc(face_boxes, quality_boxes):
    """A helper-shaped doc: faces with pose, plus standalone quality
    observations to pair by overlap."""
    return {
        "faces": [{"box": box, "confidence": 0.9,
                    "pose": {"yaw": 0.25, "pitch": 0.21, "roll": 0.01}}
                   for box in face_boxes],
        "landmarks": [],
        "face_quality": [{"box": box, "capture_quality": 0.6}
                         for box in quality_boxes],
        "hands": [],
    }


# ── M3 keeps pose and capture quality ───────────────────────────────


def test_frame_record_keeps_pose_and_capture_quality():
    """A helper face with pose and a paired quality observation lands in
    M3 with both - dropping either would unread the requests MU-03 paid
    ~17-27 ms/frame to run."""
    doc = _helper_doc([[0.4, 0.3, 0.6, 0.7]], [[0.4, 0.3, 0.6, 0.7]])
    record = person_measurements.frame_record(doc, [True], 0.5)
    face = record["faces"][0]
    assert face["pose"] == {"yaw": 0.25, "pitch": 0.21, "roll": 0.01}
    assert face["capture_quality"] == 0.6


def test_frame_record_pairs_quality_by_overlap_not_order():
    """The quality request observes independently, so its observations
    pair to faces by box overlap - a quality box over the wrong face
    must not attach to the first one."""
    doc = _helper_doc([[0.0, 0.0, 0.2, 0.2], [0.4, 0.3, 0.6, 0.7]],
                      [[0.7, 0.7, 0.9, 0.9]])
    record = person_measurements.frame_record(doc, [True, True], 0.5)
    assert record["faces"][0]["capture_quality"] is None
    assert record["faces"][1]["capture_quality"] is None


def test_frame_record_without_new_keys_reads_as_unmeasured():
    """Pre-v2 helper output carries no pose or quality list - it reads
    as all-None rather than failing, because the slot version (not this
    reader) is what retires those records."""
    record = person_measurements.frame_record(
        {"faces": [{"box": [0.4, 0.3, 0.6, 0.7], "confidence": 0.9}],
         "hands": []}, [True], 0.0)
    face = record["faces"][0]
    assert face["pose"] == {"yaw": None, "pitch": None, "roll": None}
    assert face["capture_quality"] is None


# ── M7 exposes per-span median capture quality ──────────────────────


def _m3_with_runs():
    """Two on-screen runs of one track: an early low-quality run and a
    later high-quality one, split by an absent gap the span bridge
    cannot cross."""
    frames, assignment = [], []
    for i in range(14):
        if 4 <= i < 10:
            frames.append({"t": i * 0.5, "faces": [], "hands": []})
            assignment.append([])
        else:
            quality = 0.2 if i < 4 else 0.9
            frames.append({"t": i * 0.5,
                           "faces": [_face(quality=quality)], "hands": []})
            assignment.append(["t1"])
    return {"frames": frames}, assignment


def test_on_screen_spans_carry_median_capture_quality():
    """Each on-screen span reports the median quality of its own faces -
    without it the query has nothing to order by."""
    m3, assignment = _m3_with_runs()
    spans = event_spans.on_screen_spans(m3, assignment)
    assert len(spans) == 2
    assert spans[0]["capture_quality"] == 0.2
    assert spans[1]["capture_quality"] == 0.9


def test_speaking_spans_carry_median_capture_quality():
    """A voice turn linked to a face reports that face's median quality
    inside the turn; a turn with no linked face reports None rather
    than borrowing a stranger's."""
    m3, _ = _m3_with_runs()
    assignment = [["t1"]] * 4 + [[]] * 6 + [["t1"]] * 4
    identity = {"voices": [{"track_id": "v1", "spans": [[0.0, 2.0]]},
                           {"track_id": "v2", "spans": [[5.0, 7.0]]}]}
    links = [{"voice_track": "v1", "face_track": "t1", "evidence": []},
             {"voice_track": "v2", "face_track": None, "evidence": []}]
    spans = event_spans.speaking_spans(identity, links, m3, assignment)
    assert spans[0]["capture_quality"] == 0.2
    assert spans[1]["capture_quality"] is None


# ── the query orders by quality ─────────────────────────────────────


def _write_m7(tmp_path):
    """A real M7 built from fixture M3 + M3b under a scratch memory
    root: the early span looks worse than the later one."""
    from library.tools import person_entity

    digest = "digest-mu03"
    m3, _ = _m3_with_runs()
    m3.update({
        "content_digest": digest,
        "size_bytes": 10,
        "source_file": "/media/mu03.MOV",
        "status": person_measurements.M3_STATUS_MEASURED,
        "coordinates": "image-normalised, top-left origin",
        "frame_count": len(m3["frames"]),
        "instrument": {"method": person_measurements.M3_METHOD,
                       "frame_pixels": [384, 216],
                       "persistence_iou": 0.3},
    })
    pixels = [384, 216]
    boxes = [[int(0.4 * pixels[0]), int(0.3 * pixels[1]),
              int(0.6 * pixels[0]), int(0.7 * pixels[1])]]
    identity = {
        "content_digest": digest,
        "instrument": {"frame_source": person_entity.FRAME_SOURCE_M2_TIMES,
                       "frame_pixels": pixels},
        "faces": [{"track_id": "t1",
                   "spans": [{"box": boxes[0], "start": 0.0, "end": 2.0},
                             {"box": boxes[0], "start": 5.0, "end": 7.0}]}],
        "voices": [],
    }
    root = tmp_path / "memory"
    for slot, doc in ((source_memory.SLOT_PERSONS, m3),
                      (source_memory.SLOT_IDENTITY, identity)):
        path = source_memory.source_dir(digest, root) / slot
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(doc), encoding="utf-8")
    report = event_spans.build_source_events(digest, "/media/mu03.MOV",
                                             root=root)
    assert report["on_screen"] == 2
    return root, digest


def test_person_query_returns_higher_quality_span_first(tmp_path, monkeypatch):
    """On a fixture M3 whose early span looks worse, a person query
    answers the better face first - in time order (main) the worse
    span leads."""
    root, digest = _write_m7(tmp_path)
    person = {"person_id": "person_001",
              "face_tracks": [{"content_digest": digest, "track_id": "t1"}]}
    monkeypatch.setattr(event_spans, "resolve_person",
                        lambda *a, **k: (person, {"by": "person_id"}))
    answer = event_spans.query(str(tmp_path), "person_001", "on_screen",
                               root=root)
    qualities = [hit["capture_quality"] for hit in answer["hits"]]
    assert qualities == [0.9, 0.2]
    assert [hit["start"] for hit in answer["hits"]] == [4.75, 0.0]


# ── stale M3 rebuilds ───────────────────────────────────────────────


def test_persons_lane_refuses_a_pre_v2_m3(tmp_path):
    """A v1-stamped M3 (pose/quality absent) reads stale so `ren
    analyze` rebuilds it; without the version check the lane would
    serve faceless quality as absent forever."""
    from library.tools import footage_analysis

    lane = next(lane for lane in footage_analysis.LANES
                if lane.name == "persons")
    digest = "digest-mu03"
    sdir = source_memory.source_dir(digest, tmp_path)
    sdir.mkdir(parents=True, exist_ok=True)
    m2 = {"content_digest": digest}
    (sdir / source_memory.SLOT_FRAMES_INDEX).write_text(json.dumps(m2),
                                                        encoding="utf-8")
    persons = {"content_digest": digest,
               "instrument": {"method": "vision-helper-v1 over M2"}}
    (sdir / source_memory.SLOT_PERSONS).write_text(json.dumps(persons),
                                                   encoding="utf-8")
    assert footage_analysis.slot_is_fresh(lane, digest, tmp_path) is False
    persons["instrument"]["method"] = person_measurements.M3_METHOD
    (sdir / source_memory.SLOT_PERSONS).write_text(json.dumps(persons),
                                                   encoding="utf-8")
    assert footage_analysis.slot_is_fresh(lane, digest, tmp_path) is True


# ── the helper measures both, mirrored yaw flips ────────────────────


def test_helper_reports_pose_and_quality_with_mirrored_yaw(tmp_path):
    """The live helper on a turned-face fixture and its mirror: yaw has
    opposite signs (mirroring flips the head's turn), and every face
    carries a capture quality. Skipped where the helper cannot build
    (non-macOS); on main the keys do not exist, so it fails."""
    from library.steps.step_1_04_temporal_index import vision_measure

    helper, reason = vision_measure.ensure_helper()
    if helper is None:
        pytest.skip(f"Vision helper unavailable: {reason}")
    try:
        from PIL import Image
    except ImportError:
        pytest.skip("PIL unavailable to mirror the fixture")
    mirrored = tmp_path / "mirrored.jpg"
    with Image.open(FIXTURE_FACE) as image:
        image.transpose(Image.FLIP_LEFT_RIGHT).save(mirrored)
    docs = vision_measure.measure_frames(
        [str(FIXTURE_FACE), str(mirrored)], helper)
    yaws = []
    for doc in docs:
        assert doc["faces"], "fixture face went undetected"
        pose = doc["faces"][0]["pose"]
        yaws.append(pose["yaw"])
        qualities = [entry["capture_quality"]
                     for entry in doc["face_quality"]]
        assert qualities, "capture-quality request returned no faces"
        assert all(isinstance(q, (int, float)) for q in qualities)
    assert yaws[0] is not None and yaws[1] is not None
    assert (yaws[0] > 0) != (yaws[1] > 0), f"yaw did not mirror: {yaws}"
    assert sys.platform == "darwin"
