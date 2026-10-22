# The model admission gate (INFRA-007)

The evidence and the story behind this gate are in
[`docs/evidence/model_admission_gate.md`](evidence/model_admission_gate.md).

Optional model capabilities are **gated**, not merely listed. Every optional
model in `library/model_registry/registry.json` carries an `admission`
record, and the gate refuses to load, fetch, package or route to a model
whose record is missing, incomplete, or claims more than the licence
evidence allows.

The punchlist asked for four things and the record carries all four:

| The punchlist asks | The record's field |
|---|---|
| Track model weights, dataset restrictions, code licenses, redistribution conditions | `license` (verbatim from the licence evidence) and `commercial_use` |
| Keep commercially distributable dependencies separate from personal-only experimental options | `commercial_use`, plus the edition manifests and `model_admission.configurations()` |
| Permit a personal configuration to include additional capabilities without weakening the commercially permitted baseline | `model_admission.configurations()` refuses a personal configuration that loses a commercial capability |
| Keep evaluation evidence comparable between configurations | `required_gain` names the metric, the split, the incumbent, the fixture and the harness |

## The measurement this lane exists to move

**Share of the registry's optional models carrying a complete admission
record, before vs after.**

| | complete | optional models | share |
|---|---:|---:|---:|
| before (`b15d67dbb`) | 0 | 18 | 0.0% |
| after (this change) | 18 | 18 | 100.0% |

The denominator is every implementation with `optional_download: true`, which
is all eighteen rows in the registry; it is not a denominator of "the models
we happened to look at". Reproduce the after figure with:

```sh
bin/vep -m library.tools.model_admission
```

and the before figure with the parent commit's registry:

```sh
git show b15d67dbb:library/model_registry/registry.json \
  | python3 -c 'import json,sys; r=json.load(sys.stdin); rows=[x for x in r["implementations"] if x.get("optional_download")]; print(sum(1 for x in rows if x.get("admission")), len(rows))'
```

Before this change the registry had **no** admission record at all: the
licence evidence lived in `license_evidence.json`, the size of every
download was unrecorded, and nothing anywhere said what a model had to show
before it could be admitted.

## What the record says

```json
"admission": {
  "license": "MIT per OpenAI repository",
  "commercial_use": "unresolved",
  "size": {
    "bytes": 1710540580,
    "basis": "runtime_weights",
    "parts": [
      {
        "source": "https://huggingface.co/api/models/openai/clip-vit-large-patch14/revision/32bd...?blobs=true",
        "method": "hub_api_blobs",
        "files": ["model.safetensors"],
        "bytes": 1710540580
      }
    ]
  },
  "required_gain": {
    "metric": "macro_recall",
    "unit": "recall",
    "split": "held_out_visual_8q",
    "direction": "higher_is_better",
    "incumbent": "clip.openai_vit_large_patch14",
    "incumbent_value": 0.1473,
    "measured_value": 0.1473,
    "comparison": "no_regression",
    "threshold": 0.1473,
    "basis": "The frozen 8-query visual held-out set measured a present-query macro recall of 0.1473 ...",
    "fixture": "docs/benchmarks/2026-10-16-model-upgrade-baseline.md",
    "evidence": "library/tools/retrieval_eval.py"
  }
}
```

### `commercial_use` is a restatement, and it may only withhold

`commercial_use` reads `permitted`, `conditional`, `prohibited` or
`unresolved`, and it follows the licence evidence's approval status
(`approved` / `conditional` / `unresolved` / `rejected`). A record may
**withhold** permission the evidence grants - that is safe and is allowed -
but it may never **grant** permission the evidence does not. The licence
evidence, not the record, is what authorises a load.

### `size` is the bytes the runtime actually loads

The size is the weight payload the loader fetches, not the whole
repository: `model.safetensors` for a `transformers` model, the
`*.mlmodelc` tree for the CoreML Sortformer, the `english_us_arpa` zip,
dictionary and G2P assets for MFA, the Zenodo `pth` for PANNs, the
`buffalo_l.zip` pack for InsightFace. Every part names the publisher source
it was measured from, the method used to read it, and the files summed.

`scripts/measure_model_weight_sizes.py` re-reads those publisher sources
and compares them with the recorded numbers. It reads **metadata only** and
never downloads a model:

```sh
bin/vep scripts/measure_model_weight_sizes.py
```

At the time of writing, all eighteen models match their publisher sources.

### `required_gain` is what the model must show, before it is admitted

Three comparisons, and no threshold is invented for a measurement nobody
made:

| comparison | meaning | threshold |
|---|---|---|
| `no_regression` | the model's own recorded measurement is the floor: it must not get worse | the recorded value |
| `delta_over_incumbent` | a candidate must beat the named incumbent by at least the threshold | 10% relative over the incumbent's recorded value, in the metric's own unit |
| `establish_baseline` | nothing has been measured for this model or its incumbent: the named metric must be measured for both on the named fixture first | `null` |

`establish_baseline` is not a hole. It is the honest statement that the
comparison has not been made yet, and it names the metric, the split, the
fixture and the harness that will make it. An unmeasured value is never
recorded as a zero - Florence-2, which failed to load in the detector
bakeoff, is `NOT VIABLE` with its reason, and its `measured_value` is
`null`, not `0`.

### The thresholds that are numbers

| model | metric | recorded | threshold | source |
|---|---|---:|---:|---|
| `clip.openai_vit_large_patch14` | macro recall, held-out visual 8q | 0.1473 | 0.1473 | `docs/benchmarks/2026-10-16-model-upgrade-baseline.md` |
| `minilm.all_minilm_l6_v2` | absent-subject max cosine, 001/409 | 0.396 | 0.396 | `docs/FOOTAGE_INDEX_PROTOTYPE.md` |
| `owlv2.base_patch16` | detection recall at IoU >= 0.5, 40 frames | 0.892 (132/148) | 0.892 | `docs/benchmarks/object-detector-bakeoff-2026-10-13.md` |
| `grounding_dino.tiny` | detection recall at IoU >= 0.5, 40 frames | 0.27 (40/148) | 0.0892 over OWLv2 | same |
| `florence2.base` | detection recall at IoU >= 0.5, 40 frames | not viable | 0.0892 over OWLv2 | same |
| `sam2.1_hiera_small` | selected-subject track coverage, 1,615 frames | 0.956 (1,544/1,615) | 0.956 | `docs/SUBJECT_MASKING_MEASURED.md` |

## Where the gate refuses

- `model_registry.require_implementation()` - the fetch / load / package
  authorisation. This is the path `ren.edition.require_component()` and
  `ren.edition.authorize_component()` already used, so refusing a model with
  no record refuses it at the point it would be loaded.
- `model_registry.route()` - a model with an incomplete record is not a
  candidate for selection, and the refusal is reported with the missing
  fields.
- `model_registry.validate_registry()` - a record that is present but
  malformed is refused by field name, so a broken record cannot be committed.
- `model_watch.load_database()` - a **candidate** may not reach
  `decision.status: "adopted"` without a complete admission record, either
  its own or the one it inherits from the registry implementation it names.

The watchlist (`data/model_watch/candidates.json`) holds 77 leads; nine of
them are already registered implementations and now inherit that
implementation's record (`registry_implementation`), and the remaining
sixty-eight show `no record` until they are admitted to the registry. The
watchlist stays a lead list: it never downloads, benchmarks or admits.

## The configurations

`model_admission.configurations()` reports what each configuration admits
and refuses a personal configuration that loses a commercial capability.

- Commercial: 13 implementations.
- Personal: 18 implementations - the commercial baseline plus five that
  the licence evidence keeps out of client work (`gemma`, `clip`,
  `deepfilternet`, `audio_flamingo`, `insightface`).

Across all eighteen records the commercial use reads `permitted` 3 times,
`conditional` 10, `unresolved` 3 and `prohibited` 2; the comparisons are
`establish_baseline` 12 times, `no_regression` 4 and
`delta_over_incumbent` 2.

## What is NOT measured

Stated plainly, because the gate is about measurement and would be
worthless if it blurred this:

- Twelve of the eighteen records are `establish_baseline`: the audio event
  label lane (PANNs, AST, CLAP), the diarization lane (Sortformer, ECAPA),
  the alignment lane (MFA), the depth lane, the audio captioner, the
  denoiser, the face pack, V-JEPA and Gemma. Each names the fixture and
  the harness that would measure it. None of them has a Ren quality row
  today.
- The size of a model is a property of the pinned artefact, not of the
  machine: the numbers here are the publisher's bytes at the pinned
  revision, and they do not include the runtime, the Python wheels or the
  CoreML toolchain.
- Two rows measure a derived artefact rather than a publisher download:
  the Sortformer size is the palettized `SortformerOffline_v2.1.mlmodelc`
  tree Ren fetches, and the DeepFilterNet size is the published
  `DeepFilterNet3.zip` at the pinned release tag, because the binary
  fetches its weights itself and does not pin them.
- The threshold policy (10% relative over the incumbent) is a policy, not
  a measurement. The measurement it is applied to is recorded next to it.

## What was left undone

- No model was downloaded, benchmarked or qualified. The gate states what
  each model must show; the qualification lane still has to measure it.
- The twelve `establish_baseline` records have no threshold yet, because
  there is nothing honest to state. They become thresholds when the
  measurement exists.
- The licence lane's evidence snapshot is unchanged; this lane records what
  the evidence already says, and it does not decide any licence.
