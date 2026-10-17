# Lane 3 identity and relation calibration baseline (2026-10-16)

## Scope and existing implementation

This is a calibration-only measurement for OBJ-036..045 on Apple 001. It reads
the current project artifacts and the two blind agent label sets; source-memory
frames and hand measurements were written only to the shared source-memory
cache, and one candidate segmentation artifact was written under `/tmp`. No
source media, project, or Resolve timeline was modified. The lane 3 implementation
is already on main at `535b920a8` (PR 2631), so this report adds no model or
relation implementation.

The existing code includes:

- Clip-local track reassociation candidates and identity-switch scoring in
  `library/tools/identity_reassociation.py`.
- Typed temporal and physical relation candidates, hand-joint/object-box
  proximity, and relation scoring in `library/tools/relation_classes.py`.
- Candidate pickup, put-down, and state-change events in
  `library/tools/temporal_action_candidates.py`.
- Earlier person-region ownership for hands and person-to-person gestures in
  `library/tools/relationships.py`. Its own module contract says object boxes
  are not carried there, so it cannot evaluate person-hand-object relations.

## Matching rule, fixed before scoring

Use calibration sources IMG_1807, IMG_1808, and IMG_1816 only. For identity,
match within the same source and entity kind at the same labeled second using
one-to-one maximum box IoU, accepting IoU >= 0.5. Count a switch only between
successive accepted observations of one labeled identity when the matched
local track ID changes. Count an identity link as improved continuity only
when an explicit candidate or verified re-identification link joins the two
matched local tracks.

For physical relations, accept only `relation_class=physical` and
`predicate=touching`; require the same source clock, person and object labels,
and temporal IoU >= 0.5. Match one-to-one. For label agreement, collapse
overlapping or adjacent contacts of the same person-object pair into one
physical-contact event, irrespective of the annotator's touching/grasping
subtype. For pickups, require the same object and source clock and temporal
IoU >= 0.5, matched one-to-one. Report pickup recall only when the labeled
denominator is nonzero. The identity, contact, and pickup thresholds are
unchanged from the existing scorer contract; no threshold was tuned here.

Calibration labels are the independent blind agent submissions
`vep-labels-r1-a/round2/vep-mim-l3-identity` and
`vep-labels-r1-b/round2/vep-mim-l3-identity`. Earlier action annotations from
`vep-agent-actions-a` and `vep-agent-actions-b2` were available as context,
but do not contain dense object-track boxes and are not treated as identity or
physical-contact ground truth. These are agent labels, not human labels.

## Calibration label agreement

| Measure | Agent A | Agent B | Agreement |
|---|---:|---:|---:|
| Identity box observations in the three calibration windows | 153 | 139 | 139 comparable same-kind, same-time assignments; 0/139 reach IoU 0.5; mean IoU 0.228 |
| Physical-contact windows | 2 positive, 1 negative | 2 positive, 1 negative | 3/3 window-level presence agrees |
| Physical-contact intervals | 2 events | 3 intervals | W01 intervals overlap at IoU 0.667; W02 B's adjacent grasping/touching intervals merge to one interval with A's at IoU 0.750 |
| Pickup events | 0 | 0 | 0 positives from either submission |

Identity box geometry has substantial inter-agent disagreement, so an
identity-switch score against these boxes would not be defensible without a
tie-break. That disagreement does not change this baseline's verdict: no
model identity tracks or predictions are available in the Apple 001 project
artifacts, so there is nothing for either submission to score. A tie-break is
not requested at this stage. The contact labels agree on event presence at the
chosen broad physical-contact level; their exact subtype and boundaries differ
in W02. No pickup-recall denominator exists in these calibration labels.

## Calibration candidate pass and measured runtime

After checkpoint approval, M2 samples were generated for IMG_1807, IMG_1808,
and IMG_1816: 18, 29, and 195 frames, with decode times of 0.8, 0.9, and 2.9
seconds. Apple Vision M3 measurements then produced 0/6/164 face observations
and 6/22/50 hand observations over 18/29/195 frames; measured Vision time was
3.7/5.5/19.6 seconds (28.8 seconds total). These are source-memory cache
artifacts, not writes to the Apple 001 project.

OWLv2 (`google/owlv2-base-patch16-ensemble`, publisher revision
`cfd3195ba4ea9592eec887ded089f4c08eff231d`) queried the first IMG_1807 frame for
"car door handle". The selected detector seed scored 0.647, had normalized
area 0.047, and took 24.5 seconds including cold initialization. Against the
pre-labeled time-zero handle boxes, its IoU was 0.019 for Agent A and 0.000 for
Agent B, below the frozen 0.5 match rule. SAM 2.1 Hiera Small
(`facebook/sam2.1-hiera-small`, publisher revision
`ee5bba1d82bb8749febdf90f45e84b687142ba03`) propagated this seed over
IMG_1807 in 78.6 seconds: 11 of 34 sampled frames received a box, with mean
mask area ratio 0.0093. Its time-zero box IoU was 0.000 against both agent
labels.

The same OWLv2 and SAM 2 process measured IMG_1808’s "baseball cap". The
selected seed scored 0.153, area 0.065, and took 19.7 seconds; propagation
took 69.2 seconds, with boxes on 46 of 56 sampled frames and mean mask area
ratio 0.0385. Its time-zero box IoU was 0.000 against both agent labels. Both
misses are below the frozen 0.5 match rule. The two segmentation files remain
at `/tmp/vep-l3-tracks/` and are not attached to the live project. Both
checkpoints were read from the existing Hugging Face cache in full precision;
this task downloaded no weights.

The earlier scheduler readings were RSS only: 0.82 GB for the first pass and
1.42 GB for the second. macOS `/usr/bin/time -lp` on the second combined pass
reported maximum resident set 1,501,315,072 bytes and peak memory footprint
13,249,300,976 bytes (12.34 GiB), above the 8 GB ceiling. After scheduler PR
2667, which samples process-tree physical footprint, OWLv2 was rerun alone on
the W02 first frame at a 4 GB reservation. It selected the same seed and
finished in 27.29 seconds after 80.07 seconds queued, with a measured peak of
2.708 GB. The first separate seeded SAM pass reached 8.187 GB and was stopped
by its 8 GB reservation after 12.70 seconds. After removing the unused
automatic-mask generator from the seeded pipeline path, a second pass reached
8.118 GB and was stopped after 21.00 seconds. Neither the detector pass nor
the capped SAM attempts wrote to the project.

### Scoring the available scratch tracks

I scored the already-saved IMG_1807 and IMG_1808 detector-seeded scratch
tracks against the calibration object boxes using the matching rule above:
same source, same object kind, same labeled second, one-to-one IoU matching,
and IoU >= 0.5. Track boxes are sampled at 2 fps; only samples aligned to
integer labeled seconds are compared. For the seed target O1, IMG_1807 had
boxes at all 6 Agent A and all 5 Agent B labeled observations. IMG_1808 had
boxes at 23 of 28 observations for each agent; there were no aligned
predictions at seconds 2-6. None of the 57 covered observations reached IoU
0.5. Mean IoU on covered observations was 0.000 for IMG_1807 Agent A and B,
0.0016 for IMG_1808 Agent A, and 0.0351 for IMG_1808 Agent B. This is a sparse
seed-track box comparison, not identity-switch or relation scoring; the labels
also disagree substantially on IMG_1808 object geometry, as reported above.

No person-hand-object relation candidates were produced by these standalone
tracks. In particular, W02 relation scoring is **not measured**: its
generator-free 56-frame SAM propagation was admitted at a 9.00 GB reservation
and stopped at a measured 9.127 GB process-tree footprint after 15.62 seconds;
the one permitted retry at 9.90 GB was stopped at 10.143 GB after 15.51
seconds. Both values are from scheduler history. No W02 relation artifact was
saved. These stopped attempts do not count as zero relation predictions or as
a relation-recall result.

## Memory cause investigation

A four-frame, 640x360 SAM-only diagnostic loaded the video predictor without
OWLv2 or the automatic mask generator. It processed the four frames in 3.22
seconds and reached a 3,450 MiB `phys_footprint_peak` (process physical
footprint), sampled with macOS `/usr/bin/footprint` against the model PID. This
confirms the predictor alone has substantial Metal footprint, even on this
small input. The earlier 0.84 GB `/usr/bin/time -lp` result from an MP4 probe
is excluded: it loaded the model but failed before frame initialization
because `decord` is not installed.

The 13.25 GB pass used detector and segmenter in the same Python process.
`open_vocabulary_detect._LOADED` retains OWLv2 after detection;
`ObjectSegmenter._load_sam2` constructs both the video predictor and the
automatic mask generator, although detector- and face-seeded tracking do not
use the generator. `sam2_video_predictor.init_state` defaults
`offload_video_to_cpu=False` and `offload_state_to_cpu=False`, placing sampled
frames and inference state on MPS. The corrected scheduler isolated the
seeded SAM path at 8.187 GB and stopped it at the declared 8 GB cap. Code
inspection showed the seeded pipeline had also loaded an unused generator, so
this branch makes its face- and detector-seeded session load only the video
predictor; direct legacy blob-seeded callers still load the generator by
default. The generator-free retry peaked at 8.118 GB, a 0.069 GB reduction,
and was also stopped at the cap. The video predictor plus sampled-frame and
inference state therefore remains over the 8 GB ceiling for this 56-frame
input. Under the later 10 GB task ceiling, the 9.00 GB reservation retry
peaked at 9.127 GB and the single 9.90 GB retry peaked at 10.143 GB; both were
stopped when they exceeded their declared reservation. The separate 4 GB
OWLv2 run measured 2.708 GB, 27.29 seconds cold, and did not retain OWLv2
during SAM propagation.

An earlier combined-process attempt exited with code 247 before saving the
IMG_1808 track; a later single-clip process produced the track above. Neither
scratch track was materialized through the Project Model migration. No physical
relation prediction, identity-switch prediction, or pickup event prediction
was available for end-to-end scoring. The two exact-frame box misses are
evidence against these seeds, not aggregate relation precision/recall. No
held-out labels were opened.

## Current Apple 001 artifact coverage and baseline scores

The read-only Apple 001 project currently has no
`pipeline_output/steps/1_06_object_segmentation` output, no object tracks in
`pipeline_data.json`, and no materialized lane 3 Project Model relations to
score. The project artifact inventory likewise contains no object
segmentation step. This is consistent with the existing coverage audit in
`2026-10-15-object-tracking-coverage.md`.

| Lane metric | Calibration result | Interpretation |
|---|---:|---|
| Identity prediction coverage | 0/153 Agent A observations; 0/139 Agent B observations | No model tracks; identity switch count is not estimable, not zero |
| Physical relation predictions | 0 | No object tracks or derived physical relations |
| Physical relation precision | N/A (0 predictions) | Undefined denominator |
| Physical relation recall | 0/2 broad contact events = 0.000 | Neither of the two labeled contact events has a prediction |
| Pickup event recall | N/A (0 labeled pickups) | Empty gold denominator; not a successful 0% score |
| Partial inference/runtime | 192.0 s for the earlier combined OWLv2 + SAM 2 passes on IMG_1807/1808; corrected W02 OWLv2 27.29 s at 2.708 GB; seeded SAM stopped at 8.187 GB after 12.70 s, then at 8.118 GB after 21.00 s without the generator; 9.127 GB after 15.62 s at a 9 GB reservation; 10.143 GB after 15.51 s at a 9.9 GB reservation; 3.22 s for the SAM-only four-frame diagnostic | No end-to-end relation runtime; scheduler stopped both generator-free W02 retries at their declared reservations, with the 9.9 GB retry crossing the 10 GB task ceiling |

The 0/2 recall is a coverage baseline for the current materialized project
state, not evidence that the candidate-generation algorithms themselves fail.
The identity result is unscorable because of absent predictions and
inter-agent box disagreement. The pickup result is unscorable because the
calibration labels contain no positive pickup examples. No held-out labels
were opened or scored.

## What remains for the measurement

The partial candidate pass did not create a complete track set for all three
calibration clips. It measured two poor object seeds and propagations but
never materialized those scratch results through the Project Model migration.
Aggregate candidate precision/recall, identity switches, and pickup recall
remain unmeasured; the current-project coverage figures above remain the only
end-to-end baseline. Scheduler PR 2667 is merged and the branch is rebased.
The generator-free pipeline path is in place, and all 22 object-segmentation
unit tests pass. The W02 SAM relation measurement is not measured: retries at
9 GB and 9.9 GB reservations were stopped at 9.127 GB and 10.143 GB. Existing
scratch tracks have been scored against same-time agent object boxes, with no
IoU >= 0.5 matches among 57 covered observations. Aggregate relation
precision/recall, identity switches, and pickup recall remain unmeasured; the
current-project coverage figures above remain the only end-to-end baseline.
No thresholds changed and no held-out labels were opened.
