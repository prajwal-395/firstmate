# Latency budgets by operation class

**What this is.** The punch list's PERF-005: *implement clear latency
budgets by operation class*. A budget says what one UNIT of work costs
the engine, measured without the contention of a shared machine, so a
regression in the engine is visible as a number rather than as a feeling.

**The seven classes** are the punch list's, by name:

| Class | What it is |
| --- | --- |
| `metadata_read` | Metadata reads and capability discovery |
| `property_edit` | Simple timeline property edits |
| `caption_graphic_revision` | Caption and graphic revisions |
| `structural_edit` | Structural timeline edits |
| `reel_construction` | Full reel construction |
| `source_analysis` | Source analysis and video reasoning |
| `delivery_render` | Final delivery and rendering |

**The code.** `library/tools/latency_budget.py` owns the vocabulary, the
measurement and the check. The baseline table is
`library/tools/latency_budgets.json`, and the front door is:

```sh
ren budget <project>          # the table, with the classes over budget
ren budget <project> --json   # the same, as a structure
```

## The three rules that make a budget a budget

### 1. The unit is the point

A budget is a cost PER UNIT OF WORK, and two units are only comparable
when they are the same thing. Every operation names the unit it is
priced in, and the divisor that makes it comparable is COUNTED from the
ledger, never assumed:

| Operation | Unit | Divisor |
| --- | --- | --- |
| `catalogue_read` | one source file catalogued | the run's `source_ingest` rows |
| `project_scan` | one project folder scan | one |
| `capability_discovery` | one registry derivation | one |
| `timeline_property_edit` | one property edit | one |
| `caption_text_revision` | one caption revision | one |
| `overlay_position_revision` | one overlay revision | one |
| `timeline_trim` | one trim | one |
| `reel_build` | one reel build | one |
| `coarse_frame_decode` | one decoded frame | the span's own `calls` |
| `video_reasoning` | one model call | the span's own `calls` |
| `reel_render` | one rendered frame at 1920x1080 | the span's own `frames` |
| `reel_verification` | one verification | one |

Why this matters, measured: the scratch ledger's decode spans cost
**24.2 s for 47 frames** and **467.4 s for 990 frames** - a 19x
difference in the same class, entirely from clip length. One raw number
for both hides which one a reader is looking at. Divided by frames,
they are **0.516 s and 0.472 s per frame**: the same operation, and a
number that means something.

### 2. Waiting is not working

One Resolve instance serves every lane. A run's wall time therefore
contains two kinds of time that are not the engine's work:

* **`resolve_wait`** - the seconds spent queueing for the Resolve
  lease, whether the lease was granted or refused. A refused wait is
  still seconds spent waiting.
* **`schedule.queue_wait_s`** - the seconds spent queueing for the
  machine's heavy-work slot.

Both are subtracted from the wall, and both are reported beside the
result, so a reader can see how much of the hour was contention. This
is what the broker KPI work (PR 3087) separated; the budget uses that
separation.

The measured case: the scratch project's successful
`axi.property_change` task (run `2026-10-21T132742Z-c6bf37`) took
**1088.8 s** of wall, of which **900.0 s** was the lease queue and
**188.5 s** was work. A budget set on the wall says a property edit
costs 1088 s; the same edit on a quiet machine costs 188 s. Setting the
budget on the wall measures the weather, not the engine.

### 3. A missing measurement is not a zero

Only a COMPLETED operation contributes to a budget. A refused edit
costs what it costs to refuse - which is not what a slow edit costs -
and pooling the two makes the budget a statement about refusals. The
scratch ledger holds **33 refused `axi.property_change` tasks against 3
that succeeded**; pooling them would say a property edit costs 95 s
when the three that actually happened cost 59-379 s and the 33 that did
not happen cost nothing at all.

So a class with no completed sample reads **unmeasured**, never `0`, and
never "within budget". The baseline table says which classes are
unmeasured and why.

### 4. A budget is a tripwire, not a tripwire that cries wolf

The machine is shared by every lane, so a class's measured cost moves
with the load. The check therefore compares a class's TYPICAL (p50) cost
against its baseline, and fires only when the class costs **materially**
more - above the baseline's own magnitude, or above a five-second
floor. The same shape as the suite-performance gate's regression check
(`scripts/ci_perf_report.py`). A gate that fired on every load blip
would be noise dressed as a regression, and a gate that cries wolf gets
ignored.

## The baseline table

`library/tools/latency_budgets.json` is written by
`scripts/write_latency_budgets.py` from the ledger, never typed by
hand, so every number names the runs it came from:

```sh
python3 scripts/write_latency_budgets.py \
    --projects-root "$PIPELINE_PROJECTS_ROOT" \
    --private-root "$REN_QUALIFICATION_MEDIA" \
    --out library/tools/latency_budgets.json
```

The runs it reads are **Ren-owned**: the `ren-scratch-*` copies under
the projects root, and Ren's own qualification runs under its
qualification media store (which build and render on synthetic
footage). A captain's or a client's project is never measured here: a
budget set on somebody else's footage would be a budget for their
content, not for the engine. The scratch copies share their history -
the same run was written into four of them - so identical rows are
counted once, which is what makes the sample count a count of RUNS and
not a count of folders.

Each baseline row carries the target, the unit, the sample count, the
basis it was derived from, the date, and the runs it came from, with
the corpus named. The machine is recorded because **a budget is a fact
about a machine**: a budget measured on a loaded machine is not a budget
for a quiet one, and `ren budget` says so when the machine differs.

## What a budget is NOT

A budget is a **regression tripwire**, not a quality score and not a
correctness gate. `ren budget` answers one question - *is this class
slower than the last time it was measured?* - and a class over budget
means "look at this", not "this output is wrong". No number in the
table may be cited as evidence that an edit is good; the verdict on the
output belongs to the checks that judge the output.

The same rule the repo already follows for the performance profile
(`library/tools/perf_report.py`): a measurement is a measurement, and a
number no row measured is `None` and named as unmeasured.

## Reproducing the measurement

The table above was measured on 2026-10-21 from Ren's own runs: the
`ren-scratch-*` copies and Ren's qualification runs. The live
measurements behind the classes that the historical ledger could not
supply were taken on the Ren-owned `Ren Scratch - breaktest` scratch
project, through the serving broker. No captain project, timeline or
media was written.

The evidence behind each rule here, with the measurements that
produced it, is in
[docs/RULE_EVIDENCE.md](RULE_EVIDENCE.md#the-budget-that-measured-the-weather).
