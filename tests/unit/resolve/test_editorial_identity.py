"""Defect: a clip's identity was its POSITION, so an insert renamed it.

The Project Model minted each clip use's id from the clip's index in the
placement list - `use:<reel>:0003`.  An insert in front of the list
renamed every later clip, and because the minting SKIPS an id it already
holds, the rename did not mint a new identity: it left the model
describing the clip that used to be there.  The model said "clip use 0003
is the interview at 0:12" while 0003 was now a different shot.

`library/tools/editorial_identity.py` owns the identity now: what a clip
IS - the material it plays and the role it plays it in - as distinct from
where it sits and what Resolve calls it.
"""

from __future__ import annotations

from library.tools import edit_state_preservation as preservation
from library.tools import editorial_identity as identity
from library.tools import reel_project_model as rpm
from library.tools.project_model import validate_project_model


def _clip(**overrides):
    clip = {
        "source_file": "/m/a.MXF",
        "media_pool_item_id": "media-1",
        "unique_id": "uid-1",
        "track_type": "video",
        "track_index": 1,
        "track_name": "A-roll",
        "name": "Shot",
        "source_in_frame": 100,
        "source_out_frame": 200,
    }
    clip.update(overrides)
    return clip


def _placements():
    return [
        {"source_file": "/m/a.MXF", "source_in": 10.0, "source_out": 15.0,
         "record_start": 0.0, "record_frame": 0, "record_frames": 120,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll"},
        {"source_file": "/m/b.MXF", "source_in": 1.0, "source_out": 2.0,
         "record_start": 5.0, "record_frame": 120, "record_frames": 24,
         "track_type": "video", "track_index": 2, "track_name": "B-Roll"},
    ]


# ── The identity itself ──────────────────────────────────────────────


def test_an_identity_is_the_material_and_the_role_not_the_position():
    here = identity.identity_of(_clip())
    there = identity.identity_of(_clip(unique_id="uid-99",
                                        source_in_frame=900,
                                        source_out_frame=950))
    assert here.token == there.token, (
        "the same material in the same role is the same editorial moment, "
        "whatever Resolve calls it and wherever it sits")


def test_an_identity_survives_a_trim():
    before = identity.identity_of(_clip())
    after = identity.identity_of(_clip(source_in_frame=120,
                                       source_out_frame=180))
    assert before.token == after.token
    assert before.passage_id != after.passage_id, (
        "the passage the clip plays changed, and the passage identity has "
        "to say so")
    carried = identity.carry_identity(before, after)
    assert carried is not None
    assert carried.token == before.token
    assert carried.passage_id == after.passage_id
    assert carried.source_span == (120, 180)


def test_a_replacement_of_other_material_is_a_substitution_not_a_carry():
    before = identity.identity_of(_clip())
    other = identity.identity_of(_clip(media_pool_item_id="media-2"))
    assert identity.carry_identity(before, other) is None, (
        "a clip that plays other footage is a different editorial moment, "
        "and letting it inherit the identity would let a substitution "
        "pass as the thing it replaced")
    assert identity.is_substitution(before, other)


def test_a_replacement_of_the_same_material_keeps_the_identity():
    before = identity.identity_of(_clip())
    same = identity.identity_of(_clip(unique_id="uid-new",
                                      source_in_frame=140,
                                      source_out_frame=190))
    carried = identity.carry_identity(before, same)
    assert carried is not None
    assert carried.token == before.token


def test_a_clip_with_no_media_has_no_identity():
    assert identity.identity_of(_clip(source_file="",
                                       media_pool_item_id="")) is None


# ── The Project Model's clip uses ────────────────────────────────────


def test_an_insert_does_not_rename_the_clips_after_it():
    model = rpm.blank_model("project-x")
    before = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", _placements(), {("video", 1): "a_roll"})
    inserted = [{
        "source_file": "/m/c.MXF", "source_in": 3.0, "source_out": 4.0,
        "record_start": 0.0, "record_frame": 0, "record_frames": 24,
        "track_type": "video", "track_index": 2, "track_name": "B-Roll",
    }] + _placements()
    after = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", inserted, {("video", 1): "a_roll"})
    assert after["use_ids"][1:] == before["use_ids"], (
        "inserting a clip in front renamed the clips after it")
    assert after["use_ids"][0] not in before["use_ids"], (
        "the inserted clip must have an identity of its own")


def test_the_model_describes_the_footage_that_is_actually_placed():
    """The defect the positional id caused: the model kept describing
    the clip that used to be at that index."""
    model = rpm.blank_model("project-x")
    inserted = [{
        "source_file": "/m/c.MXF", "source_in": 3.0, "source_out": 4.0,
        "record_start": 0.0, "record_frame": 0, "record_frames": 24,
        "track_type": "video", "track_index": 2, "track_name": "B-Roll",
    }] + _placements()
    out = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", inserted, {("video", 1): "a_roll"})
    rows = {row["id"]: row for row in model["clip_uses"]}
    assets = {use_id: rows[use_id]["asset_id"] for use_id in out["use_ids"]}
    placed = [place["source_file"] for place in inserted]
    assert len(set(assets.values())) == len(placed), (
        "two clip uses describe the same footage, so the model is "
        "describing a clip that is not there")
    validate_project_model(model)


def test_a_trim_keeps_the_clip_use_identity():
    model = rpm.blank_model("project-x")
    before = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", _placements(), {("video", 1): "a_roll"})
    trimmed = [dict(_placements()[0], source_in=11.0, source_out=14.0,
                    record_frames=72)] + [_placements()[1]]
    after = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", trimmed, {("video", 1): "a_roll"})
    assert after["use_ids"][0] == before["use_ids"][0], (
        "a trim changes the passage, not the editorial moment")
    row = next(row for row in model["clip_uses"]
               if row["id"] == before["use_ids"][0])
    assert row["source_interval"]["start"] == 1_320_000, (
        "the carried clip use must describe the passage it now plays")


def test_a_rebuild_of_the_same_plan_mints_the_same_identities():
    first = rpm.blank_model("project-x")
    second = rpm.blank_model("project-x")
    one = rpm.clip_uses_from_placements(
        first, "reel-01", "Reel 01", _placements(), {("video", 1): "a_roll"})
    two = rpm.clip_uses_from_placements(
        second, "reel-01", "Reel 01", _placements(), {("video", 1): "a_roll"})
    assert one["use_ids"] == two["use_ids"]


def test_the_placement_mapping_comes_from_the_minting():
    """A placement the minting SKIPPED used to shift every later
    placement's index, so the mapping pointed at the wrong clip."""
    model = rpm.blank_model("project-x")
    placements = _placements() + [
        {"source_file": "", "source_in": 1.0, "source_out": 2.0,
         "record_start": 6.0, "record_frame": 144,
         "track_type": "video", "track_index": 1, "track_name": "A-Roll"},
    ]
    out = rpm.clip_uses_from_placements(
        model, "reel-01", "Reel 01", placements, {("video", 1): "a_roll"})
    assert out["skipped"], "the empty placement should have been skipped"
    assert set(out["use_id_by_index"]) == {0, 1}, (
        "the skipped placement must not take an index, and the placements "
        "after it must keep theirs")
    assert out["use_id_by_index"][0] == out["use_ids"][0]


# ── The replacement path carries the identity ────────────────────────


class _Pool:
    def __init__(self, path="/m/a.MXF", media_id="media-1"):
        self._path = path
        self._media_id = media_id

    def GetUniqueId(self):
        return "pool-uid"

    def GetMediaId(self):
        return self._media_id

    def GetClipProperty(self, name):
        return {"File Path": self._path}.get(name, "")


class _Item:
    def __init__(self, pool=None):
        self._pool = pool or _Pool()

    def GetFlagList(self):
        return []

    def GetClipEnabled(self):
        return True

    def GetClipColor(self):
        return ""

    def GetMarkers(self):
        return {}

    def GetMediaPoolItem(self):
        return self._pool

    def GetUniqueId(self):
        return "uid-1"

    def GetLinkedItems(self):
        return []

    def GetTrackTypeAndIndex(self):
        return ["video", 1]

    def GetSourceStartFrame(self):
        return 100

    def GetSourceEndFrame(self):
        return 200

    def GetStart(self):
        return 10

    def GetEnd(self):
        return 110


def test_the_replacement_captures_the_durable_identity():
    state = preservation.capture_clip_metadata(_Item())
    captured = state["editorial_identity"]
    assert captured["token"], "the replacement must capture the identity"
    assert captured["material_id"]
    assert captured["role"]
    assert captured["passage_id"] != captured["token"], (
        "the passage and the durable identity are two different claims")


def test_two_items_of_the_same_material_and_role_share_one_identity():
    first = preservation.capture_clip_metadata(_Item())["editorial_identity"]
    second = preservation.capture_clip_metadata(_Item())["editorial_identity"]
    assert first["token"] == second["token"]


def test_other_material_gets_another_identity():
    first = preservation.capture_clip_metadata(_Item())["editorial_identity"]
    other = preservation.capture_clip_metadata(
        _Item(_Pool(path="/m/b.MXF", media_id="media-2")))["editorial_identity"]
    assert first["token"] != other["token"]
