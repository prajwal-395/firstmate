# Editorial evaluation corpus manifest

The machine's available rights-cleared project camera originals are the seven
FI-01 GEO podcast/interview sources plus the seventeen Apple 001 iPhone takes
(IMG_1806-IMG_1822, cleared by the captain 2026-10-15). The manifest records
their SHA-256 hashes, byte sizes, durations, project and use permissions in
[`2026-10-15-editorial-eval-corpus.json`](2026-10-15-editorial-eval-corpus.json).
Source media stays outside this repository and is read-only. Adding a source is
a data-only manifest change; media is never downloaded by the validator.
Each `sha256` is a full-file SHA-256 measured from the read-only disk, not
the FI-01 partial fingerprint (size plus first/last 1 MB); byte verification
of all 24 sources passes under the command below.

## Split and access report

The split unit is the measured synchronized-camera/session group, not an
individual camera file. This keeps alternate views of the same recording out
of different splits.

| Split | Sources | Leakage groups | Included media |
|---|---:|---:|---|
| Training | 3 | 1 | LCATL0013, LC4932, LCATL0014 (podcast) |
| Calibration | 19 | 2 | LCATL0012, LC4931 (podcast) + 17 Apple 001 takes |
| Held out | 2 | 1 | LC4930, LCATL0011 (podcast) |
| Total | 24 | 4 | 195.03 minutes |

The seven podcast sources are interview footage with dialogue and multi-camera
coverage. The seventeen Apple 001 takes are daylight outdoor walk-and-talk
vlog: selfie walking, handheld and mounted takes across street, sidewalk,
storefront, parking lot/garage and in-vehicle segments of one outing, kept in
a single session leakage group inside calibration. The annotation schema covers
scene, action, object, camera motion, speech, ambience/music, and provenance
fields. Annotation entry has not begun; the manifest does not claim gold labels
or model quality scores. Actions, objects, and camera motion remain unassessed
in both collections. Still empty: classroom, product close-ups, standalone
B-roll, noisy-speech labels, ambience and music. These classes have no
positive labels or held-out quality claims.

Held-out labels have not been created, and no label payload is committed to the
repository. The manifest marks tuning access false for held-out annotations.
When labels are created, they must be held by the corpus custodian outside
tuning workspaces, identified by a separate access audit, and the manifest must
use `sealed_custodian_only` with tuning access false. Source footage itself is
not represented as a label payload.

## Validation

The lightweight validation checks rights scope, source hashes' format, split
presence, split-group isolation, annotation identity, and held-out label access.
Manifest paths are relative to `$HOME/Documents`, the common ancestor of both
collections, so one read-only root verifies every source:

```sh
bin/vep -m library.tools.editorial_eval_corpus \
  docs/benchmarks/2026-10-15-editorial-eval-corpus.json

bin/vep -m library.tools.heavy_work_lock run --owner vep-eval01 -- \
  bin/vep -m library.tools.editorial_eval_corpus \
  docs/benchmarks/2026-10-15-editorial-eval-corpus.json \
  --source-root "$HOME/Documents" \
  --verify-hashes
```

The JSON report prints genre/task balance and held-out label access state. This
is a split and rights manifest for the available project-only collections; it
does not turn absent categories into coverage. Further footage acquisition
and annotation are separate follow-up work.
