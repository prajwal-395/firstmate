"""The E5 angle plan is a per-reel declaration, not master-row copying."""

from types import SimpleNamespace

import pytest

from library.tools import edit_ledger, reel_angle_plan


def _row(kind, phrase, camera, *, min_shot=2.0, lead=0):
    anchor = {"kind": kind}
    if phrase:
        anchor["phrase"] = phrase
    return {"op": "angle_plan", "anchor": anchor,
            "reel": "Reel 09 - hook",
            "params": {"camera": camera,
                       "min_shot_seconds": min_shot,
                       "lead_frames": lead},
            "stated_by": "requester", "reason": "show the speaker"}


def _transcript():
    return {"segments": [{"words": [
        {"word": "guest", "start": 4.0, "end": 4.2, "timed": True},
        {"word": "speaks", "start": 4.2, "end": 4.5, "timed": True},
    ]}]}


def _angles():
    return [{"key": "1", "label": "Wide"},
            {"key": "2", "label": "Close"}]


def _place(track, start=0, end=10, *, media="video"):
    return {"clip": SimpleNamespace(track_type=media,
                                    track_index=track,
                                    source_file=f"camera-{track}.mov"),
            "source_in": float(start), "source_out": float(end),
            "record": float(start), "snapped_record": int(start * 10),
            "master": (float(start), float(end)),
            "track_index": track, "speaker": "speaker"}


def test_switch_uses_word_anchor_lead_and_declared_minimum():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0,
        "Reel 09 - hook")

    assert [(shot["start_frame"], shot["end_frame"], shot["camera_key"])
            for shot in plan["shots"]] == [(0, 30, "1"), (30, 100, "2")]
    assert plan["shots"][0]["min_shot_frames"] == 20
    assert plan["shots"][1]["lead_frames"] == 10


def test_picture_switch_keeps_audio_and_splits_the_chosen_camera():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0,
        "Reel 09 - hook")
    audio = _place(1, media="audio")
    result, record = reel_angle_plan.select_picture_placements(
        [_place(1), _place(2), audio], plan, 10.0)

    pictures = [place for place in result
                if place["clip"].track_type == "video"]
    assert [(place["clip"].track_index, place["snapped_record"],
             round(place["source_in"], 3), round(place["source_out"], 3))
            for place in pictures] == [(1, 0, 0.0, 3.0),
                                       (2, 30, 3.0, 10.0)]
    assert result[0] is audio
    assert record["picture_placements"] == 2


def test_camera_without_full_interval_coverage_refuses_black_picture():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0,
        "Reel 09 - hook")

    # No camera at all is placed over frames 80-100: nothing to sync from.
    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="would show black"):
        reel_angle_plan.select_picture_placements(
            [_place(1, end=8), _place(2, end=8)], plan, 10.0)


def test_lead_that_breaks_minimum_shot_refuses_instead_of_ignoring_one():
    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="minimum"):
        reel_angle_plan.resolve(
            [_row("reel", "", "Wide", min_shot=2),
             _row("words", "guest speaks", "Close", min_shot=2,
                  lead=25)],
            _angles(), [(0.0, 10.0)], _transcript(), 10.0,
            "Reel 09 - hook")


def test_same_camera_overlapping_placements_cannot_fake_full_coverage():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide")], _angles(), [(0.0, 10.0)],
        _transcript(), 10.0, "Reel 09 - hook")
    first = _place(1)
    duplicate = _place(1)
    duplicate["clip"].source_file = "overlapping-wide-take.mov"

    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="two picture placements overlapping"):
        reel_angle_plan.select_picture_placements(
            [first, duplicate], plan, 10.0)


def test_camera_plan_requires_a_positive_minimum_shot_length():
    row = _row("reel", "", "Wide", min_shot=0)

    with pytest.raises(edit_ledger.EditLedgerError,
                       match="positive minimum"):
        edit_ledger.validate_rows([row])


def test_camera_plan_requires_an_explicit_lead_value():
    row = _row("reel", "", "Wide")
    del row["params"]["lead_frames"]

    with pytest.raises(edit_ledger.EditLedgerError,
                       match="lead_frames"):
        edit_ledger.validate_rows([row])


def test_camera_plan_refuses_an_unread_parameter():
    row = _row("reel", "", "Wide")
    row["params"]["cut_style"] = "whip"

    with pytest.raises(edit_ledger.EditLedgerError,
                       match="does not read"):
        edit_ledger.validate_rows([row])


# ── A podcast master places each camera only while its person speaks ──
#
# Measured 2026-09-25 on a scratch copy of geo-podcast: every plan that
# chose the LISTENER (MC1.1's reaction switches), cut ahead of the next
# speaker (MC3.1/C3.3's 12-frame lead) or held a shot past a speaker
# change (PA2.3's 3 s minimum) refused with "covers frames through N ...
# would show black", because the reel's only picture of a camera was the
# master's own placement of it. The camera rolled the whole time; its
# frames come from its file through the offset the master's cuts measure.

def _clip(track, file, start, end, source_in, *, frames=10_000):
    return SimpleNamespace(track_type="video", track_index=track,
                           track_name=f"Camera {track}", speaker=f"S{track}",
                           source_file=file, source_in=source_in,
                           source_out=source_in + (end - start),
                           timeline_start=start, timeline_end=end,
                           source_frames=frames)


def _speaker_place(track, start, end, source_in):
    """A placement exactly as `reel_build.placements` makes one, at 10fps."""
    return {"clip": SimpleNamespace(track_type="video", track_index=track,
                                    source_file=f"camera-{track}.mov"),
            "source_in": float(source_in),
            "source_out": float(source_in + end - start),
            "record": float(start), "snapped_record": int(start * 10),
            "master": (float(start), float(end)),
            "track_index": track, "speaker": f"S{track}"}


def _podcast_master():
    # Camera 2's file runs 5 s ahead of camera 1's: frame f of camera-1
    # was recorded with frame f + 50 of camera-2. Three gapless cuts say so.
    return [_clip(1, "camera-1.mov", 0.0, 3.0, 100.0),
            _clip(2, "camera-2.mov", 3.0, 10.0, 108.0),
            _clip(1, "camera-1.mov", 10.0, 12.0, 110.0),
            _clip(2, "camera-2.mov", 12.0, 15.0, 117.0)]


def test_camera_sync_is_what_a_majority_of_gapless_cuts_agree_on():
    from library.tools import camera_sync

    measured = camera_sync.measure(_podcast_master(), 10.0,
                                   angle_key=lambda c: str(c.track_index))
    assert measured["offsets"][("camera-1.mov", "camera-2.mov")] == 50
    assert measured["offsets"][("camera-2.mov", "camera-1.mov")] == -50


def test_cuts_that_disagree_give_no_sync_rather_than_an_average():
    from library.tools import camera_sync

    assert camera_sync.agreed_offset([50, 50, 51, 400, -30]) == 50
    assert camera_sync.agreed_offset([50, 400]) is None
    assert camera_sync.agreed_offset([50, 50, 400, 400]) is None


def test_listener_shot_plays_the_listening_camera_from_its_own_file():
    from library.tools import camera_sync

    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0, "Reel 09 - hook")
    # The master placed camera 1 for the whole reel and camera 2 nowhere:
    # the plan's camera-2 shot (frames 30-100) is the listener.
    speaking = _speaker_place(1, 0, 10, 100)
    master = _podcast_master()
    result, record = reel_angle_plan.select_picture_placements(
        [speaking], plan, 10.0, master_clips=master,
        sync=camera_sync.measure(master, 10.0,
                                 angle_key=lambda c: str(c.track_index)))

    pictures = [(p["clip"].source_file, p["snapped_record"],
                 round(p["source_in"], 3), round(p["source_out"], 3))
                for p in result]
    assert pictures == [("camera-1.mov", 0, 100.0, 103.0),
                        ("camera-2.mov", 30, 108.0, 115.0)]
    assert record["synced_placements"] == 1
    # The words under the listener shot are still the speaker's.
    assert result[1]["master"] == (3.0, 10.0)


def test_listener_shot_with_no_measured_sync_refuses_by_name():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0, "Reel 09 - hook")

    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="no agreed offset"):
        reel_angle_plan.select_picture_placements(
            [_speaker_place(1, 0, 10, 100)], plan, 10.0,
            master_clips=_podcast_master(), sync={"offsets": {}})


def test_synced_frames_outside_the_file_refuse_instead_of_clamping():
    plan = reel_angle_plan.resolve(
        [_row("reel", "", "Wide"),
         _row("words", "guest speaks", "Close", lead=10)],
        _angles(), [(0.0, 10.0)], _transcript(), 10.0, "Reel 09 - hook")
    short = [_clip(2, "camera-2.mov", 3.0, 10.0, 108.0, frames=1_100)]

    with pytest.raises(reel_angle_plan.AnglePlanError,
                       match="outside the file"):
        reel_angle_plan.select_picture_placements(
            [_speaker_place(1, 0, 10, 100)], plan, 10.0,
            master_clips=short,
            sync={"offsets": {("camera-1.mov", "camera-2.mov"): 50}})


def test_motion_ask_describes_the_shots_the_angle_plan_places(tmp_path):
    """Measured 2026-09-25 on Reel 02 of a scratch geo-podcast: the motion
    ask was spelled over the master's per-speaker shots, the build placed
    the angle plan's shots, and the Fusion pass refused every comp as
    reaching no timeline item. The ask (`write_visual_asks`, shared by
    `reel.ask` and the build) must describe the planned picture."""
    import json

    from library.tools import reel_build

    project = tmp_path / "proj"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    (project / "external").mkdir()
    name = "Reel 09 - hook"
    (project / "external" / "edit_ledger.json").write_text(json.dumps({
        "version": 1,
        "rows": [{"op": "angle_plan", "anchor": {"kind": "reel"},
                  "reel": name,
                  "params": {"camera": "Camera 2", "min_shot_seconds": 1.0,
                             "lead_frames": 0},
                  "stated_by": "requester", "reason": "hold the listener"}]}),
        encoding="utf-8")
    # Camera 1 speaks 0-12 and 13-30, camera 2 12-13: two gapless cuts
    # agree that camera-2 runs 5 s ahead of camera-1.
    master = [_clip(1, "cam-1.mov", 0.0, 12.0, 100.0),
              _clip(2, "cam-2.mov", 12.0, 13.0, 117.0),
              _clip(1, "cam-1.mov", 13.0, 30.0, 113.0)]
    moment = SimpleNamespace(number=9, timeline_name=name,
                             timeline_start=10.0, timeline_end=18.0)
    fps = 24000 / 1001

    reel_build.write_visual_asks(
        moment, {"segments": []}, [(10.0, 18.0)], master, str(project),
        fps, name + " (rebuild staging)", [], {"origin": "test"})

    ask = json.loads((project / "pipeline_output" / "llm_requests"
                      / "reel_motion_09.json").read_text(encoding="utf-8"))
    shots = ask["context"]["shots"]
    assert [s["speaker"] for s in shots] == ["S2", "S2", "S2"]
    assert shots[0]["timeline_start"] == 0.0
    assert shots[-1]["timeline_end"] == pytest.approx(8.0, abs=1 / fps)


def test_a_one_frame_gap_is_played_as_a_handle_not_a_sliver():
    """Scratch Reel 04, 2026-09-25: the plan's cut fell one frame before
    the camera's own clip, the gap was filled with a 1-frame synced
    piece, and the F7 readability floor refused the reel. The camera's
    own clip plays that frame as a handle - no sync needed for it."""
    plan = {"declared": True, "shots": [
        {"start_frame": 0, "end_frame": 30, "camera_key": "1",
         "camera": "Wide", "anchor": {"kind": "reel"},
         "min_shot_seconds": 2.0, "lead_frames": 0, "name": "opening"},
        {"start_frame": 30, "end_frame": 100, "camera_key": "2",
         "camera": "Close", "anchor": {"kind": "words", "phrase": "x"},
         "min_shot_seconds": 2.0, "lead_frames": 0, "name": "switch"}]}
    first = _speaker_place(1, 0, 3.1, 100)
    second = _speaker_place(2, 3.1, 10, 200)
    second["snapped_record"] = 31
    result, record = reel_angle_plan.select_picture_placements(
        [first, second], plan, 10.0, sync={"offsets": {}})

    assert [(p["clip"].track_index, p["snapped_record"],
             round(p["source_in"], 3), round(p["source_out"], 3))
            for p in result] == [(1, 0, 100.0, 103.0), (2, 30, 199.9, 206.9)]
    assert record["synced_placements"] == 0


def test_a_plan_opening_on_the_second_camera_keeps_the_master_row_order():
    """Scratch Reels 04/18/19/25, 2026-09-25: a plan whose first shot is
    the master's SECOND camera put that camera on V1, and the verifier -
    reading rows in the master's order - counted the freeze tail on the
    other speaker ("Craig planned 16.43s, got 15.64s")."""
    from library.tools import timeline_layout

    angles = [{"key": "1", "label": "Akshita", "speech_name": "Akshita CH1",
               "program_channel": 1},
              {"key": "2", "label": "Craig", "speech_name": "Craig CH1",
               "program_channel": 1}]
    plan = timeline_layout.plan_layout({"angles": angles,
                                        "picture_angles": ["2", "1"]})
    assert [(row.occupant, row.name) for row in plan.aroll_rows()] == [
        ("1", "Akshita"), ("2", "Craig")]
