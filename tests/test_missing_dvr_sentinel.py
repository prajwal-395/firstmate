import pytest
from library.tools.execution import apply_fusion_comps as afc

def test_missing_dvr_sentinel_can_be_monkeypatched(monkeypatch):
    """
    Ensure the sentinel object bound to `dvr` when DaVinciResolveScript 
    is missing allows monkeypatching of attributes without raising an error 
    on attribute access. It should only raise when the attribute is called.
    """
    # Assuming dvr is the sentinel if the test is run in CI without DaVinciResolveScript.
    # If the real module is present, patching is obviously fine.
    
    # This should not raise an exception
    monkeypatch.setattr(afc.dvr, "scriptapp", lambda name: "mocked")
    
    # Verify the patch worked
    assert afc.dvr.scriptapp("Resolve") == "mocked"
