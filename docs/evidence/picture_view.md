# view:picture sees the whole clip

Tests: `tests/test_picture_view.py`.

`analysis.scene` is `scene[]` rendered as prose, and on project 001
`scene[]` describes 374.2 s of 807.0 s - 46.4%.  `IMG_1816_v3` is 188.6
seconds, supplies seven of the eleven A-roll blocks in the finished cut,
and is described for 18.9 of them.  Five of the sixteen source windows
the video actually plays fall outside the described range; all sixteen
fall inside the vision pass's per-window action records, which reach the
last second of all seventeen clips.

`view:picture` is the reading of those records - what happens, in which
clip, between which two seconds - and 2.01, 2.02, 3.02, 4.02, 4.03 and
4.04 all declare it.

**It is not a substitute for `scene[]`.**  `scene[]` says WHERE (location,
type, lighting, notable features); the action windows say WHAT HAPPENS.
The view goes beside `analysis.scene`, never in place of it, and a step
that drops the place axis fails below.

Rows are keyed by the CATALOG clip id wherever a routed clip list makes
that join possible, because that is the id every other table in a
planning step's context uses (AGENTS.md 10.1).

What a scene boundary should MEAN is a separate, open question (#225);
why `scene[]` covers 46.4% is #302.  Neither is touched here.
