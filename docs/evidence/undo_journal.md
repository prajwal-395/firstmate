# `library.tools.undo_journal` - the history behind its contract

This is the module docstring of `library/tools/undo_journal.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
UNDO: a touch is reversed IN PLACE from its journal; a rebuild is rolled back to its version.

The captain, 2026-09-23 (D5): *"Both: in-place for touches, versions for
rebuilds"*. Before this module a touch-up left a live `(archived round
NNN)` copy of the pre-touch timeline in his project as its only way
back, promotion and `--delete-files` had no way back at all, and the
stated recovery was "check out the data and rebuild".

One verb, two mechanisms
------------------------
`ren undo <project> [reel]` reverses the newest act nothing has reversed
yet, read off the reel version ledger (`versions.reel_versions`):

- a TOUCH (`reel_touchup.apply_touchup`, one reel or `--all-reels`) is
  reversed IN PLACE on the same timeline from its journal entry, below.
  An `--all-reels` touch is one act across reels: its entries share a
  `batch` and are reversed together.
- a REBUILD is rolled back to the version before it: that version's
  plan moment is restored into `reel_proposals_v2.json`, the reel is
  rebuilt through the reels process, the touches that stood on the
  earlier version are re-applied from their journals, and the result is
  compared with the version it was meant to reach. This is the one
  restore route the 2026-09-10 ruling allows (the Resolve project is
  derived - declarations plus a rebuild, never a restored binary), and
  it is D7's "binaries recorded by hash and regenerated".

The journal entry
-----------------
Written BEFORE the touch changes anything, under
`pipeline_output/review/undo/<entry id>/` - on the store's allow-list,
text only (JSON and exported `.comp` Lua), so it is versioned with the
project and a lean retention policy never has to guess which binary it
needs:

    entry.json
      format        "undo_journal/1"
      id, reel, final, resolve_project, batch, spec, gate_class
      status        open -> applied | failed -> undone | undo_failed
      before        {"tracks": reel_read.read_tracks(approved timeline)}
                    - every item's media, span, row, transform, comp
                    windows, clip colour and markers, read with the
                    timeline CURRENT so the transform is not
                    cursor-scaled (`reel_read.read_tracks`)
      removed       per item the touch deletes without re-placing it
                    (`remove_overlay`, the old side of `swap_pixels`):
                    its `composed_edit.capture_item` - properties,
                    geometry, node count and its comps exported to
                    `comps/` with their media windows
      fusion_manifest  a copy of the recorded manifest, when the touch
                    changes a played length (the undo changes it back
                    and needs the same route to the comp pass)
      after         {"tracks": ...} of the promoted timeline, written
                    before the replaced generation is deleted
      version       the reel version the touch produced
      undo          the undo's receipt, once reversed

Why the live copy can go
------------------------
`reel_touchup._promote` used to RETIRE the replaced generation into
`05 - Reels/Archive` as `... (archived round NNN)`. With `before`,
`removed` and `after` on disk, that copy is no longer the only way
back, so a touch now DELETES it (`reel_retirement.delete_backups`, the
same bounded delete promotion uses) - once `after` is written, never
before. A reel carrying a sign-off still retires, exactly as promotion
does: the captain approved that cut. The one state the journal cannot
record - a colour grade on an item the touch deletes outright - is
refused at the touch (`reel_touchup`: a graded remove, like a graded
swap, needs a rebuild), so nothing the delete takes is unrecorded.

How the undo runs, in place
---------------------------
1. REFUSE by name when the live timeline no longer reads as `after` -
   the projection below, row by row. Something moved it since the
   touch (the captain's hand, a rebuild, another touch), and reversing
   the touch over it would destroy that work.
2. Plan the inverse by DIFF, not per op: items only `after` has are
   the touch's; items only `before` has are what it took. A pair on
   one row playing the same file from the same source offset is one
   item the touch moved or retimed - re-placed at its old span through
   `composed_edit.apply_composed_edit`, with its grade copied from a
   transient reference duplicate of the live timeline (a re-placed item
   comes back with one colour node). An unpaired `after` item is
   deleted; an unpaired `before` item is re-placed from its `removed`
   capture and restored by `composed_edit.restore_item` - its own comps,
   at its own played length, so the comp is exact rather than stale. A
   transform or comp the touch changed on an item it did not move is
   written back in place.
3. Carry the captain's clip markers off every re-placed item.
4. VERIFY by re-reading: the live timeline must read as `before`, or
   the undo raises and the reference duplicate stays standing, named,
   holding the post-touch state.
5. Delete the reference duplicate, mark the journal `undone`, append an
   `undo` version and close the carried signature.

The projection
--------------
What "reads as" compares, per row in record order: span, source offset,
source file, name, the transform (every key `GetProperty()` returns
except the read-only ones), each comp's media-window frames, and clip
colour. Not compared: unique ids (a re-placed item is a new object),
markers (carried, and a note typed after the touch must not block its
undo), CDL and flags (a touch never carried either, so the journal
would claim a restore nothing performs).

`tests/unit/resolve/test_undo_journal.py`.
```
