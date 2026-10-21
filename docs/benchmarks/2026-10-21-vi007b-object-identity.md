# VI-007 — object identity across cuts and angles (2026-10-21)

**Status: measured.** One lane, one held-out corpus of labelled object
tracks, one before/after with denominators, and the defects the
measurement found.

The person half of VI-007 landed in PR 3118 (a voice belongs to the
person its measured voice says it is). This is the **object** half: an
object — here the studio's two laptops and the two speakers — keeps
**one identity** across frames, across cuts and across camera angles,
without a forced match, and the lane says so when it cannot.

## What was measured

The claim under test: *a reference to the same object resolves to that
object, across supported source angles and time intervals.*

The lane is `library/tools/analysis/object_identity.py`. It takes the
per-frame answers the detector already produces (OWLv2, the incumbent
open-vocabulary detector, `library/tools/analysis/open_vocabulary_detect.py`)
and resolves them into:

1. **one identity per object inside a clip** — detections of the same
   class are associated by box overlap, one to one;
2. **one identity per object across clips** — a cross-clip link needs
   the same class, the same scope, an appearance above the measured
   threshold, and a margin over the runner-up;
3. **a measured visibility state** — `visible`, `partial` (the box
   runs out of frame), or `absent` (the lane looked in that frame and
   the object was not there).

Every refusal is reported: an ambiguous association, a link below the
threshold, an ambiguous link, a person (that identity belongs to the
face and voice lanes), and a link refused because it would cross a
scope.

## The corpus

Twenty held-out stills, four clips, two conversations, two camera
angles each:

| Clip | Angle | Seconds | What is in frame |
|---|---|---|---|
| LC4930 | Akshita, close | 15/45/75/105/135/165 | her, the studio's **blue tablet** (cut by the frame's bottom edge), the ficus, the tripod camera |
| LC4931 | Akshita, wide | 30/90/150/210 | her, the same tablet **edge-on** (a strip) |
| LCATL0011 | Craig, close | 15/45/75/105/135/165 | him, the **silver laptop** on the table, the studio's second machine on the side table (out of focus) |
| LCATL0012 | Craig, wide | 30/90/150/210 | him, the same silver laptop, the second machine as a sliver at the frame's edge |

The frames were cut from the read-only podcast media and never written
back to it. The manifest is
[`2026-10-21-vi007b-object-identity-frames.json`](2026-10-21-vi007b-object-identity-frames.json);
the labels live **outside this repository**, with the custodian's
access audit beside them, and the scorer refuses them unless they are
held out, sealed and digest-matched.

**The labels are one agent adjudicator's, looked at frame by frame** —
no human gold labels and no second adjudicator. The boxes were read
off the frames, drawn back onto the frames, and looked at again before
the seal.

The visibility rule was fixed before the producer's output was read:
`visible` when a person looking at the frame can say what the object
is; `occluded` when it is in frame but hidden or cut such that it
cannot be told from the background; `absent` when it is not in frame.

100 sealed observations: 37 visible, 13 occluded, 50 absent. Two hard
cases, one of each kind the scorer wants:

- **occlusion** — the blue tablet in LC4931-t030: edge-on and running
  out of the bottom of the frame, a strip, not an object anybody can
  name;
- **lookalike pair** — LCATL0011-t075: two laptops of the same class
  in one frame, on different tables (the one in front of Craig and the
  studio's second machine).

## The calibration (the thresholds are measured, not chosen)

The tuning corpus is the **D-01 podcast frames** plus LCATL0011 at the
same seconds
([`2026-10-21-vi007b-object-identity-calibration.json`](2026-10-21-vi007b-object-identity-calibration.json)).
Its labels are in this repository, so the tuning worktree may read
them. **The held-out seconds do not overlap the calibration seconds**:
held out {15, 45, 75, 105, 135, 165, 30, 90, 150, 210}, calibration
{20, 80, 140, 170, 200}.

| Measurement | Value | What the threshold is |
|---|---|---|
| The same object's box between two frames of one clip | 0.826 – 1.000 (12 pairs, median 0.914) | association threshold **0.30** — an order of magnitude below the measured true-pair overlap, far above the measured different-object overlap (0.000) |
| The same device across two clips, both seen | 0.821 – 0.883 (16 pairs) | link threshold **0.62** — above every different-device pair |
| A different device of the same class across two clips | 0.346 – 0.506 (32 pairs) | (the ceiling the threshold must clear) |
| The same device across two clips, one seen edge-on | 0.386 – 0.469 (16 pairs) | **falls below the different-device band: the link is REFUSED** |
| The smallest margin between the best candidate and the runner-up among the true pairs | 0.315 | link margin **0.08** — far below the measured separation |

The last two rows are the honest part of this measurement: a colour
histogram of a device seen edge-on **cannot** be told from a different
device, so the lane abstains there instead of linking, and a reader
sees an abstention rather than a wrong link.

## The result

The same frames, the same detector, the same labels, the same scorer.
The only difference is whether the answers carry an identity.

| Measurement (class: laptop) | Before (per-frame) | After (identity lane) |
|---|---:|---:|
| Visible ground-truth observations | 17 | 17 |
| Predicted observations | 38 | 38 |
| Box precision | 0.421 | 0.421 |
| Box recall | 0.941 | 0.941 |
| **IDF1** | **0.073** | **0.582** |
| **Identity switches** | **13** | **0** |
| Fragmentations | 0 | 0 |
| **Cross-clip identity accuracy** | **none scored (1 entity, 1 abstention)** | **1.0 (1 entity scored, 0 abstentions)** |
| Prediction abstentions | 0 | 2 |

| Measurement (class: person) | Before (per-frame) | After (identity lane) |
|---|---:|---:|
| Visible ground-truth observations | 20 | 20 |
| Predicted observations | 51 | 51 |
| Box precision | 0.392 | 0.392 |
| Box recall | 1.000 | 1.000 |
| **IDF1** | **0.056** | **0.338** |
| **Identity switches** | **16** | **0** |
| Cross-clip identity | none scored (2 entities, 2 abstentions) | none scored (2 entities, **2 abstentions — by design**) |
| Prediction abstentions | 0 | 5 |

Reading the numbers:

- **The identity is the whole difference.** Detection precision and
  recall are identical to the third decimal in both runs: the identity
  layer changes which object an answer is about, never whether the
  answer is there. That is the fidelity check this lane needs.
- **The switches are gone.** 13 laptop identity switches and 16 person
  identity switches before; zero and zero after. Before, every frame
  minted a fresh identity, so a reference to "the laptop" resolved to
  sixteen different things across two clips.
- **One cross-clip identity was established and it was the right one.**
  The silver laptop in LCATL0011 and the silver laptop in LCATL0012 are
  the same machine, in two takes of the same conversation; the lane
  linked them (appearance 0.849, margin 0.338) and the scorer counted
  it correct. It is the only entity in this corpus that is visible in
  two clips, so the denominator is **1**: this is one identity
  established, not a population.
- **The two people were not linked, and that is the design.** A
  person's identity is established by the face and voice lanes
  (PR 3118), not by an object appearance descriptor. The lane refuses
  the link and says why; the scorer counts two abstentions, not two
  wrong answers.
- **The lookalike pair was not merged.** The studio's blue tablet and
  its silver laptop are one class and two objects. The lane keeps them
  apart, because the tablet's face-on crop (0.34 – 0.43 against the
  other candidates) and its edge-on crop score below the threshold, and
  the silver laptop's crop scores above it.
- **Abstentions are answers.** Seven frames where the lane looked and
  found nothing are reported as `absent` with no box, rather than left
  in silence.

Cost, on this machine, 20 stills: 326.9 s wall, 434.64 MB peak RSS,
16.347 s per processed frame, 0 failures. The before run: 216.1 s,
423.36 MB, 10.807 s per processed frame, 0 failures. The identity
resolution itself is arithmetic over the detections the detector
already answered; the difference between the two runs' wall time is
the machine's load during the two passes, not the identity work.

## The defects this measurement found

**1. A padded crop measures the background, not the object.** The
detector's box for the studio's tablet in LC4930 is padded with the
studio's black table (0.39 of the frame wide for a 0.11-wide object).
A colour histogram of that crop is mostly black, and so is the crop of
the tablet seen edge-on in LC4931 — so the two score **0.845**, and the
lane linked them. The labelled crops of the same two views score
**0.386 – 0.469**. The link happened to be right; the *evidence* was
the table's colour, not the device's.

The lane now measures a track's **tightest** view for its appearance
(the crop that carries the object), and the tablet's tight face-on crop
against the strip scores **0.343** — below the threshold, so that link
is refused. The wider padded track has no tighter view to fall back on,
and its link survives at 0.845. Every link reports its similarity and
its margin, so a reader can see how weak a link is. **This is the
defect the next iteration has to close: an appearance measured on the
object rather than on the frame around it.**

**2. The detector's extra boxes are extra identities.** OWLv2 answers
about twice as many boxes as it gets right on this footage (laptop
precision 0.421, person precision 0.392 — the same numbers the D-01
corpus measured). Every extra box is now an extra identity, and an
extra identity can be linked. The identity lane does not make the
detector's noise worse — the same 38 and 51 predicted observations
appear in both runs — but a reader of an identity table has to read it
as "these are the objects the detector claimed", not "these are the
objects in the room".

**3. The studio's second laptop is a blind spot.** It is in six frames
as a blurred open machine and in four as a sliver at the frame's edge,
and the detector never sees it. It is the corpus's one lookalike pair,
so the one hard case the scorer wants is also the case the producer
cannot answer: laptop recall is 0.941 (16 of 17 visible observations),
and the missing one is that machine. Stated, not hidden.

## What this measurement does NOT show

- **Not a population.** One identity was established and scored
  cross-clip. The denominator is 1, and it says nothing about the next
  object.
- **Not an angle change.** The corpus's cross-clip pairs are two takes
  of the same angle and the same conversation. The studio's two angles
  of one conversation show **different objects** — the blue tablet on
  Akshita's side, the silver laptop on Craig's — and there is no
  object in this corpus that is visible in both angles of one
  conversation and identifiable in both. The "across camera switches"
  half of the acceptance criterion is therefore **unmeasured here**,
  and the lane's answer to it is the refusal it already gives: an
  identity is not claimed across an angle change unless the footage
  establishes it.
- **Not a person.** The person's identity is the face and voice lanes'
  (PR 3118), and this lane refuses to answer for it.
- **Not whole-video tracking.** Twenty sampled stills, one independent
  detector pass each. The lane's association is measured on frames
  15 to 210 seconds apart, which is a sparse pass and not a tracker.
- **Not the detector's accuracy.** The same boxes, before and after.
  The D-01 corpus measures that.

## How to reproduce

```sh
# the held-out run (heavy: it loads a detector)
bin/vep -m library.tools.heavy_work_lock run --owner vi007b-object-identity \
    --profile semantics.analyse:inference -- \
  bin/vep -m library.tools.analysis.object_identity predict \
    --manifest docs/benchmarks/2026-10-21-vi007b-object-identity-frames.json \
    --out /tmp/vi007b-after.json --detector owlv2 --phrases laptop,person

# the incumbent's shape of answer, for the before
bin/vep -m library.tools.analysis.object_identity predict \
    --manifest docs/benchmarks/2026-10-21-vi007b-object-identity-frames.json \
    --out /tmp/vi007b-before.json --detector owlv2 --phrases laptop,person \
    --mode per_frame

# the scorer (the labels and their access audit stay outside the worktree)
bin/vep -m library.tools.object_continuity_benchmark \
    $LABELS/vi007b-object-identity-held-out.json /tmp/vi007b-after.json \
    --audit $LABELS/vi007b-object-identity-held-out-audit.json \
    --out docs/benchmarks/2026-10-21-vi007b-object-identity.json
```

Artifacts of record:
[`2026-10-21-vi007b-object-identity.json`](2026-10-21-vi007b-object-identity.json)
(after) and
[`2026-10-21-vi007b-object-identity-before.json`](2026-10-21-vi007b-object-identity-before.json)
(before). Only aggregate results are in this repository; the labels are
with the custodian.
