"""M3e saliency lane (`library/tools/attention_saliency.py`): helper-path
regression. The lane's `_helper`/`_measure` must reach the shared helper
`library.tools.vision_measure` the way M3 (`person_measurements`) and M3d
(`person_regions`) do - not `library.steps.step_1_04_temporal_index`,
which has no such module (ImportError). Would catch: the lane merging
with an unimportable helper path while every test only exercised the
pure `assemble`, so the build broke on first real use.
"""
from library.tools import attention_saliency


def test_saliency_helper_path_is_the_shared_vision_measure(monkeypatch):
    """Would catch: `_helper`/`_measure` importing `vision_measure`
    from `step_1_04_temporal_index` (ImportError) instead of the shared
    `library.tools.vision_measure`. The shared module itself is real -
    only its compiler is stubbed, so no swiftc runs; the import lines
    under test execute for real, unlike a wholesale `_helper` stub."""
    from library.tools import vision_measure as shared

    monkeypatch.setattr(
        shared, "ensure_helper", lambda source, name: ("stub-helper", None))
    assert attention_saliency._helper() == "stub-helper"
    assert attention_saliency._measure([], "stub-helper") == []
