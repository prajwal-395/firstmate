# Object identity and continuity benchmark

The scorer in `library/tools/object_continuity_benchmark.py` evaluates frozen
frame observations from the object detector/tracker against human identity
labels. It is independent of the producers, uses normalized boxes, and refuses
annotations unless `split` is `held_out` and `tuning_access` is false. Labels
must remain with the corpus custodian; only aggregate results belong in this
repository.

## Annotation protocol

Use the existing editorial corpus manifest and its synchronized-session split
unit. Select held-out clips only. Annotate each sampled frame with a persistent
entity ID, class, normalized box when visible, visibility (`visible`,
`occluded`, or `absent`), and a state label where applicable. Use the same
entity ID across synchronized clips only when the same physical person/object
is established from the footage. Keep visually identical instances separate.
Include at least one occlusion and a same-class lookalike case. Record the
source ID, source digest, frame time, annotation version, annotator, review
status, and frozen label-set SHA-256 in the private annotation envelope. A
separate access-audit record names the custodian, external custody path, seal
time, and no-tuning policy; both files stay outside the tuning worktree. The
scorer refuses labels without the envelope and digest.

The label digest covers canonical JSON for both `observations` and
`hard_cases`. A hard-case row names either an occluded `(clip_id, frame_id,
entity_id)` or a `lookalike_pair` with at least two distinct visible entity IDs
of the same class in one frame. The report marks coverage incomplete unless
both kinds are present.

Predictions use the same frame keys and classes. A missing/ambiguous box is
an explicit row with `status: "abstained"`; each observed box separately says
whether cross-clip identity was `linked` or `abstained`. Producer failures are
listed in the cost record; neither kind of abstention becomes a negative
detection or an incorrect identity.
The prediction envelope records producer name/revision, threshold, sampling
schedule, requested and processed frame counts, wall seconds, peak RSS, and
per-frame failure reasons. Do not tune thresholds, sampling, or identity links
on held-out labels.

## Metrics

- Box precision and recall use one-to-one class-matched IoU >= 0.50 on visible
  observations. Occluded/absent rows are excluded from box counts and retained
  in the visibility totals.
- IDF1 uses a maximum-weight global assignment between annotated entity IDs
  and predicted track IDs, weighted by matched observations. This exposes a
  track split across clips even if every individual frame's box is correct.
- Identity switches count adjacent visible observations within the same clip
  whose matched predicted IDs differ. Fragmentation counts a missed visible
  observation followed by a recovered observation within one entity's clip
  track; explicitly occluded rows do not create a fragmentation.
- State-transition accuracy scores annotated state changes only when both
  endpoint observations were matched and both predicted states are present.
  Unscored transitions and abstentions are reported separately.
- Cross-clip identity accuracy is the fraction of GT entities visible in two
  or more clips whose per-clip majority predicted ID is the same. An entity
  with a clip that has no matched prediction or an explicit `abstained` link
  is counted as a cross-clip abstention, not a correct or incorrect
  re-identification.

Run the pure scorer with:

```sh
bin/vep -m library.tools.object_continuity_benchmark \
  /path/to/sealed-held-out-annotations.json \
  /path/to/frozen-predictions.json \
  --audit /path/to/held-out-access-audit.json \
  --out /tmp/object-continuity-score.json
```

The annotation and prediction files stay outside the worktree. Detector or
tracker runs that perform inference or decode footage must use
`bin/vep -m library.tools.heavy_work_lock run --owner <task-id> -- ...`.
Report per-class detection, identity, transition, fragmentation, abstention,
failure, wall-time, and peak-memory results. A no-link result is an abstention
or identity failure, never evidence of successful continuity.

## First measured report (2026-10-14)

[`2026-10-14-object-continuity-benchmark.json`](2026-10-14-object-continuity-benchmark.json):
12 held-out stills (LC4930 + LCATL0011 at 15/45/75/105/135/165 s, 36 sealed
observations: 20 visible, 10 occluded, 6 absent; occlusion + lookalike-pair
hard cases complete), scored against an Apple Vision person-instance producer
with one independent pass per still (no tracker, every cross-clip link
explicitly abstained; 12/12 frames processed, no failures, 1.282 s wall,
70.53 MB peak RSS, 0.107 s/frame).

- person: precision/recall 0.643 (9/14). The 5 host misses are
  reflection-merged masks: on the glass table the 384 px person-instance
  mask swallows the subject's reflection (and the laptop) into one
  instance, so the box IoU falls below 0.50 - the same defect M3d measured
  on thumbnails. IDF1 0.143 with 6 identity switches: every still mints a
  fresh track ID, so detection success still yields identity failure.
- person cross-clip: 2 entities, 0 scored, 2 abstained. The system does
  not re-identify across clips; it says so.
- person state: 4 annotated transitions (guest hands_clasped to hands_open;
  host arms_down to hands_flat to gesturing to fists), 0 scored and 4
  unscored - the producer emits no state, so transitions are reported,
  never guessed.
- laptop: recall 0.0 (6 visible observations, person-only producer fires
  nothing). Occluded in the guest camera, visible in the host camera, so
  it contributes no cross-clip entity. A detector that cannot see the
  class is a stated blind spot, not a silent zero.
