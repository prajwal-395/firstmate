import pytest
from unittest.mock import MagicMock
from library.tools.resolve_lock import assert_current_timeline, ResolveRaceError

def test_assert_current_timeline_fails_on_mismatch():
    project = MagicMock()
    expected = MagicMock()
    expected.GetUniqueId.return_value = "expected-123"
    
    current = MagicMock()
    current.GetUniqueId.return_value = "rogue-456"
    
    project.GetCurrentTimeline.return_value = current
    
    with pytest.raises(ResolveRaceError) as exc:
        assert_current_timeline(project, expected)
        
    assert "Mutator changed it" in str(exc.value)

def test_assert_current_timeline_passes_on_match():
    project = MagicMock()
    expected = MagicMock()
    expected.GetUniqueId.return_value = "expected-123"
    
    # current matches expected
    current = MagicMock()
    current.GetUniqueId.return_value = "expected-123"
    
    project.GetCurrentTimeline.return_value = current
    
    # Should not raise
    assert_current_timeline(project, expected)
