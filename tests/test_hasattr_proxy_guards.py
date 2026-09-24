import builtins
import pytest
from unittest.mock import patch, Mock

def test_four_hasattr_guards_removed():
    """
    Test that the hasattr guards have been removed.
    hasattr is an illusion on Resolve proxies, so we must not use it.
    """
    from library.tools.fusion_macro_loader import apply_macro_to_transition
    from library.tools.neural_engine import apply_super_scale

    original_hasattr = builtins.hasattr

    def fake_hasattr(obj, name):
        if getattr(obj, "_is_resolve_proxy", False):
            raise RuntimeError(f"Code used hasattr({name}) on a proxy!")
        return original_hasattr(obj, name)

    class FakeProxy:
        _is_resolve_proxy = True
        
        def GetProperty(self, *args):
            return True
        
        def ImportFusionComp(self, *args):
            return True
            
        def GetMediaPoolItem(self, *args):
            return self
            
        def SetClipProperty(self, *args):
            return True
            
        def GetName(self):
            return "Fake"

    with patch("builtins.hasattr", side_effect=fake_hasattr):
        proxy = FakeProxy()

        # 1. fusion_macro_loader
        apply_macro_to_transition(proxy, {"file_path": "/fake"}, 100)

        # 2. neural_engine
        apply_super_scale(proxy)
