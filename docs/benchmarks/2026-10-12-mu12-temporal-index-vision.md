# MU-12: step 1.04 without its own Vision pass - 2026-10-12

Step 1.04 used to run the Apple Vision helper at 5 Hz on every clip and store
six `vision_*` keys. Nothing read them, and M3 (`person_measurements`) measures
the same requests over the shared M2 grid. MU-12 removed that pass, its
backfill and the 5 Hz JPEG branch of the shared decode. This doc records what
that saved on the fixed 17-clip set.

## Method

- Footage: project 001's 17 source clips, 807 s in total, read-only.
- Code: `git archive` exports of `main` (`5584ed642`) and of the MU-12 branch.
- Each clip ran `index_clip` once under each tree, with the order alternating
  per clip, and one warm-up run per tree first.
- Script: `docs/benchmarks/mu12_temporal_index_bench.py`.
- Speech came from the clip's cached 1.04 index. Transcription is batched
  outside `index_clip`, and MU-12 does not touch it.
- The audio models (PANNs and audio classification) were stubbed in both
  trees, since they are identical in both.
- Everything on the picture side ran for real: the shared decode, scene, motion,
  flow, Haar faces and hue.
- The run took the whole machine through `heavy_work_lock`, so no other heavy
  job ran at the same time.

## Result

| | main | branch |
|---|---:|---:|
| `index_clip` wall, 17 clips | 277.7 s | 135.9 s |
| Vision helper (`measure_clip_vision`) | 123.6 s (44.5%) | not run |
| Shared decode | 64.5 s | 56.7 s |
| `vision_*` keys in the index | 6 | 0 |
| Haar `face_presence` samples | 4,039 | 4,039 (equal on every clip) |

- Wall time drops by 141.7 s (51.0%). That is the helper's own 123.6 s, plus
  7.8 s from dropping the JPEG branch of the decode, plus about 10 s of run-to-run
  spread.
- The fresh 1.04 baseline for this set (`2026-10-03-current-head.md`), which
  includes transcription, was 469.3 s. Measured against that baseline, the saving is
  about 30%.
- The largest clip (`clip_011`, 188.6 s, 943 samples) went from 65.4 s to
  30.6 s.

## Driver

```text
for each clip, alternating tree order:
  python3 mu12_temporal_index_bench.py <tree> <clip> <clip_id> \
      <001>/pipeline_output/steps/1_04_temporal_index/index/<clip_id>.json \
      <16 kHz mono wav of the clip> <out.json>
```

## Readers

No reader lost a field, which this grep shows:

`git grep -nE "vision_(faces|face_candidates|body_pose|hand_pose|person_mask|method)\b"`

On main it hits only 1.04's own writer, its backfill and that step's tests.
The 5 Hz Haar `face_presence` keeps every reader it had (`subject_framing`,
`reframe`, `continuity`, `take_selection`, `regional_motion`,
`cutaway_window`, the 1.06 seed). It is not the Vision helper, and it is unchanged.
No reader needed Vision at 5 Hz, so M2's rate is unchanged.
