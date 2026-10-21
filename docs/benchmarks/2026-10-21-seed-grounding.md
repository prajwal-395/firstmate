# Seed grounding: the detector box, the seed, and the track (2026-10-21)

## The defect

A detector seed carried a box and nothing else:

```python
{"box": [x1, y1, x2, y2], "label": "baseball cap", "category": "cap",
 "detector": "owlv2", "score": 0.25}
```

`ObjectSegmenter.segment_clip` prompted SAM 2 on **frame 0** for every
such seed, whatever frame the detector had measured the box on. A box
measured at 5.5s was therefore placed on the picture at t=0, the track
was born on whatever happened to sit under the box there, and the
tracker inherited a seed that was never grounded.

The seed also carried no frame geometry, so a box measured on a
rotated or cropped rendering of the clip (a phone clip carries its
rotation as side data, so the same clip decodes at two sizes) could
not be distinguished from a box measured on the clip's own frames.

## The measurement

One hand-labelled sample on real footage:

- Clip: `IMG_1808` (Apple 001 raw media), read-only; the run worked on
  a Ren-owned scratch copy outside every checkout. No project, no
  timeline, no original file was touched.
- Sample grid: the tracker's own (`ffmpeg -vf fps=2`), so a labelled
  index IS a tracker sample index.
- Object: the cap the subject wears, labelled by hand on four frames
  from a tenth-grid overlay
  ([`2026-10-21-seed-grounding-labels.json`](2026-10-21-seed-grounding-labels.json)).
- Detector: OWLv2 (`google/owlv2-base-patch16-ensemble`, score floor
  0.1), the detector the MU-15 bakeoff chose.
- Measure: bounding-box IoU. A track that does not reach a labelled
  frame scores 0.0 there - the object is not tracked there.

Three numbers per labelled frame:

| Number | What it asks |
|---|---|
| Detector box IoU | Is the detector's own box on the object? (vs the hand label) |
| Seed inherited IoU | Is the tracker's box at the seed frame the seed's box? |
| Track box IoU | Is the tracker's box at the labelled frame on the object? (vs the hand label) |

## Before

[`2026-10-21-seed-grounding-before.json`](2026-10-21-seed-grounding-before.json)

| Labelled frame | Time (s) | Detector box IoU | Seed inherited IoU | Track box IoU | Track frames | Seed frame |
|---:|---:|---:|---:|---:|---:|---:|
| 11 | 5.50 | 0.4417 | 0.0000 | 0.0000 | 1 | - |
| 15 | 7.50 | 0.4060 | 0.0000 | 0.0000 | 2 | - |
| 19 | 9.50 | 0.4466 | 0.0000 | 0.0000 | 3 | - |
| 23 | 11.50 | 0.4048 | 0.0000 | 0.0000 | 3 | - |

- Detector box IoU (mean): **0.4248**
- Seed inherited IoU (mean): **0.0**
- Track box IoU at the labelled frame (mean): **0.0** (0/4 at IoU >= 0.5)

The detector was not the problem: its box on the labelled frame is on
the cap (IoU 0.40-0.45 against the hand label; the box is looser than
the label because OWLv2 takes in the hair under the cap).

The TRACK was the problem. Each seeded track occupied 1-3 frames at the
clip's start - the picture under the cap's box at t=0, which on this
clip is the car's roof lining - and never reached the frame the box
was measured on. The seed's own frame was not recorded anywhere, so
nothing downstream could say which frame the track was born on.

## After

[`2026-10-21-seed-grounding-after.json`](2026-10-21-seed-grounding-after.json)

| Labelled frame | Time (s) | Detector box IoU | Seed inherited IoU | Track box IoU | Track frames | Seed frame |
|---:|---:|---:|---:|---:|---:|---:|
| 11 | 5.50 | 0.4417 | 0.9077 | 0.4189 | 45 | 11 |
| 15 | 7.50 | 0.4060 | 0.9556 | 0.4064 | 41 | 15 |
| 19 | 9.50 | 0.4466 | 0.8945 | 0.3992 | 36 | 19 |
| 23 | 11.50 | 0.4048 | 0.9528 | 0.3925 | 32 | 23 |

- Detector box IoU (mean): **0.4248** (unchanged - the fix is not in
  the detector)
- Seed inherited IoU (mean): **0.9276**
- Track box IoU at the labelled frame (mean): **0.4042**

The track now starts on the frame the box was measured on, its box
there is the seed's box (IoU 0.89-0.96), and it holds the object: the
track seeded at 11s still scores 0.37 / 0.29 / 0.28 against the hand
labels at 7.5s, 9.5s and 11.5s, and the track seeded at 7.5s scores
0.42 / 0.36 at 9.5s and 11.5s.

The end-to-end number against the hand label (0.4042) is bounded by the
detector's own box (0.4248): the tracker's mask of a worn cap takes in
the hair under it, and the fix does not move the detector.

## The fix

`library/tools/analysis/object_segmentation.py`.

- The seed contract carries the frame it was measured on:
  `frame: {time_seconds, width, height}`. A seed without it is
  refused (`check_seed_boxes`), because a box that does not say where
  it came from is exactly the defect.
- `segment_clip` prompts the tracker on the seed's own sample
  (`seed_sample_index`: the nearest sample to `time_seconds *
  sample_fps`), and the track records the frame it was seeded on
  (`TrackedObject.seed_frame`).
- A seed whose declared frame size has another aspect than the clip's
  frames is refused (`seed_geometry_matches`): a normalized box cannot
  be mapped across a rotation or a crop, and placing it anyway seeds
  the tracker on the wrong content. A seed measured on a DOWNSCALED
  copy of the same frame still maps - a resized frame keeps its
  aspect - so the guard refuses rotations, not resizes.
- A seed whose frame falls outside the clip is refused, not clamped.
- The step's content key covers the seed's frame
  (`capabilities/objects/segment.py::_seed_key`), so a seed measured
  on another frame re-measures instead of reusing the matte cut from
  the old track.
- `spatial_grounding.tracker_seed` emits the same anchor, so a
  grounded box becomes a seed without losing its frame.

## What this does not fix

- The detector's box quality. OWLv2's box on this object is 0.42 IoU
  against a tight hand label; that is a detector question (D-01), not
  a seeding one.
- The track's own quality. The mask drifts off the cap for a frame or
  two (the seeded track's box at frame 13 is the whole head), and a
  worn cap is a hard object to hold. That is tracking (D-03).
- A crop that keeps the aspect. The guard catches a rotation or a
  crop that changes the aspect; a same-aspect crop is invisible to a
  seed that carries only a frame's size, and mapping one needs a crop
  record the contract does not carry yet.
- The frames BEFORE the seed. A track cannot exist before the frame
  its evidence comes from, so a seed at 11s yields a track from 11s
  on. Before the fix the track covered the clip's first frames - on
  the wrong object.
- Frame-exactness of the seed. The seed's time is mapped to the
  NEAREST sample (0.5s at 2 fps), so a box measured between samples
  is placed on the closest one.

## Reproducing

```sh
PY=$(bin/vep | sed -n 's/^PIPELINE_PYTHON=//p')
bin/vep -m library.tools.heavy_work_lock run --owner d02-seed -- \
  "$PY" -m library.tools.analysis.seed_grounding_bench \
  --clip /path/to/scratch/copy.mov \
  --labels docs/benchmarks/2026-10-21-seed-grounding-labels.json \
  --frames-dir /path/to/scratch/frames \
  --out /tmp/seed-grounding.json
```

The harness decodes the clip into the caller's scratch directory, asks
the detector for the phrase on each labelled frame, turns the answer
into a seed, seeds the tracker with it, and scores the result. Run it
on the pre-fix and the post-fix revision to see the two tables above.

The defect-naming tests are
`tests/unit/picture/test_seed_grounding.py`; on the pre-fix revision
12 of its 14 tests fail, including the one that names the defect (the
tracker is prompted on the seed's own frame, not on frame 0).
