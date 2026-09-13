import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# This file's tests drive a FAKE Resolve. The fake used to be installed
# into sys.modules unconditionally at IMPORT time, and pytest imports
# every test file before it runs any of them - so the fake reached the
# whole session and displaced the real module on a machine that has
# Resolve. Eighteen tests that need a real connection skipped because of
# it, reporting "Resolve is not running" on a machine where it was.
mock_dvr = MagicMock()

# The step directory this file once appended to sys.path now lives in
# tests/conftest.py; the sibling import below reaches it by absolute
# package path instead of a bare name bound by collection order.
from library.steps.step_6_01_render.resolve_build_timeline import (  # noqa: E402
    build_timeline,
    _preflight_check,
    _allocate_audio_tracks,
)
from library.tools import resolve_bin_layout  # noqa: E402

@pytest.fixture(autouse=True)
def _the_fake_resolve_is_this_files_own(monkeypatch):
    """Swap the fake in for the length of one test, and back out after.

    `monkeypatch.setitem` restores whatever was there - the real module
    on a machine with Resolve, the session stub on one without.
    """
    monkeypatch.setitem(sys.modules, 'DaVinciResolveScript', mock_dvr)


@pytest.fixture
def mock_resolve():
    resolve = MagicMock()
    mock_dvr.scriptapp.return_value = resolve
    
    project_manager = MagicMock()
    resolve.GetProjectManager.return_value = project_manager
    
    project = MagicMock()
    project_manager.GetCurrentProject.return_value = project
    project_manager.LoadProject.return_value = project
    
    media_pool = MagicMock()
    project.GetMediaPool.return_value = media_pool
    
    timeline = MagicMock()
    timeline.GetUniqueId.return_value = "12345"
    media_pool.CreateEmptyTimeline.return_value = timeline
    project.GetCurrentTimeline.return_value = timeline

    # Settings are a STORE, not a MagicMock that says yes to everything.
    # `build_timeline` now reads the resolution back off Resolve rather
    # than trusting that SetSetting worked - it printed a tick carrying
    # the manifest's own numbers for the life of the pipeline while the
    # timeline was a different shape - so a double that cannot echo a
    # setting cannot exercise the code that depends on echoing one.
    def _settings_store(obj):
        store = {}

        def set_setting(key, value):
            store[str(key)] = str(value)
            return True

        def get_setting(key=None):
            if key is None:
                return dict(store)
            return store.get(str(key), "")

        obj.SetSetting.side_effect = set_setting
        obj.GetSetting.side_effect = get_setting
        return store

    _settings_store(project)
    _settings_store(timeline)
    
    # Keep track of track counts to avoid infinite loops
    track_counts = {"video": 1, "audio": 1}
    def get_track_count(track_type):
        return track_counts.get(track_type, 1)
    def add_track(track_type):
        track_counts[track_type] = track_counts.get(track_type, 1) + 1
        return True
        
    timeline.GetTrackCount.side_effect = get_track_count
    timeline.AddTrack.side_effect = add_track
    
    mock_item = MagicMock()
    mock_item.GetStart.return_value = 0
    mock_item.GetEnd.return_value = 300 # 10 seconds at 30 fps
    timeline.GetItemListInTrack.return_value = [mock_item]
    
    root_folder = MagicMock()
    media_pool.GetRootFolder.return_value = root_folder

    # A MagicMock says "yes" to everything, including "are you still
    # rendering?" - so `segment_renderer.render_segment` polled a mock
    # that never finished and slept out its entire timeout, once per
    # frame grab. That is what hung the suite: not a deadlock, a long
    # poll. Ten minutes of wall clock for 2.75 seconds of CPU.
    #
    # It only started happening when the visual QA router was fixed to
    # find clips where they actually live: `plan_qa_checks` had been
    # reading a top-level "clips" key that has never existed, so it
    # returned zero frame grabs and this whole path lay dormant.
    project.IsRenderingInProgress.return_value = False

    # A unit test with a mocked Resolve must not launch a REAL subprocess
    # at the real application. `build_timeline` shells out to
    # apply_fusion_comps.py, and `test_media_import_logic` patches
    # os.path.exists to True for everything - so the guard that normally
    # skips a missing script let it launch for real, against the live
    # Resolve, and wait forever. That hung the whole suite three times at
    # ~58%: ten minutes of wall clock for 2.75 seconds of CPU.
    #
    # The renderer is now bounded too (FUSION_SUBPROCESS_TIMEOUT_S), so
    # this can no longer hang either way - but a unit test should not be
    # spawning processes at all, and a 600s bound is not a test runtime.
    fusion_proc = MagicMock()
    fusion_proc.returncode = 0
    fusion_proc.stdout = ""
    fusion_proc.stderr = ""
    patcher = patch("library.steps.step_6_01_render.resolve_build_timeline.subprocess.run",
                    return_value=fusion_proc)
    patcher.start()

    yield {
        'resolve': resolve,
        'project_manager': project_manager,
        'project': project,
        'media_pool': media_pool,
        'timeline': timeline,
        'root_folder': root_folder,
        'subprocess_run': patcher,
    }

    patcher.stop()

@pytest.fixture
def sample_manifest():
    return {
        "project": {
            "name": "Test Project",
            "resolution": [1920, 1080],
            "frame_rate": 30,
            "duration_seconds": 10.0
        },
        "tracks": {
            "V1": {
                "clips": [
                    {
                        "source_file": "test_v1.mov",
                        "source_in": 0.0,
                        "source_out": 2.0,
                        "timeline_in_frame": 0
                    }
                ]
            }
        }
    }

def test_resolve_connection_failure():
    """Test error handling when Resolve is not connected."""
    mock_dvr.scriptapp.return_value = None
    
    manifest = {
        "project": {"name": "Test", "resolution": [1080, 1920]},
        "tracks": {"V1": {"clips": [{"source_file": "test.mov", "timeline_in_frame": 0}]}}
    }
    
    with patch('os.path.exists', return_value=True):
        result = build_timeline(manifest)
        
    assert not result["success"]
    assert "Cannot connect to DaVinci Resolve. Is it running?" in result["errors"][0]

def test_media_import_logic(mock_resolve, sample_manifest):
    """Test that _import_to_folder creates subfolders and imports media."""
    media_pool = mock_resolve['media_pool']
    root_folder = mock_resolve['root_folder']
    
    subfolder = MagicMock()
    media_pool.AddSubFolder.return_value = subfolder
    root_folder.GetSubFolderList.return_value = []
    media_pool.ImportMedia.return_value = [MagicMock()]
    
    with patch('os.path.exists', return_value=True):
        build_timeline(sample_manifest)
        
    media_pool.AddSubFolder.assert_any_call(
        root_folder, resolve_bin_layout.SOURCE_BIN)
    media_pool.ImportMedia.assert_called()

def test_clip_placement_calculations(mock_resolve, sample_manifest):
    """Test frame math and track routing for V1 placement."""
    media_pool = mock_resolve['media_pool']
    
    pool_item = MagicMock()
    root_folder = mock_resolve['root_folder']
    root_folder.GetClipList.return_value = [pool_item]
    
    # We need to simulate that the pool clip path matches the source_file
    def get_clip_prop(prop):
        if prop == "File Path":
            return "test_v1.mov"
        return ""
    pool_item.GetClipProperty.side_effect = get_clip_prop
    
    placed_item = MagicMock()
    placed_item.GetDuration.return_value = 60
    placed_item.GetStart.return_value = 0
    media_pool.AppendToTimeline.return_value = [placed_item]
    
    with patch('os.path.exists', return_value=True):
        build_timeline(sample_manifest)
    
    append_args = media_pool.AppendToTimeline.call_args[0][0][0]
    assert append_args["startFrame"] == 0
    assert append_args["endFrame"] == 60  # 2.0s * 30fps
    assert append_args["trackIndex"] == 1
    assert append_args["recordFrame"] == 0

def test_two_pass_architecture(mock_resolve, sample_manifest):
    """Test that V1 is placed first, then extra audio tracks are added, then audio clips are placed."""
    sample_manifest["tracks"]["A2"] = {
        "clips": [
            {
                "source_file": "test_music.wav",
                "source_in": 0.0,
                "duration": 5.0
            }
        ]
    }
    
    timeline = mock_resolve['timeline']
    media_pool = mock_resolve['media_pool']
    
    call_order = []
    
    track_counts = {"video": 1, "audio": 1}
    def side_effect_add_track(track_type):
        call_order.append(f"AddTrack_{track_type}")
        track_counts[track_type] = track_counts.get(track_type, 1) + 1
        return True
        
    def side_effect_append(items):
        track_idx = items[0].get("trackIndex")
        call_order.append(f"Append_{track_idx}")
        m = MagicMock()
        m.GetDuration.return_value = 150
        m.GetStart.return_value = 0
        return [m]
        
    timeline.AddTrack.side_effect = side_effect_add_track
    media_pool.AppendToTimeline.side_effect = side_effect_append
    
    def side_effect_get_track_count(track_type):
        return track_counts.get(track_type, 1)
    timeline.GetTrackCount.side_effect = side_effect_get_track_count
    
    pool_item_v1 = MagicMock()
    pool_item_a2 = MagicMock()
    
    def get_clip_prop_v1(prop):
        if prop == "File Path":
            return "test_v1.mov"
        return ""
        
    def get_clip_prop_a2(prop):
        if prop == "File Path":
            return "test_music.wav"
        return ""
        
    pool_item_v1.GetClipProperty.side_effect = get_clip_prop_v1
    pool_item_a2.GetClipProperty.side_effect = get_clip_prop_a2
    
    root_folder = mock_resolve['root_folder']
    root_folder.GetClipList.return_value = [pool_item_v1, pool_item_a2]
    
    with patch('os.path.exists', return_value=True):
        build_timeline(sample_manifest)
        
    assert "Append_1" in call_order
    assert "Append_2" in call_order
    assert call_order.index("Append_1") < call_order.index("Append_2")

def test_allocate_audio_tracks():
    """Test the standalone SFX overlap calculation."""
    sfx_clips = [
        {"timeline_in_frame": 0, "timeline_out_frame": 30},
        {"timeline_in_frame": 15, "timeline_out_frame": 45},
        {"timeline_in_frame": 35, "timeline_out_frame": 60}
    ]
    
    allocations = _allocate_audio_tracks(sfx_clips, base_track_index=3)
    
    assert len(allocations) == 3
    assert allocations[0][1] == 3
    assert allocations[1][1] == 4
    assert allocations[2][1] == 3

def test_audio_markers_are_the_fallback_not_the_mix(mock_resolve, sample_manifest):
    """A cyan marker is a note asking a human to set a level, not a level.

    Levels reach the timeline through the OTIO round trip
    (library/tools/execution/deliver_audio_mix.py). This manifest plans
    music automation and gives it no A2 clip to sit on, so the route
    declines - and the markers that come back say UNAPPLIED, so nobody
    reads them as a mix. The master limiter stays a marker in every case:
    it is a master BUS setting, and no clip-level route reaches it.
    """
    sample_manifest["audio_mix"] = {
        "master_limiter": {
            "enabled": True,
            "threshold_db": -1.5
        },
        "music_automation": [
            {
                "timeline_start": 2.0,
                "target_level_db": -18,
                "music_behavior": "background"
            }
        ]
    }
    
    timeline = mock_resolve['timeline']
    media_pool = mock_resolve['media_pool']
    root_folder = mock_resolve['root_folder']
    
    pool_item = MagicMock()
    root_folder.GetClipList.return_value = [pool_item]
    
    def get_clip_prop(prop):
        if prop == "File Path":
            return "test_v1.mov"
        return ""
    pool_item.GetClipProperty.side_effect = get_clip_prop
    
    placed_item = MagicMock()
    placed_item.GetDuration.return_value = 60
    placed_item.GetStart.return_value = 0
    media_pool.AppendToTimeline.return_value = [placed_item]
    
    with patch('os.path.exists', return_value=True):
        result = build_timeline(sample_manifest)

    assert result["audio_mix_delivery"]["delivered"] is False
    assert "no A2 music clip" in result["audio_mix_delivery"]["reason"]
    assert timeline.AddMarker.call_count >= 2

    timeline.AddMarker.assert_any_call(
        0, "Purple", "Master Limiter: -1.5dBTP", "Set the master track limiter to this threshold", 1
    )

    # 2.0s * 30fps = frame 60. The wording is the point: an unapplied
    # target, not a "target level" that reads like one was applied.
    timeline.AddMarker.assert_any_call(
        60, "Cyan", "UNAPPLIED target: -18dB (background)",
        "The pipeline could not write this level; set it by hand", 1
    )


def test_generator_overlay_lands_on_plan_row(mock_resolve, sample_manifest):
    """Test that a generator overlay is placed on the plan's generator row
    via a transparent carrier.

    This is the acceptance test for generator routing: it proves the builder
    creates the carrier, imports it, and calls AppendToTimeline with the
    plan row and the correct frame math. No live Resolve needed.

    The row is plan-derived, not V5: this manifest has an a-roll and
    nothing else, so the SOP's fixed order puts generators on V2. A
    hardcoded V5 here was defect 4's shape (rows from constants).
    """
    # Add a generator overlay to the manifest
    sample_manifest["generator_overlays"] = [
        {
            "overlay_id": "gen_001",
            "effect_name": "fireworks",
            "target_block_position": 1,
            "timeline_start": 2.0,
            "timeline_end": 5.0,
            "composite_mode": "screen",
        },
    ]

    media_pool = mock_resolve['media_pool']
    timeline = mock_resolve['timeline']
    root_folder = mock_resolve['root_folder']

    # Pool item for V1 clip (existing pattern)
    pool_item_v1 = MagicMock()
    def get_clip_prop_v1(prop):
        if prop == "File Path":
            return "test_v1.mov"
        return ""
    pool_item_v1.GetClipProperty.side_effect = get_clip_prop_v1

    # Pool item returned by the carrier import
    carrier_pool_item = MagicMock()
    carrier_pool_item.GetName.return_value = "transparent_1920x1080_30fps.mov"
    carrier_pool_item.GetClipProperty.return_value = ""

    # Track the GetClipList result from the root folder scan
    root_folder.GetClipList.return_value = [pool_item_v1]

    # The carrier is imported via ImportMedia inside _ensure_transparent_carrier.
    # First call: V1 folder import. Later calls: Generators folder import.
    import_call_count = [0]
    def import_media_side_effect(paths):
        import_call_count[0] += 1
        # V1 import returns the V1 pool item; Generators import returns carrier
        if any('transparent' in str(p) for p in paths):
            return [carrier_pool_item]
        return [pool_item_v1]
    media_pool.ImportMedia.side_effect = import_media_side_effect

    # Track calls to AppendToTimeline to verify V5 placement
    placed_v1_item = MagicMock()
    placed_v1_item.GetDuration.return_value = 60
    placed_v1_item.GetStart.return_value = 0

    placed_v5_item = MagicMock()

    append_calls = []
    def append_side_effect(items):
        info = items[0]
        append_calls.append(info)
        if info.get("trackIndex") == 2:
            return [placed_v5_item]
        return [placed_v1_item]
    media_pool.AppendToTimeline.side_effect = append_side_effect

    # Subfolder management
    gen_subfolder = MagicMock()
    gen_subfolder.GetName.return_value = "Generators"
    root_folder.GetSubFolderList.return_value = []
    media_pool.AddSubFolder.return_value = gen_subfolder

    with patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('subprocess.run') as mock_ffmpeg:
        # ffmpeg succeeds
        mock_ffmpeg.return_value = MagicMock(returncode=0, stderr="", stdout="")
        build_timeline(sample_manifest, project_folder="/tmp/test_project")

    # ── Verify the generator row was created ──
    # The builder should have requested the plan's rows: V1 exists by
    # default, so reaching the generator row V2 needs 1 more video track.
    track_counts = {}
    for call in timeline.AddTrack.call_args_list:
        t = call[0][0]
        track_counts[t] = track_counts.get(t, 0) + 1
    assert track_counts.get("video", 0) >= 1, (
        f"Expected >= 1 AddTrack('video') call to reach the generator row, got {track_counts}"
    )

    # ── Verify AppendToTimeline was called with the plan row (V2 here) ──
    v5_appends = [c for c in append_calls if c.get("trackIndex") == 2]
    assert len(v5_appends) == 1, (
        f"Expected 1 generator-row append, got {len(v5_appends)}. "
        f"All appends: {[c.get('trackIndex') for c in append_calls]}"
    )

    v5_call = v5_appends[0]
    # Frame math: 2.0s to 5.0s = 3.0s * 30fps = 90 frames duration
    assert v5_call["endFrame"] == 90, f"Expected 90 frames, got {v5_call['endFrame']}"
    # Timeline position: 2.0s * 30fps = 60
    assert v5_call["recordFrame"] == 60, f"Expected recordFrame=60, got {v5_call['recordFrame']}"
    assert v5_call["mediaType"] == 1, "generator row must be video-only (mediaType=1)"
    assert v5_call["mediaPoolItem"] is carrier_pool_item, "generator row must use the transparent carrier"

    # ── Verify composite mode was set ──
    placed_v5_item.SetProperty.assert_any_call('CompositeMode', 5)  # 5 = Screen


def test_multiple_generators_all_land_on_plan_row(mock_resolve, sample_manifest):
    """Multiple generators each get their own carrier clip on the plan's
    generator row (V2 for this a-roll-only manifest)."""
    sample_manifest["generator_overlays"] = [
        {
            "overlay_id": "gen_001",
            "effect_name": "fireworks",
            "target_block_position": 1,
            "timeline_start": 0.0,
            "timeline_end": 3.0,
            "composite_mode": "screen",
        },
        {
            "overlay_id": "gen_002",
            "effect_name": "snow",
            "target_block_position": 2,
            "timeline_start": 5.0,
            "timeline_end": 8.0,
            "composite_mode": "add",
        },
    ]

    media_pool = mock_resolve['media_pool']
    root_folder = mock_resolve['root_folder']

    pool_item_v1 = MagicMock()
    pool_item_v1.GetClipProperty.side_effect = lambda p: "test_v1.mov" if p == "File Path" else ""
    root_folder.GetClipList.return_value = [pool_item_v1]

    carrier_pool_item = MagicMock()
    carrier_pool_item.GetClipProperty.return_value = ""
    def import_media_side_effect(paths):
        if any('transparent' in str(p) for p in paths):
            return [carrier_pool_item]
        return [pool_item_v1]
    media_pool.ImportMedia.side_effect = import_media_side_effect

    placed_v1 = MagicMock()
    placed_v1.GetDuration.return_value = 60
    placed_v1.GetStart.return_value = 0
    placed_v5_items = [MagicMock(), MagicMock()]
    v5_idx = [0]

    append_calls = []
    def append_side_effect(items):
        info = items[0]
        append_calls.append(info)
        if info.get("trackIndex") == 2:
            item = placed_v5_items[min(v5_idx[0], len(placed_v5_items) - 1)]
            v5_idx[0] += 1
            return [item]
        return [placed_v1]
    media_pool.AppendToTimeline.side_effect = append_side_effect

    root_folder.GetSubFolderList.return_value = []
    media_pool.AddSubFolder.return_value = MagicMock()

    with patch('os.path.exists', return_value=True), \
         patch('os.makedirs'), \
         patch('subprocess.run', return_value=MagicMock(returncode=0, stderr="", stdout="")):
        build_timeline(sample_manifest, project_folder="/tmp/test_project")

    v5_appends = [c for c in append_calls if c.get("trackIndex") == 2]
    assert len(v5_appends) == 2, f"Expected 2 generator-row appends, got {len(v5_appends)}"

    # First generator: 0-3s = 90 frames at frame 0
    assert v5_appends[0]["endFrame"] == 90
    assert v5_appends[0]["recordFrame"] == 0
    # Second generator: 5-8s = 90 frames at frame 150
    assert v5_appends[1]["endFrame"] == 90
    assert v5_appends[1]["recordFrame"] == 150

    # Composite modes: screen=5, add=1
    placed_v5_items[0].SetProperty.assert_any_call('CompositeMode', 5)
    placed_v5_items[1].SetProperty.assert_any_call('CompositeMode', 1)


def test_no_generators_does_not_create_v5(mock_resolve, sample_manifest):
    """Without generator_overlays, no V5 track is created."""
    media_pool = mock_resolve['media_pool']
    timeline = mock_resolve['timeline']
    root_folder = mock_resolve['root_folder']

    pool_item = MagicMock()
    pool_item.GetClipProperty.side_effect = lambda p: "test_v1.mov" if p == "File Path" else ""
    root_folder.GetClipList.return_value = [pool_item]

    placed = MagicMock()
    placed.GetDuration.return_value = 60
    placed.GetStart.return_value = 0
    media_pool.AppendToTimeline.return_value = [placed]

    with patch('os.path.exists', return_value=True):
        build_timeline(sample_manifest)

    # No V5 appends should exist
    for call in media_pool.AppendToTimeline.call_args_list:
        items = call[0][0]
        for item in items:
            assert item.get("trackIndex") != 5, "V5 should not be used without generators"

def test_loud_banner_prints_on_qa_failure_but_not_fatal(mock_resolve, sample_manifest, capsys):
    """Test that a QA failure prints the loud banner but leaves success unchanged."""
    media_pool = mock_resolve['media_pool']
    root_folder = mock_resolve['root_folder']
    
    pool_item = MagicMock()
    root_folder.GetClipList.return_value = [pool_item]
    
    def get_clip_prop(prop):
        if prop == "File Path":
            return "test_v1.mov"
        return ""
    pool_item.GetClipProperty.side_effect = get_clip_prop
    
    placed_item = MagicMock()
    placed_item.GetDuration.return_value = 60
    placed_item.GetStart.return_value = 0
    media_pool.AppendToTimeline.return_value = [placed_item]
    
    # Mock the full_timeline_qa to return a failure
    from library.tools.timeline_qa import QAReport, QACheck
    
    report = QAReport(station="full_sweep", passed=False)
    report.checks.append(QACheck(name="mock_loud_check", passed=False, expected="foo", actual="bar"))
    
    with patch('os.path.exists', return_value=True), \
         patch('library.steps.step_6_01_render.resolve_build_timeline.run_full_timeline_qa',
               return_value=report):
        result = build_timeline(sample_manifest)
        
    assert result["success"] is True, "QA failures should NOT be fatal yet."
    assert len(result["qa_failures"]) == 1
    assert result["qa_failures"][0]["check"] == "mock_loud_check"
    
    # Check that the loud banner actually printed to stderr
    # Since we can't easily capture sys.stderr inside the function if it uses file=sys.stderr directly,
    # wait, capsys can capture it.
    captured = capsys.readouterr()
    assert "QA CHECK FAILURE(S)" in captured.err
    assert "mock_loud_check" in captured.err


def test_a_project_that_will_not_hold_the_shape_fails_the_build(
        mock_resolve, sample_manifest):
    """A vertical edit must never ship as landscape in silence.

    The OTIO mix round trip re-imports the timeline and the rebuilt one
    takes the PROJECT's resolution, so the shape has to be confirmed on
    the project. It is confirmed by READING IT BACK: `SetSetting`
    returning True is a claim, `GetSetting` is the evidence. Project 001
    delivered a 1920x1080 master of a 1080x1920 edit and every structural
    check passed on it - duration, framerate, audio streams, and frame
    occupancy at 100%.
    """
    project = mock_resolve['project']
    sample_manifest["project"]["resolution"] = [1080, 1920]

    # A project that accepts the write and keeps its own value anyway -
    # exactly what a stale render preset looks like from the outside.
    def _refuse(key, value):
        return True

    project.SetSetting.side_effect = _refuse
    project.GetSetting.side_effect = lambda key=None: (
        {"timelineResolutionWidth": "1920",
         "timelineResolutionHeight": "1080"}.get(str(key), "")
        if key is not None else {})

    with patch('os.path.exists', return_value=True):
        result = build_timeline(sample_manifest)

    assert not result.get("success")
    joined = " ".join(result.get("errors", []))
    assert "will not hold the timeline shape" in joined, joined
    # It names the key, what was asked for, and what Resolve reports.
    assert "timelineResolutionWidth" in joined
    assert "asked 1080" in joined and "reads 1920" in joined, joined
