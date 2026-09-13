import os
import json
import tempfile
import pytest

from library.tools.fusion.engine import CompEngine
from library.tools.fusion.effects import fx
from library.tools.custom_asset_bank import (
    find_clip_assets,
    get_custom_asset,
    save_custom_asset,
    list_custom_assets,
    get_asset_bank_dir,
)

def test_engine_produces_valid_comp():
    engine = CompEngine(clip_dur=100, width=1080, height=1920)
    engine.add(fx.zoom(100, start=1.0, mid=1.04, end=1.03))
    engine.add(fx.grade(gain=1.1, contrast=0.1, saturation=1.2))
    
    comp_content = engine.serialize()
    
    assert "Composition {" in comp_content
    assert "Transform1 = Transform {" in comp_content
    assert "BrightnessContrast1 = BrightnessContrast {" in comp_content
    assert "MediaOut1 = MediaOut {" in comp_content
    # check zoom keys
    assert "Size = Input {" in comp_content
    assert "BezierSpline" in comp_content

def test_fx_blocks_compose_correctly():
    engine = CompEngine(clip_dur=50, width=1080, height=1920)
    
    # 1. Zoom
    engine.add(fx.zoom(50, start=1.0, mid=1.04, end=1.03))
    # 2. Grade
    engine.add(fx.grade(gain=1.05))
    # 3. Grain
    engine.add(fx.grain(power=0.5, size=1.5))
    
    comp_content = engine.serialize()
    
    assert "Transform1" in comp_content
    assert "BrightnessContrast1" in comp_content
    assert "FilmGrain1" in comp_content
    
    # Check that BrightnessContrast1's Input is connected to Transform1's Output
    assert 'BrightnessContrast1' in comp_content
    # FilmGrain1 connected to BrightnessContrast1
    assert 'SourceOp = "BrightnessContrast1"' in comp_content
    assert 'SourceOp = "Transform1"' in comp_content

def test_custom_asset_roundtrip():
    with tempfile.TemporaryDirectory() as temp_dir:
        # Create a comp
        comp_content = "Composition { test = true }"
        
        # Save it
        path = save_custom_asset(temp_dir, "test_preset", comp_content)
        
        # Check it exists
        assert os.path.exists(path)
        assert os.path.dirname(path) == get_asset_bank_dir(temp_dir)
        
        # List it
        assets = list_custom_assets(temp_dir)
        assert "test_preset" in assets
        
        # Get it
        retrieved_path = get_custom_asset(temp_dir, "test_preset")
        assert retrieved_path == path
        
        # Verify content
        with open(retrieved_path, "r") as f:
            assert f.read() == comp_content

def test_apply_fusion_comps_uses_engine_path(monkeypatch, tmp_path):
    # Testing that apply_fusion_comps uses CompEngine by mocking
    import library.tools.execution.apply_fusion_comps as afc
    
    manifest_data = {
        "project_dir": str(tmp_path),
        "tracks": {
            "V1": {
                "clips": [
                    {"source_file": "test.mov", "label": "clip_0"}
                ]
            }
        },
        "vfx": [
            {
                "timeline_start": 0,
                "params": {
                    "shake_x": 0.05,
                    "shake_y": 0.05,
                    "chromatic_aberration": True
                }
            }
        ]
    }
    
    manifest_file = tmp_path / "assembly_manifest.json"
    manifest_file.write_text(json.dumps(manifest_data))
    
    # Mock Resolve environment for apply_fusion_comps
    class MockTimelineClip:
        def GetStart(self): return 0
        def GetEnd(self): return 100
        def GetMediaPoolItem(self): return MockMediaPoolItem()
        def GetFusionCompNameList(self): return []
        def ImportFusionComp(self, path): return True
        def DeleteFusionCompByName(self, name): pass
    
    class MockMediaPoolItem:
        def GetClipProperty(self, prop):
            if prop == "File Path": return "test.mov"
            if prop == "Frames": return "100"
            # A real MediaPoolItem states its stored frame; the applier
            # refuses a comp where Resolve will not state one, so the
            # mock states one like production does.
            if prop == "Resolution": return "1080x1920"
            return None
            
    class MockTimeline:
        def GetUniqueId(self): return str(id(self))
        def GetSetting(self, name): return "30"
        def GetItemListInTrack(self, track_type, index):
            return [MockTimelineClip()]
            
    class MockProject:
        def __init__(self):
            self._current_timeline = MockTimeline()
        def GetCurrentTimeline(self): return self._current_timeline
        def SetCurrentTimeline(self, tl): self._current_timeline = tl; return True
    class MockProjectManager:
        def GetCurrentProject(self): return MockProject()
        
    class MockResolve:
        def GetProjectManager(self): return MockProjectManager()
    
    monkeypatch.setattr(afc.dvr, "scriptapp", lambda x: MockResolve())
    
    # Run apply_fusion_comps
    result = afc.apply_fusion_comps(manifest_data, str(tmp_path))
    
    # Should succeed
    assert result is True
    
    # We expect custom asset to be saved by the new engine path
    banked = find_clip_assets(str(tmp_path), "clip_0")
    assert len(banked) == 1
    custom_asset_path = banked[0]
    assert os.path.exists(custom_asset_path)
    
    with open(custom_asset_path, "r") as f:
        content = f.read()
        assert "ShakeTransform" in content
        # `chromatic_aberration` draws NOTHING and must not: Fusion
        # registers no tool of that name, so the node this once emitted
        # was a comp Resolve loaded without it. The real route is
        # DaVinci's own shipped macro through `fusion_macro_loader`.
        assert "ChromaticAberration" not in content
