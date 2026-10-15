# Cross-modal grounding and broader semantics benchmark (EVAL-09)

`library/tools/cross_modal_eval.py` is a deterministic scorer for held-out
cross-modal relation annotations. It is independent of inference: it imports
no model, reads no media, and writes only the requested report. It scores
speech/person/face/event/object relations and unfamiliar scene categories
as relation precision/recall plus open-set abstention, by modality and
domain, keeping evidence provenance and certainty labels in scoring.

Relation scoring is over distinct (subject, predicate, object) triples:
thirty-seven grounded utterances behind one speaker-to-person link are one
relation with thirty-seven instances. Both counts are reported.
Empty denominators report null rather than zero: without gold there is no
accuracy to report, only abstention, provenance, certainty, and cost.

## Evidence contract

The input is a JSON evaluation record exported by an independent executor
(`predictions_from_project_model` exports the shipped `cross_modal_grounding`
executor's relations into this shape). Top-level `split` must be `held_out`
and `tuning_access` must be false. Certainty labels are the Project Model
vocabulary (`observed`, `measured`, `inferred`, `interpreted`, from
`project_model_contract.Certainty`); every gold relation carries
`provenance_ids` and every prediction carries non-empty
`evidence_provenance_ids`. Cost records measured `elapsed_seconds` and
`model_passes`. The scorer refuses calibration splits, tuning access,
missing provenance, unknown certainty, and negative costs.

Two annotation states are accepted. `sealed_custodian_only` scores fully
against custodian gold. `labels_pending_custodian` scores abstention only:
every case must be open-set with empty gold, predictions are recorded but
never judged (no false positives without something to be false against),
and relation precision/recall report null pending custodian labels.

A record's self-declared split is not enough. When the EVAL-01 corpus
manifest is supplied (`--corpus-manifest`), every scored `source_id` must
be listed there with split `held_out`, and the manifest must deny tuning
access with no label payloads in the repository. Training, calibration,
and held-out sets stay strictly separate.

## Measured baseline on the untouched domain slice

`eval09-cross-modal-held-out-input.json` is the frozen scorer input for the
EVAL-01 held-out slice (LC4930 + LCATL0011, `podcast_interview`, one
synchronized-camera leakage group whose labels were never created and never
scored). `eval09-cross-modal-held-out-report.json` is its frozen scored
report; re-running the scorer over the input reproduces it exactly (pinned
by `test_frozen_baseline_input_rescores_to_the_frozen_report`).

The input's predictions are the shipped grounding executor run over those
sources' already-measured lanes (M1 utterances, M3b diarized voice turns,
M3 faces, M7 lip-backed speaking spans, M6 offsets), assembled read-only
per source clock with no model pass (`model_passes: 0`, 0.001 s grounding
plus export). Of 135 measured utterances, 37 grounded; the executor
ventured 40 prediction instances forming 4 distinct relations, all
certainty `inferred`, all with cited evidence:

- `speaks_as` (verified): the lip-backed voice to its face on each camera;
- `speaker_face_disagreement` (candidate): two diarized voices against one
  face on LC4930, kept as cited candidates rather than verdicts.

Scorecard: `speech_person` abstention 0.0 (ventured, unjudged);
`speech_reference`, `face_event`, `audio_visual_event`, `person_object`,
and `scene_category` abstention 1.0 (the executor emitted nothing there:
no co-timed reference events, no sound-event lane, no object participants,
and no scene-category predictor on this slice). Relation precision/recall
is null across all modalities pending custodian gold labels from
EVAL-03/05/06. Unfamiliar-footage generalization beyond abstention is
pending corpus coverage: the EVAL-01 manifest holds no unfamiliar held-out
genre (classroom, product, and b-roll are absent), so the broader-semantics
half is currently exercised by the open-set abstention mechanism only.

## Run

```sh
bin/vep -m library.tools.cross_modal_eval \
  docs/benchmarks/eval09-cross-modal-held-out-input.json \
  --corpus-manifest docs/benchmarks/2026-10-15-editorial-eval-corpus.json \
  --out /tmp/cross-modal-report.json
```

This command performs no inference or media work and needs no heavy-work
lock. It returns status 2 for invalid or non-held-out input. The main and
branch acceptance checks are:

```sh
# On main this fails: the scorer does not exist there.
git show main:library/tools/cross_modal_eval.py
bin/vep -m library.tools.cross_modal_eval \
  docs/benchmarks/eval09-cross-modal-held-out-input.json \
  --corpus-manifest docs/benchmarks/2026-10-15-editorial-eval-corpus.json

# On this branch it prints the frozen report and exits 0, with the
# manifest cross-check refusing any non-held-out source.
```

Depends on EVAL-03, EVAL-05, EVAL-06, and CAP-04, all in flight: gold
relation labels and the revision envelope they arrive under are marked
pending above rather than waited on. What those lanes owe this benchmark
is custodian-sealed speech/person/face/event/object relation gold on the
held-out slice; the scorer, exporter, abstention mechanism, and manifest
gate are ready for it.
