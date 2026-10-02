# Unparsed action windows

Tests: `tests/unit/picture/test_picture_view.py`.

MEASURED on project 001's 29 Aug run: IMG_1809 window [10,20] and
IMG_1820 window [0,10] both returned ``actions: []``, and the picture
view SILENTLY OMITTED them.  The creative director saw twenty seconds of
footage as absent rather than as unmeasured.

ROOT CAUSE: both windows WERE analysed but the VLM response could not be
parsed, and ``parse_json_object`` returned ``{}`` - which was then
recorded identically to a window where genuinely nothing happened.

An answer that came back without a key is not an answer of ``[]``.

These tests pin the three-part fix:
  1. The data distinguishes unparsed from genuinely empty.
  2. The sentinel block reaches the picture view as a visible row.
  3. The picture view reports unparsed windows explicitly.

Profiles written before the fix carry no `parse_error`, so their unparsed windows stay indistinguishable from genuinely empty ones: the fix makes future runs diagnosable and does not repair the past.
