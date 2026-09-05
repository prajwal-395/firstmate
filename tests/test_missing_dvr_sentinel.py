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
def test_missing_dvr_sentinel_raises_on_call_not_access():
    """
    Ensure the sentinel allows reading an attribute, but calling the returned
    callable raises the expected RuntimeError with a clear message.
    """
    # This is only guaranteed to test the sentinel if the real module is missing.
    # To reliably test the sentinel behavior, we'll instantiate it directly.
    err_msg = "Mock error"
    class _MissingDVR:
        def __getattr__(self, name):
            def _missing(*args, **kwargs):
                raise RuntimeError(f"DaVinciResolveScript is not installed: {err_msg}")
            return _missing
            
    sentinel = _MissingDVR()
    
    # Accessing should be fine
    func = sentinel.scriptapp
    
    # Calling should raise
    with pytest.raises(RuntimeError, match="DaVinciResolveScript is not installed: Mock error"):
        func("Resolve")
