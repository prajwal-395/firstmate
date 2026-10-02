# `library.tools.reel_rebuild_need` - the history behind its contract

This is the module docstring of `library/tools/reel_rebuild_need.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Whether a reel needs a Resolve pass at all, answered from measurements.

The cost this closes
--------------------
A build stages EVERY reel it is asked for and pays the full Resolve
placement pass for each one, whether or not anything about that reel
changed.  What that pass costs is measured, in the tree, from the
composed-edit spike (`docs/RULE_EVIDENCE.md`, "what it costs"): a
rebuild of one reel is 19.4-67.1 s of Resolve time on that reel, of
which the Fusion comp pass is 17.0-63.7 s - and the comp pass is FIXED
overhead, not per-comp work, so *"a pass over a timeline whose every
comp was already banked and unchanged still took 25.9-73.4 s"*.

That settles which instrument saves it.  `composed_edit` composes a
sub-reel edit out of the verbs Resolve has, and it cannot avoid the
comp pass - its own measurement is *"on this reel the composed path is
not faster"*.  The only thing that avoids a per-reel fixed cost is not
paying it: **a reel nothing changed about is not placed again.**

What it came to, measured 2026-09-12 end to end against the running
Resolve on `Podcast (field test)`, three real reels (13, 23, 26) built
into probe containers off an APFS clone of the captain's project:

===========================================  =========  ==============
build                                          seconds  Resolve passes
===========================================  =========  ==============
three reels, none of them built before          405.82               3
three reels, one reel's declaration changed     110.40               1
three reels, nothing changed                     45.23               0
===========================================  =========  ==============

The per-reel Resolve pass on those three was 130 s, 151 s and 34 s
against 38 s, 17 s and 11 s of derivation - so the derivation the skip
still pays is a tenth of what it avoids.  The middle row is the case
the profile priced at *"~7 min per five-reel build"*: 3.7x here, and
what is saved scales with the reels that did not change.

**A rebuild is a control arm only with the entry timeline
controlled.**  While measuring this, three consecutive builds of Reel
23, each entered on a delivery-shaped reel, rendered byte-identical
(0.000000 mean, 180/180 identical frames, head and tail).  An earlier
pair built without that control differed on 63% of subpixels, every
picture Pan/Tilt exactly two apart
(`docs/READING_A_TRANSFORM.md`, which also
carries the four-way reading table this module's `carried_digest_live`
exists for).  Issue 999 and `transform_drift` own the positioning
question; leaving an unchanged reel alone is a PROTECTION as well as a
saving, because re-placing a reel the captain already approved is the
only way any such scaling reaches it.

Why this module and not a flag
------------------------------
`only=` already lets an operator name the reels to build, and that is
the same saving bought with a GUESS.  The operator does not know which
reels a changed declaration reaches - the whole reason
`reel_divergence` exists is that "every reel inherits it" was true of
the engine and false of the project.  So the decision is made from
state, here, and the decision is FAIL-CLOSED: every answer that is not
a positive match on both halves is REBUILD.

The two halves
--------------
A reel may be left alone exactly when both are true:

1. **The derivation is identical.**  Everything the build derived for
   this reel before it touched Resolve - its ranges and placements, its
   cards, its caption segments and their rendered bytes, its overlay
   placements, its explainer and semantic segments, its motion plan,
   its ending and the project's look - digests to what the build that
   placed the live timeline recorded.  Plus the engine code and the
   project-WIDE declarations, which are digested wholesale (below);
   the per-reel pin stores are not, which is what lets a pin on one
   reel cost one reel.
2. **The live timeline is still the one that build placed.**  The
   carried digest of the timeline as it reads now equals the one
   recorded straight after its promotion.  A reel that drifted - a hand
   edit, a reset, a composed edit - is rebuilt, because a rebuild
   restores the engine's own framing and a skip preserves the drift
   (`reel_divergence`, 2026-09-12: *"A reset is not a hand edit"*).

Both halves cost almost nothing.  Half 1 is CPU the build already
spends - the derivation runs either way, and the profile measured it as
seconds.  Half 2 is one `snapshot_timeline` per reel, MEASURED at
0.043-0.153 s on the captain's eight reels, and the divergence survey
already takes exactly those snapshots on every build.

Over-covering is the safe direction, and it is deliberate
--------------------------------------------------------
A digest that misses an input skips a reel that needed rebuilding.  A
digest that covers too much rebuilds a reel that did not.  The first is
a wrong reel; the second is a minute.  So:

* :data:`ENGINE_CODE_TREES` digests whole source trees, RECURSIVELY,
  rather than a closure computed from imports.  An import closure would
  miss every step this path reaches through the operation registry by
  NAME (`operations.get("subtitles.plan")`) - the under-cover trap in
  the shape that looks most rigorous.
* :data:`DECLARATION_SOURCES` digests each project-wide declaration
  file whole rather than the fields a reel happens to read.  A field
  added to `project.yaml` next week is covered the day it is added.
* An `external/` declaration that :data:`PER_REEL_DECLARATION_STEMS`
  does not claim is folded into the project-wide half, so a new
  declaration over-covers from the day it lands rather than being
  silently ignored.

The consequence is stated plainly: **during active engine work every
build invalidates every reel, and that is correct.**  The saving lands
where the profile said it does - a build of several reels where one
reel's plan, pin or declaration changed and the engine did not.

`built_with` was too narrow to be this answer
---------------------------------------------
`plan_provenance.REEL_BUILD_CODE_FILES` names three paths, and this
module does not reuse it: measured 2026-09-12, those three cover 3 of
the 131 source files reachable from the reel build by import alone, so
a change to `reel_ending.py`, `reel_look.py`, `tight_box.py` or
`overlay_placement.py` leaves the stamp identical.  That is reported
rather than repaired here: widening `built_with` changes what a stamp
already in the captain's version record means, and that is not this
module's call.  :func:`engine_code_digest` is this module's own reading
and it covers the trees.

`tests/unit/reels/test_reel_rebuild_need.py`.
```

## The test suite's account

Moved from the module docstring of `tests/unit/reels/test_reel_rebuild_need.py` (2026-10-02).

`library/tools/reel_rebuild_need.py` is what stops a build paying a full
Resolve pass per reel per build. The measured stake, from the composed-edit
spike (`docs/RULE_EVIDENCE.md`, "what it costs"): one reel's Resolve pass is
19.4-67.1 s, of which the Fusion comp pass is 17.0-63.7 s of FIXED overhead.

The whole risk is in one direction. A digest that misses an input SKIPS A REEL
THAT NEEDED REBUILDING, and nothing downstream would say so - the reel would
simply be last week's. A digest that covers too much rebuilds a reel that did
not need it and costs a minute. So the tests are mostly about the first: every
REBUILD answer, the absence of any partial answer, and a mechanical check that
the engine trees really do cover the modules the reel build reaches.
`plan_provenance.REEL_BUILD_CODE_FILES` names three paths and the reel build
reaches far more; a digest built on that set would leave a change to
`reel_ending.py` or `reel_look.py` invisible.
