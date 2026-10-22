# The scoped retime: a clock change, scoped

PERF-003, part two (2026-10-22).  The companion to PR 3133's
`docs/RULE_EVIDENCE.md` entry on the caption change, and the
write-up for `library/tools/reel_retime_scope.py` and the scoped
route's re-placement of a retimed passage.

## The defect

PR 3133 scoped a one-word caption change: the card's bytes changed at
the same span, so the card was replaced and every other card and row
was reused - three Resolve writes against the rebuild's thirteen.

A retime was not scoped.  A retime is a CLOCK change: the passage
plays at another rate, so it occupies a different number of record
frames and every placement after it moves by the difference.  The
scoped route compared the plan's footage rows with the timeline's for
IDENTITY, so a retime was a difference it could not place, and it
refused - naming the passage and how far the clock change reached,
which is a better refusal than the row dump it replaced, but still a
refusal.

So the smallest edit a captain can make to a reel's timing - "play
this passage at 110%" - cost the whole reel: the full rebuild
re-created the timeline, re-imported the media, re-placed every card,
re-graded every picture item and re-rendered the captions that rode
the clock.

## The scope

`library/tools/reel_retime_scope.py` makes the clock change a value,
the same way `library/tools/change_scope.py` makes a diff a value.

`passages_from_placements` names the passages the plan retimes: the
rate, the record frames the passage occupies, and the source it plays.
A passage that covers more than one clip is refused there and now: it
CROSSES a clip, so the row's item count changes, and this route scopes
a retime of ONE clip.

`diff_rows` compares the plan's rows with the live rows and classifies
every difference.  The classification is provable, not heuristic:

- the items are paired BY POSITION within a row - a retime moves
  items, it never reorders them - and each pair must carry the same
  media, or the change is not a retime;
- the RETIMED item is the passage itself: its start does not move and
  its length changes;
- the MOVED items are the clock's tail: their length does not change
  and their start moves by ONE number;
- that one number is the passage's own length change, so the shift is
  derived from the passage rather than guessed from the tail;
- the passage's new length is explained by its rate - the plan plays
  `rate` times as much source in the frames it occupies - so a
  passage whose length its own rate does not explain is refused;
- the moved items all start at or after the passage's old end, so the
  clock moved them forward and never backward over the passage.

Anything that fails one of those refuses, by name, and the full
rebuild places the reel.

## The re-placement

`reel_incremental.apply_footage_retime` re-places the passage and the
clock's tail, and touches nothing else:

- the live items are deleted in ONE pass;
- the passage is re-placed at its new length, over the source the
  plan names, and the speed pass sets its rate;
- the moved items are re-placed at their new start, over the source
  they already carried - the pool items are REUSED, so a retime
  imports no media at all;
- the placement goes through the SAME machinery the full build uses
  (`commit_footage_placements`, the same op identity, the same
  batching, the same speech-row sweep), so the scoped route and the
  rebuild cannot drift on the arithmetic;
- the live item is proved to be the one the plan describes: it reads
  from the same source frame the plan places.

The captions ride the clock too - a caption after the passage starts
earlier - so the caption diff pairs the row's cards with the plan's
segments BY ORDER when the change is a retime, and re-places the cards
that moved while the cards that did not are reused.  A caption that
moved for any other reason still refuses: a card that moved with no
stated reason is a plan this route cannot prove.

## The measurement

Measured on the fake Resolve double, 2026-10-22, one reel, three
angles, one caption before the passage and one after it, the middle
passage retimed from 100% to 110%.  The counters are the engine's own
(`tests/unit/reels/scoped_counting.py`), and the denominators are kept
apart - never combined:

```
                         timeline   item     scans   cursor   wall
                         writes     writes            moves    seconds
before (bulk rebuild)       20        2       62        9      0.268
after  (scoped patch)        6        2       81       10      0.032
```

(The wall seconds are the first of three runs; the counts are the same
in all three.  The rebuild measured 0.158s / 0.194s / 0.271s and the
patch 0.035s / 0.048s / 0.050s.)

- before: the FULL REBUILD of the retimed plan - what the scoped route
  used to fall back to, because a retime was refused;
- after: the SCOPED PATCH - the passage and the clock's tail
  re-placed, everything else carried over;
- timeline writes: 20 against 6.  The rebuild creates a timeline, adds
  seven rows, names them, places the footage, imports and places both
  caption cards.  The patch deletes the passage and the tail in one
  pass, places them back in two, and re-places the one caption that
  moved;
- item writes: 2 against 2.  The retime sets the same two speeds in
  either route, so the retime's own write is not where the saving is;
- scans: 62 against 81.  The scoped route READS MORE.  It must prove
  the rest of the reel is unchanged before it touches anything, and it
  reads back again afterwards.  The saving is in writes and wall time,
  not in reads - the same honest cost PR 3133 reported for the caption
  change;
- wall seconds: 0.268s against 0.032s on the double.

The RESULT is the same reel: `tests/unit/reels/test_reel_scoped_retime.py`
proves the patched staging equals a fresh full build of the same plan,
item for item, with the same speeds and the same spans.  The
measurement above is therefore a measurement of which route costs less
to reach ONE reel.

## What it does not do

- A passage that covers more than one clip is refused, by name: it
  splits the clips it crosses and changes the row's item count.
- The other rows - transitions, the TV frame, the cards, the native
  ending hold - must be unchanged.  A retime that moved one of them
  refuses, and the full rebuild places it.
- The measurement is on the fake Resolve double, as PR 3133's caption
  measurement was.  A live run through the serving broker needs the
  Resolve instance lease, and this lane does not take one.
