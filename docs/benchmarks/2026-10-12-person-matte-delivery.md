# Delivery-tier person mattes (MU-10), measured 2026-10-12

`library/tools/person_matte.py` draws a person's matte on every played
frame (Apple Vision `VNGeneratePersonInstanceMaskRequest`), picking the
person's instance at each M3d sample by face containment and carrying
it frame to frame by IoU. The route it replaces for a person holds a
2 fps mask across each half second (`subject_grade.write_subject_matte`).

## IoU against a per-frame reference, laterally moving subject

Fixture: a real person (the qualification copy of LCATL0011, t = 60 s,
1920x1080) cut out with Vision's own instance mask and slid across a
person-free plate built from the set's wall, 72 frames at 24 fps,
10.99 px per frame. The cutout's mask, shifted per frame, is the exact
per-frame reference. GOP 12, so M2's I-frame grid is 2 Hz; anchors are
the head box at those samples (what M3 supplies). Both mattes ran
through the shipped code: `write_person_matte` for the per-frame one,
`write_subject_matte` over the same Vision masks at the 2 Hz samples
for the held one, so the comparison isolates holding.

| matte | mean IoU | p10 | min | mean, frames between samples |
|---|---|---|---|---|
| per frame (delivery tier) | **0.748** | 0.481 | 0.451 | 0.753 |
| held at 2 fps | 0.527 | 0.285 | 0.223 | 0.511 |

All 6 anchors resolved, 0 frames without the person. Wall time 20.5 s
for 72 frames at 1920 px (decode + Vision), on a shared machine.

The per-frame mean is below 1 because the reference is Vision's mask of
the ORIGINAL frame and Vision re-segments the composite (the pasted
laptop and plate edges differ); that offset is common to both rows.

Fixture recipe (scratch, not committed): extract the frame with
`ffmpeg -ss 60 -i <copy> -frames:v 1 -vf scale=1920:1080`, take the
largest person instance at full grid, drop the other speaker's hand
(rows >= 1000, cols >= 1600), alpha = person mask union the laptop
rectangle [664:1126, 568:860], plate = the wall strip x 60..370 tiled
mirrored, shift without wrap from -360 to +420 px, encode libx264
crf 12 g 12 bt709.

## Export proofs (hermetic, 2026-10-12)

No live Resolve: this task's machine rule forbids touching the running
broker, so both proofs flatten exactly what the build places - the
precomp on its overlay row over the untouched picture, premultiplied
Full - with ffmpeg and measure the EXPORTED pixels (AGENTS.md §5),
lossless end to end (PNG plate, PNG clip, qtrle precomp, PNG export).
Placement itself runs under the cursor lease in Ren Qualification; the
build-path test (`tests/unit/resolve/test_subject_grade_row.py`) proves
the segments surface and the row is minted before the lease.

- Behind-subject occlusion
  (`test_the_export_shows_the_subject_occluding_the_title`): white
  title precomposited under the subject matte, overlaid on a 128-grey
  plate. Exported mid frame: subject box reads 128 (the plate - the
  title is behind them), everywhere else 255 (the title's own white).
  A title drawn on top instead of under would read 255 in the box.
- Subject-grade luma in vs out
  (`test_the_export_grades_the_subject_and_holds_the_room`): plate 128
  graded at gain 1.4, overlaid on the same plate. Exported frame luma
  (Rec.709): inside the matte 179.0 (128 x 1.4, carried as 179),
  outside 128.0, difference 51.0. Through main's Loader route the same
  export reads the room's luma inside the matte too: main's
  `subject_grade.py` has no `precomposite_subject_grade` and its
  manifest carries no `subject_grade_overlay` (both counts 0 against
  `origin/main`), while the Loader keys it does carry
  (`subject_grade_matte` in `comp_builder.py`) never decode on a
  Resolve 21.1 timeline comp (`deliver_loaders.py`).
