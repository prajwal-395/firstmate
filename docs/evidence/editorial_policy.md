# The editorial policy, and the behaviours that read it

Ren's punch list (2026-10-20) asked for ten things under "3.3 Develop
higher-level editorial behaviours". Three of them were already built and
are listed at the end. The other seven needed a place to stand, and this
file is that place.

## The gap

Every threshold that decided an editorial outcome lived in the module
that used it:

* `library/tools/pacing.py` derived a motion threshold from the median
  motion energy of the clips it was given;
* `library/tools/retake_scan.py` carried its own filler vocabulary;
* `library/tools/sequence_scoring.py` carried its own redundancy
  threshold;
* `library/tools/phrase_coherence.py` carried its own pause bound.

So "make the cut tighter" could only be carried in a creative brief, and
no measurement read a brief. A project that wanted a different answer
had no declaration to make.

There was also no way to say what KIND of video a project was making.
The punch list names seven objectives - interview, podcast, social
reel, product demo, tutorial, documentary, promotional - and none of
them existed anywhere in the engine.

## What landed

| Module | Owns |
|---|---|
| `library/tools/editorial_policy.py` | EDIT-025: the project's own declaration, the slots it may declare, the objectives it may name |
| `library/tools/editorial_objective.py` | EDIT-024: what each objective requires of a cut, checked against the cut |
| `library/tools/silence_reduction.py` | EDIT-016: what a silence or a filler costs the rhythm if it is removed |
| `library/tools/angle_evidence.py` | EDIT-020: how each camera angle measures against speaker identity, action visibility, eyeline and continuity |
| `library/tools/shot_sequence_quality.py` | EDIT-021: which sequences of shots measure as weak, and what would break them |
| `library/tools/pacing_hold.py` | EDIT-022: which moments measure as deliberate and should be held |
| `library/tools/content_significance.py` | EDIT-023: what a reduction takes with it |
| `library/tools/editorial_report.py` | the one surface all of them reach the editor through |

## The declaration

A project declares, in `project.yaml`:

```yaml
pipeline:
  editorial_policy:
    objective: interview
    slots:
      silence:
        max_gap_seconds: 0.9
        remove_fillers: true
      redundancy:
        max_similarity: 0.8
      pacing:
        target_asl_seconds: 3.2
        hold_minimum_seconds: 4.0
      shot:
        max_same_angle_run: 4
      angle:
        order: [continuity, speaker_identity, action_visibility, eyeline]
      content:
        required_claims: ["claim:1"]
```

The run reads it on every run, into `state["editorial_policy"]`, and a
step that declares it in its manifest receives it. A project that
declares none gets NO key: the behaviours fall back to the material's
own measured distribution, and a threshold nobody chose is never
invented (AGENTS.md 10.5).

**An unknown objective or an unknown slot is REFUSED**, at declaration
time, by name. A slot the engine cannot read is a slot the editor
believes is in force and is not.

## The objective is not a preference

An objective is a genre, and a genre carries CONSTRAINTS - things that
must be true of the cut for the video to be the kind of video the
project declared:

| Objective | Constraints |
|---|---|
| `interview` | `speaker_coverage`, `question_answer_kept_together` |
| `podcast` | `speaker_coverage` |
| `social_reel` | `hook_present` |
| `product_demo` | `demonstration_visible`, `cutaway_over_demonstration` |
| `tutorial` | `step_order` |
| `documentary` | `chronology_preserved` |
| `promotional` | `claim_present` |

There is no count in any of them, so none of them is a creative floor
(`library/tools/creative_floors.py`).

A constraint whose facts were never measured reads `undetermined`, not
`satisfied` - the same discipline `library/tools/undetermined.py` and
`library/tools/evidence_graph.py` already keep. The three readings are
`satisfied`, `violated` and `undetermined`.

## What each behaviour measures

**EDIT-016 - silence and filler reduction.** `rhythm_profile` records
the passage's own pauses, its phrase boundaries, its delivery rate and
the emphasis the prosody pass measured. `rhythm_cost` prices a trim.
Two removals are refused always: a trim whose edge lands inside a word
(clips a syllable), and a trim that spans a pause longer than the
passage's own median pause that sits beside a measured emphasis (removes
a beat the speaker marked). Everything else is reported with its price,
because `library/tools/alignment_findings.py` has always refused to
decide whether a long pause is dead air or a dramatic beat.

The threshold is the project's declared `silence.max_gap_seconds`, or
the material's own distribution: the gap that nine of ten gaps in the
same material fail to reach - the derivation
`library/tools/pacing.py` already uses.

A filler is a word with a duration, and there are two edits inside
"remove the filler": keep the pause (drop the word) or close the pause
(drop the word and the silence). Both are priced, and only the one that
takes a beat is refused.

**EDIT-020 - camera angles.** Four criteria, each measured:

* *speaker identity* - is the person talking in this passage the person
  this angle shows;
* *action visibility* - does this angle cover the passage's measured
  action window (the vision pass writes one window per observed
  action, with its own bounds);
* *eyeline* - read through `library/tools/continuity.direction_cell_for_cut`,
  the one reader of the measured subject centre, so a flip stays a
  POSSIBLE line cross and not a verdict;
* *continuity* - the same scene, and a different camera from the shot
  before it.

The candidates are ordered by the criteria - heaviest first, in the
order the project declares - and never by a score. A candidate whose
range does not cover the passage is excluded mechanically.

**EDIT-021 - weak shot sequences.** A run is weak on measured grounds:
no motivated cut inside it, the same content throughout, or no variety
in the framing. Two of the three make it weak; one of them alone is an
ordinary cut. A project may declare `shot.max_same_angle_run`, and that
limit makes a long run weak on its own.

The alternatives are proposals: another camera's picture over the SAME
audio range, a shot from the material inserted inside the run, or two
shots that carry no words swapped. The last one is refused for spoken
passages, because swapping them would break the chronology the
documentary objective protects.

**EDIT-022 - moments to hold.** The other direction of pacing.
`library/tools/pacing.py` finds the stretches where nothing happens and
proposes to cut them; this finds the stretches that should be KEPT. The
evidence is the block's own rate against the material's median rate, a
measured emphasis inside it, the direction's pacing curve, and a
measured pause inside it.

A hold candidate always has words sounding. A stretch with no words is
DEAD, and dead air is `library/tools/pacing.py`'s finding - proposing to
hold it would be proposing to keep nothing.

**EDIT-023 - what a reduction takes with it.** The claims the intent
model declared, the beats it declared (a beat marked `required` is one
the edit may not drop), and the vision pass's own measurement of what
each clip shows. A reduction that dropped a required claim reads
`damaged`; a reduction that dropped a claim nobody required is
reported and is not damage.

The vision pass does NOT measure mood - its own prompt asks for what is
physically visible and refuses interpretation (AGENTS.md 10.1). So the
emotional weight of a passage is carried by the BEAT it serves, and by
nothing else here.

## The report

`python3 -m library.tools.editorial_report <project>` prints it;
`--json` prints the JSON and `--out` writes it. It is an ANALYSIS: it
changes nothing and it gates nothing.

## What was already there

Three of the ten items were built before this lane and are reported as
already done:

* **EDIT-017** - redundant statements and repetitive sections.
  `library/tools/reel_build.py` (`redundant_takes`, `suspected_takes`,
  `judge_take_cuts`, `possible_retellings`), `library/tools/
  reel_proposal.py` (`duplicate_takes`), `library/tools/retake_scan.py`
  (`false_starts`, `paraphrases`, `verbatim_reports`),
  `library/tools/speech_take.py` (`repetition_in`, `classify_pair`),
  `library/tools/sequence_scoring.py` (`score_redundancy`).
* **EDIT-018** - hooks, conclusions, key claims and turning points.
  `library/tools/intent_model.py` (`beats`, `claims`, `setup_payoff_links`,
  `validate_narrative_constraints`), `library/tools/reel_opening.py`,
  `library/tools/reel_thesis.py`, `library/tools/reel_ending.py`,
  `library/tools/reel_quality_bar.py`, `library/tools/passage_engagement.py`,
  `library/tools/narrative_relations.py`.
* **EDIT-019** - B-roll selection. `library/steps/step_3_02_select_broll/`
  with `library/tools/cutaway_window.py`, `library/tools/broll_correspondence.py`
  and `library/tools/broll_coverage.py`.
