# `library.tools.reel_look` - the PowerGrade route, measured

Moved verbatim on 2026-10-02 from `tests/unit/reels/test_reel_power_grade.py` when the
test file kept only its invariant. Where this and the code disagree, the
code and its docstring win.

## Module docstring of `tests/unit/reels/test_reel_power_grade.py`

```text
The declared PowerGrade is THE grade on a reel, and the CDL rides inside it.

Measured on the captain's `Podcast (field test)` / `Reel 09 -
your-website-is-only-20-percent`, 2026-09-10, with stills exported off
the live timeline (task `vep-fusion-grade-check-the-frame`):

* Every picture item read `GetNumNodes() == 1` with an empty label, and
  Resolve's own grade export for those clips decompressed to a 171-byte
  node body carrying no named node at all. NOTHING had ever been
  applied - the reels predate the CDL route (#885).
* The declared `v04_teal_split` CDL, pushed through the exact `SetCDL`
  call `reel_look.apply_cdl` and step 6.01 both make, returned True on
  all six clips and rendered a still BYTE-IDENTICAL to no grade: 0 of
  2,073,600 pixels moved, max delta 0. Reproduced six times including a
  six-second settle and a re-grab, with `SetCDL(saturation 0)`
  immediately before and after as a positive control (601,760 pixels,
  max delta 154), so the clip demonstrably responded. Each of the four
  terms moves the picture on its own; the declared combination does not.
* `GetNodeGraph().ApplyGradeFromDRX(path, 0)` returned True and a
  re-fetched graph read back 8 nodes - `Input`, `BAL/EXP`, `CONTRAST`,
  `SAT`, `W&B`, `Output`, `FLC`, `Corrections` - matching the `.drx`'s
  own compressed node body exactly, and moved 599,583 pixels (28.9% of
  the frame, which is the whole picture area under the bezel).
* `SetCDL({"NodeIndex": "2", ...})` onto that graph's own `BAL/EXP`
  node moved picture luma 46.91 -> 84.17 across 533,240 pixels, and
  putting the node back to unity returned the frame byte-identical to
  the DRX-only still.

So the two routes are not two strengths of one grade, they are one that
delivers and one that does not, and the pixel evidence is what settles
it rather than either call's return value. What is CHECKABLE here is
the routing, the values that reach the calls, and the refusals. Whether
the resulting picture is the look the captain wants is WATCHABLE, not
checkable, and stays the captain's call.
```

## Section 6 comment of the same file

```text
# ── 6. SetCDL RETURNS TRUE AND CHANGES NOTHING ───────────────────────────
#
# The regression this whole module exists for. Measured on the
# captain's Reel 09, 2026-09-10: the declared CDL returned True on all
# six picture clips and the exported still was byte-identical to no
# grade - 0 of 2,073,600 pixels moved - with SetCDL(saturation 0)
# either side as a positive control that moved 601,760. There is no
# GetCDL and a `.drx` exported from a SetCDL-graded clip does not carry
# the CDL, so NOTHING readable back distinguishes the two cases. The
# rule that falls out is not "check harder", it is "do not report a
# grade this route claims to have applied".
```

## Module docstring of `tests/unit/reels/test_reel_motion_reaches_every_row.py`

```text
A treatment planned for a clip on ANY picture row must reach that clip.

Found 2026-09-10 on Reel 09: the motion answer planned drift on four
shots, but the reel Fusion manifest reached V1 only, so the two drifts
on V2 (shots 0 and 5, Craig's picture row) never reached a comp and
never even reached verification - the receipt read clips_checked = 2
of 4 planned. Before the per-speaker ruling every reel had one picture
row and V1-only was harmless; the manifest did not follow the layout.

The defect is not "V2 was forgotten", it is "the manifest assumed one
row" - so this test builds a two-angle reel through the layout owner's
own answer (`timeline_layout.plan_layout`) and proves every planned
drift draws, on both rows. It fails on the old code: the old
`fusion_manifest` takes no plan and emits V1 alone, so the V2 specs
are missing and the V2 items get no comp.
```
