import pytest
import json
import os
import tempfile
from unittest.mock import MagicMock
from library.tools.timeline_serializer import serialize_timeline_state, diff_timeline_states, _clean_dict

@pytest.fixture
def mock_resolve():
    resolve = MagicMock()
    pm = MagicMock()
    project = MagicMock()
    timeline = MagicMock()
    
    resolve.GetProjectManager.return_value = pm
    pm.GetCurrentProject.return_value = project
    project.GetCurrentTimeline.return_value = timeline
    
    timeline.GetName.return_value = "Test Timeline"
    timeline.GetStartFrame.return_value = 0
    timeline.GetEndFrame.return_value = 100
    timeline.GetStartTimecode.return_value = "01:00:00:00"
    
    def get_setting(name):
        if name == "timelineFrameRate": return "24.0"
        if name == "timelineResolutionWidth": return "1920"
        if name == "timelineResolutionHeight": return "1080"
        return ""
    timeline.GetSetting.side_effect = get_setting
    
    timeline.GetMarkers.return_value = {
        10: {"color": "Red", "name": "Marker 1", "note": "Test", "duration": 1, "customData": "data"}
    }
    
    timeline.GetTrackCount.side_effect = lambda t: 1 if t == "video" else 0
    timeline.GetTrackName.return_value = "V1"
    
    # Mock item
    item = MagicMock()
    item.GetUniqueId.return_value = "uuid-1234"
    item.GetName.return_value = "Clip 1"
    item.GetStart.return_value = 0
    item.GetEnd.return_value = 50
    item.GetDuration.return_value = 50
    item.GetSourceStartFrame.return_value = 0
    item.GetSourceEndFrame.return_value = 50
    item.GetLeftOffset.return_value = 0
    item.GetRightOffset.return_value = 0
    item.GetClipColor.return_value = "Blue"
    item.GetClipEnabled.return_value = True
    
    item.GetProperty.return_value = {
        "Pan": 1.0,
        "ZoomX": 1.5,
        "Opacity": 100.0
    }
    
    mpi = MagicMock()
    mpi.GetMediaId.return_value = "media-1"
    def get_clip_property(name):
        if name == "Clip Path": return "/path/to/clip.mov"
        return ""
    mpi.GetClipProperty.side_effect = get_clip_property
    item.GetMediaPoolItem.return_value = mpi
    def item_get_clip_property(name):
        if name == "Color Group": return "Group1"
        return ""
    item.GetClipProperty.side_effect = item_get_clip_property
    
    item.GetCDL.return_value = {"Slope": "1.0 1.0 1.0"}
    item.GetFusionCompCount.return_value = 0
    item.GetFusionCompNameList.return_value = []
    item.GetMarkers.return_value = {}
    item.GetColorGroup.return_value = ""
    
    timeline.GetItemListInTrack.return_value = [item]
    
    return resolve

def test_serialize_valid_json(mock_resolve):
    state = serialize_timeline_state(resolve_mock=mock_resolve)
    assert state["schema_version"] == "1.0"
    assert state["metadata"]["name"] == "Test Timeline"
    assert state["metadata"]["fps"] == 24.0
    assert state["metadata"]["resolution"] == [1920, 1080]
    
    assert len(state["tracks"]) == 1
    assert state["tracks"][0]["type"] == "video"
    assert state["tracks"][0]["name"] == "V1"
    
    clips = state["tracks"][0]["clips"]
    assert len(clips) == 1
    assert clips[0]["unique_id"] == "uuid-1234"
    assert clips[0]["name"] == "Clip 1"
    assert clips[0]["transform"]["ZoomX"] == 1.5
    assert clips[0]["color"]["cdl"] == {"Slope": "1.0 1.0 1.0"}

def test_deterministic_output(mock_resolve):
    state1 = serialize_timeline_state(resolve_mock=mock_resolve)
    state1["timestamp"] = "2024-01-01T00:00:00Z" # override timestamp
    
    state2 = serialize_timeline_state(resolve_mock=mock_resolve)
    state2["timestamp"] = "2024-01-01T00:00:00Z"
    
    assert json.dumps(state1, sort_keys=True) == json.dumps(state2, sort_keys=True)

def test_diff_function():
    old_state = {
        "tracks": [{
            "clips": [
                {"unique_id": "1", "name": "Clip A", "record_in": 0, "color": {"cdl": {"Slope": "1"}}},
                {"unique_id": "2", "name": "Clip B", "record_in": 10, "color": {"cdl": {"Slope": "1"}}}
            ]
        }]
    }
    
    new_state = {
        "tracks": [{
            "clips": [
                {"unique_id": "1", "name": "Clip A", "record_in": 5, "color": {"cdl": {"Slope": "2"}}},
                {"unique_id": "3", "name": "Clip C", "record_in": 20, "color": {"cdl": {"Slope": "1"}}}
            ]
        }]
    }
    
    with tempfile.NamedTemporaryFile('w', delete=False) as f1, tempfile.NamedTemporaryFile('w', delete=False) as f2:
        json.dump(old_state, f1)
        json.dump(new_state, f2)
        f1_name = f1.name
        f2_name = f2.name
        
    diff = diff_timeline_states(f1_name, f2_name)
    os.remove(f1_name)
    os.remove(f2_name)
    
    assert "Clip C" in diff["added_clips"]
    assert "Clip B" in diff["removed_clips"]
    
    moved = [m for m in diff["moved_clips"] if m["name"] == "Clip A"]
    assert len(moved) == 1
    assert moved[0]["old_in"] == 0
    assert moved[0]["new_in"] == 5
    
    grades = [g for g in diff["changed_grades"] if g["name"] == "Clip A"]
    assert len(grades) == 1
    assert grades[0]["old_cdl"] == {"Slope": "1"}
    assert grades[0]["new_cdl"] == {"Slope": "2"}

def test_merge_pipeline_state(mock_resolve):
    manifest = {"project": {"name": "Test"}}
    pipeline_data = {"vision_index": 1.0}
    
    with tempfile.NamedTemporaryFile('w', delete=False) as f1, tempfile.NamedTemporaryFile('w', delete=False) as f2:
        json.dump(manifest, f1)
        json.dump(pipeline_data, f2)
        f1_name = f1.name
        f2_name = f2.name
        
    state = serialize_timeline_state(
        manifest_path=f1_name,
        pipeline_data_path=f2_name,
        resolve_mock=mock_resolve
    )
    
    os.remove(f1_name)
    os.remove(f2_name)
    
    assert state["pipeline_state"]["assembly_manifest"] == manifest
    assert state["pipeline_state"]["pipeline_data"] == pipeline_data
