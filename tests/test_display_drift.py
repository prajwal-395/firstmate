"""Display drift: a hand edit announces itself before the rebuild.

For every class whose displays live in files, this test snapshots a
fixture project, makes the shallow edit by hand, and shows the
pre-run check flagging it with the owning class and the deep path -
then shows the untouched project checking silent. Resolve-side
displays (a moved timeline item, a hand-graded node) have no file
carrier the pipeline wrote, so no fingerprint can witness them:
those two classes refuse through `edit_depth` instead (proven in
`tests/test_edit_depth.py`), and the per-class reason is stated in
`display_drift`'s docstring.
"""

import json
import os

import pytest

from library.tools import display_drift

# edit_class -> a display file the pipeline writes and a rebuild eats.
DRIFT_CASES = {
    "wording": "subtitle_plans/a_subtitles.json",
    "clip_timing": "pipeline_output/steps/5_04_compile_manifest/manifest.json",
    "overlay_position": "pipeline_output/steps/4_05_render_subtitles/box.json",
    "structure": "pipeline_output/review/reel_proposals_v2.json",
    "assets": "pipeline_output/steps/5_04_compile_manifest/manifest.json",
    "audio_levels": "pipeline_output/steps/5_02_audio_mix/mix.otio",
    "mg_content": "pipeline_output/steps/4_06_render_motion_graphics/mg.json",
}


def _write(root, relpath, document):
    path = os.path.join(root, relpath)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if isinstance(document, (dict, list)):
        with open(path, "w", encoding="utf-8") as handle:
            json.dump(document, handle)
    else:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(str(document))
    return path


@pytest.mark.parametrize("edit_class", sorted(DRIFT_CASES))
def test_hand_edit_flags_with_owner_and_deep_path(tmp_path, edit_class,
                                                  capsys):
    root = str(tmp_path)
    relpath = DRIFT_CASES[edit_class]
    _write(root, relpath, {"value": "pipeline wrote this"})
    report = display_drift.snapshot(root)
    assert report["files"] >= 1
    # Silence while nothing moved.
    quiet = display_drift.check(root)
    assert quiet["drifted"] == [] and quiet["vanished"] == []
    assert capsys.readouterr().err == ""
    # The shallow edit: a hand on the display file.
    _write(root, relpath, {"value": "hand changed this"})
    flagged = display_drift.check(root)
    assert flagged["drifted"] == [relpath]
    err = capsys.readouterr().err
    assert "DISPLAY DRIFT" in err
    assert relpath in err and edit_class in err
    assert "deep path" in err


def test_vanished_file_is_named(tmp_path, capsys):
    root = str(tmp_path)
    _write(root, "subtitle_plans/a_subtitles.json", {"cards": []})
    display_drift.snapshot(root)
    os.remove(os.path.join(root, "subtitle_plans/a_subtitles.json"))
    report = display_drift.check(root)
    assert report["vanished"] == ["subtitle_plans/a_subtitles.json"]
    assert "gone since snapshot" in capsys.readouterr().err
