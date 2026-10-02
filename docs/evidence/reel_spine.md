# `library.tools.reel_spine` - the history behind its contract

This is the module docstring of `library/tools/reel_spine.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
A reel's own spine, in the reel's own time.

What this is, and what it deliberately is NOT
---------------------------------------------
This is a PRODUCER.  It makes an input the pipeline's steps already
consume - a spine, `{"structure": [block, ...]}` satisfying AGENTS.md
section 6 - and then it gets out of the way.  It plans nothing, styles
nothing and renders nothing.

That distinction is the captain's ruling of 2026-09-04, and it is the
reason `reel_subtitles.py` is deleted rather than extended:

    "the subtitles was meant to utilize the pipeline subtitles step and
     whatever funcitonality was put into that python file should have
     been augmented into the pipeline -- we are not tryig to create any
     standalone artifacts and scripts"

`reel_subtitles.py` grouped words into cards and attached a per-speaker
style.  Both of those are step 4.01's job and 4.01 already does them,
better: it groups by MEASURED PIXELS through `safe_area.fits_in_box`
(AGENTS.md 10.2), where the standalone module counted words.  So the
fold is not a copy - two of the three things it did were a second,
weaker implementation of work that already existed, and the third (the
closer seam) is the only behaviour that had to survive.

    "each reel does have an audio spine, its just the audio in the
     timeline itself"

That is what this builds.  Nineteen reels, nineteen spines, and every
step downstream runs against one exactly as it runs against a master -
no reel branch anywhere in a step.

The two clocks, and why this module exists at all
-------------------------------------------------
A reel is its keep ranges laid end to end, so **three different clocks
touch one word** and mixing any two silently produces captions that
drift:

    MASTER timeline second  the transcript's own clock
    REEL timeline second    what the viewer sees, via reel_build.reel_time
    SOURCE second           where it sits in the raw clip

The transcript's words carry bare `start`/`end`, which
`region.domain_of` refuses to guess at - the temporal index uses those
keys for SOURCE seconds and the subtitle plan uses them for TIMELINE
seconds, and on project 001's first word those differ by 0.836s.  So
every word here is put through `region.read_words(..., TIMELINE)` to
NAME its domain before anything reads it, and the spine's
`word_timestamps` come out in SOURCE, which is what section 6 requires.

A block's `timeline_start`/`timeline_end` are REEL seconds.  A word's
`source_start`/`source_end` are SOURCE seconds.  Those are different
clocks in one dict on purpose, and the contract says which is which.

Two mics, one sentence
----------------------
A two-camera shoot records the same words on both mics, so the
transcript carries the sentence TWICE - once on the speaker's own mic
and once, a fraction later and quieter, as bleed on the other's.  Left
alone that becomes two spine blocks claiming the same speech, and every
step downstream captions it twice with two different speakers.

Resolving it is the PRODUCER's job and not step 4.01's, because it is a
fact about the transcript rather than about captions: by the time 4.01
sees a spine the duplication is indistinguishable from two people
saying the same thing.  4.01 already handles what it CAN see - a card
running short, or two cards overlapping - and that division is
deliberate.  Each half resolves what it alone can detect.

The earlier card wins: the primary mic hears the words first and the
bleed arrives delayed.

**A row is not always bleed WHOLE.**  A speaker's turn ends and the
other's begins while the first mic is still recording, so a row often
carries its own speaker's sentence and then, at its TAIL (or at its
HEAD), a few words of the other person picked up as bleed.  Measured on
the captain's field test, 31 of 875 transcript rows carry another
speaker's words at the same instant, 20 of them on rows bound to a clip
and therefore reaching a block today.  `_drop_bleed` cannot see those:
it compares whole blocks, and a long row with a four-word bleed tail is
neither a subset of the short row nor half its vocabulary.

So the tail is CUT, and it is cut HERE - at the block boundary, which
is where a card's boundary is decided.  Step 4.01 groups WITHIN a block
and cannot see across one, so a block carrying two speakers is the only
way a card carrying two speakers can exist.  `_cut_cross_speaker_edges`
is that cut, and the rule it applies is exact rather than a threshold:
**two people cannot utter the same word at the same instant**, so a word
that a different speaker's block carries with the same text at an
overlapping reel second is one mic hearing the other.

Nothing is lost by the cut, and that is a property rather than a hope:
a word is only ever removed from a block while another surviving block
carries it at that same instant, `_cut_would_orphan` checks exactly
that, and a cut that would leave a word carried by nobody is CANCELLED.
The cost of picking the wrong side is which speaker's style the words
are drawn in, never the words.

What a reel cannot supply, said rather than invented
----------------------------------------------------
A reel has no `hook` block, no bookends and no music behaviour; it is
speech that was already cut.  Every block here is `speech`, and a step
that needs more than speech gets an honest absence rather than a
fabricated block (AGENTS.md 10.5).

`tests/test_reel_spine.py`.
```

## `tests/test_reel_fragment_blocks.py` module docstring (moved 2026-10-02)

```text
A mid-sentence transcript row is given back to its sentence.

The defect, on Reel 23 of the captain's field test: three caption cards
under half a second - ``them.`` 0.181s, ``comes in.`` 0.422s and
``that's`` 0.140s.  None was a grouping fault.  Each was a WHOLE spine
block, because the reel spine makes one block per transcript row, and
each of those rows was one half of a sentence WhisperX split in two:

    135.616-136.460  "It was definitely going to help"
    136.540-136.721  "them."

Same speaker, same clip, an 80ms pause and nothing removed.  Step 4.01
groups WITHIN a block and clamps every card to it, so the second row's
block is 0.181s and its only card is 0.181s at every partition.  The
block is the ceiling on the card, so the block is where it is fixed.

These tests prove BOTH directions, because a rule that only ever fires
is as useless as one that never does:

* it fires on a fragment whose sentence continues into a neighbour, and
  the short card is gone;
* it does NOT fire across a cut, across a clip, across a speaker, or on
  a block long enough to carry a card - and a spine with no fragment in
  it comes back unchanged, block for block.
```
