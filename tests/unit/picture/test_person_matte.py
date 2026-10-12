"""Delivery-tier person mattes (`library/tools/person_matte.py`, MU-10).

The defects these name: a matte held at 2 fps lags a moving subject;
Vision's per-frame instance index swaps, so "instance 0" is not a
person; a plan that names one person must never be drawn as another;
and a clip with no person instance keeps the SAM 2 route rather than
refusing.
"""

import os

import numpy as np
import pytest

from library.tools import behind_subject, person_matte, timeline_layout
from library.tools.person_matte import PersonMatteUnavailable


def _box_mask(x0, x1, y0=4, y1=20, size=(24, 64)):
    mask = np.zeros(size, dtype=bool)
    mask[y0:y1, x0:x1] = True
    return mask


def _walker_frames(n=12, step=2):
    """A person walking right past a second person who stands still.
    Vision's instance order flips every other frame, as it does."""
    frames = []
    for f in range(n):
        walker = _box_mask(4 + step * f, 14 + step * f)
        stander = _box_mask(50, 60)
        frames.append([walker, stander] if f % 2 else [stander, walker])
    return frames


def _face(x0, x1, y0=4, y1=8, size=(24, 64)):
    h, w = size
    return [x0 / w, y0 / h, x1 / w, y1 / h]


# ── Picking the person ──────────────────────────────────────────────


def test_the_walker_is_followed_through_index_swaps():
    frames = _walker_frames()
    choice, account = person_matte.select_person(
        frames, {0: _face(6, 12)})
    assert account["anchor_frames"] == [0]
    assert account["frames_without_person"] == 0
    for f, pick in enumerate(choice):
        # The walker's box, never the stander's, whatever its index.
        assert frames[f][pick][10, 4 + 2 * f + 5]
        assert not frames[f][pick][10, 55]


def test_a_later_anchor_reaches_frames_before_it():
    frames = _walker_frames()
    choice, _ = person_matte.select_person(frames, {11: _face(28, 34)})
    assert frames[0][choice[0]][10, 9]


def test_an_anchor_face_in_no_instance_resolves_nothing():
    frames = _walker_frames()
    choice, account = person_matte.select_person(
        frames, {0: _face(30, 34, y0=21, y1=23)})
    assert choice == [None] * len(frames)
    assert account["unresolved_anchors"] == [
        {"frame": 0, "instances_holding_face": 0}]


def test_a_cut_away_from_the_person_is_absent_not_guessed():
    frames = _walker_frames(n=6)
    frames[3] = []  # nobody on this frame
    frames[4] = [_box_mask(50, 60)]  # only the other person
    frames[5] = [_box_mask(50, 60)]
    choice, account = person_matte.select_person(frames, {0: _face(6, 12)})
    assert choice[3:] == [None, None, None]
    assert account["frames_without_person"] == 3


# ── Which region track is the person ────────────────────────────────


def _doc(*tracks):
    return {"tracks": [dict(t) for t in tracks], "samples": []}


OWNED = {"track_id": "region_001", "start": 0.0, "end": 10.0,
         "face_track": "face_01"}
OTHER = {"track_id": "region_002", "start": 0.0, "end": 10.0,
         "face_track": "face_02"}
HAND = {"track_id": "region_003", "start": 0.0, "end": 10.0,
        "face_track": None, "unassigned": "no_face_inside"}


def test_the_single_owned_track_is_the_subject():
    assert person_matte.subject_track(
        _doc(OWNED, HAND), 1.0, 2.0)["track_id"] == "region_001"


@pytest.mark.parametrize("doc, kwargs, reason", [
    (None, {}, "no_region_record"),
    (_doc(HAND), {}, "no_person_region"),
    (_doc(OWNED, OTHER), {}, "several_person_regions"),
    (_doc(OWNED), {"region_track": "region_009"}, "region_track_unknown"),
    (_doc(OWNED, HAND), {"region_track": "region_003"},
     "region_track_unowned"),
])
def test_what_is_not_a_person_says_why(doc, kwargs, reason):
    with pytest.raises(PersonMatteUnavailable) as exc:
        person_matte.subject_track(doc, 1.0, 2.0, **kwargs)
    assert exc.value.reason == reason


def test_a_named_track_wins_over_the_other_person():
    assert person_matte.subject_track(
        _doc(OWNED, OTHER), 1.0, 2.0,
        region_track="region_002")["track_id"] == "region_002"


def test_face_anchors_reach_just_outside_a_short_window():
    doc = {"samples": [
        {"t": t, "instances": [{"track": "region_001", "face": 0,
                                "face_track": "face_01"}]}
        for t in (0.0, 1.5, 3.0)]}
    m3 = {"frames": [{"t": t, "faces": [{"box": [0.1, 0.1, 0.2, 0.2]}]}
                     for t in (0.0, 1.5, 3.0)]}
    anchors = person_matte.face_anchors(doc, m3, OWNED, 1.6, 2.0)
    # No sample inside 1.6-2.0: the nearest one either side anchors it.
    assert [t for t, _ in anchors] == [1.5, 3.0]


def test_played_frames_follow_the_source_rate():
    assert person_matte.played_source_frames(1.0, 3, 24.0, 24.0) == [
        24, 25, 26]
    assert person_matte.played_source_frames(1.0, 3, 25.0, 50.0) == [
        50, 52, 54]


# ── The behind-subject route ────────────────────────────────────────


def _title_sequence(directory, count=40, size=(32, 24)):
    from PIL import Image

    os.makedirs(directory, exist_ok=True)
    pattern = os.path.join(directory, "t_%05d.png")
    for i in range(count):
        Image.fromarray(np.full((size[1], size[0], 4), 255, dtype=np.uint8),
                        mode="RGBA").save(pattern % i)
    return {"segment_id": "t1", "overlay_path": pattern % 0,
            "timeline_start": 2.0, "timeline_end": 6.0,
            "total_frames": count,
            "sequence": {"pattern": pattern, "frame_count": count}}


def _apply(tmp_path, person_matte_fn):
    clip = {"label": "clip1", "clip_id": "c1", "source_file": "/f/c1.mp4",
            "timeline_in": 0.0, "timeline_out": 10.0, "source_in": 0.0}
    return behind_subject.apply_behind_subject(
        [_title_sequence(str(tmp_path / "titles"))], {"c1": None},
        clip_at=lambda s, e: [clip], matte_dir=str(tmp_path / "mattes"),
        precomp_dir=str(tmp_path / "precomp"), timeline_fps=10.0,
        clip_metadata={"c1": {"width": 32, "height": 24}},
        person_matte=person_matte_fn)


def test_a_person_that_cannot_be_drawn_refuses_rather_than_falls_back(
        tmp_path):
    def unanchored(clip, **_):
        raise PersonMatteUnavailable("no_face_anchor", "no face in reach")
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        _apply(tmp_path, unanchored)
    assert "no_face_anchor" in str(exc.value)


def test_no_person_instance_keeps_the_sam_route(tmp_path):
    def nobody(clip, **_):
        raise PersonMatteUnavailable("no_region_record", "never measured")
    # The SAM route is asked next and has no 1.06 result: its refusal,
    # not the person route's, is what the captain reads.
    with pytest.raises(behind_subject.BehindSubjectRefused) as exc:
        _apply(tmp_path, nobody)
    assert "no 1.06 segmentation" in str(exc.value)


@pytest.mark.skipif(not __import__("shutil").which("ffmpeg"),
                    reason="needs ffmpeg - CI installs it")
def test_a_delivery_matte_is_what_the_title_goes_under(tmp_path):
    from PIL import Image

    asked = {}

    def drawn(clip, *, stem, source_in, played_frames, resolution,
              region_track=None):
        asked.update(stem=stem, frames=played_frames, source_in=source_in)
        files = []
        for t in range(played_frames):
            path = str(tmp_path / f"{stem}_subject_matte_{t:05d}.png")
            mask = np.zeros((24, 32), dtype=np.uint8)
            mask[:, t % 32] = 255
            Image.fromarray(mask, mode="L").save(path)
            files.append(path)
        return {"clip_stem": stem, "files": files,
                "frame_count": played_frames, "resolution": [24, 32],
                "region_track": "region_001",
                "provenance": {"method": person_matte.DELIVERY_METHOD}}

    placed, mattes, per_segment = _apply(tmp_path, drawn)
    assert asked["frames"] == 40 and asked["source_in"] == 2.0
    assert per_segment[0]["precomps"][0]["matte_route"] == "person_instance"
    assert mattes[0]["region_track"] == "region_001"
    assert os.path.isfile(placed[0]["overlay_path"])


# ── The row ─────────────────────────────────────────────────────────


def test_the_subject_grade_row_sits_on_the_picture_under_the_captions():
    plan = timeline_layout.plan_layout({
        "has_broll": True, "caption_spans": [(0, 10)],
        "subject_grade_spans": [(0, 5), (3, 8)], "mg_spans": [(0, 4)]})
    roles = [t.role for t in plan.video_tracks]
    assert roles == ["a_roll", "b_roll", "subject_grade", "subject_grade",
                     "captions", "motion_graphics"]
    assert [t.name for t in plan.video_tracks][2:4] == [
        "Subject Grade", "Subject Grade 2"]
    assert "subject_grade" not in [t.role for t in
                                   timeline_layout.plan_layout(
                                       {}).video_tracks]
