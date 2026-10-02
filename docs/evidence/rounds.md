# `library.tools.versions.rounds` - the history behind its contract

This is the module docstring of `library/tools/versions/rounds.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
ROUNDS: which promoted reel versions belong together, and what changed
between two of them. Part of the version model
(`library/tools/versions/__init__.py`).

A round is stamped once per batch of the captain's feedback, across every
reel that batch touched.

The captain, asked whether a version is per reel, per round or per
project (2026-09-12, answering
`data/vep-can-it-hold-up-in-a-real-editing-workflow` §7): **per round** -
*"six markers at once, one pass, one result to look at, and a round that
can be diffed as a whole"*.

Before this module the pipeline had no version object at all. Approval
lived on a PROPOSED moment before a build (`reel_proposal.Approval`), the
newest timeline under the final name was the answer by construction, and
promotion deleted what it replaced - so after a round there was the new
reel and nothing to compare it to.

A round is DISCOVERED, never declared
-------------------------------------
Nobody types a round number. The captain types markers and firstmate
promotes reels, and the interleaving of those two events IS the round
boundary:

    a new round opens at the first ASK that arrives after at least one
    promotion has landed since the current round opened.

That rule has no clock threshold in it, which is the point. A pull that
re-reads notes already asked opens nothing (the ledger folds them onto
one identity, so their `first_asked` does not move). Two pulls seconds
apart carrying the same words are one batch because they are the same
words, not because seven seconds is under some window. And a second
marker typed before anything was rebuilt joins the batch it belongs to,
which is exactly how the captain works.

Replies of ours never open a round: `feedback_ledger` tells an ask from
a reply off the note's own record, and only `KIND_ASK` entries are read
here.

Round 1 is the first cut - no feedback preceded it - and it says so
rather than claiming an opener it does not have.

Measured on the captain's own project, `lucie/geo-podcast`, at the time
this landed: three rounds. Round 1 the first cut; round 2 opened by the
five asks of 2026-09-11T03:08Z (the jarring Craig cut, the TV close
animation, Akshita's clipped audio, the broken graphics renders, the
ending to apply to every reel); round 3 by the single Reel 09 ask of
2026-09-11T22:51Z, and carrying the seven-reel rebuild of 2026-09-12.

What a round holds
------------------
Per reel it touched: when it was promoted, the `built_at`/`built_with`
stamps `plan_provenance` captures at build time, and the ROW SNAPSHOT
(`reel_read.rows_of`) of what was promoted. The rows are the payload
that makes the round diff (below) free: two rounds' stored rows through
`reel_replace_guard.diff_rows` answers "what changed between round 3 and
round 4" off disk, with no Resolve and no git.

Storing the rows here rather than reaching for them later is deliberate.
A timeline can be retired, archived and eventually collected
(`reel_retirement`); a few kilobytes of JSON per reel per round cannot
clutter a bin and never expires, so the record of what a round contained
outlives the timeline it describes. That is the whole reason retirement
is allowed to end.

Stamped, or reconstructed - and the record says which
-----------------------------------------------------
`stamp_promotion` records a round at the moment of promotion, which is
the only moment `built_with` can be known (AGENTS.md 10.1: merge time is
not build time). `backfill` reconstructs earlier rounds from the
committed timeline snapshots in the project's own git repo
(`store.record_reel_promotion` has written one per
promoted reel since 2026-09-11), and marks every entry it makes
`SOURCE_RECONSTRUCTED` with no `built_with` at all - a stamp invented
after the fact is not a measurement, and the record must not read as one.

Diffing two rounds
------------------
What changed between round 3 and round 4, in the captain's terms.

This is the payoff of the version object, and the reason the versioning
question mattered at all. Without it a round is SUPERSEDED: the new reel
is there and the old one is gone, and the only way to know what a round
did is to remember. With it a round is REVIEWABLE.

It is also nearly free, which is why it is a function and not a project.
The scout report measured it
(`data/vep-can-it-hold-up-in-a-real-editing-workflow` §3): the only
differ in the repository joins timeline items on `unique_id` and 0 of 26
identities survive a rebuild, so it reports every clip as both added and
removed. But `reel_replace_guard.diff_rows` over `reel_read.rows_of`
- the diff the promotion guard already runs live-against-live on every
promote - runs perfectly well over two STORED snapshots, off disk, in
milliseconds, with no Resolve. The rounds above store exactly those rows
per reel per round, so this half is the guard's own diff pointed at
two rounds instead of two live timelines.

One diff, two callers
---------------------
Nothing new is computed here. `diff_rows` is the guard's, `rows_of` is
`reel_read`'s, and the row key (`"video:Semantic"`) is the vocabulary the
refusal messages already print - so a row named in a round diff and a row
named in a promotion refusal are the same row, spelled the same way. A
second differ for the same question is how two records come to disagree.

What it reports, and what it deliberately does not
--------------------------------------------------
Per reel, per row: items and frames on each side, and the items present
in one and not the other. A reel promoted in one round and not the other
is reported as such - that is a real answer ("round 4 did not touch Reel
13"), not a gap.

It does NOT report WHY anything changed. The rows say a Semantic row
went from 2 items to 4; the feedback the round was opened by is printed
beside it, and joining the two is the reader's judgement. A tool that
guessed which note caused which row would be inventing a finding.

The standing limit is the guard's own, stated in its docstring: a
substitution that keeps every item name and span is invisible to a
span-based diff. That is the limit of what a row snapshot can see, not a
new hole.

`tests/test_version_rounds.py`.
```
