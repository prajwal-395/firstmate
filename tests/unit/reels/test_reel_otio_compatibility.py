from types import SimpleNamespace

import pytest

from library.tools.reel_otio_placement import (
    OtioPlacementRefused,
    prove_mismatch_route,
    restore_project_default,
    select_placement,
    size_imported_timeline_before_current,
    switch_project_default,
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
        target_resolution=(1080, 1920),
        import_resolution=(1080, 1920))

    assert mode == "otio"
    assert reasons == ()


@pytest.mark.parametrize(
    ("import_resolution", "reason"),
    [
        (None, "project timeline resolution could not be read"),
    ],
    ids=("unknown-resolution",),
)
def test_auto_falls_back_before_placement_when_import_resolution_is_unsafe(
        import_resolution, reason):
    # An unreadable project default can prove no restore, so `auto`
    # falls back before placement. A READABLE mismatch is the mismatch
    # route's (`prove_mismatch_route`), not a fallback.
    mode, reasons = select_placement(
        "auto", _plan(resolved_channels={"1": 1}),
        target_resolution=(1080, 1920),
        import_resolution=import_resolution)

    assert mode == "append"
    assert any(reason in item for item in reasons)


def test_auto_selects_otio_on_a_readable_resolution_mismatch():
    # The mismatch route switches the project default transiently
    # around the import and restores it read-back, so a mismatch the
    # build can read is placed by OTIO, not by append.
    mode, reasons = select_placement(
        "auto", _plan(resolved_channels={"1": 1}),
        target_resolution=(1080, 1920),
        import_resolution=(3840, 2160))

    assert mode == "otio"
    assert reasons == ()


def test_explicit_otio_attempts_the_mismatch_route():
    # Explicit `otio` on a readable mismatch carries no refusal: the
    # route is attempted live (`prove_mismatch_route` first), and only
    # an unprovable route refuses.
    mode, reasons = select_placement(
        "otio", _plan(resolved_channels={"1": 1}),
        target_resolution=(1080, 1920),
        import_resolution=(3840, 2160))

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
        def GetSetting(self, key):
            return {"timelineResolutionWidth": "1080",
                    "timelineResolutionHeight": "1920"}[key]

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
        # A readable mismatch selects otio free and proves the route
        # live; this stub project cannot take the switch (no
        # SetSetting), so the probe falls back to append before the
        # pool is touched.
        ((3840, 2160), "fell back to append before placement"),
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
    # The size check reads first (and skips the writes when the import
    # already matches); the writes land in deadline order after it.
    assert events[0] == ("read", "timelineResolutionWidth")
    assert events[1] == ("read", "timelineResolutionHeight")
    first_write = events.index(
        ("write", "useCustomSettings", "1"))
    assert first_write == 2

    with pytest.raises(OtioPlacementRefused, match="made the OTIO timeline current"):
        size_imported_timeline_before_current(
            Project(Timeline([])), "Reel 09", Timeline([]), 1080, 1920)

    with pytest.raises(OtioPlacementRefused,
                       match=r"expected \(1920, 1080\)"):
        verify_imported_resolution(
            "Reel 09", SimpleNamespace(GetSetting=lambda key: {
                "timelineResolutionWidth": "1080",
                "timelineResolutionHeight": "1920"}[key]),
            1920, 1080)


def test_matching_import_needs_no_write_even_when_current():
    # Measured live 2026-10-08: the import makes the new timeline
    # current. Where the project default matches, the import already
    # reads back the reel size, so no setting is written - and the
    # settings-sync deadlock is never risked.
    reads = []

    class Timeline:
        def GetSetting(self, key):
            reads.append(key)
            return {"timelineResolutionWidth": "1080",
                    "timelineResolutionHeight": "1920"}[key]

        def SetSetting(self, *_args):
            pytest.fail("a matching import must not write timeline values")

    class Project:
        def GetCurrentTimeline(self):
            return Timeline()

    size_imported_timeline_before_current(
        Project(), "Reel 09", Timeline(), 1080, 1920)
    assert reads == ["timelineResolutionWidth",
                     "timelineResolutionHeight"]


def _mismatch_project():
    from tests.resolve_double import make_project
    return make_project(width=3840, height=2160, frame_rate=24)


def test_prove_mismatch_route_leaves_default_and_cursor_untouched():
    from tests.resolve_double import FakeTimeline
    project = _mismatch_project()
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    previous = pool.CreateEmptyTimeline("previous")
    project.SetCurrentTimeline(previous)

    prove_mismatch_route(project, "Reel 09", 1080, 1920)

    assert (project.GetSetting("timelineResolutionWidth"),
            project.GetSetting("timelineResolutionHeight")) == (
        "3840", "2160")
    assert project.GetCurrentTimeline() is previous


def test_prove_mismatch_route_is_a_noop_on_a_match():
    from tests.resolve_double import make_project
    project = make_project(width=1080, height=1920, frame_rate=24)
    writes = []
    original = project.SetSetting
    project.SetSetting = lambda key, value: (
        writes.append((key, value)) or original(key, value))

    prove_mismatch_route(project, "Reel 09", 1080, 1920)

    assert writes == []


def test_prove_mismatch_route_falls_back_without_a_park_target():
    project = _mismatch_project()

    with pytest.raises(OtioPlacementRefused, match="no timeline is current"):
        prove_mismatch_route(project, "Reel 09", 1080, 1920)

    assert (project.GetSetting("timelineResolutionWidth"),
            project.GetSetting("timelineResolutionHeight")) == (
        "3840", "2160")


def test_prove_mismatch_route_falls_back_when_the_default_will_not_switch():
    from tests.resolve_double import FakeTimeline
    project = _mismatch_project()
    pool = project.GetMediaPool()
    pool.next_timeline = FakeTimeline()
    previous = pool.CreateEmptyTimeline("previous")
    project.SetCurrentTimeline(previous)
    project.SetSetting = lambda _key, _value: False

    with pytest.raises(OtioPlacementRefused, match="would not switch"):
        prove_mismatch_route(project, "Reel 09", 1080, 1920)


def test_switch_reads_back_before_trusting_the_return():
    project = _mismatch_project()
    project.SetSetting = lambda _key, _value: True  # lies: writes nothing

    with pytest.raises(OtioPlacementRefused, match="did not read back"):
        switch_project_default(project, 1080, 1920)


def test_restore_names_the_values_to_fix_by_hand():
    project = _mismatch_project()
    project.SetSetting = lambda _key, _value: False

    with pytest.raises(OtioPlacementRefused, match="restore it by hand"):
        restore_project_default(project, "Reel 09", (3840, 2160))
