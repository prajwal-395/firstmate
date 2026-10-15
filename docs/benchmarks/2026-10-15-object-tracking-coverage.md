# Object detection and tracking coverage (2026-10-15)

## Scope and method

This is a measurement of existing runs and checked-in benchmark records. No
detector threshold, model, seed, or sampling setting was changed. Podcast
media and 001 raw clips were read-only; the 001 Resolve project was not
opened. Current project artifact inspection found no `1_06_object_segmentation`
outputs in either the GEO Podcast project (7 clips, 10,895.052 s catalogued)
or 001 (17 clips, 807.048 s catalogued). Therefore neither current project
has object-track coverage that can be read back from its project model.

The real-footage measurements below are the existing podcast detector
bakeoff and SAM 2 seed-track run, the 001 SAM 2 run, and the held-out identity
score. They establish coverage and cost, but do not constitute a new
held-out-label evaluation of object identity. The frozen 001 vision pass
covered only `IMG_1822` (85.82 s of 807.048 s, 10.6%); its parsed object list
is empty in both saved revisions, so it supplies no object boxes or tracks.

## Detector coverage and cost

The podcast bakeoff used 40 sampled frames and 148 hand-labelled boxes from
the detector's Gemma-named object phrases. The measured slate is open
vocabulary (Florence-2, OWLv2, Grounding DINO) plus Apple's class-agnostic
foreground-instance proposal. The existing results are:

| Producer | Availability | Box recall at IoU >= 0.5 | Cost |
|---|---|---:|---:|
| OWLv2 | available, MPS | 132/148 (0.892) | 145.55 s / 40 frames; 1,174.56 MB peak RSS |
| Apple Vision foreground | available, ANE | 36/148 (0.243) | 6.53 s / 40 frames; 26.73 MB peak RSS |
| Grounding DINO tiny | available, MPS | 40/148 (0.270) | 5.52 s median/frame in the bakeoff |
| Florence-2 base | unavailable in the measured Transformers runtime | not scored | load failed: missing `forced_bos_token_id` |

The cheaper Vision pass is useful as a foreground proposal, but misses 75.7%
of the labelled boxes and cannot support a negative answer. OWLv2 missed
16/148 (10.8%), so its empty result also remains inconclusive. The fixed-
vocabulary detector comparison is **not measured**: Ren has no general
fixed-vocabulary object detector in this measured slate. Face detectors are
not a substitute for object classes. The 40-frame set is a sparse sample of
podcast material, not exhaustive coverage of all 10,895 seconds.

## Track coverage and fragmentation

| Footage / run | Coverage result | Identity and fragmentation result |
|---|---|---|
| Podcast, LCATL0013, 30-40 s | OWLv2 found the nominated laptop in 20/20 samples; SAM 2 masks covered the hand-marked laptop box in 20/20 samples (1.0) | One seeded object in one clip; no gap. No across-clip object association was attempted. |
| 001, all 17 clips | Existing SAM 2 benchmark: 1,544/1,615 samples for the selected largest-area subject track (95.6% aggregate); five clips are partial, with 71 missed samples total | Five partial tracks have longest uninterrupted runs of 6.0, 4.0, 3.5, 0.5, and 1.0 s. The published record does not contain exact gap counts or a reappearance-link score. |
| Podcast held-out identity set | 12 stills, 20 visible observations overall (14 people, 6 laptops), 10 occluded and 6 absent | Independent Apple Vision passes produced person IDF1 0.143, 6 within-clip identity switches, and explicitly abstained on 2/2 cross-clip person identities. Laptop recall was 0/6 visible observations because this producer is person-only. |
| Current GEO Podcast / 001 project artifacts | 0 object segmentation artifacts found in either project | No current project-local track, visibility, or fragmentation evidence to query. |

The 001 figure is the selected subject track's frame coverage, not detection
recall over every object. The 001 mask generator selects large first-frame
regions; it does not know which object an editor wants. SAM 2 maintains the
seeded target within a clip and records missing mask frames as gaps, but a
gap is not classified as occlusion and a later sighting is not associated
back to the old identity. Object IDs are clip-scoped. There is no measured
cross-segment object re-identification.

## Relationships and uncertainty

The Project Model carries detector name and optional detector score on a
detector-seeded object, and carries sampled box geometry and explicit
unmeasured gaps. Migrated per-frame track confidence is `null`; SAM 2 does not
provide a calibrated track-visibility probability. The object visibility
record means “a box sample exists here,” not a semantic visible/occluded/
absent judgement.

Existing person relationship analysis can associate hands with people and
derive hand-to-hand candidates. It does not link an object track to a person's
hand or represent a measured person-holds-object relation. 001's semantic
description sometimes says a person holds a small object, but this is sparse
language evidence, not a persistent geometric relationship.

## Association defect found and corrected

Project Model migration used the segmentation file's `frames` list as its
visibility list even when a listed frame had no stored bounding box. The
track-sample migration skips such a frame, so the model claimed visibility
where it had no queryable track geometry. A regression fixture with a missing
bbox at frame 3 showed the old record listing frame 3 as visible and only
frame 4 as a gap. Migration now derives visibility from frames with stored
bboxes: frame 3 is no longer visible, and frames 3-4 are reported as an
unmeasured gap. A track with no bbox samples reports visibility as unmeasured
instead of indexing an empty list. This fix does not reconnect a track after
occlusion or alter the detector.

## Practical finding

Ren can find named objects in sampled frames with OWLv2 and follow a prompted
object inside a short clip with SAM 2. The evidence does not show reliable
object reappearance handling, cross-segment identity, calibrated visibility
uncertainty, or person-hand-object relations. Current project artifacts do
not provide those tracks to editorial queries. Full 001 SAM 2 coverage cost
1,615 frames / 807.0 s of footage at 6,558.7 s wall time (8.1x real time),
with 58.3 MB of stored masks; the object detector's 40-frame OWLv2 pass alone
cost 145.55 s and peaked at 1,174.56 MB RSS. These costs make always-on,
dense tracking impractical under the measured implementation.

Source measurements: [`object-detector-tiering-2026-10-14.md`](object-detector-tiering-2026-10-14.md), [`object-detector-bakeoff-2026-10-13.md`](object-detector-bakeoff-2026-10-13.md), [`object-continuity-benchmark.md`](object-continuity-benchmark.md), and [`SUBJECT_MASKING_MEASURED.md`](../SUBJECT_MASKING_MEASURED.md).
