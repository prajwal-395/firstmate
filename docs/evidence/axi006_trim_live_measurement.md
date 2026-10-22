# The AXI-006 live measurement: a pause-removal trim on a built reel

2026-10-22.  The companion to `docs/evidence/axi006_trim_carry.md` (the
comp carry itself): what the trim did on a REAL built reel, before and
after, and what the machine could not do.

## The subject

Reel: `Reel 13 - breaktest-selected-dc7fa3f8` in the Resolve project
`Ren Scratch - breaktest` - a copy of the captain's Podcast reel, on a
Ren-owned scratch copy of the project (`ren-scratch-axi006`).  The
captain's project, timelines and media were never touched, and the
source media is read only.

The request: a bounded source trim of the reel's last picture block -
`V1[3]`, the block at record frames 1503..1815, source frames
12006..12318 - narrowed to 12006..12300, the eighteen silent frames at
its tail.

## BEFORE (the code before the comp carry)

    error: trim refused before writing: timeline snapshot cannot preserve
    'LC4932.MXF': the item carries Fusion compositions (1) that this
    replacement path cannot carry - a re-place gives the item a new
    identity, and a comp is keyed to the item it was written for.

The trim never reached the reel: the capture refused the item because it
carries a treatment, and the reel's treatment is the reel's LOOK (the
grade, the glow, the vignette, the drift) - every picture clip of a
built reel carries one, so this refusal met every trim the engine could
receive.

## AFTER (the comp carry)

The trim passes the capture and reaches the carry, which reads the
reel's own record of its treatments and refuses - PRECISELY, and for a
reason that is the reel's and not the engine's:

    error: the reel's recorded manifest does not describe V1[3]
      why:  the comp pass maps a clip spec to an item by source path
            and window; a manifest that does not describe this item
            would write treatments onto the wrong clips
      fix:  rebuild the reel so its manifest describes this timeline,
            then trim it

That refusal is the carry working: it will not re-derive a treatment
from a record that does not describe the reel, because the comp pass
maps specs to items by source path and window, and a manifest from
another cut writes treatments onto the wrong clips.

## What the record is missing - and it is not the engine

The reel's own recorded manifest does not describe the reel, and the
numbers say why.  The manifest's `tracks` section carries each picture
clip's source window in SECONDS; the live items carry it in FRAMES:

| spec | seconds | -> frames | the live item is at | off by |
|---|---|---|---|---|
| `reel_picture_01` | 1860.6087 | 44610 | 44610 | 0 |
| `reel_picture_03` | 1945.7355 | 46651 | 46653 | **2** |
| `reel_picture_05` | 1983.7735 | 47563 | 47563 | 0 |
| `reel_picture_06` | 500.712 | 12005 | 12006 | **1** |
| `reel_picture_07` | 512.9713 | 12299 | (no item: the reel's ending hold replaced it) | - |

The comp pass's matcher requires the frames to agree EXACTLY, so two of
the five specs reach no item - and the same mismatch refused the reel's
own build, minutes before this measurement:

    ✗ [V1:3] reel_picture_06: planned comp reaches no timeline item
      (spec file '.../LC4932.MXF'; placed files ['.../LC4932.MXF', ...])

So the reel on disk carries the treatments of an EARLIER build (whose
record is gone), its own build cannot apply its own treatments, and a
trim of it cannot re-derive them either - because the record and the
reel disagree by one and two frames, and by the clip the reel's ending
hold replaced.

That is the finding of this measurement, and it is in the reel BUILD's
lane rather than the trim's: **the recorded manifest carries the
PLANNED source windows, and the reel's items are placed with a sync
offset and with the ending hold substituted for the last block, so the
manifest does not describe the reel it built.**  The comp pass is
right to refuse (a spec that reaches no item would write a treatment
onto the wrong clip), and the fix is to record the windows the reel
ACTUALLY has - or to let the matcher carry the sync offset it knows
about.

## What did not land, and why

The write itself did not land on this machine, and the reason is the
finding above: the reel's record does not describe the reel, so the
carry refuses rather than write treatments onto the wrong clips.  The
session, the lease that names the project, the scratch open and the
trim's own command all ran - the run reached the carry every time.

The measurement therefore closes the REFUSAL quality (before: the
engine cannot carry a composition at all; after: the engine names the
reel's own record, and the reel's record is what is wrong), and leaves
the landing to the build's fix.
