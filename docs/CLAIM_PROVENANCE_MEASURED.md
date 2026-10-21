# Claim provenance, measured (VI-012)

Punchlist item VI-012: *separate measurements from interpretations*. A
world model (M9, `library/tools/world_model.py`) holds claims - a
transcribed word, a detected face, a scene description, a sound event,
a speaking span, a model's verdict. Before this change every one of
them was filed the same way, so a VLM's description of a room read
exactly like the device's own record of the frame rate.

## What changed

`library/tools/claim_provenance.py` is the one enumeration:

| class | who produced it | example |
|---|---|---|
| `measured` | an instrument or the capture device | the multicam offset, a deterministic shot boundary |
| `inferred` | a model asserted it about the picture or the sound | a transcribed word, a detected face, a scene description, a sound-event label |
| `interpreted` | a human judgement read it | an evaluation, a creative decision |
| `unknown` | nobody declared it | never defaulted to a measurement |

The class is a CONSEQUENCE of the registry type
(`library/tools/evidence_contract.py`), and the registry type is
declared BY THE PRODUCER (`CLAIM_TYPES` in each producer module). A
contract test fails when a producer's declaration and the lookup drift
apart.

Three rules ride on it:

- **A description is never promoted to a measurement without a
  verifier.** `may_file_as_measured` allows the promotion only for a
  DETERMINISTIC verifier; a model's confirmation of another model's
  claim is recorded as a verification and the class does not move.
- **Unknown stays unknown.** A claim no producer declared keeps the
  class `unknown`.
- **Conflicting observations are recorded, not merged.** Two speaking
  spans for one second, two scene segments describing one second, two
  verdicts on one candidate, and two instruments measuring one offset
  that disagree are all kept, and the disagreement is written into
  `conflicts`.

## The measurement

Seven real podcast sources from the machine's source memory
(`~/.local/share/vep/source_memory`), rebuilt OFFLINE from the stored
lanes - no decode, no model, no Resolve, no captain media touched.
"Before" is the world model built by `main`; "after" is the same
sources rebuilt by this branch. The census is
`python3 -m library.tools.claim_provenance <world_model.json>`.

| | before (main) | after (this branch) |
|---|---:|---:|
| claims | 51,611 | 52,744 |
| claims carrying a class | 0 (0.0%) | 52,744 (100.0%) |
| model descriptions filed as measurements | 51,603 | **0** |
| conflicting observations recorded | 0 | 20 |
| claims filed `measured` | - | 8 |
| claims filed `inferred` | - | 52,736 |
| claims filed `interpreted` | - | 0 |

Per source (claims; class-carrying share; model descriptions filed as
measurements; conflicts):

| source digest | lanes | claims before | claims after | share with a class | descriptions filed as measurements (before -> after) | conflicts |
|---|---|---:|---:|---:|---:|---:|
| `157522998320` | m0 m1 m3 m3b m6 m7 | 19,034 | 19,413 | 0% -> 100% | 19,033 -> 0 | 11 |
| `dca969b8ed90` | m0 m1 m3 m3b m6 m7 | 23,353 | 23,920 | 0% -> 100% | 23,352 -> 0 | 0 |
| `f683a9759e06` | m0 m1 m3 m3b m6 m7 | 4,155 | 4,217 | 0% -> 100% | 4,154 -> 0 | 3 |
| `c11218156a07` | m0 m1 m3 m3b m6 m7 | 1,467 | 1,522 | 0% -> 100% | 1,466 -> 0 | 0 |
| `4fa282cea147` | m0 m1 m3 m3b m6 m7 | 1,464 | 1,484 | 0% -> 100% | 1,463 -> 0 | 0 |
| `9b7fd11e02e5` | m0 m1 m3 m3b m6 m7 | 1,110 | 1,150 | 0% -> 100% | 1,109 -> 0 | 0 |
| `d19007c31379` | m0 m1 m3 m3b m4 m5 m6 m7 | 1,028 | 1,038 | 0% -> 100% | 1,026 -> 0 | 6 |

`d19007c31379` is LCATL0011.MXF, the reference camera of the podcast
project - the reel's own footage.

### Why the denominator moved

The after-census counts 1,133 more claims than the before-census on the
same sources, and every one of them is a speaking span. The old join
kept a speaking span's voice track and face track and dropped the
span's own bounds, so the census could not tell one span from the next
and counted them as one. The bounds are now carried, so each span is a
claim of its own. No claim was lost: an entry-by-entry diff of the two
world models shows added keys only (`provenance`, `conflicts`,
`speech.speakers`, `events.*.start/end`, `verification`, verdict
frames) and no removed or altered value.

### What the numbers say

Eight of 52,744 claims are measurements. Everything else is a model's
inference - a transcribed word, a detected face, a scene description,
a sound event, a candidate span, a verdict. The world model read as a
tape of objective facts; it is a tape of a model's reading of the
footage, and now it says so on every claim.

No claim in these sources is `interpreted`: no human judgement has
entered the world model yet. The class exists and a judgement lands in
it instead of being filed as a measurement.

### The conflicts that were invisible

Twenty disagreements were being merged away. Two are worth naming:

- `d19007c31379` (LCATL0011.MXF): the audio n-gram measurement puts
  this source 11.31 s from the reference, and the Resolve timeline
  cross-check puts it 37.775 s away. The world model carried the first
  and dropped the second, so the source's own offset read as settled.
  Both readings are kept now.
- The same source's M4 scene segments overlap the last five seconds and
  describe them differently; the join kept the first segment and
  dropped the second.

## Reproducing

```sh
# the machine's stored lanes, copied to a scratch root (never the store itself)
python3 -m library.tools.world_model build <project> --memory-root <scratch>
python3 -m library.tools.claim_provenance <scratch>/<digest>/world_model.json
```
