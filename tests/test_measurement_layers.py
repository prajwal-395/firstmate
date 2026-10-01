"""Per-layer measurement cache for the semantic profile.

The defect: any change to step 1.03's eleven identity files deleted
every profile and re-measured every clip whole (project 001: 17 clips,
3,167 s of model time, 29 times in September 2026). These pin the three
things the fix rests on: a layer's key moves with its OWN code and no
one else's, a compose from cached layers calls no model and yields the
profile the measurement did, and the store is source memory's.
"""

import json
import textwrap
from pathlib import Path
from unittest.mock import patch

from library.tools import source_memory
from library.tools.analysis import measurement_layers as ml
from library.tools.analysis import vision_pipeline_v3 as vp

MODULE = textwrap.dedent('''
    """Module docstring."""
    from library.tools.helper import shared
    LIMIT = 5

    def _clamp(x):
        return min(x, LIMIT)

    def windows(x):
        """Windows pass."""
        return _clamp(x) + shared(x)

    def objects(x):
        return x * 2
''')


def _tree(tmp_path, module=MODULE, helper="def shared(x):\n    return x\n"):
    (tmp_path / "library" / "tools").mkdir(parents=True, exist_ok=True)
    (tmp_path / "library" / "tools" / "helper.py").write_text(helper)
    path = tmp_path / "mod.py"
    path.write_text(module)
    return {layer: ml.method_digest(path, [layer], tmp_path)
            for layer in ("windows", "objects")}


def test_a_layer_key_moves_with_its_own_code_and_no_one_elses(tmp_path):
    base = _tree(tmp_path)

    # An objects-only edit leaves the windows layer alone.
    edited = _tree(tmp_path, MODULE.replace("x * 2", "x * 3"))
    assert edited["objects"] != base["objects"]
    assert edited["windows"] == base["windows"]

    # A helper two names down, and an imported repo module, are both
    # part of the windows identity without anyone listing them.
    assert _tree(tmp_path, MODULE.replace("LIMIT = 5", "LIMIT = 6")
                 )["windows"] != base["windows"]
    moved = _tree(tmp_path, helper="def shared(x):\n    return -x\n")
    assert moved["windows"] != base["windows"]
    assert moved["objects"] == base["objects"]

    # Prose is not method.
    prose = _tree(tmp_path, MODULE.replace("Windows pass.", "Reworded.")
                  .replace("return x * 2", "return x * 2  # comment"))
    assert prose == base


def test_the_store_is_source_memory(tmp_path, monkeypatch):
    for env in ({}, {"PIPELINE_VEP_HOME": str(tmp_path / "home")},
                {"XDG_DATA_HOME": str(tmp_path / "xdg")},
                {"PIPELINE_SOURCE_MEMORY_ROOT": str(tmp_path / "mem")}):
        for name in ("PIPELINE_SOURCE_MEMORY_ROOT", "PIPELINE_VEP_HOME",
                     "XDG_DATA_HOME"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        assert ml.store_root() == source_memory.memory_root()


class _CountingAnalyzer:
    """Canned answers that count every model call."""

    def __init__(self):
        self.calls = 0

    def analyze_with_retry(self, prompt, parse_fn, images=None, video=None,
                           max_tokens=512, label="pass", audio=None):
        self.calls += 1
        if label.startswith("Objects"):
            result = [{"label": "mug", "appearances": [[0.0, 10.0]]}]
            return result, json.dumps(result), 0.2
        result = {
            "actions": [{"action": "talks", "start": 0.0, "end": 9.0}],
            "scene": [{"start": 0.0, "end": 10.0, "description": "room"}],
            "camera": [{"start": 0.0, "end": 10.0, "mode": "static"}],
            "assessment": {"content_type": "person_talking_to_camera",
                           "primary_subject_visible": [[0, 10]]},
        }
        return result, json.dumps(result), 0.3


def test_a_compose_from_cached_layers_calls_no_model(tmp_path):
    source = tmp_path / "clip.mov"
    source.write_bytes(b"not really a movie")
    meta = {"clip_id": "clip", "file_path": str(source), "duration_s": 10.0,
            "fps": 30.0, "resolution": [1920, 1080]}
    frames = [{"timestamp": 0.0, "path": "/tmp/f0.jpg"}]
    video_clips = [{"index": 0, "start": 0.0, "end": 10.0,
                    "path": "/tmp/stub.mp4", "has_audio": False}]
    methods = {"windows": "w1", "objects": "o1", "picture": "p1"}

    def compose(analyzer, cache, measurable=True):
        soft = [{"start": 1.0, "end": 2.0}] if measurable else None
        with patch.object(vp.picture_quality, "measure_soft_picture",
                          return_value=soft) as picture:
            profile = vp.analyze_clip(
                analyzer, meta, frames, video_clips, "", None,
                str(tmp_path / "cache"), layer_cache=cache)
        return profile, picture.call_count

    uncached, _ = compose(_CountingAnalyzer(), None)

    first = _CountingAnalyzer()
    measured, picture_calls = compose(first, ml.LayerCache.for_source(
        source, methods, {}, root=tmp_path / "mem"))
    assert first.calls and picture_calls == 1

    again = _CountingAnalyzer()
    reused, picture_calls = compose(again, ml.LayerCache.for_source(
        source, methods, {}, root=tmp_path / "mem"), measurable=False)
    assert again.calls == 0 and picture_calls == 0

    layers = {}
    for profile in (measured, reused):
        layers[id(profile)] = profile["analysis_metadata"].pop(
            "measurement_layers")
        for entry in profile["actions"]:
            entry.pop("analysis_time_s")
    for entry in uncached["actions"]:
        entry.pop("analysis_time_s")
    assert {v["outcome"] for v in layers[id(measured)].values()} == {
        "measured"}
    assert {v["outcome"] for v in layers[id(reused)].values()} == {"reused"}
    # The same profile, whether measured with no cache, measured into
    # one, or composed back out of it.
    for profile in (measured, reused, uncached):
        profile["analysis_metadata"].pop("window_inference_wall_s")
    assert measured == uncached
    assert reused == measured

    # A moved objects method re-measures objects alone.
    partial = _CountingAnalyzer()
    compose(partial, ml.LayerCache.for_source(
        source, {**methods, "objects": "o2"}, {}, root=tmp_path / "mem"))
    assert partial.calls == 1
