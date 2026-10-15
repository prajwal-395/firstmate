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

The split unit is the measured synchronized-camera group for the podcast and
the capture-scene group for Apple 001, not an individual camera file. Every
take assigned to a scene stays in that scene's leakage group and split.

| Split | Sources | Leakage groups | Included media |
|---|---:|---:|---|
| Training | 3 | 1 | LCATL0013, LC4932, LCATL0014 (podcast) |
| Calibration | 11 | 3 | LCATL0012, LC4931 (podcast) + IMG_1806-IMG_1811 and IMG_1814-IMG_1816 |
| Held out | 10 | 3 | LC4930, LCATL0011 (podcast) + IMG_1812-IMG_1813 and IMG_1817-IMG_1822 |
| Total | 24 | 7 | 195.03 minutes |

The seven podcast sources are interview footage with dialogue and multi-camera
coverage. The seventeen Apple 001 takes are daylight outdoor walk-and-talk
vlog from one outing. For leakage control, their four capture-scene groups are
IMG_1806-IMG_1811, IMG_1812-IMG_1813, IMG_1814-IMG_1816, and
IMG_1817-IMG_1822. The first and third groups are calibration; the second and
fourth are held out. This preserves both proposed EVAL-04 visual moments in
held out: IMG_1812 (`visual_object`) and IMG_1818 (`visual_action`), while
leaving nine varied takes in calibration.

Scene boundaries were inferred only from numbered filenames, ffprobe capture
times, and ffprobe durations/stream metadata. All 17 are sequentially named
IMG_1806.MOV through IMG_1822.MOV; their capture times run from
2026-03-25 23:39:42Z through 2026-03-26 00:08:27Z. In filename order, gaps
from the prior clip end to the next capture are under 120 seconds except
IMG_1811 to IMG_1812 (about 240 seconds) and IMG_1813 to IMG_1814 (about 265
seconds). Those two breaks define the four groups above:

| Scene group | Capture interval (UTC) | Combined duration | Split |
|---|---|---:|---|
| IMG_1806-IMG_1811 | 2026-03-25 23:39:42 to 23:43:11 | 106 s | Calibration |
| IMG_1812-IMG_1813 | 2026-03-25 23:47:34 to 23:51:03 | 148 s | Held out |
| IMG_1814-IMG_1816 | 2026-03-25 23:55:37 to 23:58:39 | 241 s | Calibration |
| IMG_1817-IMG_1822 | 2026-03-26 00:02:13 to 00:08:27 | 312 s | Held out |

The files report H.264
1920x1080 video at approximately 29.97/30 fps with matching capture-time tags;
their measured durations are retained per source in the JSON manifest. This is
a metadata-based scene grouping, not a claim of newly assessed visual labels.
The annotation schema covers
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
