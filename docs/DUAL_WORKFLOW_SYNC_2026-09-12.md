# Reading the captain's hand edits back off eight live timelines, 2026-09-12

The captain made both marker comments and manual Edit-page changes
across the eight `lucie/geo-podcast` reels and asked whether the two
halves of the workflow - an agent building and a human editing - can
stay synced. This is what the attempt measured. The project-side record
is `pipeline_output/review/CAPTAIN_MANUAL_EDITS_2026-09-12.md` in that
project's own store; what is here is what belongs to the ENGINE.

## 1. `resolve_placement_lock` had no call site, and is now gone

As it stood on the morning of 2026-09-12, `library/tools/resolve_lock.py`
exported two things. One was wired and one was not, and the module's
prose read as though both were:

| export | call sites | what it does |
|---|---|---|
| `assert_current_timeline` | 22 (`reel_build`, step 6.01, `segment_renderer`, `execution/resolve_render`) | re-asserts the current timeline and reads it back immediately before a write; a loser RAISES |
| `resolve_placement_lock` | **0** | would have made a loser WAIT - if anything had taken it |

> **Superseded 2026-09-12, later the same day.** `resolve_placement_lock`
> stayed removed; the shape that failed was a lock a caller had to
> remember to take. `resolve_lease` replaces it and cannot be forgotten,
> because `assert_current_timeline` - the 22-call-site check this table
> calls the only real guard - now REFUSES to run outside one. See §2's
> "CLOSED" note below for the mechanism, and `library/tools/resolve_lock.py`
> for the whole of it.

`tests/test_resolve_lock.py` never touched the lock either: it imported
`assert_current_timeline` and `ResolveRaceError` and tested those. So
the lock had no caller and no test, which is the shape AGENTS.md 10.4
names - a guard that reads as coverage.

**Measured while a sibling lane built into the same Resolve project**:
366 samples, two seconds apart, over twelve minutes. The lock was
UNHELD on every single one, while that lane created a staging timeline,
promoted it and moved the current timeline six times. No contention was
observed because none was possible.

**The complementary measurement, taken from the other side.** Observing
that nobody holds the lock is weaker than it sounds - a reader might
simply have watched a quiet hour. So the second run HELD the lock and
watched what a lock is supposed to prevent. This process took
`/tmp/resolve_placement.lock` exclusively and sampled Resolve every
three seconds for two minutes while a sibling lane worked in the same
project:

    LOCK acquired after 0.000s (0.000 => nobody held it)
    t=0    current='Reel 09 - your-website-is-only-20-percent (final)' timelines=15
    t=  82  CURRENT TIMELINE MOVED WHILE LOCK HELD: 'Reel 09 ... (final)' -> 'Reel 23 ... (lane11probe)'
    t= 112  CURRENT TIMELINE MOVED WHILE LOCK HELD: 'Reel 23 ... (lane11probe)' -> 'Reel 09 ... (final)'
    t= 118  CURRENT TIMELINE MOVED WHILE LOCK HELD: 'Reel 09 ... (final)' -> 'Reel 23 ... (lane11probe)'

    40 samples over 121s while THIS process held the lock exclusively:
      current-timeline moves by another process: 3
      timelines created by another process:      0
      timelines deleted by another process:      0

Three moves of the current timeline by another process, during an
exclusive hold. `flock` is working exactly as written - the acquisition
cost 0.000s because there was no one to wait for, and the other process
never asked. **This is not "the lock was not exercised". It is the lock
exercised and measured to protect nothing**, because the state it
guards is reached by a route that does not pass through it.

A mutual-exclusion primitive only excludes parties that take it, so the
useful number here is not the lock's own behaviour but its call-site
count, which is zero.

The two guards are not interchangeable. Within one process
`assert_current_timeline` closes the window between `SetCurrentTimeline`
and the append. Across processes it closes nothing: the other process
re-asserts its own timeline just as happily, and whoever calls last
wins. Only a lock serialises two processes, and nothing takes the lock.

**What was done about it: the lock was REMOVED**, in #1036, by the
codebase-coherence lane reaching this same conclusion from its own
evidence. That is the right call and it is this repository's own rule
applied literally - AGENTS.md 10.4, *a gate that cannot fail is worse
than no gate, because it reads as coverage. If you cannot make it read
real state, delete it.* This lane had proposed instead to keep the lock
and pin its call-site count at zero, on the grounds that every call
site sits on a build path it had been told not to run. Merging the two,
the removal wins: a pinned zero still leaves a `with` block in the
module for the next reader to reach for, and the deferral it encodes
("wire this later") is the state that produced the defect.

What survives here is the measurement, which is what makes the removal
evidence-backed rather than asserted, plus two tests that keep the
removal honest:

* `test_the_lock_exists_exactly_when_something_enters_it` fails in both
  directions. Re-adding the context manager with no callers re-creates
  the guard nobody enters; a caller left behind by the removal is
  caught here rather than at import time on a build path. Re-adding it
  WIRED passes, which is the correct outcome.
* `test_the_removal_note_survives_and_says_why` keeps `resolve_lock.py`
  pointing at this document, so the removal cannot decay into a bare
  deletion that the next reader undoes for the reason the lock was
  written the first time.

Neither test asserts a constant, which is the improvement over what was
proposed: a number in a module drifts, a relationship between the
module and the repository cannot.

## 2. What actually contends is Resolve, and no lock helps

The Resolve scripting API serialises on the application. While the
sibling lane placed clips, this task's READ-ONLY calls
(`marker_feedback.pull`, plain getters) blocked for over twelve minutes
and had to be abandoned and retried. An earlier identical read, taken
before that lane started placing, returned all eight timelines in 1.7
seconds.

Sampled while blocked, the reader is not slow inside Resolve - it has
not reached Resolve at all:

    Fusion::ScriptApp(...)
      Fusion::RemoteApp::Connect()
        Fusion::RemoteObject::DoCommand(...)
          Fusion::RemoteApp::WaitPkt(...)
            Fusion::Platform::WaitEvent(...)
              _pthread_cond_wait

The block is the `scriptapp("Resolve")` HANDSHAKE. Every route into
Resolve in this repository begins with that call, so a lane that is
placing does not merely slow a reader down, it prevents one from
connecting - `reel_read`'s hold check, `assert_current_timeline`, and any
lock a reader might take all sit on the far side of a connect that never
returns.

A reader cannot protect itself with a placement lock - it does not
place, and taking a writer's lock to read would only make the starvation
mutual. So a reader racing a writer is unmediated by construction. If
this workflow is to run routinely with an agent and a human on one
Resolve instance, that is the gap to close, and it is a different
mechanism from the one this module has.

### CLOSED, later the same day, and this is the mechanism

The paragraph above is right that a WRITER'S lock does not help a
reader. The mechanism that does is a SHARED one, and the reason it
works is in the stack trace above rather than in any argument about
locks: the reader was blocked in the `scriptapp` HANDSHAKE, before it
reached a timeline. A reader that takes no lease does not avoid
waiting - it waits in a place with no bound, no diagnostic and nobody
to name.

`library/tools/resolve_lock.py` moves that wait to a file lock, which
has all three. `concurrency_routing.RESOLVE_READ` is the class: a
shared lease, so several readers run together and none runs while a
writer holds the instance. A reader that cannot have it now SKIPS or
raises `ResolveBusy` naming the holder and how long they have held it,
instead of sitting inside `_pthread_cond_wait` for twelve minutes.

Three corrections to what is written above, and nothing else changes:

1. *"a reader racing a writer is unmediated by design"* - it is
   mediated now, by the shared half of the lease.
2. *"any lock a reader might take sits on the far side of a connect
   that never returns"* - true of a lock taken after connecting. The
   lease is taken BEFORE `scriptapp`, at the entry point, which is why
   it can be waited on at all.
3. *"`reel_read` ... does not cover a lane driving Resolve directly"* -
   it does now: `read_reel` takes the shared lease, and a lane driving
   Resolve directly holds the exclusive one because
   `assert_current_timeline` refuses to place without it.

The count that made the original lock removable has been repaired
rather than argued with: it had 0 call sites, and the lease that
replaces it has 12 across `library/`, checked by
`tests/test_resolve_guard_wiring.py` against the routing table row by
row.

`library/tools/reel_read.py` refuses while `run_control.hold_requested`
is set, which covers a build driven through the pipeline runner. It does
not cover a lane driving Resolve directly, which is what happened here.

## 3. `captain_edits capture-transform` records the drifted value

`captain_edits._capture_transform` reads the captain's hand move out of
the live timeline with `GetProperty` and records what it reads. On this
project that is wrong by a clean power of two.

`transform_drift` already documents that each reel's live Pan/Tilt sits
a power of two away from what its own build snapshot records, cause
unidentified. Measured today, the factor is 0.25 on 207 of 214 moved Pan
readings across seven reels, and two declarations already in force
confirm the direction: `captain_edits`' Pan -12 reads -3.0 live, and the
Reel 09 caption Tilt of -1700 recorded in `overlay_intent`'s docstring
reads -425.0 live.

So `capture-transform` run today would record a QUARTER of the captain's
move, `validate_edits` would accept it, and the next build would apply
it. The four overrides recorded for this project were written with the
factor undone and the arithmetic stated in each `reason`, because a
number nobody can re-derive is not reviewable.

What would close it: `capture-transform` already knows the reel, and the
reel's build snapshot is on disk beside the declaration. Comparing the
live reading against `transform_drift.built_transforms` for that snapshot
before recording would let it refuse, or correct, rather than record a
fraction. That was not built here - it changes a capture path that needs
Resolve and a reel proposal to exercise, and this task was capture-only.

## 4. Two gaps in what the store can hold

Both are per-project findings in form and engine findings in substance,
because the declaration vocabularies are the engine's.

**A caption's position has no durable carrier.** `overlay_intent` v2
pins a canvas centre per segment id. All 21 pins in `geo-podcast`'s file
name segment ids that are on no live timeline: the caption re-render
kept the speaker, uuid and source-span prefix and changed only the
trailing content hash, retiring every pin. The only durable slot,
`style.subtitle_position.caption_row`, is one fraction for the whole
project - and the captain set one row on four reels, a different row on
a fifth, and left three alone. Neither shape can say that.

**`transform_override` is per-phrase and the captain edits per-reel.**
Four reels play the same closing source range (LC4932 12130-12317) and
hold three different Pan values. An override anchored to those words -
the only anchor the vocabulary allows, and rightly, since frames do not
survive a rebuild - moves all four.

The shared shape: the declaration vocabularies are keyed to the SPEECH
or to a RENDERED ARTEFACT, and a reel is neither. A reel-scoped key is
what both gaps want.

## 5. The limit that no amount of reading removes

The method is "the live value is not what the build snapshot recorded".
An edit that happens to reproduce the number the build already wrote is
indistinguishable from no edit at all. Nothing in this pass could have
found one, and nothing in a better pass could either. It is a property
of diffing against a baseline, and the only defence is that the captain
says what they changed.

## 6. Can the two work on the same timelines at once and stay synced?

Not yet routinely, and the reasons are specific rather than general.

**What held.** Reading is complete and it is cheap. One pass over eight
live timelines returned every clip, every transform and every marker at
every level in under a second per reel, and 2 of the 13 notes it found
were CLIP markers - one of them a captain ask that a timeline-marker-only
read does not see. The diff against the committed per-reel snapshots is
decisive about STRUCTURE: every clip on every reel paired by Resolve
`unique_id`, so "was this rebuilt" is answerable rather than guessed.
`feedback_ledger`'s durable identity worked as designed - its
`first_asked` dating picked out exactly the same four new asks the
snapshot diff did, computed from different evidence.

**What did not.** Three things, in order of how much they cost:

1. **The biggest edit the captain made cannot be written down.** A
   per-reel caption row has no declaration (§4). It is not a gap in this
   pass; it is a gap in the vocabulary, and until it exists a rebuild
   destroys that edit silently.
2. **Nothing serialises two writers** (§1), and nothing mediates a reader
   against a writer at all (§2). Today that cost twelve minutes of
   blocked reads and a retry. It did not corrupt anything, which is luck
   rather than design.
3. **The capture path would record the wrong number** (§3). The one
   command built for exactly this job is currently unsafe on this
   project.

**What would have to be true.** A reel-scoped declaration key, so an edit
the captain makes to one reel can be said about that reel. A single
gate on the Resolve connection rather than on placement, so a reader and
a writer take turns instead of one starving the other. And
`capture-transform` reconciling against the build snapshot before it
records.

**The limit that stays.** §5 - an edit that reproduces what the build
already wrote is invisible to any diff. The sync can be made reliable
for edits that differ from the plan; it can never be made complete
without the captain saying what they changed.
