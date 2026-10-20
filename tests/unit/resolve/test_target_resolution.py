"""AGENT-011: a described target resolves to a clip, deterministically.

An agent that knew the task - "trim the second interview B-roll" - used
to read `items`, count rows in a TOON table and hand a `--track video2
--index 1` to the next command. The count was the model's, and the
`items` table did not print the one column that would let it address a
clip by its stable identity.

These tests name the defects that made that necessary:

- a description that does not name exactly one clip is REFUSED, with the
  candidates named, rather than guessed at;
- a resolution that is not by stable identity SAYS SO, because a rename
  or a reorder since the description was written misresolves it;
- a typo in the description is refused, never ignored - a silently
  dropped key resolves to something the caller did not ask for;
- the answer carries the exact addressing arguments a mutating verb
  takes, so the caller does not assemble them by hand.

They run hermetic: plain clip dicts, no Resolve, no store, no model.
"""
from __future__ import annotations

import json

import pytest

from library.tools import target_resolution as tr


def _clip(uid, name, track_type="video", track_index=1, track_name="",
          record_in=0, record_out=100, source_in=10, source_out=110,
          source_file="/m/a.mov", media_type="video"):
    return {"unique_id": uid, "name": name, "track_type": track_type,
            "track_index": track_index, "track_name": track_name,
            "record_in": record_in, "record_out": record_out,
            "duration": record_out - record_in,
            "source_in_frame": source_in, "source_out_frame": source_out,
            "source_file": source_file, "media_type": media_type}


CLIPS = [
    _clip("uid-1", "Interview A", track_index=1, track_name="V1",
          record_in=0, record_out=100, source_file="/m/a.mov"),
    _clip("uid-2", "B-roll 1", track_index=2, track_name="B-roll",
          record_in=0, record_out=50, source_in=5, source_out=55,
          source_file="/m/b.mov"),
    _clip("uid-3", "B-roll 2", track_index=2, track_name="B-roll",
          record_in=50, record_out=120, source_in=70, source_out=140,
          source_file="/m/c.mov"),
    _clip("uid-4", "Interview A", track_type="audio", track_index=1,
          track_name="A1", record_in=0, record_out=100, source_file="/m/a.mov",
          media_type="audio"),
]


def test_a_row_and_a_position_resolve_to_that_clip_and_say_it_is_assumed():
    # Defect: a position resolved without saying so reads as an identity,
    # and a reorder since the description was written misresolves it.
    resolution = tr.resolve_text("row=B-roll item=2", CLIPS)
    assert resolution.status == "assumed"
    match = resolution.single
    assert match.unique_id == "uid-3"
    assert match.name == "B-roll 2"
    assert match.row == "V2"
    assert match.position == 1
    assert "reorder" in match.assumption


def test_a_row_may_be_named_by_its_track_code_or_its_track_name():
    by_code = tr.resolve_text("row=V2 item=1", CLIPS)
    by_name = tr.resolve_text("row=B-roll item=1", CLIPS)
    assert by_code.single.unique_id == by_name.single.unique_id == "uid-2"
    assert by_code.single.track_name == "B-roll"


def test_a_clip_id_resolves_to_exactly_that_clip_by_stable_identity():
    resolution = tr.resolve_text("id=uid-3", CLIPS)
    assert resolution.status == "resolved"
    assert resolution.single.unique_id == "uid-3"
    assert resolution.single.assumption == ""


def test_a_name_two_clips_share_is_refused_with_the_candidates_named():
    # Defect: two clips called "Interview A" - a video and its audio -
    # made the agent pick one by counting. A name that is not unique
    # cannot name one clip, so it refuses and says which ones.
    with pytest.raises(tr.TargetAmbiguousRefused) as refused:
        tr.resolve_text('name="Interview A"', CLIPS)
    assert "Interview A" in refused.value.what
    assert "uid-1" in refused.value.what
    assert "uid-4" in refused.value.what
    assert "V1" in refused.value.what and "A1" in refused.value.what


def test_a_name_narrowed_by_media_type_resolves_to_the_one_clip():
    resolution = tr.resolve_text('name="Interview A" media=video', CLIPS)
    assert resolution.single.unique_id == "uid-1"
    assert resolution.status == "assumed"


def test_a_frame_range_that_spans_two_clips_resolves_to_the_group():
    # Defect: "replace the segment from 12s to 18s" spanned two clips and
    # had no resolver at all, so the agent interpolated.
    resolution = tr.resolve_text("row=V2 start=40 end=60", CLIPS)
    assert resolution.status == "group"
    assert [match.unique_id for match in resolution.matches] == ["uid-2", "uid-3"]
    assert "V2" in resolution.note


def test_a_frame_range_inside_one_clip_resolves_to_that_clip():
    resolution = tr.resolve_text("start=0 end=10 row=V1", CLIPS)
    assert resolution.status == "resolved"
    assert resolution.single.unique_id == "uid-1"


def test_a_typo_in_the_description_is_refused_rather_than_ignored():
    # Defect: a key that was silently dropped resolved to something the
    # caller did not ask for.
    with pytest.raises(tr.TargetResolutionRefused) as refused:
        tr.resolve_text("roww=V2 item=2", CLIPS)
    assert "roww" in refused.value.what


def test_an_ordinal_with_no_row_refuses_because_it_has_no_meaning():
    with pytest.raises(tr.TargetResolutionRefused) as refused:
        tr.resolve_text("item=2", CLIPS)
    assert "row" in refused.value.why


def test_a_position_outside_the_row_refuses_and_names_what_the_row_holds():
    with pytest.raises(tr.TargetNotFoundRefused) as refused:
        tr.resolve_text("row=V2 item=9", CLIPS)
    assert "B-roll 1" in refused.value.what
    assert "B-roll 2" in refused.value.what


def test_a_row_that_does_not_exist_refuses_rather_than_matching_nothing():
    with pytest.raises(tr.TargetNotFoundRefused) as refused:
        tr.resolve_text("row=V9 item=1", CLIPS)
    assert "V9" in refused.value.what


def test_a_clip_id_that_is_not_on_the_timeline_refuses():
    with pytest.raises(tr.TargetNotFoundRefused):
        tr.resolve_text("id=nope", CLIPS)


def test_the_refusal_carries_a_recovery_class_the_host_can_act_on():
    # Defect: a bare error gave the host nothing to decide with.
    with pytest.raises(tr.TargetAmbiguousRefused) as refused:
        tr.resolve_text('name="Interview A"', CLIPS)
    assert refused.value.recovery_class == "needs_question"
    assert refused.value.code == "REN_TARGET_AMBIGUOUS"
    assert refused.value.fix
    with pytest.raises(tr.TargetNotFoundRefused) as missing:
        tr.resolve_text("id=nope", CLIPS)
    assert missing.value.recovery_class == "auto_recoverable"


def test_the_resolved_clip_is_the_record_the_other_readers_already_use():
    # The match carries the clip record itself, so `EditTarget`
    # (which the edit request path uses) reads it without a second shape.
    from library.tools.edit_request import EditTarget

    resolution = tr.resolve_text("row=B-roll item=2", CLIPS)
    target = EditTarget.from_shadow_clip(resolution.single.clip)
    assert target.unique_id == "uid-3"
    assert target.track_type == "video"
    assert target.track_index == 2


# ── The two faces of the capability ──────────────────────────────────


class _FakeView:
    """The recorded generation a read hands back."""

    def __init__(self, clips):
        self.snapshot = {"tracks": [{"type": "video", "index": 1,
                                     "name": "V1", "clips": list(clips)}]}
        self.generation = _FakeGeneration()

    @property
    def clip_details(self):
        return tr.clips_of(self.snapshot)


class _FakeGeneration:
    project = "Podcast"
    timeline_id = "tl-1"
    timeline_name = "Reel 29"
    generation = 4
    verified_at = 0


def test_the_axi_command_gives_the_exact_addressing_arguments(capsys, monkeypatch):
    # Defect: the agent had to assemble `--target-unique-id` and the
    # five `--expected-*` guards by hand, from a table that printed
    # none of the identity columns it needed.
    from argparse import Namespace

    from library.tools import resolve_axi_core_commands as axi

    view = _FakeView(CLIPS)
    monkeypatch.setattr(axi, "_timeline_view", lambda *a, **k: (view, ""))
    assert axi.cmd_target(Namespace(
        description="row=B-roll item=2", timeline="Reel 29",
        project="Podcast", reel=None, staging=False,
        refresh_live=False, json=True)) == 0
    payload = json.loads(capsys.readouterr().out)
    arguments = payload["arguments"]
    assert arguments["track"] == "video2"
    assert arguments["index"] == 1
    assert arguments["target-unique-id"] == "uid-3"
    assert arguments["expected-source-file"] == "/m/c.mov"
    assert arguments["expected-source-in-frame"] == 70
    assert arguments["expected-source-out-frame"] == 140
    assert arguments["expected-record-in-frame"] == 50
    assert arguments["expected-record-out-frame"] == 120
    assert payload["status"] == "assumed"


def test_the_axi_command_refuses_a_description_that_names_no_one_clip(monkeypatch):
    from argparse import Namespace

    from library.tools import resolve_axi_core_commands as axi

    view = _FakeView(CLIPS)
    monkeypatch.setattr(axi, "_timeline_view", lambda *a, **k: (view, ""))
    with pytest.raises(tr.TargetAmbiguousRefused):
        axi.cmd_target(Namespace(
            description='name="Interview A"', timeline="Reel 29",
            project="Podcast", reel=None, staging=False,
            refresh_live=False, json=True))


def test_the_front_door_resolves_a_target_against_the_recorded_generation(
        capsys, tmp_path):
    # The editor side reads the shadow store and never touches Resolve.
    from library.tools import timeline_shadow as shadow

    snapshot = {
        "timeline": "Reel 29", "project": "Podcast", "fps": 30.0,
        "tracks": [{"type": "video", "index": 2, "name": "B-roll",
                    "clips": list(CLIPS)}],
        "markers": {"notes": []},
    }
    database = str(tmp_path / "shadow.db")
    store = shadow.ShadowStore(database)
    store.record(project="Podcast", timeline_id="tl-1",
                 timeline_name="Reel 29", snapshot=snapshot,
                 source=shadow.OBSERVED, expected_head=0)
    assert shadow._main(["--db", database, "target", "row=B-roll item=2",
                         "--project", "Podcast",
                         "--timeline", "Reel 29"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["target"]["status"] == "assumed"
    assert payload["target"]["matches"][0]["unique_id"] == "uid-3"

