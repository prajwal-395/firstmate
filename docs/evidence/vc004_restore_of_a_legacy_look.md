# VC-004: a restore reads the look the version RECORDED

The live run of PR 3096's last three steps, on a Ren-owned scratch copy,
2026-10-21. What it measured, what it found, and what the fix changed.

## Where

The Resolve project `Ren Scratch - vc004` (a scratch project) and the
Ren project `vc004-hand-edit`
(`~/Documents/content_stuff/video_projects/vc004-hand-edit`). Every
Resolve-side step went through the SERVING broker
(`ren resolved submit resolve_axi ...` / the broker's own turn for the
lease); the Ren-side steps took the machine lease. No captain project,
timeline or media was touched: the podcast source media was never read,
and the only media in the scratch project is a 3-second clip generated
with ffmpeg into /tmp.

The reel: one picture clip on `Ren Scratch - vc004 - Empty`, carrying a
Fusion composition (a BrightnessContrast node) and a CDL.

## The gate, and what it did

The punchlist's release gate for VC-004:

> A user can modify a reel manually, let Ren make an additional change,
> inspect the result, reject it, and restore the exact accepted
> generation - including its grade and relevant effects - without losing
> the human edit.

The steps, on the ledger as PR 3096 left it (version 5 is the adopted
hand edit, version 6 is Ren's additional change):

| step | what it does |
|---|---|
| `ren-change` | Ren's own change on the live timeline (the clip enabled again, re-graded) |
| `record-ren-change` | the ledger record of that change (a build version) |
| `archive-retiring` | the promotion's archiving step over the generation being replaced |
| `restore` | `undo_journal.restore_version` of the ADOPTED version |

### Before the fix (the run of 2026-10-21)

```
restore -> UndoNotVerified: the restored 'Ren Scratch - vc004 - Empty' does
not hold the grade and effects version 5 recorded: vc004_clip.mp4: fusion.
The timeline holds the archive's generation, which is not the recorded one;
the rollback was not recorded. the recovery archive carries the effects, so a
lost effect is a loss the restore did not make good
```

The refusal was WRONG. The read-back of the same timeline, minutes
earlier, said:

```
live_rows_digest   12301caa39f97816   (== version 5's rows_digest)
live fusion        comp_count 1, comp_names ["Composition 1"],
                    comps [ {tools: [BrightnessContrast, MediaIn, MediaOut]} ]
recorded fusion     comp_count 1, comp_names ["Composition 1"]
```

The rows came back right, the Fusion composition was still there, and
the restore refused the generation because the RECORDED look carried no
`comps` key while today's read does. The Fusion comps were added to the
read by #3097 on 2026-10-21, and version 5 was recorded before that, so
the two shapes differ by a key - and the comparison demanded the same
keys on both sides.

Two more things the same run measured:

- the refusal came AFTER the archive's generation had already taken the
  reel's name: the live Resolve id went from `e4e3cf87-...` to
  `2891be92-...` while the ledger recorded no rollback, so the ledger
  and the timeline disagreed about what the reel held;
- the next restore of that reel was refused outright - "the live
  'Ren Scratch - vc004 - Empty' is Resolve ID '2891be92-...', not the
  recorded current version 'e4e3cf87-...'" - because the version the
  restore had recorded named the object the ARCHIVE was taken from, and
  a restore replaces that object with the imported one.

### After the fix

```
restore -> {"restored": 5, "replaced": 7, "restored_from_archive": true,
            "preservation_verified": true,
            "preservation_note": "the restored generation holds the recorded
            grade and effects"}

read-back: live_rows_digest 12301caa39f97816  (== version 5's rows_digest)
           rows_agree true
           live fusion comp_count 1, comp_names ["Composition 1"],
                      comps [ {tools: [BrightnessContrast, MediaIn, MediaOut]} ]
```

The hand edit came back (the clip is disabled again, the rows digest is
version 5's), the Fusion composition came back with it, and the ledger
recorded the rollback with `preservation_verified: true`.

And the identity: the version the restore recorded names the object that
holds the generation NOW (`2fe9d92b-...`, the imported timeline), not the
object the archive was taken from.

### The refusal, live, with nothing changed

The look that does NOT survive is the grade: a recovery archive carries
the effects, not the grade (measured 2026-10-21 over the 117 `.drt`
recovery archives on this machine: 77 carry the Fusion composition, none
carries a CDL, a LUT or a node graph). With a CDL written into version
5's recorded look (a fault injection on the scratch copy, put back
afterwards), the restore refuses:

```
restore -> UndoNotVerified: ... does not hold the grade and effects
version 5 recorded: vc004_clip.mp4: color. The timeline was NOT changed:
the generation that was there is still there; the rollback was not recorded.

live Resolve id before the attempt  2fe9d92b-9359-4a61-9f02-1534d7e15ba3
live Resolve id after the attempt   2fe9d92b-9359-4a61-9f02-1534d7e15ba3
versions in the ledger               8 (no new one)
```

The refusal happens where the evidence is - on the imported archive,
before anything is replaced - so a refused restore changes nothing and
records nothing.

## Before and after, with the denominators

| measurement | before | after |
|---|---|---|
| live restores of the adopted generation | 0 of 1 succeeded (1 of 1 refused a generation that kept its look) | 1 of 1 succeeded, look verified |
| live Resolve id across a REFUSED restore | changed (the timeline was replaced) | unchanged |
| versions recorded by a refused restore | 0 | 0 |
| the Resolve id a restored version names | the archive's object (the next restore refused) | the live object (the next restore is allowed) |
| unit tests naming the defects | 4 of the 7 new tests fail without the fix | 7 of 7 pass; the 3 that guard the fidelity pass both ways |

The fidelity guard: what the recorded look held and the live one does
not is still a loss, and is still named - a lost comp, a lost grade and a
lost comp record are each reported. Only a detail the LIVE read carries
and the RECORDED look never held stopped reading as a loss.
