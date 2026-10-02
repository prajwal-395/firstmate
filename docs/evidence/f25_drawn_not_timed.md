# F25 forgives played words no honest caption can draw or time

Tests: `tests/test_f25_drawn_not_timed.py` (moved from its module docstring, 2026-10-02).

F25 forgives played words no honest caption can draw or time.

Two Reel 10 findings (2026-09-20), both correct output the gate
refused (AGENTS.md 10.4):

1. A RECORDED display suppression strands in a card hole. The global
   "um" suppression (lc-0049, captain) hides Akshita's filler while
   the audio plays it; the planner groups "their" with the card
   before and "website" with the card after, so no card covers the
   suppressed token. Demanding one would caption against the
   recorded decision. Suppressed words sit out the uncovered
   computation; the existing "suppressed" warning still names them
   with their ids, never silence.

2. A SUB-FRAME word drawn but untimed. 'are' stamped at 30ms (under
   one frame at 23.976fps); the planner draws it in the card text
   ("so what's happening, what are you") but its highlight window
   rounds to zero frames, so `generate_remotion_props` skips the
   dead sweep loudly and times from "what" to "you". The identity
   diff read the skipped sweep as a dropped word. A played word
   under one frame whose norm the card TEXT carries is timing dust:
   forgiven with an "untimed_drawn" warning naming word and card.

Both stay strict where it matters: an unsuppressed word in a hole
still errors; a word long enough to sweep (>= 1 frame) that the
card does not time still errors; a sub-frame word the card text
does not carry still errors.

`library/tools/subtitle_coverage.py` (`check_word_coverage`);
wired as F25 in `library/tools/reel_conformance_verifier.py`.
