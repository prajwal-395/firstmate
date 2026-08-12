import os
import pytest
from unittest.mock import MagicMock, patch
from library.tools.builtin_effect_loader import import_customized_effect

@pytest.fixture
def mock_setting_content():
    return '''{
        Tools = ordered() {
            AdvancedCameraShake1 = MacroOperator {
                Inputs = ordered() {
                    Input1 = InstanceInput {
                        SourceOp = "CameraShake1",
                        Source = "overall_magnitude",
                    },
                },
                Tools = ordered() {
                    CameraShake1 = CameraShake {
                        Inputs = {
                            overall_magnitude = Input { Value = 0.5, },
                        }
                    }
                }
            }
        }
    }'''

def test_import_customized_effect(mock_setting_content):
    mock_clip = MagicMock()
    
    with patch('library.tools.builtin_effect_loader.load_effect_setting', return_value=mock_setting_content):
        def fake_import(path):
            with open(path, 'r') as f:
                mock_clip.imported_content = f.read()
            return True
            
        mock_clip.ImportFusionComp.side_effect = fake_import
        
        overrides = {"overall_magnitude": 0.8}
        
        success = import_customized_effect(mock_clip, "advanced_camera_shake", overrides)
        assert success
        
        assert mock_clip.ImportFusionComp.called
        assert "overall_magnitude = Input { Value = 0.8, }" in mock_clip.imported_content

def test_import_customized_effect_prefixed(mock_setting_content):
    mock_clip = MagicMock()
    
    with patch('library.tools.builtin_effect_loader.load_effect_setting', return_value=mock_setting_content):
        def fake_import(path):
            with open(path, 'r') as f:
                mock_clip.imported_content = f.read()
            return True
            
        mock_clip.ImportFusionComp.side_effect = fake_import
        
        overrides = {"CameraShake1.overall_magnitude": 0.9}
        
        success = import_customized_effect(mock_clip, "advanced_camera_shake", overrides)
        assert success
        
        assert mock_clip.ImportFusionComp.called
        assert "overall_magnitude = Input { Value = 0.9, }" in mock_clip.imported_content
