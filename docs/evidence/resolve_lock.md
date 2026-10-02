# `library.tools.resolve_lock` - the history behind its contract

This is the module docstring of `library/tools/resolve_lock.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
One Resolve, many writers: the unit of exclusion, and the fence.

`assert_current_timeline` at the bottom is the enforcement; the rest of
this module is what makes it enforceable when more than one process is
driving the same DaVinci Resolve.

What the unit of exclusion is, and why
--------------------------------------
It is the instance's CURSOR - the (current project, current timeline)
pair - held for a CRITICAL SECTION, not for a build.

Nothing finer is available. `MediaPool.AppendToTimeline` writes to the
project's CURRENT timeline; there is no per-timeline write handle to
take a lock on, so "lock the timeline I am writing to" is not a thing
the API can express. Two agents writing two different timelines still
contend, because they contend for the one cursor.

Nothing coarser is affordable. A build is mostly not Resolve: vision,
ffmpeg, Remotion renders, planning, the LLM calls. Holding the instance
for a whole build would serialise all of that and delete the
parallelism this exists to protect. So the lease is taken around the
section that establishes the cursor and writes through it, and released
the moment that section ends.

`library/tools/concurrency_routing.py` is the table that says which
operation class needs which of these, stated so a supervisor applies it
without judgement.

Why a lock is not enough, and what the fence is for
---------------------------------------------------
The captain edits by hand in Resolve while agents build. That is the
workflow, not a hazard to design out - and a human will never take a
lock. Neither will a test that connects to Resolve incidentally.

So exclusion is only half. The other half is DETECTION: a holder
records the cursor it established and re-reads it before every write
and once more at release. `assert_current_timeline` is the per-write
check; `cursor_fence` is the whole section. A foreign move is raised by
name rather than silently written through - which is the only thing
that works against a writer who never cooperates.

What was measured, 2026-09-12, and what it settled
--------------------------------------------------
`docs/DUAL_WORKFLOW_SYNC_2026-09-12.md` is the measurement, taken from
opposite sides while a sibling lane built into the captain's project:

* From outside: 366 samples over twelve minutes, two seconds apart,
  found the placement lock UNHELD every time while that lane created a
  staging timeline, promoted it, and moved the current timeline six
  times.
* From inside: a process HELD the lock exclusively for two minutes and
  watched that lane move the current timeline three times regardless.
  `flock` behaved exactly as written. The acquisition cost 0.000s
  because there was no one to wait for.

So the lock was not merely untested. It was exercised and measured to
protect nothing, because the state it guarded is reached by a route
that does not pass through it. A mutual-exclusion primitive only
excludes parties that take it.

That is a finding about WIRING, not about locking, and this module is
the repair: the route now passes through the lease, because
`assert_current_timeline` - the 22-call-site check the measurement
found was the only real guard - refuses to run outside one.

ONE CONCLUSION OF THAT MEASUREMENT IS OVERTURNED HERE, deliberately.
It read: *"a read-only reader cannot take the placement lock to protect
itself and would not be helped by it - it does not place - so a reader
racing a writer is unmediated by design."* True of a lock only writers
take. A reader IS helped by a SHARED lease: it does not exclude other
readers, and it does exclude a writer that would delete the timeline
its handle points at halfway through the read.
`concurrency_routing.RESOLVE_READ` is that class. The rest of the
measurement stands unchanged.

Why the guard cannot be forgotten
----------------------------------
`resolve_placement_lock()` was removed on 2026-09-12 with zero callers
in library, tests, docs or scripts, while `assert_current_timeline` had
22. A guard nobody enters is worse than no guard, because it reads as
coverage (AGENTS.md 10.4). It does not come back under that name: the
name went with the shape that failed, which was a lock a caller had to
remember to take.

`resolve_lease` is the replacement and it is WIRED, with the failure
repaired at its source. Taking it is no longer a rule a caller may
forget, because `assert_current_timeline` REFUSES without one. Every
one of those 22 write paths is guarded by the check it already called,
and the work was placing the lease at eight entry points rather than at
every write. `tests/test_resolve_guard_wiring.py` pins that the entry
points hold it and that the refusal cannot be turned off from inside
`library/`.

The captain's signal: deference, not just detection
---------------------------------------------------
Detection protects the agent FROM the captain. The other direction -
protecting the captain from the agents - is a file the captain owns,
next to the lease, whose mere presence means "hands off, I am
editing": `captain_hold()` sets it, `captain_release()` clears it,
`captain_present()` reads it, and
`python -m library.tools.resolve_lock captain-status` says aloud
whether it is set. Every `resolve_lease` acquisition waits for it to
clear before contending for the flock; `prefer_lease` does not, because
a human-initiated action never queues behind its own owner's signal.

Four properties, stated so a later reader does not renegotiate them:

* An agent that finds the captain present WAITS, not fails. Failing
  would punish the captain for touching their own editor, and failing
  mid-build is its own damage. This is the opposite of what
  `cursor_fence` does to a foreign cursor move on purpose, and both
  behaviours are correct for their own direction.
* A held lease is NEVER revoked. An agent inside its critical section
  when the signal appears finishes the section - seconds long, cursor
  already established - and releases. A half-written timeline is worse
  than a delayed one, which is the same reason the handbrake finishes
  the step it is in before stopping (`run_control.py`). The fence
  still detects any cursor move that actually happened meanwhile.
* A stale signal raises instead of wedging or expiring. The wait is
  bounded by the acquisition's own timeout; past it, `CaptainPresent`
  (a `ResolveBusy`, so the live-Resolve suite still skips on it) names
  the signal path, its age, and the exact clear command. Nothing
  auto-clears it: expiring the hold behind the captain's back would
  make the guarantee a lie exactly when they are relying on it, and
  proceeding while it stands would too.
* Presence is the signal, and presence fail-closed. An unreadable
  signal file still counts as set - failing open would run the build
  the captain asked to stop, the same rule `run_control` holds for
  `pipeline.hold`.
```
