"""VI-007: an object keeps ONE identity across cuts and angles.

Every test here names a defect the identity lane fixes, and each one
can fail: the assertions are about behaviour, not about the code
happening to run.

The defects, in the order the lane meets them:

- a per-frame detector mints a fresh identity per answer, so a
  reference to one object resolves to nothing (identity switches);
- a detection that two tracks explain about equally well is merged
  into one of them, which is a forced match;
- two objects of the same class in different clips are merged because
  their class agrees;
- a link is made on a candidate that does not beat the runner-up;
- a person's identity is taken from an object appearance descriptor;
- a detection that runs out of frame is reported as a whole object;
- an object that is not in an inspected frame is left in silence.
"""

import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools.analysis.object_identity import (
    ABSENT,
    ABSTAINED,
    LINKED,
    OBSERVED,
    PARTIAL,
    VISIBLE,
    Frame,
    ObservedDetection,
    associate_tracks,
    link_tracks,
    per_frame_observations,
    resolve,
)


def _frame(frame_id, clip_id, time_s, width=1000, height=1000):
    return Frame(frame_id=frame_id, clip_id=clip_id, time_s=time_s,
                 image=f"/nonexistent/{frame_id}.jpg", width=width,
                 height=height, scope="test-scope")


def _detection(frame, phrase, box, score=0.9):
    return ObservedDetection(clip_id=frame.clip_id, frame_id=frame.frame_id,
                             time_s=frame.time_s, phrase=phrase, box=box,
                             score=score)


def test_a_track_keeps_one_id_across_the_frames_of_a_clip():
    """The defect: every answer carried its own id, so the same object
    was a different object in every frame."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C1", 10.0),
              _frame("f3", "C1", 20.0)]
    detections = [
        _detection(frames[0], "laptop", (0.10, 0.10, 0.40, 0.40)),
        _detection(frames[1], "laptop", (0.11, 0.11, 0.41, 0.41)),
        _detection(frames[2], "laptop", (0.12, 0.12, 0.42, 0.42)),
    ]
    tracks = associate_tracks(frames, detections)
    assert len(tracks) == 1, "the same object in three frames is one track"
    assert [row["frame_id"] for row in tracks[0].observations] == [
        "f1", "f2", "f3"]


def test_an_ambiguous_association_refuses_to_merge():
    """The defect: a detection two tracks explain about equally well was
    merged into one of them. The lane refuses and says so."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C1", 10.0)]
    detections = [
        _detection(frames[0], "cup", (0.10, 0.10, 0.30, 0.30)),
        _detection(frames[0], "cup", (0.70, 0.10, 0.90, 0.30)),
        # Sits between the two, and explains each about equally well.
        _detection(frames[1], "cup", (0.39, 0.10, 0.61, 0.30)),
    ]
    tracks = associate_tracks(frames, detections)
    ambiguous = [track for track in tracks if track.started_ambiguous]
    assert ambiguous, "an ambiguous detection must not be merged silently"
    assert len(tracks) == 3, "the ambiguous detection starts its own track"


def test_two_objects_of_the_same_class_in_different_clips_are_not_merged():
    """The defect: two things of one class were merged because their
    class agreed. The lane links on evidence, and the studio's blue
    tablet and its silver laptop are two objects."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C2", 0.0)]
    tracks = [
        associate_tracks([frames[0]],
                         [_detection(frames[0], "laptop",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
        associate_tracks([frames[1]],
                         [_detection(frames[1], "laptop",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
    ]
    tracks[0].entity_id, tracks[1].entity_id = "tablet", "laptop"
    descriptors = {"tablet": (1.0, 0.0), "laptop": (0.0, 1.0)}
    identity, decisions = link_tracks(tracks, descriptors,
                                      scopes={"C1": "s", "C2": "s"})
    assert all(decision.status == ABSTAINED for decision in decisions)
    assert identity["tablet"] != identity["laptop"], (
        "two objects of one class must keep two identities")
    assert any("appearance below" in decision.reason
               for decision in decisions)


def test_a_link_needs_the_best_candidate_to_beat_the_runner_up():
    """The defect: a link was made on a candidate that did not beat the
    runner-up. Two candidates that agree about as well are ambiguous,
    and the lane abstains."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C2", 0.0)]
    tracks = [
        associate_tracks([frames[0]],
                         [_detection(frames[0], "laptop",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
        associate_tracks([frames[1]],
                         [_detection(frames[1], "laptop",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
    ]
    # The two candidates score the same: nothing says which one it is.
    tracks[0].entity_id, tracks[1].entity_id = "first", "second"
    descriptors = {"first": (1.0, 0.0), "second": (0.0, 1.0)}
    third = associate_tracks([_frame("f3", "C3", 0.0)],
                             [_detection(_frame("f3", "C3", 0.0), "laptop",
                                         (0.10, 0.10, 0.40, 0.40))])[0]
    third.entity_id = "third"
    tracks.append(third)
    _, decisions = link_tracks(tracks, descriptors,
                               scopes={"C1": "s", "C2": "s", "C3": "s"},
                               threshold=0.5, margin=0.2)
    statuses = {decision.entity_id: decision.status for decision in decisions}
    assert LINKED not in statuses.values(), (
        "two candidates that agree about as well must not produce a link")


def test_a_person_is_not_linked_across_clips_by_an_object_descriptor():
    """The defect: a person's identity was taken from an object
    appearance descriptor. That identity belongs to the face and voice
    lanes, and the lane says so instead of guessing."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C2", 0.0)]
    tracks = [
        associate_tracks([frames[0]],
                         [_detection(frames[0], "person",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
        associate_tracks([frames[1]],
                         [_detection(frames[1], "person",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
    ]
    tracks[0].entity_id, tracks[1].entity_id = "akshita", "craig"
    descriptors = {"akshita": (1.0, 0.0), "craig": (1.0, 0.0)}
    identity, decisions = link_tracks(tracks, descriptors,
                                      scopes={"C1": "s", "C2": "s"})
    assert all(decision.status == ABSTAINED for decision in decisions)
    assert all("face and voice lanes" in decision.reason
               for decision in decisions)
    assert identity["akshita"] != identity["craig"]


def test_a_detection_that_runs_out_of_frame_is_reported_as_partial():
    """The defect: a box that does not bound the object was reported as
    a whole object, with no state saying so."""
    frames = [_frame("f1", "C1", 0.0)]
    detections = [_detection(frames[0], "laptop", (0.80, 0.60, 1.0, 1.0))]
    resolution = resolve(frames, detections)
    states = {row["state"] for row in resolution.observations
              if row["status"] == OBSERVED}
    assert states == {PARTIAL}, "a box that runs out of frame is partial"
    assert all(row["state"] != VISIBLE for row in resolution.observations)


def test_an_object_that_is_not_in_an_inspected_frame_is_reported_absent():
    """The defect: an object that had gone was left in silence. The lane
    says ABSENT where it looked and found nothing."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C1", 10.0),
              _frame("f3", "C1", 20.0)]
    detections = [
        _detection(frames[0], "laptop", (0.10, 0.10, 0.40, 0.40)),
        # f2 carries no laptop at all: the object went, and the lane
        # looked.
        _detection(frames[2], "laptop", (0.11, 0.11, 0.41, 0.41)),
    ]
    resolution = resolve(frames, detections)
    rows = {(row["frame_id"], row["entity_id"]): row
            for row in resolution.observations}
    assert any(row["status"] == "abstained" and row["state"] == ABSENT
               for row in rows.values()), (
        "a frame the track covers but the object is missing is absent")


def test_the_per_frame_mode_mints_no_identity_at_all():
    """The incumbent's shape of answer, kept as the before/after's
    baseline: every detection on its own, and no cross-clip claim."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C1", 10.0)]
    detections = [
        _detection(frames[0], "laptop", (0.10, 0.10, 0.40, 0.40)),
        _detection(frames[1], "laptop", (0.11, 0.11, 0.41, 0.41)),
    ]
    rows = per_frame_observations(frames, detections)
    assert len({row["entity_id"] for row in rows}) == 2, (
        "the per-frame mode must not carry an identity across frames")
    assert all(row["cross_clip_identity"] == ABSTAINED for row in rows)


def test_a_link_inside_one_scope_never_crosses_a_scope_boundary():
    """Identities are project-local: a link never crosses a scope."""
    frames = [_frame("f1", "C1", 0.0), _frame("f2", "C2", 0.0)]
    tracks = [
        associate_tracks([frames[0]],
                         [_detection(frames[0], "laptop",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
        associate_tracks([frames[1]],
                         [_detection(frames[1], "laptop",
                                     (0.10, 0.10, 0.40, 0.40))])[0],
    ]
    tracks[0].entity_id, tracks[1].entity_id = "here", "there"
    descriptors = {"here": (1.0, 0.0), "there": (1.0, 0.0)}
    identity, decisions = link_tracks(tracks, descriptors,
                                      scopes={"C1": "scope-a",
                                              "C2": "scope-b"})
    assert all(decision.status == ABSTAINED for decision in decisions)
    assert identity["here"] != identity["there"]


def test_the_module_refuses_a_manifest_it_does_not_understand():
    import json
    import tempfile
    from pathlib import Path

    from library.tools.analysis.object_identity import load_manifest

    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.json"
        path.write_text(json.dumps({"schema": "something-else"}),
                        encoding="utf-8")
        with pytest.raises(ValueError):
            load_manifest(path)


def test_the_request_path_answers_with_the_identity_of_what_it_saw():
    """The defect: the request path answered a request for an object
    with a pile of boxes, so a reference to the object resolved to
    nothing. The answer now carries the identity."""
    from pathlib import Path

    from library.tools.analysis import object_grounding

    detections = [
        {"phrase": "laptop", "label": "laptop", "box": [0.1, 0.1, 0.4, 0.4],
         "score": 0.9, "image": "000001.jpg"},
        {"phrase": "laptop", "label": "laptop", "box": [0.11, 0.11, 0.41, 0.41],
         "score": 0.9, "image": "000002.jpg"},
        {"phrase": "person", "label": "person", "box": [0.6, 0.1, 0.9, 0.9],
         "score": 0.9, "image": "000002.jpg"},
    ]
    rows = object_grounding.resolve_window_identities(
        detections, images=[Path("000001.jpg"), Path("000002.jpg")],
        indices=[0, 30], geometry={"fps": 30.0, "width": 1920, "height": 1080})
    laptop = [row for row in rows if row["class"] == "laptop"]
    person = [row for row in rows if row["class"] == "person"]
    assert len({row["entity_id"] for row in laptop}) == 1, (
        "the same object in two frames of a window is one identity")
    assert person[0]["entity_id"] != laptop[0]["entity_id"], (
        "two objects of one window are two identities")
    assert all(row.get("state") for row in rows), (
        "every answer carries the visibility state of its object")
