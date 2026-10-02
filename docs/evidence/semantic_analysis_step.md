# Step 1.03 re-analysed footage it had already analysed

Tests: `tests/unit/picture/test_semantic_analysis_step.py`.

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
