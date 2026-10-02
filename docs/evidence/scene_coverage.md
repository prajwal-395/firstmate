# scene coverage

Test: `tests/unit/picture/test_picture_view.py`.

`scene[]` describes 46.4% of 001's footage, and no reader could see the gap.

Issue #302: `scene[]` covers 374.2 s of 807.0 s. The vision pass returns
one segment for fifteen of seventeen clips - IMG_1816 is 188.6 s and is
described for 18.9 of them - and the segments were stored verbatim and
rendered as prose with no account of the range they do not cover. Step
2.01's trace on the run of record: "I chose those moments blind to their
picture."

The fix is honest coverage, not invented coverage: the producer
normalizes the model's bounds and records `scene_coverage` on the
profile, and the prose every consumer reads marks each undescribed range
explicitly. No location, lighting or feature is ever written for a range
the vision pass never described.

Run this file at the parent of the fix and the gap-marking tests fail -
the prose renders the described prefix and says nothing about the other
90%.
