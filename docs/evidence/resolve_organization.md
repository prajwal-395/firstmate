# `library.tools.resolve_organization` - the history behind its contract

This is the module docstring of `library/tools/resolve_organization.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Where a Resolve project's timelines and clips BELONG, and why.

The captain, on the field-test project (2026-09-06): *"one thing i
mentioned was having folder setups and organization inside the davinci
project to be able to view all of the reels timelines and have all other
assets organized as well"*.

What the project actually looked like
-------------------------------------
Measured on `Podcast (field test)` before any of this ran, twice - once
from a COPY of `Project.db` and once from the live scripting API, which
agreed on every count:

- 49 timelines: one master, 20 reels the live plan built, 20 from plans
  since replaced, and 8 one-off experiments.
- 2,583 media-pool items, of which **10 are real footage** - seven camera
  files, two vfx assets and a still.  The other 2,573 are rendered
  caption overlays this pipeline wrote.
- **1,216 of those overlays are placed on no timeline at all**: renders
  whose reel was rebuilt under a new name, left behind in the pool.
- 21 of the 49 timelines sat in the root bin among 449 overlays; the
  other 28 sat in the captain's own `Reels` bin among 760 more.

Nothing decided any of that.  `pool.CreateEmptyTimeline` and
`pool.ImportMedia` put what they make into whatever bin happens to be
CURRENT, which is wherever the operator last clicked - so a batch built
on Friday and a batch built on Saturday land in different places for no
reason either of them recorded.

The rule has one dimension, and it is a MEASUREMENT
---------------------------------------------------
Every artefact is filed by facts read off the project, never by parsing
its name:

1. A **timeline** goes to `05 - Reels/<state>` (below) - but only out of
   root or out of one of the pipeline's own bins.  A timeline sitting in
   any other bin is where a human put it and stays there (`TIMELINE_BINS`).
   A proof timeline (`SOP Proof_...`, firstmate's own) goes to
   `05 - Reels/Proof`, never among the captain's reels.  The project's
   own declared master timeline is NEVER moved - AGENTS.md 5.
2. A clip whose file lives **under the project's own directory** is
   something this pipeline generated. It files under the timeline that
   PLACES it, nested under the render bin its KIND belongs to
   (`06 - Subtitle renders` for subtitle segments, `07 - Motion
   graphics` for motion-graphics, timed-text and carrier renders - a
   path fact read off `project_layout.Area`, never a name parse), or
   under that bin's `Not placed on any timeline` when nothing does.
   A generated clip SEVERAL timelines place belongs to no single one,
   so it files under its render bin's SHARED leaf - still ours, never
   as outside material (rule 3 below is for files the pipeline did not
   write). The leaf names the state the way the unplaced leaf does,
   and it is what keeps the category root itself empty: per-reel
   means that reel uses it, shared means several do, unplaced means
   none do, and nothing sits at the root.
   NEITHER half applies to a generated clip no render area owns - a
   frame overlay, a freeze hold, which the reel builder writes beside
   its own step: KIND is a path fact too (`resolve_bin_layout.
   is_render_file`), and such a clip files under the captain's
   `03 - Assets` whether one timeline places it, several do, or none
   does, because a production asset is not a per-reel render and the
   subtitle fallback is not its category.
3. Anything else is material that came from outside, and files under
   `Source footage`.

"Which timeline places it" is read from the timelines themselves, not
from the filename.  The rendered overlays do encode their timeline
(`library/tools/subtitle_segment_id.py`), but a name is a claim and the
timeline is the fact - and 1,216 of them are placed nowhere, which no
filename can say.

Per-reel bins are FLAT under the render bin rather than nested under
the reel's state, and that is a decision the no-deleting rule forces:
a reel moves from `Current plan` to `Earlier plans` on the next build,
and a captions tree that mirrored the state would leave the bin it
moved out of behind and EMPTY - for every reel, on every plan change,
for ever, with nothing here allowed to clean them up.  State belongs
to the timeline; the captions bin answers a different question -
"which clips does THIS reel place" - and that answer does not move
when the plan does.

The bin tree derives from reality, not from build history
---------------------------------------------------------
Measured 2026-09-10, the captain's third report of the same bins: the
design above was an ACCUMULATION of everything ever built.  Per-reel
bins are keyed by placing-timeline NAME, every variant and every
superseded build is a new timeline name, timelines are never deleted -
so one reel is N names is N leaves under `06` and `07`, and the sweep
(`plan_retirements`) could only ever retire EMPTY legacy shells, never
a canonical per-reel leaf.  Every build made it worse and no amount of
sweeping kept up.

It is a function of what currently exists instead: a per-reel leaf
under a render bin whose name is NO live timeline's name is dead
whether or not it still holds files, and `plan_dead_render_bins`
retires it with its contents - proven unplaced and pipeline-generated,
journalled with what it held.  A leaf named for a timeline that still
exists stays, whatever state that timeline is in: `Earlier plans`
timelines are reality too, kept deliberately under the no-delete
ruling, and their bins stay with them.

`UNRECORDED` keeps its bucket for the same reason.  The exact-match
rule means a suffixed one-off (`" (fragment fix)"`) is UNRECORDED
rather than EARLIER - calling it EARLIER would assert by prefix a
provenance nobody recorded.  Those timelines are unclassifiable BY
DESIGN, not by failure: the bucket is where timelines no plan names
are kept rather than deleted or misfiled, and emptying it is the
captain's call (a plan record naming them) never the sweeper's.  The
`Not placed on any timeline` leaves are the same bargain on the clip
axis: canonical destinations the organiser files into, whose removal
belongs to the prune path under its own authority - never to this one.

CURRENT is the plan, not the last build
------------------------------------
A reel's state comes from the live PROPOSALS file
(`reel_proposals_v2.json`, read through
`plan_provenance.current_plan_names`), not from which reels a build
call happened to place:

- `CURRENT` - the live plan's approved moments name it.
- `EARLIER` - an ARCHIVED plan names it, and the live plan does not.
- `UNRECORDED` - no plan on disk names it.

The provenance record (`plan_provenance.json`) says which plan a build
consumed, and on a plan-hash change it drops every entry but the reels
that build placed - so after one single-reel build it names one reel
while the plan still names them all. Filing by it read a partial build
as a plan change and filed every other reel as history (measured
2026-09-18: building Reel 02 alone demoted eight accepted reels). When
the live plan cannot be read at all, filing falls back to the
provenance record's built reels rather than mass-demoting - an
unreadable plan is "nothing here can say", never evidence that every
reel left it.

Exact names only.  A near match lands elsewhere is already the rule for
addressing a Resolve project and a Resolve timeline (AGENTS.md 5), and
it is the same rule here: eight of the field test's timelines are a plan
name plus a hand-typed suffix (`" (fragment fix)"`), and calling those
EARLIER would mean deciding by prefix that a build nobody recorded came
from a plan nobody wrote down.  They are UNRECORDED, which is what the
evidence says.

`UNRECORDED` is therefore a real answer and not a failure.  The captain
built those deliberately.

Nothing here deletes
--------------------
There is no delete call in this module or in the executor that drives it,
and `tests/test_resolve_organization.py` asserts that of both files.  A
reel the live plan no longer names is MOVED and RELABELLED, never
removed: the captain's ruling of 2026-09-06 is *"a refusal is cheap and a
deleted timeline is not"*, and the same reasoning makes an accumulated
timeline cheaper than a lost one.

Every move is journalled with the bin it came FROM, so the whole thing
reverses.  What a revert cannot undo is a bin that was CREATED - undoing
a create means deleting - so a revert reports those by name and leaves
them, the same bargain `project_migration.revert_from_manifest` strikes
with copies.

This module holds no Resolve calls and does no I/O, so every rule in it
is testable without the application running.
`library/tools/execution/organise_media_pool.py` is the half that talks
to Resolve.

`tests/test_resolve_organization.py`.
```
