# Blind editorial labelling pilot

The pilot asks for evaluator-only human judgments; no answers are in the
repository. The frozen 30-row search-window sampling spec is
[`2026-10-15-blind-editorial-pilot-spec.json`](2026-10-15-blind-editorial-pilot-spec.json).
Its `design_stratum` is for sample balance only and is removed from the kit
shown to the labeller. Likely-positive and hard-negative descriptions are
sampling hypotheses, not gold labels. The source split is checked against the
EVAL-01 corpus manifest every time the kit is built.

## Create a kit

Run the existing EVAL-04 runner on calibration footage into a scratch result
directory. Combine its actual `edit.otio.json` outputs with the search spec;
the helper refuses unless it finds the requested count of valid calibration
proposals. It never fills a shortfall with invented decisions.

```sh
bin/vep -m library.tools.blind_label_pilot add-eval04-proposals \
  --spec docs/benchmarks/2026-10-15-blind-editorial-pilot-spec.json \
  --runner-output /tmp/eval04-calibration \
  --out /tmp/eval04-calibration/pilot-spec.json

bin/vep -m library.tools.blind_label_pilot generate \
  --spec /tmp/eval04-calibration/pilot-spec.json \
  --source-root "$HOME/Documents" \
  --scratch /tmp/ren-blind-editorial-pilot
```

The generator checks every source against its manifest split and duration,
probes source duration with `ffprobe`, then makes short silent H.264 windows
with `ffmpeg`. `label.html` runs offline and displays only a window, its query
or the plain-language proposed decision, and the answer controls. It does not
display scores, proposal IDs, source IDs, or sample strata. Keep the generated
directory outside the repository and project folders. Open `label.html` in a
local browser; the page downloads an answer JSON when saved.

## Ingest and consume

```sh
bin/vep -m library.tools.blind_label_pilot ingest \
  --answers /tmp/ren-blind-editorial-pilot/blind-editorial-answers.json \
  --kit /tmp/ren-blind-editorial-pilot/kit.json \
  --out /tmp/ren-evaluator-records/blind-labels.json

bin/vep -m library.tools.editorial_task_benchmark /tmp/eval04/result.json \
  --pilot-labels /tmp/ren-evaluator-records/blind-labels.json
```

Ingest validates the complete answer set, records labeller and date, refuses
to overwrite an existing record, and marks the file evaluator-only, not a
training data source or frozen calibration input. D02 can pass this record to
`visual_retrieval_benchmark.enrich_visual_result(...,
evaluator_labels_path=..., evaluator_window_scores=...)`; the scores are keyed
by kit item ID and the output reports a candidate balanced-accuracy cutoff,
never applies it to retrieval. F02/F03 read proposal judgments through
`editorial_task_benchmark --pilot-labels` (or `blind_label_pilot summary
--kind edit`). Human judgments remain a separate report field and do not
rewrite benchmark inputs or acceptance results.

## Labelling time estimate

Thirty search windows at about 20 seconds each and twenty proposal decisions at
about 30 seconds each take roughly 20 minutes of focused review. Allow 5 to 10
minutes for playback/notes and breaks: **25 to 30 minutes total**. This is an
estimate, not a measured labelling result.

## A head-to-head against a professional editor's selection

The kit above judges ONE proposal: is this decision acceptable? A second
question asks something different - was REN'S choice the better one? - and it
needs two treatments side by side, with the evaluator unable to tell which
side is Ren's.

`library/tools/blind_comparison.py` constructs that comparison:

```python
from library.tools import blind_comparison as eu

record = eu.comparison(
    "req-1", "the opening feels slow",
    ren_treatment,        # {"treatment_id", "name", "plan_summary", "evaluation"}
    professional_treatment,
    criteria=[{"criterion": "the claim lands first"}])
```

The sides are SHUFFLED: the order is a deterministic function of the
comparison id, not of which side is Ren's, so the same comparison always shows
the same order and no order is systematically Ren's. `evaluator_view` is what
the evaluator is shown - the request, each side's plan summary and its
per-criterion verdicts, and no origin, no treatment id and no treatment name.
A plan summary that names either treatment is refused, because a description
that gives the answer away is not a blind comparison.

`render_evaluator_page` writes the offline page (no scripts, stylesheets,
fonts or network calls outside the file). The evaluator saves an answer JSON
naming a SIDE, never an origin:

```python
preferences = eu.read_preferences(answers)          # {"labeller", "choices"}
scored = eu.score_preferences([record], preferences)
print(scored["agreement"]["ren_preferred_rate"])
```

`score_preferences` unblinds each preference and measures how often Ren's
treatment was preferred over the professional's. A pair the evaluator could
not decide on is reported as UNDECIDED, never as a loss for either side, and a
preference for a comparison nobody built is reported rather than dropped.

Nothing here decides which treatment is better; that is the evaluator's
answer, and this module's job is to make the answer mean something.

Judging a DELIVERED RENDER against its brief is a different measurement, and
it lives in `library/tools/editorial_brief_scorer.py`: it keeps the editorial
verdict separate from the technical gate, counts an unmeasured criterion as
neither a pass nor a fail, and binds the score to one exact render by its
SHA-256.
