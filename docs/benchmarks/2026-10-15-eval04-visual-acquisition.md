# EVAL-04 visual evidence acquisition (2026-10-15)

## Scope and method

This repeat acquired evidence only inside the held-out brief windows:

| Source | Brief window | Object detector sample times |
|---|---:|---|
| IMG_1812 | 10-70 s | 15, 25, 35, 45, 55, 65 s |
| IMG_1818 | 0-40 s | 5, 15, 25, 35 s |

OWLv2 ran once at each 10-second window midpoint, with the fixed queries
`person`, `backpack`, `smartphone`, `shoulder bag`, and `object held in hand`.
The detector took 62.07 s total (34.73 s on IMG_1812 and 25.61 s on
IMG_1818). These are point samples, not exhaustive detection over each
10-second interval. The requested windows and source media were read-only.
Cached OWLv2 and SAM 2 weights were used; no model was downloaded.

OWLv2 nominated four backpack/shoulder-bag spans on IMG_1812 (25-30,
45-50, 55-60, and 65-70 s) and two on IMG_1818 (5-10 and 35-40 s).
SAM 2 tracked each named detection separately at 2 Hz, in six five-second
clips. The resulting scratch Project Model carries 50 object track samples,
six `object.detection` measurements and six `object.visibility`
measurements. Each span has its own object identity because each was seeded
independently; the measurements do not establish identity continuity across
spans. One of the six spans yielded only one usable mask sample. It is kept
as a single sampled detection and does not establish full-span visibility.

For action nomination on IMG_1818, Apple Vision measured 80 frames at 2 Hz
over 0-40 s (4.0 s in the Vision helper). It found faces on 75 frames and
hands on none. `nominate_hand_motion_intervals` returned zero candidates;
the bounded verifier was called with that empty candidate set and returned
no verdicts. No hand/object action is claimed from object presence.

## Calibration repeat

[`eval04-brief-visual-action-object-calibration.json`](eval04-brief-visual-action-object-calibration.json)
adds a proposed calibration task on IMG_1808 in the calibration scene group
`apple-001-scene-1806-1811`, scoped to 3.5-5.5 s. The brief freezes no gold
range or reference edit.

The 2 Hz Vision helper sampled 20 frames over 0-10 s and nominated two
hand-to-face candidates at 3.5-5.5 s and 9-10 s. Bounded Gemma verification
returned `no` for both (13.29 s and 4.59 s). A separate bounded check of
the cap-adjustment wording also returned `no` (9.87 s). These are
verification refusals, not measured action absences. No calibration action
was promoted into the cuts.

## Evidence-to-edit reruns

The held-out visual brief and the new calibration brief each ran once through
`evidence_to_edit` against the scratch Project Model. Both wrote reviewable
outputs and completed evidence and plan stages. Both emitted zero cuts, so
their edit graph, render, and verification stages remain incomplete. The
held-out object query found two intervals on IMG_1818; the query for people
and faces remained `not_yet_analysed`. On both runs the ledger abstained on
the required `action` and `objects` plan intersection. No Resolve timeline
was created and no render or `verify_render` verdict exists because the
runner produced no cut to place.

The current runner also has a routing gap: `REQUIRED_SELECTION_PLANS`
requires the plan named `action`, but `VISUAL_PLANS` declares only
`objects`, `agents`, `scene_structure`, and `sound_energy`. Thus an action
candidate cannot currently satisfy the visual action requirement through
the runner's evidence query. This was not bypassed or treated as permission
to weaken the requirement.

The native-video `vision_pipeline_v3` pass was attempted offline on the ten
requested 10-second windows. Its lazy `load` name first failed to resolve
from `_LazyAnalyzer`; binding the existing module lazy import in the
scratch driver let the cached model load. The first window then ran above
100% CPU for over four minutes without returning a result. The task's own
process was interrupted and unloaded the model. No window result was
recorded, so no VLM action or scene measurement is claimed and the runtime
reliability issue remains open.

## Remaining benchmark gaps

- No held-out action candidate was nominated or verified in IMG_1818's
  0-40 s window.
- The Project Model has no measured hand/object relation or cross-span
  object identity for the independent backpack tracks.
- The visual action requirement is not queryable by `evidence_to_edit` on
  this base because its required plan has no corresponding entry in
  `VISUAL_PLANS`.
- Both reruns have zero cuts, so native-cadence Resolve rendering and
  `verify_render` were not possible.
- There are still no frozen gold ranges, blinded reference edits, or
  ingested independent action labels. Metrics remain unknown, not zero.

Scratch records and intermediate media were kept under
`/tmp/eval04/visual-acquire/`; none were copied into this repository.
