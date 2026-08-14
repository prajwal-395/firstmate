"""Step 1.03 collects clip profiles - it must not destroy them.

The rename from file stem to catalog clip_id runs `os.rename`, which
overwrites its destination.  A catalog entry with no clip_id produced
`clip_profile_.json` and silently replaced whatever already sat there.
"""

import json
import os

from library.steps.step_1_03_semantic_analysis.step import _rename_profile


def _write(path, payload):
    with open(path, "w") as f:
        json.dump(payload, f)


def test_rename_moves_a_profile_to_its_catalog_clip_id(tmp_path):
    analysis = str(tmp_path)
    _write(os.path.join(analysis, "clip_profile_IMG_1806.json"), {"a": 1})

    _rename_profile(analysis, "IMG_1806", "clip_001", "")

    assert not os.path.exists(os.path.join(analysis, "clip_profile_IMG_1806.json"))
    with open(os.path.join(analysis, "clip_profile_clip_001.json")) as f:
        assert json.load(f) == {"a": 1}


def test_missing_clip_id_leaves_the_profile_alone(tmp_path):
    """This is what wrote `clip_profile_.json` over an existing profile."""
    analysis = str(tmp_path)
    _write(os.path.join(analysis, "clip_profile_IMG_1806.json"), {"a": 1})
    _write(os.path.join(analysis, "clip_profile_.json"), {"someone": "else"})

    _rename_profile(analysis, "IMG_1806", "", "")

    with open(os.path.join(analysis, "clip_profile_IMG_1806.json")) as f:
        assert json.load(f) == {"a": 1}
    with open(os.path.join(analysis, "clip_profile_.json")) as f:
        assert json.load(f) == {"someone": "else"}


def test_rename_refuses_to_overwrite_another_clips_profile(tmp_path):
    analysis = str(tmp_path)
    _write(os.path.join(analysis, "clip_profile_IMG_1806.json"), {"a": 1})
    _write(os.path.join(analysis, "clip_profile_clip_001.json"), {"b": 2})

    _rename_profile(analysis, "IMG_1806", "clip_001", "")

    with open(os.path.join(analysis, "clip_profile_clip_001.json")) as f:
        assert json.load(f) == {"b": 2}
    assert os.path.exists(os.path.join(analysis, "clip_profile_IMG_1806.json"))


def test_rename_is_a_no_op_when_nothing_was_written(tmp_path):
    _rename_profile(str(tmp_path), "IMG_1806", "clip_001", "_video_only")
    assert os.listdir(tmp_path) == []
