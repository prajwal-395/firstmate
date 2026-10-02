# passage engagement

Test: `tests/test_passage_engagement.py`.

A missing measurement must never resolve to a value that reads as a
real one.

The `engagement_scorer` this replaces gave nine of project 001's eleven
speech passages an identical composite of 49, and a `hook` component of 30
to ten of eleven, because `prosody_data.get("energy_rms", 0)` returned 0
for every line the captain has ever said and `0 < 0.3` subtracted twenty
points from a base of fifty.  The scorers are withdrawn - see
library/tools/passage_engagement.py for why each one was not a
measurement.

These tests hold the property, not the implementation: with the
measurement absent, the score must be ABSENT, never a plausible-looking
number, and every reader must say it has no basis.
