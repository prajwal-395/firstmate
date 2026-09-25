"""Step 1.03 must not re-analyse footage it has already analysed.

This file used to cover `_rename_profile`, a helper that renamed each
fresh profile from the media file's stem to the catalog's `clip_XXX` id.
Four tests, all passing, for a function that **never fired**: it looked
for `clip_profile_<stem>.json` while `vision_pipeline_v3` writes
`clip_profile_<stem>_v3.json`. The tests proved the helper was careful,
not that it ran.

What that hid is the expensive half. The "already analysed?" check
compared catalog clip_ids (`clip_001`) against the names on disk
(`IMG_1806_v3`), so it never matched and every clip was re-analysed on
every run - 45 to 90 minutes of local vision on project 001, redone from
scratch each time and, under the runner's old 600s step timeout, killed
about four clips in and retried forever.

Both ends now use the media file's stem, which is also the analyser's own
cache key.
"""

import os
import subprocess
import sys

import pytest

from library.steps.step_1_03_semantic_analysis.step import (
    _profile_stems,
    _run_clip_vision,
)


def _touch(d, name):
    open(os.path.join(d, name), "w").write("{}")


def test_v3_profiles_are_recognised(tmp_path):
    """The regression. `_v3` is the suffix the analyser actually writes."""
    _touch(tmp_path, "clip_profile_IMG_1806_v3.json")
    _touch(tmp_path, "clip_profile_IMG_1812_v3.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806", "IMG_1812"}


def test_partial_video_only_profiles_do_not_count(tmp_path):
    _touch(tmp_path, "clip_profile_IMG_1806_video_only.json")
    assert _profile_stems(str(tmp_path)) == set()


def test_the_empty_clip_id_artifact_is_ignored(tmp_path):
    """`clip_profile_.json` exists in the wild - the old rename wrote it."""
    _touch(tmp_path, "clip_profile_.json")
    _touch(tmp_path, "clip_profile_IMG_1806_v3.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806"}


def test_clip_vision_chatter_never_reaches_step_stdout(capfd):
    """A per-clip vision child prints progress to its own stdout
    ("Model loaded/bound in ...", window lines). Run uncaptured, that
    chatter inherits the step's stdout and is prepended to the step's
    own JSON, so the runner rejects the whole step as "Step produced
    invalid JSON" after every clip was analysed (rung-0a proof run:
    11 profiles collected, step failed). The helper captures both
    streams and forwards them to stderr, where the runner streams them
    as the step's log - the step's stdout stays empty until its own
    `json.dump`.

    `capfd`, not `capsys`: a child inherits the OS file descriptor,
    bypassing Python-level `sys.stdout` replacement, so only an
    fd-level capture sees the pollution - with `capsys` this test
    would pass against the buggy uncaptured call.
    """
    child = [
        sys.executable, "-c",
        "import sys;"
        " print('  Model loaded/bound in 0.0s');"
        " print('{\"profiles\": 1}');"
        " print('a warning line', file=sys.stderr)",
    ]
    _run_clip_vision(child)
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "Model loaded/bound in 0.0s" in captured.err
    assert '{"profiles": 1}' in captured.err
    assert "a warning line" in captured.err


def test_clip_vision_failure_still_raises_for_the_caller():
    """`check` semantics are unchanged: a nonzero exit raises
    `CalledProcessError` so the step skips the clip exactly as before.
    """
    with pytest.raises(subprocess.CalledProcessError):
        _run_clip_vision(
            [sys.executable, "-c", "import sys; sys.exit(1)"])
