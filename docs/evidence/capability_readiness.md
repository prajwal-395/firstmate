# Capability readiness: what the machine can actually deliver

Tests: `tests/unit/tools/test_capability_readiness.py`,
`tests/contracts/test_capability_discovery.py`.

## The gap the join closed

`ren doctor` answered the DEPENDENCY axis - the needs a capability refuses
without (`library/tools/machine_needs.py`) - and nothing else. Three facts
about the machine sat outside that answer, and each of them can refuse work
the needs say is fine:

* the edit operation a capability's executor dispatches may carry no
  live-qualified backend, so ordinary production routing refuses it
  (`library/tools/axi_qualification_records.json`);
* the qualification it was earned on may name a Resolve build this machine
  does not run, so the recorded predicates can never be satisfied here;
* this machine may not hold the memory the scheduler reserves for the
  capability's phase, and the scheduler would refuse the job.

`ren capabilities` advertised all three as supported: a caller that read the
catalog and asked for the work met the refusal at the write, not at the
answer.

`library/tools/capability_readiness.py` joins the three axes, each read from
the registry that owns it. The join adds what the machine can DELIVER; it does
not change what the needs say, and the dependency verdicts are identical
before and after it.

## One reading per fact

| fact | read from | never re-implemented as |
|---|---|---|
| a capability's edit operations | the operation registry's own EditPatch composition | a second roster of capabilities |
| the Resolve build's fit | `execution_router.qualification_mismatches` - the router's own predicate check | a second comparison of versions |
| a RAM reservation's fit | `resource_scheduler.admission_verdict` - the admission rule, over LIVE available memory and the swap | the machine's TOTAL, which is a different reading |
| the machine's Resolve build and memory | the check that measured them (`Check.measured`) | a second probe per surface |

The TIMELINE half of a qualification - the media type, the density, the
frame rate - is LISTED and never judged. It is a property of the timeline in
front of you, and `resolve-axi support <operation> --conditions ...` answers
it against a measured timeline.

An unmeasured fact stays unmeasured: a Resolve build nobody read, a memory
reading that failed, a check that timed out. `unknown` is neither a pass nor
a refusal, and a capability in that state is not advertised as supported.

## Measured, 58 capabilities on each machine

**This machine** (24 GiB, Resolve answering on 21.1.0.14 - the build the
qualifications were earned on):

| | before | after |
|---|---|---|
| dependency verdicts | 53 available, 5 unavailable | 53 available, 5 unavailable |
| the catalog's supported list | 53 | 51 |
| the doctor's readiness | - | 50 available, 1 degraded, 7 unavailable |

* `reel.touchup` is DEGRADED, and the report says why: it declares 24 edits
  and has a production-enabled backend for 6 of them; the other 18 are named
  as unavailable features.
* `semantics.analyse` and `objects.segment` are UNAVAILABLE for a MEASURED
  reason - the scheduler's own refusal, on a machine paging at 86% of its
  swap device with 10.6 GB of 24 GB free. The TOTAL says 21.6 GB of
  headroom; the admission rule says the job would be refused, and the report
  now says the same thing the scheduler would.
* Before, the catalog advertised both model capabilities as supported.

**A simulated machine** (no Resolve scripting module, no optional search
embedder):

| | before | after |
|---|---|---|
| dependency verdicts | 43 available, 2 degraded, 13 unavailable | 43 available, 2 degraded, 13 unavailable |
| the catalog's supported list | 43 | 41 |
| the doctor's readiness | - | 41 available, 2 degraded, 15 unavailable |

The two degraded capabilities are the search ones the missing embedder
degrades: the report names the optional need separately from the required
ones, because they cost different things.

**A machine whose Resolve runs a build the records do not name** reads the
executor-backed capabilities as unavailable, with the router's own refusal as
the reason:

```
the executor cannot deliver here: clip.insert - the recorded qualification
names a Resolve build this machine does not run: resolve_version is
'21.1.0.99', not '21.1.0.14'
```

## The advertisement and the work agree

`ren capabilities` publishes the readiness per row, guides a capability this
machine cannot deliver, and drops it from the supported list. `ren doctor
--for <id>` exits 1 for a capability this machine cannot deliver, even when
every need it names is satisfied - the executor is what does the work, and
an executor that cannot run here is the answer a caller needs.
