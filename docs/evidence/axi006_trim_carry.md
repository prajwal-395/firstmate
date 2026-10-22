# The comp carry: a trim that re-places a clip keeps its treatment

AXI-006, part two (2026-10-21).  The companion to
`docs/evidence/reel_touchup.md` (the touch-up path's own history), and
the write-up for `library/tools/comp_carry.py` and the trim's own
read-back promises.

## The defect

A trim re-places the clip it trims - the Resolve scripting API has no
trim, only append plus delete - and a re-placed item is a NEW object.  A
composition is keyed to the item it was written for, so the trim DROPPED
the clip's treatment, and the capture refused it by name:

    the item carries Fusion compositions that this replacement path
    cannot carry

On a built reel every picture clip carries a treatment (the reel's
look, the grade, the drift), so the refusal met every trim the engine
could receive.  The measured refusals are in
`docs/benchmarks/2026-10-20-live-user-visible-latency.md` (findings 3
and 7) and in PR 3076's own measurement.

## The carry

`library/tools/comp_carry.py` owns it, in three steps and a proof:

1. **the reel's RECORD is read before the write** - the recorded Fusion
   manifest (`scratch/reel_look/<slug>_fusion_manifest.json`), and the
   label of the clip the trim names.  The label is found with the comp
   pass's OWN matcher (`map_clips_to_items`, made public for this),
   which matches a clip spec to an item by full source path AND window
   - never by position alone, and never by basename: a matcher that
   succeeds against wrong material is worse than one that fails.
2. **the clip is re-placed**, and the composition goes with the old
   item.
3. **the comp pass writes the treatment again**, keyed to the length
   the clip NOW plays - in its own process (AGENTS.md 5: never create a
   timeline and use `ImportFusionComp` in the same Python process), and
   to a manifest of its own, so the reel's own record is not
   overwritten by a one-clip manifest.
4. **the write is PROVED**: the composition is counted on the new
   item, and its media window is read back against the frames the item
   plays.  A composition whose `MediaIn` does not cover the played
   frames draws nothing over them, and the reel renders black there.

A record that does not describe this reel is a REFUSAL, not a guess:
the comp pass maps specs to items by source path and window, so a
manifest from another cut would write treatments onto the wrong clips.

The manifest's own name is not enough either, and that was the other
half of the defect: the build files the manifest under the STAGING
container it composited on, and the promotion renames the timeline but
not the file - so a built reel's record was filed under a name only the
staging container had.  The promotion now renames the manifest with the
timeline, and the lookup finds the reel's record by the rows it
DESCRIBES (PR 3076).

## The read-back's own promises

Two reds on `main`, in the same family, and the same cause: the
read-back's COLLATERAL check was standing in for the operation's own
promises, and the collateral check's scope depends on whether the route
carries the engine's own comparison.

- **A split's two halves read as clips that appeared where none was
  authorized** - a split replaces ONE item with TWO, and the scope did
  not know that - so a GOOD split was refused before it wrote anything
  (`tests/unit/resolve/test_split_adapter.py`).  Fixed on `main` by
  PR 3104's follow-up, which gives the split's blast radius to the
  preservation verdict.
- **A trim's refusal changed when its route was qualified** - the
  linked audio and the target's picture state are the trim's own
  promises, and the collateral check named them only when the scope did
  not cover them.  The trim's test now asserts that BOTH violations
  are named and stops pinning the order the comparison happened to find
  them in: the order was never the contract.

## Two more defects the live work found

- **The touch-up took many leases, and a lease is a broker CALL.**
  Between two of them the broker could grant another lane's turn, and
  that lane's exclusive lease moves the cursor to its own project - so
  the touch-up's next call found a project that was not its own (the
  2026-10-22 live build lost its Resolve project and timeline between
  its own calls, the same way).  `apply_touchup` now runs inside ONE
  broker session (`library/tools/resolve_session.py`, PR 3021), the
  same way the reel build and the delivery do.
- **The semantic compound's trim never reached its clip.**  It
  addressed its clip as `--track V1`, and the AXI reads tracks as
  `video1`/`audio2` and refuses a track it does not recognise: "bad
  track 'V1' - tracks read video1, audio2, ...".  The row is now
  spelled the way the AXI reads it.

## What was not measured here

The live WRITE did not land on the machine this ran on.  Every Project
Manager WRITE is refused there, machine-wide: `OpenFolder` answers
None, `LoadProject` does not take, `SaveProject` answers None, and even
`CreateProject` answers "Resolve did not create scratch project" - in
the broker's own worker as well as in a direct client.  The reads
answer; the writes do not.  With no project switch available, no scratch
project can be opened, and a trim on a scratch copy of a built reel
cannot run.

Everything up to that point was verified on the machine: the session
takes the broker's turn, the lease names the project, the runner polls
every ten seconds, and the run fails on the project open and nothing
else.
