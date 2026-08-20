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

from library.steps.step_1_03_semantic_analysis.step import _profile_stems


def _touch(d, name):
    open(os.path.join(d, name), "w").write("{}")


def test_v3_profiles_are_recognised(tmp_path):
    """The regression. `_v3` is the suffix the analyser actually writes."""
    _touch(tmp_path, "clip_profile_IMG_1806_v3.json")
    _touch(tmp_path, "clip_profile_IMG_1812_v3.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806", "IMG_1812"}


def test_legacy_unsuffixed_profiles_are_recognised(tmp_path):
    """Older runs left profiles with no suffix; they still count."""
    _touch(tmp_path, "clip_profile_IMG_1806.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806"}


def test_partial_video_only_profiles_do_not_count(tmp_path):
    _touch(tmp_path, "clip_profile_IMG_1806_video_only.json")
    assert _profile_stems(str(tmp_path)) == set()


def test_the_empty_clip_id_artifact_is_ignored(tmp_path):
    """`clip_profile_.json` exists in the wild - the old rename wrote it."""
    _touch(tmp_path, "clip_profile_.json")
    _touch(tmp_path, "clip_profile_IMG_1806_v3.json")
    assert _profile_stems(str(tmp_path)) == {"IMG_1806"}


def test_an_empty_directory_analyses_everything(tmp_path):
    assert _profile_stems(str(tmp_path)) == set()


def test_the_stem_matches_what_the_analyser_names_its_output():
    """Bind the key to the producer, not to a fixture.

    `vision_pipeline_v3` builds its output name from `clip_path.stem`.
    If that ever changes, this check should be what says so.
    """
    import re
    from pathlib import Path

    src = (Path(__file__).resolve().parent.parent / "library" / "tools"
           / "analysis" / "vision_pipeline_v3.py").read_text()
    assert re.search(r'clip_profile_\{clip_path\.stem\}_v3\.json', src), (
        "vision_pipeline_v3 no longer names profiles "
        "clip_profile_<stem>_v3.json; _profile_stems must follow it."
    )
