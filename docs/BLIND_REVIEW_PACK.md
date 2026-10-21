# The blind review pack

QA-007 of the captain's punch list (2026-10-20): support human editorial
review on a blind evaluation set.

> - Have reviewers inspect delivered videos without knowing which
>   candidate or model produced them.
> - Provide the original brief, constraints, and relevant reference
>   material.
> - Use a structured rubric alongside an overall acceptance decision.
> - Record disagreement among reviewers instead of treating one subjective
>   opinion as absolute truth.
> - Distinguish acceptable work from work that requires significant human
>   repair.

A **pack** is the surface a human reviewer is handed. It is a directory:

```
pack/
  entries/entry-<opaque>.mp4      the delivered videos, renamed and stripped
  references/reference-0.txt       the reference material, copied in
  review-form.html                 the rubric form, self-contained
  pack.private.json                the PRIVATE half: which candidate is which
```

`review-form.html` and `entries/` are what the reviewer receives.
`pack.private.json` stays with whoever organised the review: it holds the
blinding map, and it is the only file that says which candidate or model
produced which video.

The code lives in `library/tools/editorial_acceptance/blind_review.py`.

## What is measured and what is not

The pack builds a SURFACE. It does not review anything itself, and it does
not decide whether a deliverable is good.

What the pack DOES do:

- **blinds the set** - the entries are opaque ids, the media are copies
  under those ids with their container metadata stripped, the order says
  nothing about the producer, and the reviewer-facing view is REFUSED if it
  names a producer at all;
- **gives the reviewer the material** - the brief's public half (the
  instruction, the project state, the acceptance criteria), the
  constraints and the reference material;
- **asks a structured question** - the eight legs of the editorial review
  contract (QA-006, PR 3088), each with a status, a rationale, evidence
  and a stated uncertainty, ALONGSIDE one overall acceptance decision;
- **records what was said** - every reviewer's rows, every reviewer's
  score, every reviewer's decision, and the legs they disagreed on.

The rubric is not restated here. `judgement.REVIEW_LEGS` owns the eight
legs and `judgement.review` grades a reviewer's rows exactly as it grades
an agent's, so a reviewer cannot write a judgment with no evidence or no
stated uncertainty either.

## The acceptance decision

Three answers, and the third is the one the punch list asks for:

| decision | what it means |
|---|---|
| `acceptable` | the deliverable stands as it is |
| `acceptable_with_minor_repair` | the deliverable stands, and something small has to be fixed |
| `requires_significant_repair` | a human has to do real work on this before it ships |

A candidate two reviewers judged differently has standing
`disagreement`. It is not the average of the two, and it is not the more
confident of the two: the average of two opinions is not an opinion
anybody held.

## The record

```
ren accept --blind-results --pack pack/pack.private.json --answers <dir>
```

writes a record that carries, per candidate:

- every reviewer's leg rows, score and decision;
- the legs the reviewers agreed on and the legs they did not;
- the candidate's standing, and why;
- the engine's own objective measurements of that delivered file, published
  BESIDE the panel's reading.

That last one matters: a human's approval is an opinion about the edit,
and it does not clear a measured defect. The clean candidate in the proof
below reads `pass`; the defect candidate reads `fail` on the engine's own
black-frame gate WHILE the two reviewers disagree about it.

## Running it

```
ren accept --blind-pack \
    --candidates <candidates.json> \
    --brief-file <the brief's PUBLIC half> \
    --constraints <constraints.json> \
    --references <references.json> \
    --pack-dir <where the pack goes> \
    [--platform tiktok] [--declared-black 75.94:76.50] \
    [--declared-silence 75.70:79.53]

ren accept --blind-results \
    --pack <pack/pack.private.json> \
    --answers <one answers file, or a directory of them> \
    [--report <out.json>]
```

`--candidates` is a list of the delivered videos under review:

```json
[{"candidate_id": "cand-alpha", "origin": "producer-alpha",
  "path": "/path/to/the/delivered/file.mp4"}]
```

`origin` is the identity the pack hides. It must be at least three
characters long: a secret shorter than that matches everywhere, so it
cannot be checked for leaks and a pack carrying it is refused rather than
half-blind.

`--platform` makes the pack MEASURE each delivered file with the engine's
own gates (QA-002, QA-003, QA-004). Those measurements are not shown to
the reviewer - a measurement would anchor a human's reading - and they are
published beside the panel's reading in the results.

## What the blinding does not cover

The PICTURE. A deliverable that shows its producer in the frame is not
made blind by renaming it. The pack blinds the pack's own fields, the
file names, the metadata and the order; it cannot blind the content of
the video, and it does not claim to.

## The proof

`docs/benchmarks/blind_review_pack_proof.py` builds the surface on two
real deliverables and two SIMULATED reviewers, and prints the before and
after with their denominators. No reviewer is contacted: the proof is of
the SURFACE, not of the deliverables.

The two deliverables are Ren's own `ren-scratch-bench-demo` export and a
copy of it with the same two defects QA-006 seeded - 2.08s cut out of the
middle of a spoken sentence, and a 0.709s undeclared black flash at the
seam - so the two candidates differ in exactly one thing. The strict
reader sees the broken sentence and the black flash; the lenient reader
sees a reel that plays.

| | the clean candidate | the defect candidate |
|---|---|---|
| standing | `acceptable` (2/2 reviewers agreed) | `disagreement` |
| reviewers' scores | 1.0 and 1.0 | 0.75 and 1.0 |
| legs disagreed | 0/8 | 2/8 (`narrative_coherence`, `shot_quality`) |
| engine's objective failures | none | `audio_continuity`, `shot_quality` |

The disagreement is recorded, not resolved: the candidate's standing is
`disagreement`, and the engine's own measurement of the black flash sits
beside it, uncontradicted.

The before/after, measured by the same script against the base commit:

| | before | after |
|---|---|---|
| files carrying an acceptance-decision vocabulary | 0 | 1 |
| files carrying a blind review pack | 0 | 1 |
| files carrying a reviewer's form | 0 | 1 |
| files carrying a blind results record | 0 | 1 |
| the blind head-to-head's evaluator view carries a delivered video | no | yes (the pack) |
| candidates that reached one standing | - | 1/2 |
| leg readings that agreed | - | 222/224 |

The engine's pre-existing blind surfaces were measured, not assumed:
`library/tools/blind_comparison.py` shows a reviewer two PLAN SUMMARIES
with no video, no brief, no rubric and no decision, and
`library/tools/blind_label_pilot.py` shows a labeller clip windows with
no brief and no acceptance decision. Neither can hand a reviewer a SET of
delivered videos, and neither records what two reviewers disagreed about.

The incident behind the rule, and what the pack does not fix:
[docs/RULE_EVIDENCE.md](RULE_EVIDENCE.md#the-review-that-had-one-opinion).
