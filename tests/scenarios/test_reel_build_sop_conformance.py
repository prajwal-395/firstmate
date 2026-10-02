"""The reel builder obeys the timeline SOP, and the verifier reads it back.

Defects covered here (fake Resolve, no live connection):
  1. two speakers collapsed onto one video row with mixed audio rows -
     a-roll gets one video row per angle and speech one audio row per
     angle, named from the master's own rows;
  2. the MXF program stream never explicitly selected - exactly one
     recorded program stream per source reaches the timeline, and a
     stray is deleted on the spot and recorded;
  3. nothing linked - picture links to its speech, and a caption whose
     span falls inside a speech span joins that group in ONE call
     (linking is exclusive, not additive);
  4. unnamed and empty rows - every row is named from the plan, and a
     row whose placements all fail is deleted, never kept blank.

`library/tools/timeline_conformance.py` reads a built timeline back
against the plan the build recorded. It is deterministic and it can
fail, so it is a real gate.
"""

import json

import pytest

from library.tools.reel_build import (
    ReelBuildError,
    build_reel_timeline,
    resolve_reel_program_channels,
)
from library.tools.resolve_transform import FALLBACK_DRAW_GAIN
from library.tools.timeline_conformance import (
    verify_timeline,
)
from library.tools.timeline_ingest import TimelineClip
from tests.resolve_double import (
    FakeMediaPoolItem,
    FakeTimeline,
    make_project,
)


def _media_properties(path):
    low_resolution_sources = ("LCATL0013", "LC4932", "reel_freeze_")
    resolution = (
        "1920x1080"
        if any(name in path for name in low_resolution_sources)
        else "3840x2160"
    )
    return {"Resolution": resolution, "FPS": "23.976"}


def _pool_item(path):
    item = FakeMediaPoolItem(path.rsplit("/", 1)[-1])
    item.SetClipProperty("File Path", path)
    item.SetClipProperty("FPS", "23.976")
    item.SetClipProperty("Resolution", _media_properties(path)["Resolution"])
    return item


class FakeMoment:
    timeline_name = "Reel 99 - sop-proof"
    timeline_start = 0.0
    timeline_end = 20.0
    number = 99
    call_to_action = None


def _clip(
    track_type, index, track_name, speaker, source, tl_start, tl_end, src_in=100.0
):
    return TimelineClip(
        resolve_item_id=f"{track_name}-{tl_start}",
        track_type=track_type,
        track_index=index,
        track_name=track_name,
        speaker=speaker,
        source_file=source,
        source_in=src_in,
        source_out=src_in + (tl_end - tl_start),
        source_in_frame=int(src_in * 24),
        source_out_frame=int(src_in * 24) + 1,
        source_frames=100000,
        timeline_start=tl_start,
        timeline_end=tl_end,
        name="clip",
    )


def _master_clips():
    return [
        _clip("video", 1, "Akshita", "Akshita", "/m/akshita.MXF", 0.0, 10.0),
        _clip("video", 2, "Craig", "Craig", "/m/craig.MXF", 10.0, 20.0),
        _clip("audio", 1, "Akshita CH1", "Akshita", "/m/akshita.MXF", 0.0, 10.0),
        _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF", 10.0, 20.0),
    ]


def _world(audio_channels=(1,), fail_paths=()):
    timeline = FakeTimeline()
    paths = ["/m/akshita.MXF", "/m/craig.MXF", "/m/cap.mov", "/m/sem.mov"]
    project = make_project(width=1080, height=1920, frame_rate=23.976)
    pool = project.GetMediaPool()
    pool.next_timeline = timeline
    pool.audio_channels = tuple(audio_channels)
    pool.import_failures = set(fail_paths)
    pool.media_properties = {path: _media_properties(path) for path in paths}
    pool.ImportMedia([path for path in paths if path not in pool.import_failures])
    return timeline, pool, project


def _transcript():
    return {"segments": []}


def _caption(start, end, name="cap"):
    return {
        "overlay_path": "/m/cap.mov",
        "timeline_start": start,
        "timeline_end": end,
        "source_in_frame": 0,
        "segment_id": name,
    }


def _semantic(start, total, name="sem"):
    return {
        "overlay_path": "/m/sem.mov",
        "timeline_start": start,
        "total_frames": total,
    }


def _build(
    timeline,
    pool,
    project,
    clips,
    captions=(),
    semantic=(),
    look=None,
    program_channels=None,
    edit_ledger_rows=None,
    draw_gain=FALLBACK_DRAW_GAIN,
):
    return build_reel_timeline(
        project,
        FakeMoment(),
        clips,
        list(captions),
        23.976,
        1080,
        1920,
        "/tmp/no-such-project",
        _transcript(),
        look=look,
        semantic_segments=list(semantic) or None,
        master_timeline=None,
        program_channels=program_channels,
        edit_ledger_rows=edit_ledger_rows,
        draw_gain=draw_gain,
    )


def test_reel24_build_uses_one_units_conversion_for_punches_and_override(
    tmp_path, monkeypatch
):
    """The real timeline builder proves Reel 24 transforms offline.

    The opening two-shot has no automatic face aim, so its word-anchored
    transform override is the only thing that can cover the TV window.
    Five later shots use the normal punch-in path. The real
    ``build_reel_timeline`` runs against fake Resolve objects; no Resolve
    connection, project, media or rendered asset is used.
    """
    from types import SimpleNamespace

    from library.tools import reel_look, tv_frame
    from library.tools.project_layout import ProjectLayout

    reel = "Reel 24 - why-ai-trusts-youtube"
    source = "/media/LCATL0013.MXF"
    window = (56.106, 530.6365, 1022.967, 1829.827)
    monkeypatch.setattr(
        tv_frame, "screen_window_rect", lambda *_args, **_kwargs: window
    )
    monkeypatch.setattr(
        reel_look, "frame_overlay_segments", lambda *_args, **_kwargs: []
    )
    monkeypatch.setattr(
        reel_look, "frame_properties", lambda *_args, **_kwargs: {"ZoomX": 1.0}
    )
    monkeypatch.setattr(
        "library.tools.reel_post_header.plan_for_reel",
        lambda *_args, **_kwargs: SimpleNamespace(segments=[], as_dict=dict),
    )

    subject = SimpleNamespace(
        center_x=0.518, center_y=0.325, others=0, detected=12, samples=12
    )
    monkeypatch.setattr(
        "library.tools.reel_build._recorded_first_measure",
        lambda _project: (
            lambda _source, source_in, _source_out: (
                None if source_in < 3944.0 else subject
            )
        ),
    )

    def _segment(text, start):
        words = []
        cursor = start
        for token in text.split():
            words.append(
                {"word": token, "start": cursor, "end": cursor + 0.2, "timed": True}
            )
            cursor += 0.25
        return {
            "speaker": "Craig",
            "text": text,
            "timeline_start": start,
            "timeline_end": start + 4.0,
            "words": words,
        }

    phrases = [
        "why do ai platforms love video content",
        "later shot two has a different sentence",
        "later shot three carries its own sentence",
        "later shot four ends with another sentence",
        "later shot five carries a different thought",
        "later shot six finishes the thought",
    ]
    transcript = {
        "segments": [_segment(text, index * 4.0) for index, text in enumerate(phrases)]
    }
    master_clips = []
    source_files = [
        source,
        "/media/LC4932.MXF",
        "/media/LC4932.MXF",
        "/media/LC4932.MXF",
        "/media/reel_freeze_1b8ac2919c.mov",
        source,
    ]
    for index, source_file in enumerate(source_files):
        start, end = index * 4.0, (index + 1) * 4.0
        source_in = 3943.372 + index * 4.0
        master_clips.extend(
            [
                _clip(
                    "video",
                    1,
                    "Craig",
                    "Craig",
                    source_file,
                    start,
                    end,
                    src_in=source_in,
                ),
                _clip(
                    "audio",
                    1,
                    "Craig CH1",
                    "Craig",
                    source_file,
                    start,
                    end,
                    src_in=source_in,
                ),
            ]
        )
    moment = SimpleNamespace(
        number=24,
        slug="why-ai-trusts-youtube",
        timeline_name=reel + " (rebuild staging)",
        timeline_start=0.0,
        timeline_end=24.0,
        call_to_action=None,
    )

    def _build(gain, suffix):
        project_folder = tmp_path / suffix
        project_folder.mkdir()
        ProjectLayout(str(project_folder)).ensure()
        edit_path = project_folder / "external" / "captain_edits.json"
        edit_path.parent.mkdir(parents=True, exist_ok=True)
        edits = [
            {
                "kind": "transform_override",
                "anchor_phrase": phrases[0],
                "property": prop,
                "value": value,
                "reason": "offline Reel 24 regression",
                "reel": reel,
            }
            for prop, value in (
                ("Pan", 39.263),
                ("Tilt", -696.041),
                ("ZoomX", 2.1386),
                ("ZoomY", 2.1386),
            )
        ]
        edit_path.write_text(
            json.dumps({"key": "captain_edits", "source": "test", "value": edits}),
            encoding="utf-8",
        )

        timeline = FakeTimeline(frame_rate="23.976")
        project = make_project(width=1080, height=1920, frame_rate=23.976)
        pool = project.GetMediaPool()
        pool.next_timeline = timeline
        pool.media_properties = {
            path: _media_properties(path) for path in set(source_files)
        }
        pool.ImportMedia(sorted(set(source_files)))
        return (
            build_reel_timeline(
                project,
                moment,
                master_clips,
                [],
                23.976,
                1080,
                1920,
                str(project_folder),
                transcript,
                look={
                    "asset": "frame.png",
                    "punch_in": 2.3,
                    "scale": 2.1386 / 2.3,
                    "power": {},
                    "origin": "offline regression",
                },
                program_channels={"1": 1},
                ranges=[(0.0, 24.0)],
                edit_ledger_rows=[],
                draw_gain=gain,
            ),
            timeline,
        )

    built = {}
    for gain in (1.0, 4.0):
        record, timeline = _build(gain, f"reel24-legacy-override-gain-{gain:g}")
        picture_items = timeline.GetItemListInTrack("video", 1)
        assert len(picture_items) == 6
        built[gain] = [dict(item.GetProperty()) for item in picture_items]
        assert record["motion_coverage"] == []

    # The same legacy 1.0-reference values cover the opening at both
    # measured gains. The five automatic punch-ins also keep their
    # pixel aims: only the raw Resolve Pan/Tilt values scale by 1/gain.
    assert built[1.0][0]["Pan"] == pytest.approx(39.263)
    assert built[1.0][0]["Tilt"] == pytest.approx(-696.041)
    assert built[4.0][0]["Pan"] == pytest.approx(39.263 / 4)
    assert built[4.0][0]["Tilt"] == pytest.approx(-696.041 / 4)
    for one, four in zip(built[1.0], built[4.0]):
        assert four["Pan"] == pytest.approx(one["Pan"] / 4, abs=0.001)
        assert four["Tilt"] == pytest.approx(one["Tilt"] / 4, abs=0.001)
        assert four["ZoomX"] == pytest.approx(one["ZoomX"])


# ── Angles come from the master's own picture rows ──


def test_an_unresolvable_input_refuses_before_creating():
    timeline, pool, project = _world()
    with pytest.raises(ReelBuildError, match="no recorded program stream"):
        _build(timeline, pool, project, _master_clips())
    assert project.GetTimelineCount() == 0, (
        "the refusal must fire before a timeline exists"
    )
    assert pool.next_timeline is timeline

    # A grade row whose declared asset vanished refuses before creation
    # too, rather than leaving a half-built reel without that look.
    timeline, pool, project = _world()
    grade = {
        "op": "grade",
        "anchor": {"kind": "reel"},
        "params": {
            "drx": "missing.drx",
            "provenance": {
                "source": "Resolve export",
                "authorised_by": "captain",
                "licence": "captain's own asset",
            },
        },
        "stated_by": "requester",
        "reason": "apply the look",
    }

    with pytest.raises(
        ReelBuildError, match="grade cannot be resolved before the timeline"
    ):
        _build(
            timeline,
            pool,
            project,
            _master_clips(),
            program_channels={"1": 1, "2": 1},
            edit_ledger_rows=[grade],
        )
    assert project.GetTimelineCount() == 0
    assert pool.next_timeline is timeline


def test_caption_import_failure_refuses_instead_of_dropping_the_card(monkeypatch):
    """A rendered caption with no pool item must stop the reel build.

    Reel 17's staging build rendered the cards, but Resolve returned no
    media-pool item for most of them. The caption loop logged the failed
    import and continued, leaving those planned cards absent until F14
    found them on the timeline. This drives the actual timeline builder
    against the stub Resolve objects above and pins the refusal at the
    import that failed.
    """
    import library.tools.reel_placed_assets as placed_assets

    caption_path = "/m/new-caption.mov"
    timeline, pool, project = _world(fail_paths=(caption_path,))
    monkeypatch.setattr(placed_assets, "assert_placeable", lambda *_: None)

    with pytest.raises(ReelBuildError, match="would not import.*new-caption"):
        _build(
            timeline,
            pool,
            project,
            _master_clips(),
            captions=[
                {
                    "overlay_path": caption_path,
                    "timeline_start": 0.0,
                    "timeline_end": 1.0,
                    "source_in_frame": 0,
                    "segment_id": "sub_akshita_source-clip_0-1000_abcdef12",
                }
            ],
            program_channels={"1": 1, "2": 1},
        )


def test_caption_placement_failure_refuses_instead_of_dropping_the_card(monkeypatch):
    import library.tools.reel_placed_assets as placed_assets
    from library.tools import overlay_placement, reel_build

    caption_path = "/m/caption.mov"
    timeline, pool, project = _world()
    monkeypatch.setattr(placed_assets, "assert_placeable", lambda *_: None)
    monkeypatch.setattr(
        reel_build,
        "import_pool_item",
        lambda _pool, path, *_args, **_kwargs: _pool_item(path),
    )
    monkeypatch.setattr(
        overlay_placement,
        "place_overlay_segment",
        lambda *_args, **_kwargs: (False, "stub placement refusal"),
    )

    with pytest.raises(ReelBuildError, match="stub placement refusal"):
        _build(
            timeline,
            pool,
            project,
            _master_clips(),
            captions=[
                {
                    "overlay_path": caption_path,
                    "timeline_start": 0.0,
                    "timeline_end": 1.0,
                    "source_in_frame": 0,
                    "segment_id": "sub_akshita_source-clip_0-1000_abcdef12",
                }
            ],
            program_channels={"1": 1, "2": 1},
        )


# ── The three defects ──


def test_two_angles_get_two_picture_rows_and_two_named_speech_rows():
    """Defect 3: each speaker their own video AND audio row, named from
    the master - never "Video 1" / "Audio 2"."""
    timeline, pool, project = _world()
    _build(timeline, pool, project, _master_clips(), program_channels={"1": 1, "2": 1})
    assert timeline.GetTrackCount("video") == 2
    assert timeline.GetTrackCount("audio") == 2
    assert timeline.GetTrackName("video", 1) == "Akshita"
    assert timeline.GetTrackName("video", 2) == "Craig"
    assert timeline.GetTrackName("audio", 1) == "Akshita CH1"
    assert timeline.GetTrackName("audio", 2) == "Craig CH1"


def test_captain_override_window_check_uses_the_builds_measured_draw_gain(monkeypatch):
    """The override recheck must use the same 1.0 gain as the placed aim.

    Geo Podcast measured 1.0 while the machine fallback is 2.0. Using
    that fallback only for the captain's Pan=-8 recheck doubles the
    predicted vertical shift and falsely refuses a picture that covers
    the TV window at the measured gain.
    """
    from library.tools import reel_build

    received = {}

    def capture_override_recheck(*args, **kwargs):
        received.update(kwargs)
        return 0

    monkeypatch.setattr(
        reel_build, "apply_transform_overrides", capture_override_recheck
    )
    timeline, pool, project = _world()

    record = _build(
        timeline,
        pool,
        project,
        _master_clips(),
        program_channels={"1": 1, "2": 1},
        draw_gain=1.0,
    )

    assert received["draw_gain"] == pytest.approx(1.0)
    v1 = timeline.GetItemListInTrack("video", 1)
    a1 = timeline.GetItemListInTrack("audio", 1)
    v2 = timeline.GetItemListInTrack("video", 2)
    a2 = timeline.GetItemListInTrack("audio", 2)
    assert len(v1) == len(a1) == 1 and len(v2) == len(a2) == 1
    assert "akshita" in v1[0].GetName() and "craig" in v2[0].GetName()
    assert "akshita" in a1[0].GetName() and "craig" in a2[0].GetName()
    assert record["track_plan"]["material"]["angles"][0]["label"] == "Akshita"


def test_declared_angle_plan_limits_picture_rows_but_keeps_all_speech():
    """An E5 camera plan must drive picture placement independently of
    speech placement, or the reel keeps copying every master camera row."""
    timeline, pool, project = _world()
    clips = [
        _clip("video", 1, "Akshita", "Akshita", "/m/akshita.MXF", 0.0, 20.0),
        _clip("video", 2, "Craig", "Craig", "/m/craig.MXF", 0.0, 20.0),
        _clip("audio", 1, "Akshita CH1", "Akshita", "/m/akshita.MXF", 0.0, 20.0),
        _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF", 0.0, 20.0),
    ]
    row = {
        "op": "angle_plan",
        "anchor": {"kind": "reel"},
        "reel": FakeMoment.timeline_name,
        "params": {"camera": "Akshita", "min_shot_seconds": 3, "lead_frames": 0},
        "stated_by": "requester",
        "reason": "stay on the host",
    }
    record = _build(
        timeline,
        pool,
        project,
        clips,
        program_channels={"1": 1, "2": 1},
        edit_ledger_rows=[row],
    )
    assert timeline.GetTrackCount("video") == 1
    assert timeline.GetTrackCount("audio") == 2
    assert timeline.GetTrackName("video", 1) == "Akshita"
    assert timeline.GetTrackName("audio", 1) == "Akshita CH1"
    assert timeline.GetTrackName("audio", 2) == "Craig CH1"
    assert record["angle_plan"]["declared"] is True
    assert record["angle_plan"]["picture_placements"] == 1


def test_non_program_streams_are_deleted_on_the_spot_and_recorded():
    """Defect 1: the MXF's four streams reach placement, and only the
    recorded program stream stays - the rest are deleted and said.

    The spill is the live shape, not the return value's: the append
    returns the program item while a non-program copy lands on the
    next audio row. Enforcement reads the rows back, so the copy is
    found whatever the call admitted to."""
    timeline, pool, project = _world(audio_channels=(1, 2, 3, 4))
    record = _build(
        timeline, pool, project, _master_clips(), program_channels={"1": 1, "2": 1}
    )
    enforcement = record["stream_enforcement"]
    assert enforcement["checked"] == 8, "four streams over two placements"
    assert len(enforcement["deleted"]) == 6
    assert {d["row"] for d in enforcement["deleted"]} == {1, 2}, (
        "strays land on both rows and both are swept"
    )
    assert {tuple(sorted(d["placed_channels"])) for d in enforcement["deleted"]} == {
        (2,),
        (3,),
        (4,),
    }
    for index in (1, 2):
        items = timeline.GetItemListInTrack("audio", index)
        assert len(items) == 1
        import json as _json

        mapping = _json.loads(items[0].GetSourceAudioChannelMapping())
        assert mapping["track_mapping"]["1"]["channel_idx"] == [1], (
            "no stray stream survives on a speech row"
        )


def test_picture_links_to_speech_in_one_call_per_pair():
    """Defect 2: picture and speech travel together - one link call per
    pair, read back."""
    timeline, pool, project = _world()
    record = _build(
        timeline, pool, project, _master_clips(), program_channels={"1": 1, "2": 1}
    )
    pair_calls = [c for c in timeline.link_calls if len(c[0]) == 2 and c[1]]
    assert len(pair_calls) == 2
    assert len(record["link_groups"]) == 2
    for row in (1, 2):
        for item in timeline.GetItemListInTrack(
            "video", row
        ) + timeline.GetItemListInTrack("audio", row):
            assert item.GetLinkedItems(), (
                f"every a-roll item is linked, found {item.GetName()} alone"
            )


def test_seven_frame_audio_lead_links_same_angle_a_roll():
    """Reel 11's source-edge offset still links picture and speech.

    Craig's speech starts seven frames before his picture item. The
    placement entry point must join the overlapping items from the
    same angle despite their different start frames.
    """
    from library.tools.timeline_layout import TrackPlan, TrackSpec

    timeline, pool, project = _world()
    clips = _master_clips()
    clips[-1] = _clip("audio", 2, "Craig CH1", "Craig", "/m/craig.MXF", 9.7, 20.0)
    record = _build(timeline, pool, project, clips, program_channels={"1": 1, "2": 1})
    raw = record["track_plan"]
    plan = TrackPlan(
        video_tracks=[TrackSpec(**row) for row in raw["video_tracks"]],
        audio_tracks=[TrackSpec(**row) for row in raw["audio_tracks"]],
        material=raw.get("material", {}),
    )

    picture = timeline.GetItemListInTrack("video", 2)[0]
    speech = timeline.GetItemListInTrack("audio", 2)[0]
    assert picture.GetStart() == 240
    assert speech.GetStart() == 233
    assert picture.GetLinkedItems() == [speech]
    assert speech.GetLinkedItems() == [picture]
    report = verify_timeline(timeline, plan=plan)
    assert report["passed"]
    assert "aroll_linked" in report["checks_run"]
    assert not [v for v in report["violations"] if v["check"] == "aroll_unlinked"]


def test_caption_inside_speech_joins_one_three_group():
    """Defect 2 (captions): picture, speech and caption link in a single
    call - a later pair-call would break the group."""
    timeline, pool, project = _world()
    record = _build(
        timeline,
        pool,
        project,
        _master_clips(),
        captions=[_caption(2.0, 4.0)],
        program_channels={"1": 1, "2": 1},
    )
    assert timeline.GetTrackName("video", 3) == "Subtitles"
    triples = [c for c in timeline.link_calls if len(c[0]) == 3 and c[1]]
    assert len(triples) == 1, (
        f"expected one three-group link call, saw {timeline.link_calls}"
    )
    assert len(record["caption_links"]) == 1
    cap = timeline.GetItemListInTrack("video", 3)[0]
    assert len(cap.GetLinkedItems()) == 2, (
        "the group holds: nothing re-linked afterwards to break it"
    )


def test_sparse_overlay_rows_pack_with_no_empty_row_left():
    """Defect 4 as the verifier sees it: a semantic row with no
    transitions or explainer above it packs onto V4 - no blank V4/V5
    kept, and every surviving row named."""
    timeline, pool, project = _world()
    record = _build(
        timeline,
        pool,
        project,
        _master_clips(),
        captions=[_caption(2.0, 4.0)],
        semantic=[_semantic(0.0, 48)],
        program_channels={"1": 1, "2": 1},
    )
    assert timeline.GetTrackCount("video") == 4
    assert timeline.GetTrackName("video", 4) == "Semantic"
    for index in range(1, 5):
        assert timeline.GetItemListInTrack("video", index), (
            f"V{index} is empty and must have been deleted"
        )
    assert record["deleted_empty_tracks"] == [], (
        "packing means no empty row is ever created, so none is deleted"
    )


# ── The look keeps one picture row per speaker ──


def test_program_channels_prefer_the_catalog_then_the_master():
    """Known unknown, answered: the reel path reaches the recorded
    program stream through the catalog first and the live master's own
    speech rows second - and refuses when neither names one."""
    clips = [_clip("audio", 1, "Akshita CH1", "Akshita", "/m/a.MXF", 0.0, 10.0)]
    angles = [{"key": "1", "label": "Akshita", "track_index": 1}]
    assert resolve_reel_program_channels(angles, clips, "", explicit={"1": 3}) == {
        "1": 3
    }
    with pytest.raises(ReelBuildError, match="no recorded program stream"):
        resolve_reel_program_channels(angles, clips, "/tmp/no-such-project")
