from types import SimpleNamespace

import pytest

from library.tools.reel_otio_placement import (
    OtioPlacementRefused,
    select_placement,
    size_imported_timeline_before_current,
    verify_imported_resolution,
)


def _plan(*, subtitle_segments=(), resolved_channels=None,
          suppressions=()):
    return SimpleNamespace(
        name="Reel 09",
        ledger_rows=[],
        suppressions=list(suppressions),
        suppressed_ids=[],
        placements_list=[],
        subtitle_segments=list(subtitle_segments),
        offset_links=[],
        freeze_tail=None,
        resolved_channels=dict(resolved_channels or {}),
        post_header=None,
        track_plan=None,
        video_row_by_angle={},
        speech_row_by_angle={},
        build_record={},
        cards=[],
    )


def test_auto_selects_otio_for_a_fully_representable_plan():
    mode, reasons = select_placement(
        "auto", _plan(resolved_channels={"1": 1, "2": 1}),
        target_resolution=(1080, 1920))

    assert mode == "otio"
    assert reasons == ()


def test_auto_uses_safe_import_sizing_for_any_project_default():
    mode, reasons = select_placement(
        "auto", _plan(resolved_channels={"1": 1}),
        target_resolution=(1080, 1920))

    assert mode == "otio"
    assert reasons == ()


@pytest.mark.parametrize(
    ("plan", "transitions", "reason"),
    [
        (_plan(), [object()], "transition elements"),
        (_plan(subtitle_segments=[{
            "container": "frames",
            "frames": {"dir": "/captions/segment-1"},
            "segment_id": "caption-1",
        }]), None, "image-sequence caption 'caption-1'"),
        (_plan(resolved_channels={"1": 2}), None, "program channel 2"),
    ],
    ids=("transitions", "image-sequence-captions", "program-channel"),
)
def test_auto_falls_back_for_every_known_unsupported_shape(
        plan, transitions, reason):
    mode, reasons = select_placement("auto", plan, transitions)

    assert mode == "append"
    assert any(reason in item for item in reasons)


def test_auto_reports_every_incompatibility_in_one_free_check():
    plan = _plan(
        subtitle_segments=[{
            "container": "frames",
            "frames": {"dir": "/captions/segment-1"},
            "segment_id": "caption-1",
        }],
        resolved_channels={"1": 2},
    )

    mode, reasons = select_placement("auto", plan, [object()])

    assert mode == "append"
    assert len(reasons) == 3
    assert "transition elements" in reasons[0]
    assert "image-sequence caption" in reasons[1]
    assert "program channel 2" in reasons[2]


def test_an_explicit_otio_refusal_precedes_media_pool_access():
    from library.tools.reel_build import build_reel_timeline

    class Project:
        def GetMediaPool(self):
            pytest.fail("compatibility must be checked before pool access")

    with pytest.raises(OtioPlacementRefused, match="transition elements"):
        build_reel_timeline(
            Project(), None, [], [], 24, 1080, 1920, "/project", {},
            prepared=_plan(), overlay_placements=[object()],
            placement_mode="otio")


@pytest.mark.parametrize(
    ("project_size", "expected"),
    [
        ((1080, 1920), "selected otio"),
        ((3840, 2160), "selected otio"),
    ],
    ids=("matching-resolution", "mismatched-resolution"),
)
def test_build_auto_selects_before_media_pool_access(
        project_size, expected, capsys):
    from library.tools.reel_build import build_reel_timeline

    class PoolReached(RuntimeError):
        pass

    class Project:
        def GetSetting(self, key):
            return {
                "timelineResolutionWidth": str(project_size[0]),
                "timelineResolutionHeight": str(project_size[1]),
            }[key]

        def GetMediaPool(self):
            raise PoolReached

    with pytest.raises(PoolReached):
        build_reel_timeline(
            Project(), None, [], [], 24, 1080, 1920, "/project", {},
            prepared=_plan(), placement_mode="auto")

    assert expected in capsys.readouterr().err


def test_imported_resolution_is_verified_without_setting_timeline_values():
    class Timeline:
        def __init__(self, events):
            self.events = events
            self.unique_id = "imported"
            self.settings = {
                "useCustomSettings": "0",
                "timelineResolutionWidth": "3840",
                "timelineResolutionHeight": "2160",
            }

        def GetSetting(self, key):
            self.events.append(("read", key))
            return self.settings[key]

        def GetUniqueId(self):
            return self.unique_id

        def SetSetting(self, key, value):
            self.events.append(("write", key, value))
            self.settings[key] = value
            return True

    class Project:
        def __init__(self, current):
            self.current = current

        def GetCurrentTimeline(self):
            return self.current

    class OtherTimeline:
        def GetUniqueId(self):
            return "other"

    events = []
    imported = Timeline(events)
    size_imported_timeline_before_current(
        Project(OtherTimeline()), "Reel 09", imported, 1080, 1920)
    assert imported.settings == {
        "useCustomSettings": "1",
        "timelineResolutionWidth": "1080",
        "timelineResolutionHeight": "1920",
    }
    assert [event[1] for event in events if event[0] == "write"] == [
        "useCustomSettings", "timelineResolutionWidth",
        "timelineResolutionHeight"]
    assert events.index(("write", "timelineResolutionHeight", "1920")) \
        < events.index(("read", "timelineResolutionWidth"))

    with pytest.raises(OtioPlacementRefused, match="made the OTIO timeline current"):
        size_imported_timeline_before_current(
            Project(imported), "Reel 09", imported, 1080, 1920)

    with pytest.raises(OtioPlacementRefused,
                       match=r"expected \(1920, 1080\)"):
        verify_imported_resolution(
            "Reel 09", SimpleNamespace(GetSetting=lambda key: {
                "timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}[key]),
            1920, 1080)
