# Report provenance

A report is a claim. This page says what a claim carries, which claims
may be compared, and what the two ordinary-request measurements in
`tests/live/reports/` each measured.

The vocabulary lives in one module: `library/tools/report_provenance.py`.

## The header

Every canonical report carries a `provenance` block with seven fields:

| field | what it answers |
|---|---|
| `evidence_kind` | what KIND of evidence the claim rests on |
| `source_commit` | the revision the claim was produced from |
| `environment` | the machine it ran on |
| `fixture` | the material it ran against |
| `resolve_version` | the Resolve build it ran on |
| `run_timestamp` | when it ran |
| `evaluation_schema` | the schema its numbers are written in |

plus the `scope` (below) and the `producer` - the executable that
produced it.

A field nobody measured reads `not_recorded`. It is never a zero, never
a default that looks like a reading, and never a value carried over
from another report.

## The five evidence kinds

| kind | what it establishes |
|---|---|
| `implementation` | the code path exists and its own contract test passes; it says nothing about the backend |
| `simulated` | a fixture or a double answered; no real backend was exercised |
| `live-qualification` | a real backend was exercised on a real fixture and the evidence was read back from it |
| `orchestration` | the agent chose the operations and the targets itself |
| `editorial-acceptance` | a delivered file was judged against a creative brief |

The kind is not decoration. A typed request handed to a driver that
already knows the operation is `live-qualification`, NOT
`orchestration`: the difference between the two is whether the engine
chose the operation. The blind run, where the instruction is the only
input, is `orchestration`.

## The scope, and what may be compared

Two numbers are comparable only when their scopes match. The scope has
four fields - `measurement`, `evaluation_level`, `task_set` and
`fixture` - and it is what makes "1 of 10" and "6 of 10" two
measurements rather than one number that changed.

`report_provenance.write_report` enforces the rule:

- a report measured in the SAME scope replaces the one at the path, and
  records what it superseded;
- a report measured in a DIFFERENT scope is written BESIDE it, under a
  name that carries the scope, and the older evidence stays readable.

A newer result never silently replaces prior evidence.

## The writers that carry it

| writer | report |
|---|---|
| `library/tools/editorial_acceptance/runner.py` | the brief acceptance report (`ren accept`) |
| `library/tools/editorial_acceptance/record.py` | the task record a run leaves behind |
| `library/tools/retrieval_eval.py` | the retrieval evaluation (`ren eval-search`) |
| `library/tools/axi_qualification.py` | the qualification registry and each imported row |
| `library/tools/perf_report.py` | the performance profile (`ren profile`) |
| `tests/live/editorial_acceptance.py` | the ordinary-request and the blind reports |

The committed reports under `tests/live/reports/` carry the header too.
They were produced before the header existed, so each one names the
revision that RECORDED it and reads `not_recorded` for the machine and
the time - the run that produced them is not recorded in them, and this
page will not pretend otherwise.

## The two ordinary-request measurements

Both files measure the SAME thing: the ten ordinary typed requests at
evaluation level 2, on the `Ren Qualification / fm-scratch qualify`
fixture. They differ in the environment they ran in, and they are kept
side by side rather than one replacing the other.

| file | recorded | fixture | result | what the failures were |
|---|---|---|---|---|
| `editorial-request-acceptance-2026-10-20.json` | 2026-10-20 | could NOT be seeded | **1 of 10** | 9 rows are `missing_evidence`: the pool root carried no `qual_a.mp4` |
| `editorial-request-acceptance.json` | 2026-10-21 | seeded | **6 of 10** | 4 rows are production-route refusals: no production-qualified executor for `clip.delete`, `clip.trim`'s source delete, `overlay.replace`'s delete, or the camera switch |

Read with the denominators: the first is 1 pass of 10 typed requests in
an environment that could not produce the fixture, so 9 of its rows
measure the environment rather than the backend. The second is 6
passes of the same 10 typed requests with the fixture seeded, and its 4
failures are supported failures - the engine refused an edit it had no
qualified route for, and nothing was written.

Neither number replaces the other, and neither may be quoted without
its scope.

The E2E workflow assessment, which PR #2952 replaced in place with the
first of those measurements, is restored in
`tests/live/reports/editorial-acceptance.json`. It is a repository
assessment with no live run behind it, and its provenance says so.

## What the recent execution and retrieval work rests on

The reports the recent work landed, and what kind of evidence each one
is. A reader who wants to know what a PR measured reads this table
before the file.

| report | evidence kind | measurement | level | revision that recorded it |
|---|---|---|---|---|
| `editorial-request-acceptance.json` | live-qualification | ordinary_request_acceptance | 2 | `53a22521` |
| `editorial-request-acceptance-2026-10-20.json` | live-qualification | ordinary_request_acceptance | 2 | `5336019a` |
| `editorial-acceptance-blind.json` | orchestration | blind_editorial_requests | 3 | `e6394291` |
| `editorial-brief-acceptance.json` | editorial-acceptance | editorial_brief_acceptance | 4 | `85b20281` |
| `editorial-acceptance.json` | implementation | e2e_workflow_assessment | not applicable | `ec91965c` |
| `library/tools/axi_qualification_records.json` | live-qualification | axi_qualification_registry | 1 | the revision that recorded it |

The revision in the last column is the one on `main` that recorded the
MEASUREMENT the file reports - not the annotation. For the reports
produced before the header existed it is not the revision the
measurement ran at, and the header says so rather than implying
otherwise.

## The incident

The rule exists because of
[the report that replaced the report](RULE_EVIDENCE.md#the-report-that-replaced-the-report).
