import os
import json
import pytest
from unittest.mock import MagicMock, patch
from library.tools.marker_feedback import (
    ChangeRequest,
    get_all_feedback,
    save_snapshot,
    load_snapshot,
    get_new_feedback,
    acknowledge_feedback,
    clear_processed_markers
)

@pytest.fixture
def mock_resolve_api():
    with patch('library.tools.marker_feedback._connect_resolve') as mock_connect:
        mock_resolve = MagicMock()
        mock_connect.return_value = mock_resolve
        
        mock_pm = MagicMock()
        mock_resolve.GetProjectManager.return_value = mock_pm
        
        mock_project = MagicMock()
        mock_pm.GetCurrentProject.return_value = mock_project
        
        mock_timeline = MagicMock()
        mock_project.GetCurrentTimeline.return_value = mock_timeline
        
        yield mock_timeline

def test_timeline_markers(mock_resolve_api):
    # Set up mock timeline
    mock_resolve_api.GetMarkers.return_value = {
        100: {"color": "Blue", "name": "Note 1", "note": "Make this louder", "duration": 1, "customData": '{"key":"val"}'}
    }
    mock_resolve_api.GetTrackCount.return_value = 0
    
    requests = get_all_feedback()
    assert len(requests) == 1
    req = requests[0]
    assert req.frame_position == 100
    assert req.instruction == "Make this louder"
    assert req.source == "timeline_marker"
    assert req.marker_color == "Blue"
    assert req.custom_data == {"key": "val"}

def test_clip_markers_and_comments(mock_resolve_api):
    mock_resolve_api.GetMarkers.return_value = {}
    
    # Mock tracks: 1 video track
    def get_track_count(track_type):
        return 1 if track_type == "video" else 0
    mock_resolve_api.GetTrackCount.side_effect = get_track_count
    
    # Mock items
    mock_item = MagicMock()
    mock_item.GetStart.return_value = 50
    mock_item.GetEnd.return_value = 150
    mock_item.GetLeftOffset.return_value = 0
    mock_item.GetDuration.return_value = 100
    mock_item.GetName.return_value = "clip1.mp4"
    mock_item.GetProperty.side_effect = lambda prop: "Fix color" if prop == "Comments" else None
    mock_item.GetMarkers.return_value = {
        10: {"color": "Red", "name": "Marker", "note": "Cut here", "duration": 1, "customData": ""}
    }
    mock_item.GetClipColor.return_value = "Orange"
    mock_item.GetFlagList.return_value = ["Cyan"]
    
    mock_resolve_api.GetItemListInTrack.return_value = [mock_item]
    
    requests = get_all_feedback()
    
    # We expect 2 requests: 1 comment, 1 clip marker
    assert len(requests) == 2
    
    comment_req = [r for r in requests if r.source == "clip_comment"][0]
    assert comment_req.frame_position == 50
    assert comment_req.instruction == "Fix color"
    assert comment_req.clip_name == "clip1.mp4"
    
    marker_req = [r for r in requests if r.source == "clip_marker"][0]
    assert marker_req.frame_position == 60  # 50 + 10
    assert marker_req.instruction == "Cut here"
    assert marker_req.clip_name == "clip1.mp4"
    assert marker_req.clip_color == "Orange"
    assert marker_req.clip_flags == ["Cyan"]

def test_snapshot_round_trip(tmp_path, mock_resolve_api):
    mock_resolve_api.GetMarkers.return_value = {
        200: {"color": "Pink", "name": "", "note": "Awesome", "duration": 1, "customData": ""}
    }
    mock_resolve_api.GetTrackCount.return_value = 0
    
    snapshot_path = str(tmp_path / "snap.json")
    save_snapshot(snapshot_path)
    
    loaded = load_snapshot(snapshot_path)
    assert len(loaded) == 1
    assert loaded[0].frame_position == 200
    assert loaded[0].instruction == "Awesome"

def test_get_new_feedback(tmp_path, mock_resolve_api):
    snapshot_path = str(tmp_path / "snap.json")
    
    # Snapshot state
    mock_resolve_api.GetMarkers.return_value = {
        100: {"color": "Blue", "name": "", "note": "Old note", "duration": 1, "customData": ""}
    }
    mock_resolve_api.GetTrackCount.return_value = 0
    save_snapshot(snapshot_path)
    
    # New state
    mock_resolve_api.GetMarkers.return_value = {
        100: {"color": "Blue", "name": "", "note": "Old note", "duration": 1, "customData": ""},
        150: {"color": "Green", "name": "", "note": "New note", "duration": 1, "customData": ""}
    }
    
    new_fb = get_new_feedback(snapshot_path)
    assert len(new_fb) == 1
    assert new_fb[0].frame_position == 150
    assert new_fb[0].instruction == "New note"

def test_acknowledge_and_clear_feedback(tmp_path, mock_resolve_api):
    acknowledge_feedback(100, "Done this")
    mock_resolve_api.AddMarker.assert_called_with(
        frameId=100, color='Green', name='Done', note='Done this', duration=1, customData=''
    )
    
    snapshot_path = str(tmp_path / "snap.json")
    mock_resolve_api.GetMarkers.return_value = {
        100: {"color": "Blue", "name": "", "note": "Fix", "duration": 1, "customData": ""}
    }
    mock_resolve_api.GetTrackCount.return_value = 0
    save_snapshot(snapshot_path)
    
    clear_processed_markers(snapshot_path)
    mock_resolve_api.DeleteMarkerAtFrame.assert_called_with(100)
