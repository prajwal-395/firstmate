import os
import sys
import pytest
from unittest.mock import MagicMock, patch

# Mock DaVinciResolveScript before importing the module
mock_dvr = MagicMock()
sys.modules['DaVinciResolveScript'] = mock_dvr

# Add library path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '../library/steps/step_6_01_render')))

from resolve_build_timeline import build_timeline, _preflight_check, _allocate_sfx_tracks

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
    media_pool.CreateEmptyTimeline.return_value = timeline
    
    # Keep track of track counts to avoid infinite loops
    track_counts = {"video": 1, "audio": 1}
    def get_track_count(track_type):
        return track_counts.get(track_type, 1)
    def add_track(track_type):
        track_counts[track_type] = track_counts.get(track_type, 1) + 1
        return True
        
    timeline.GetTrackCount.side_effect = get_track_count
    timeline.AddTrack.side_effect = add_track
    
    root_folder = MagicMock()
    media_pool.GetRootFolder.return_value = root_folder
    
    return {
        'resolve': resolve,
        'project_manager': project_manager,
        'project': project,
        'media_pool': media_pool,
        'timeline': timeline,
        'root_folder': root_folder
    }

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
        "project": {"name": "Test"},
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
        
    media_pool.AddSubFolder.assert_any_call(root_folder, "V1")
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
    media_pool.AppendToTimeline.return_value = [placed_item]
    
    with patch('os.path.exists', return_value=True):
        build_timeline(sample_manifest)
    
    append_args = media_pool.AppendToTimeline.call_args[0][0][0]
    assert append_args["startFrame"] == 0
    assert append_args["endFrame"] == 60  # 2.0s * 30fps
    assert append_args["trackIndex"] == 1
    assert append_args["recordFrame"] == 0
    assert "mediaType" not in append_args

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
    
    def side_effect_add_track(track_type):
        call_order.append(f"AddTrack_{track_type}")
        return True
        
    def side_effect_append(items):
        track_idx = items[0].get("trackIndex")
        call_order.append(f"Append_{track_idx}")
        return [MagicMock()]
        
    timeline.AddTrack.side_effect = side_effect_add_track
    media_pool.AppendToTimeline.side_effect = side_effect_append
    
    def side_effect_get_track_count(track_type):
        return 1
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

def test_allocate_sfx_tracks():
    """Test the standalone SFX overlap calculation."""
    sfx_clips = [
        {"timeline_in_frame": 0, "timeline_out_frame": 30},
        {"timeline_in_frame": 15, "timeline_out_frame": 45},
        {"timeline_in_frame": 35, "timeline_out_frame": 60}
    ]
    
    allocations = _allocate_sfx_tracks(sfx_clips, base_track_index=3)
    
    assert len(allocations) == 3
    assert allocations[0][1] == 3
    assert allocations[1][1] == 4
    assert allocations[2][1] == 3
