# M3d person regions: resolution, cut-off and cost on geo-podcast

Date: 2026-10-11 (final run finished 2026-10-12)
Branch: `fm/vep-mu05` (plan item MU-05)
Code: `library/tools/person_regions.py`, `library/tools/person_regions_helper.swift`

## Result

On the two long geo-podcast cameras, measured into scratch memory roots (the captain's memory and media were read only):

| Source | Samples (M2, ~2 Hz) | Face samples with an M3b face track | Inside exactly one instance | Inside none | Inside several |
|---|---:|---:|---:|---:|---:|
| LCATL0013 (68 min) | 8,191 | 8,145 | **8,116 (99.64%)** | 29 | 0 |
| LC4932 (82 min) | 9,873 | 9,750 | **9,735 (99.85%)** | 15 | 0 |
| LCATL0011 (3.6 min, smoke) | 429 | 422 | 422 (100%) | 0 | 0 |

The acceptance bar was 95%. The faces inside no instance are samples where Vision returned no person mask for a visible face. LCATL0013 t=75.2 is one of them: Craig is on screen and unmasked.

**The off-frame hand (LCATL0013 t=60-90).** Vision DOES segment the hand of the person off the right edge as a person instance. That happened at every input size measured (384, 768, 1152 and 1920 px), so the detector cannot be what keeps it out. M3d keeps it out by ownership:
- In the window there are 85 instances. 59 belong to Craig's region tracks (face track `face_001`).
- The other 26 are unowned, reason `no_face_inside`.
- No region track in the window is owned by anyone else, and no owned instance lies in the right part of the frame.
- Checked by eye on an overlay of the stored masks at t=60.5, 63, 66, 70, 75 and 80. Owned masks cover Craig, unowned masks cover the hand, and every interior point lies inside its mask.

Command (needs a project whose catalog names the source, plus a memory root):

```sh
python3 -m library.tools.person_regions status <project> --memory-root <root>
python3 -m library.tools.person_regions window <project> --clip <LCATL0013 clip> \
  --start 60 --end 90 --memory-root <root>
```

On `main` (f3e8b15fc) neither exists:
- `ren analyze ... --lanes regions` fails with `invalid choice: 'regions'`.
- `python3 -m library.tools.person_regions` fails with `No module named library.tools.person_regions`.

## Wall time per source

Final run: `python3 -m library.tools.footage_analysis <scratch project> --no-steps --lanes regions --memory-root <scratch root>` on commit 144e101. Machine load average was about 7.

| Source | Decode (1152 px I-frames) | Vision | ms/frame | Wall |
|---|---:|---:|---:|---:|
| LCATL0013 | 239.1 s | 220.5 s | 26.9 | **489.4 s** |
| LC4932 | 284.1 s | 214.0 s | 21.7 | **523.1 s** |

The same sources at load average 20-37, while other lanes ran their gates:
- LCATL0013 took 681.7 s and 930.7 s.
- LC4932 took 674.2 s and 692.5 s.
- Vision ms/frame reached 62.

Projected cost is ~8-15 minutes per hour of footage. About half of that is the second decode; the plan's estimate of ~5.5 min counted Vision only.

Disk use:
- The sidecar is ~1.55 MB per source (12,007 and 10,113 masks).
- `regions.json` is 4.0-4.5 MB.
- The 1152 px decode needs a transient temp directory of ~160 KB per frame (~1.3 GB for LCATL0013), deleted when the source finishes.

## Decision 1: model input resolution

The test set was 50 real frames: 20 evenly spaced over LCATL0013, 10 from its t=60-90 window and 20 evenly spaced over LC4932, each decoded at its M2 sample time. Masks were compared on the 384x216 grid against a 1920 px decode.

| Model input | Median IoU vs 1920 | p10 | Frames whose instance count disagrees |
|---|---:|---:|---:|
| 384 px (the M2 thumbnails) | 0.934 | 0.727 | 6 / 50 |
| 768 px | 0.983 | 0.952 | 2 / 50 |
| 1152 px | 0.991 | 0.964 | 3 / 50 |

Looked at, the 384 px low tail is mostly the glass table's reflection of the subject being absorbed into the mask (LC4932 t=1300, 3641, 3900). The 384 px masks also missed Craig on LCATL0013 t=69 and t=72. At 768 px the reflection still leaks on the worst frames; at 1152 px it does not. Vision cost was the same at 768 and 1152 (~37 ms/frame on that loaded machine). Chosen: **1152 px** (`DECODE_WIDTH`). Masks are still stored on the M2 grid, so later lanes can read pixel statistics straight off the M2 frames.

The model's native label map (`instanceMask`, 512x384 for a 16:9 frame) is not a plain stretch of the frame: resampled naively, its IoU against the scaled mask was 0.51. So the helper uses `generateScaledMaskForImage` and resamples that onto the grid.

## Decision 2: codec

The codec is alternating run lengths over the row-major grid, background first (`runs-rowmajor-v1`). All of a source's masks go in one gzip JSON sidecar, named by the sha256 of the UNCOMPRESSED payload.

The first version hashed the gzip bytes. Two runs of LCATL0013 then produced identical masks (12,007 of 12,007 equal) and identical byte counts, but different hashes: the two runs used different Python builds, and their zlib compressed the same payload to different bytes. Hashing the payload makes the name a real content address.

## Decision 3: linking instances into tracks

Vision's instance index is per frame and swaps between adjacent samples. So instances are linked one-to-one by mask IoU between adjacent measured samples, best pair first.

Adjacent-sample IoU, measured on the first full run, split by owner:

| Source | Same face track: n, min, p1 | Face-owned vs anything else: n, share at or below 0.1 |
|---|---|---|
| LCATL0013 | 8,072, 0.519, 0.755 | 7,676, 98.9% |
| LC4932 | 9,687, 0.294, 0.495 | 560, 84.6% |
| LCATL0011 | 420, 0.324, 0.469 | 90, 96.7% |

Across all three sources:
- Same-owner pairs never fell below 0.294 (n = 18,179).
- 97.9% of other-owner pairs were at or below 0.1 (n = 8,326).
- The other-owner pairs above 0.3 were not inspected one by one. Their shape (a high IoU between a face-owned mask and a faceless one) is what the same body looks like at a sample where Vision lost the face, and those should join.

Chosen: **0.2** (`TRACK_LINK_MIN_IOU`), inside the gap.

Resulting tracks:

| Source | Owned region tracks | Samples they cover | Longest owned track | Unowned tracks |
|---|---:|---:|---:|---:|
| LCATL0013 | 30 | 8,161 | 1,856 samples | 770 |
| LC4932 | 13 | 9,860 | 5,356 samples | 111 |

The unowned tracks are faceless instances, nearly all the partner's hands and arms at the frame edge. They move fast, so between adjacent ~0.5 s samples they rarely overlap and they fragment into short tracks.

## Decision 4: slot version

`person-instance-mask-v1` (`M3D_METHOD`) is written to `instrument.method`. `footage_analysis.Lane.method` now carries each lane's slot version, and `slot_is_fresh` rebuilds a record stamped with anything else. That replaces the `expressions`-only special case.

A record whose sidecar is missing also reads as stale. `tests/unit/picture/test_person_regions.py::TestLane` pins both.

## Validation

- Dependent tests: `scripts/select_dependent_tests.py` over the changed files (24 files) plus `tests/contracts/` gave **909 passed, 0 skipped**.
- `ruff check --config ruff-ci-gate.toml` passed on the changed Python files.
