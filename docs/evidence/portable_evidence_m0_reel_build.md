# The two producers whose records were not evidence yet

Code: `library/tools/portable_evidence.py`,
`library/tools/reel_project_model.py`,
`library/tools/evidence_contract.py`.
Tests: `tests/unit/context/test_portable_evidence.py`,
`tests/unit/context/test_reel_project_model.py`.
Model: `docs/PORTABLE_EVIDENCE_MODEL.md`.
Report: `docs/benchmarks/2026-10-22-portable-evidence.json`.

**The rule.** A portable evidence record names the SOURCE it was read
from, the epistemic CLASS of the claim and the COVERAGE behind it, and
it is LOSSY when it describes the media rather than measuring it. The
source's length is counted UP on the clock's own rate, and a span that
ends ONE FRAME past it is the source's length, rounded by the duration
it was measured from - the reel build's writer asks the model the same
question, so a span one of them accepts is a span the other wrote. A
record whose span was re-based onto a finer clock is recovered from the
row's OWN integers, and where more than one span fits inside the
source, the source's own transcript says which. One enumeration:
`library/tools/portable_evidence.py`.

## What happened

PR 3163 measured the portable model on Reel 26 of the podcast project
`lucie/geo-podcast` and left two producers named and unmigrated: `m0`
and the reel build. Both turned out to be defects of their own.

**The reel build re-based its own rows, once per build.** Its rows
carry no evidence contract at all - nothing says what they are - and
their spans reach 1e43 ticks on a source 4,099.6 s long. The cause is
in the store's own history, not a guess: the event log's first commit
carries the rows at their measured spans, and every later `reel.build`
commit multiplies them again.

```
event 1    replace  reel.build   start 593420        end 907820
event 63   replace  reel.build   start 71210400      end 108938400
event 64   replace  reel.build   start 8545248000    end 13072608000
...
event 84   replace  reel.build   start 1.58e43       end 2.42e43
```

Every step is the grid ratio: the evidence base counts on the
millisecond, the reel build's clock counts 120 times finer, and the
merge re-based the model's rows onto it. The reel build wrote its rows
ONCE and never wrote them again, so a row left on an older grid stayed
in the model forever: 431 rows on Reel 26, multiplied 18 times, and no
reference to them could recover a frame that was there.

**The M0 row is the source's own length, measured on another grid.** The
M0 lane measures the source's duration to six decimals and covers the
span it measured, so its row ends on the next whole millisecond; the
reel build's clock upgrade multiplies that by 120. A reader that
measured the source's length with a floor instead of the lane's own
cover refused the row: the row said 4099.596 s where the reader's own
length said 4099.5955 s, and half a millisecond of nothing was enough
to refuse a record that IS the source.

## The fix

**The source's length is read ONE way.** The portable model counts the
source's length UP on the clock's own rate, and the reel build's own
writer asks the model the same question: a span one of them accepts is
a span the other wrote.

**The reel build's rows are recovered from the row's own integers.** The
recorded span is the measured span times a whole number of grid
ratios, so the recovery is arithmetic, not approximation: the span it
recovers IS the span the reel build measured. Which multiple is undone
is decided by the source. A span that does not fit inside its source is
not a span of that source, so the candidates are the multiples whose
span does fit; where more than one of them fits (19 rows of Reel 26),
the source's own words say which - a passage is anchored by SEARCH
(AGENTS.md section 6), never by the time a hint claims. Where the
arithmetic leaves one candidate (412 rows), the row's own measurement
has determined the span, and the words are a report on it.

**The reel build's writer carries a contract, replaces the row it
owns, and refuses a span that does not fit the source.** The contract is
the registry's `speech.utterance` - the same type the M1 transcript
lane files its utterances under, so the two spell one thing once. The
reel build is the producer of these rows, so it replaces them: a row
left behind by an earlier build cannot survive as the measurement. And
a span past the source's own length is a span of frames that are not
there, which is the last place the writer can say so.

## Measured

One real reel: **Reel 26 - write-for-the-question-your-customer-ask**,
of the podcast project `lucie/geo-podcast`, read-only, on the machine's
own source memory.

```
python3 -m library.tools.portable_evidence census \
    --model <project>/project_model/snapshot.json --reel reel-26
```

The whole reel's scope, before and after:

| Measure | Before (reel) | Before (m0 + reel build) | After (m0 + reel build) |
|---|---:|---:|---:|
| records | 95,653 | 434 | **434** |
| distinct record shapes | 18 | 2 | **1** |
| carry a source reference | 18,064 (18.9%) | 0 (0.0%) | **434 (100%)** |
| carry a provenance class | 94,282 (98.6%) | 3 (0.7%) | **434 (100%)** |
| carry a coverage | 94,282 (98.6%) | 3 (0.7%) | **434 (100%)** |
| carry all three | 18,064 (18.9%) | 0 (0.0%) | **434 (100%)** |
| refused (named) | - | 434 | 0 |

The two producers, on their own:

| Producer | Rows | Before: all three | After: all three | Distinct shapes |
|---|---:|---:|---:|---:|
| `m0` | 3 | 0 (0.0%) | **3 (100%)** | 2 -> 1 |
| `reel-build` | 431 | 0 (0.0%) | **431 (100%)** | 1 -> 1 |

The whole migrated set - M1, M3d, M6, M0 and the reel build - is
93,940 records in ONE shape, and 93,940 of 93,940 (100%) carry a
source reference, a class and a coverage. The 940 refusals that remain
carry no evidence contract at all: the M1 `cross_modal_disagreement`
rows (16) and the M3d track summaries (924), which are a collection
of their own and not yet lifted.

### The recovery is exact, not an approximation

The store's event log holds the reel build's rows as they were first
MEASURED, in its first commit. Every recovered span was compared with
that original measurement:

| Measure | Value |
|---|---:|
| rows recovered | 431 of 431 |
| rows the store's first commit also carries | 304 |
| recovered spans IDENTICAL to that first measurement | **304 of 304 (100%)** |
| rows whose span the arithmetic alone determined | 412 |
| rows where the source's own transcript chose between two spans | 19 |
| rows whose words aligned completely | 340 of 431 |

The 91 rows whose words did not align completely are the timeline
transcript's wording and the source transcript's wording differing
("chatgpt" against "chat gpt", a filler the one pass heard and the
other did not). The span is the row's own measurement either way.

## Left

- The Project Model's events, relations, tracks, masks, signals and
  identity links are still censused, not lifted: a relationship and an
  event need their own kind and their own entity references, and the
  Project Model's schema has no `evidence` field on them yet.
- The other producers of this reel's records - `m3b`, `m3d` and `m6` -
  are counted and named, not yet migrated.
- The reel build's 431 rows are recovered in the model that is stored
  today. The next reel build writes them again, correctly, with the
  contract they now carry.
