# `library.tools.reel_retirement` - the history behind its tests

Moved verbatim from the module docstring of `tests/test_reel_retirement.py` on
2026-10-02, when the test kept only its invariant. The archive bin is
`resolve_bin_layout.REELS_ARCHIVE_BIN`, declared since the 2026-09-09 reset.

```text
A promotion DELETES what it replaced, unless asked to keep it.

Three things are being proved, and the first is the default:

1. the default promotion deletes the backup, so one timeline per reel
   and an empty archive (the captain, 2026-09-18, withdrawing his own
   2026-09-12 retire rule after seeing "(archived round 001)" on reel
   titles - no leftovers wins, comparison becomes explicit);
2. the explicit opt-in still retires exactly one generation per reel,
   bounded so the archive cannot grow with time - the answer to the
   clutter question the captain has asked about four times (a scratch
   left in a reels bin is how they came to review a throwaway by
   mistake, `resolve_bin_layout.SCRATCH_BIN`);
3. a signed-off generation is never deleted or collected, and the
   deletion scope refuses a list that reaches beyond the reels being
   promoted.

The lifecycle is what reconciles the first two, and each half has a
test that fails without it: the retention bound is per REEL rather
than per round; the archived name can never be mistaken for the live
cut; and a stray archived timeline is filed back by the organiser
rather than sitting beside a deliverable.
```
