# The editorial review contract

QA-006 of the captain's punch list (2026-10-20): a structured editorial review
that evaluates a delivered file on eight named legs, where every judgment
carries its evidence, its uncertainty, and a clear distinction between an
**objective failure** and a **subjective preference**.

The contract lives in `library/tools/editorial_acceptance/judgement.py`, beside
the independent judge it extends. It is not a second judge: the judge is still
handed `judge_input` (the brief's public half, the file's digest, its evidence
and its length) and nothing else, and the contract reads what the judge wrote.

## The eight legs

They are DATA, in one place (`judgement.REVIEW_LEGS`), because a review that
quietly judged five of them is a review whose coverage nobody can read.

| leg | what it asks |
|---|---|
| `narrative_coherence` | narrative coherence and preservation of meaning |
| `moment_selection` | correctness and relevance of selected moments |
| `pacing_and_rhythm` | pacing and rhythm |
| `speaker_camera_continuity` | speaker and camera continuity |
| `shot_quality` | shot quality and visual relevance |
| `audio_continuity` | audio continuity |
| `caption_and_graphic_clarity` | caption and graphic clarity |
| `creative_direction_adherence` | adherence to the user's creative direction |

## The two kinds of row

A review carries two SECTIONS, and they are apart on purpose.

- an **objective** row is a measurement the engine's own gates made ON THE
  DELIVERED FILE. It names the measurement it came from: the audio spec
  (`deliverable_qc.verify_audio_spec`, QA-004), the caption spec
  (`delivered_caption_spec.verify_delivered_captions`, QA-003) and the picture
  spec (`deliverable_qc.verify_picture_spec`, QA-002) - whose legs are the
  delivered frame, the framing, the overlays, the black frames and the frozen
  frames. A gate that could not run is reported `not_measured`, with the reason,
  and is never folded into a pass.
- a **subjective** row is a judge's reading of the same file: the status, the
  rationale, the evidence the judge looked at, and the uncertainty it admits.

A row declares which kind of claim it is. A subjective row may not claim an
objective basis and an objective row may not claim a subjective one, so the
review can never present a preference as a technical defect or a defect as a
preference.

## The verdict

| verdict | when |
|---|---|
| `fail` | an objective leg failed. A measurable defect in the delivered file. |
| `preference` | no objective leg failed and a subjective leg did. An editorial opinion, not a defect. |
| `not_measured` | nothing failed and a leg was not read. |
| `pass` | every leg was read and nothing failed. |

The score is over the MEASURED subjective rows only, and it is published beside
the verdict rather than inside it: `subjective_score_cannot_clear` names
`objective_failure`, and a file that failed its audio spec reads `fail` with a
score of 1.0 over the other seven legs.

## What the contract refuses

- a measured row with no evidence, or evidence a reader cannot look at;
- a row with no stated uncertainty, or an uncertainty with no note;
- a leg that was left out instead of being read as `not_measured`;
- two rows for one leg;
- an objective row that names no measurement.

## Running it

```
python3 -m library.tools.editorial_acceptance \
    --review <delivered file> \
    --brief-file <the brief's PUBLIC half: instruction, project state, \
                   acceptance criteria> \
    --judgments <a JSON file holding the judge's rows for the eight legs> \
    [--declared-silence 75.70:79.53] [--declared-black 75.94:76.50] \
    [--expected-duration 79.496] [--caption-intents <file>] \
    [--platform tiktok] [--report <out.json>]
```

`--brief` names one of the acceptance set's briefs instead of a file. The
objective legs are measured by the CLI; the subjective legs are the judge's own
rows, which the CLI scores and never re-runs.

## The proof

Two delivered scratch reels from Ren's own `ren-scratch-bench-demo` export
(79.5s, 1080x1920): one clean, and one with two seeded defects - 2.08s cut out
of the MIDDLE of a spoken sentence, and a 0.709s undeclared black flash at the
seam.

| | the clean reel | the defect reel |
|---|---|---|
| verdict | `pass` | `fail` |
| objective failures | none | `shot_quality` |
| subjective failures | none | 6 of 8 legs |
| subjective score | 1.0 | 0.25 |

The defect reel's objective failure is the picture spec's own black-frames leg:
0.709s of black at 47.172-47.881s, undeclared. The caption leg is
`not_measured` on both reels - this scratch project holds no compiled
manifest, so no caption intent was supplied - and the picture spec's overlays
leg is `not_measured` for the same reason: no overlay expectations.

The incident behind the contract, the measurements, and what it does not fix:
[docs/RULE_EVIDENCE.md](RULE_EVIDENCE.md#the-review-that-folded-a-technical-failure-into-a-score).
