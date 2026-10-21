# The representative editorial validation corpus (BASE-003)

The punch list asked for a corpus that evaluates Ren on its ability to
complete meaningful EDITS, rather than on whether it rejects requests it
cannot obey. This directory is that corpus: the briefs, the footage they
were written against, and the answer key the evaluator holds.

The eight-brief set that already ships
(`library/tools/editorial_acceptance/brief_set.json`) is untouched and
keeps its score. Its briefs were written against premises the footage
did not carry, so a run was measured for failing to find what was never
there. The captain ruled that set stays scored as it is; these briefs
are additions, not a rescore.

## The three halves, and why they are apart

| path | what it holds | who reads it |
|---|---|---|
| `corpus/sealed/` | the briefs WITH the answer key, the measured substrates, and the evidence behind every measurement | the evaluator only |
| `corpus/public/` | what a run is handed: the instruction, the project state, the acceptance criteria | a run |
| `../tests/`, `../tests/fixtures/` | development material: the engine's own fixtures and the tests that guard them | the engine's development |

**The sealed key is not in a path the engine reads.** The engine ships
`library/`, `ren/` and `native/`; the sealed half is at the repository
root, outside all three, and
`tests/contracts/test_editorial_corpus.py` fails if any module under
those trees names it. The evaluator's scorer lives outside the engine
too (`tools/score_corpus.py`), and it calls the engine's own acceptance
runner with the brief set built in memory, so the harness is reused
rather than rebuilt and the key never reaches a path the engine opens.

**The public half is derived, not written.** `tools/corpus.py` builds it
from the sealed half with the harness's own `public_view`, which copies
key by key and cannot copy the answer key. A test fails if the
committed public half and the derived one differ.

**Development fixtures stay out of the sealed half.** The sealed
substrates are measured against real footage. The engine's synthetic
fixtures are development material: a brief scored against them measures
the fixture, not Ren.

## How a brief was written

Every brief here was written against footage that CARRIES the event it
asks for, and the premise was settled by reading the footage:

1. find the moment in the transcript, with its timeline times and the
   source file and source times it was spoken in;
2. pull the frame at that source time and LOOK at it;
3. read the timeline's own items for the cut, row by row;
4. look at the delivered file, frame by frame, where there is one.

The measurement behind every premise is written down in
`corpus/sealed/evidence.json`, with the file it was read from, the time
it was read at, and what was seen.

A brief whose correct answer is to report back rests on a premise the
footage does NOT carry - there is no music bed, there is no cutaway, the
footage has no second angle - and the corpus says WHICH kind of report
is right: refuse, ask, or report what could not be determined.

## The corpus today

```
python3 evaluation/tools/corpus.py --shape     # the counts
python3 evaluation/tools/corpus.py --audit     # every brief, its substrate, its premises
python3 evaluation/tools/corpus.py --write-public
```

13 briefs: 8 that ask for an edit, 5 whose correct answer is to report
back (3 refusals, 1 question, 1 report of what could not be determined).
Every one of the 8 rests on a premise the footage carries, measured.
The shape is published in `corpus/public/manifest.json`, and the
category the corpus does NOT cover is named there rather than hidden.

## Adding a brief

1. Measure the footage first. If the premise cannot be settled, the
   brief does not go in - a premise nobody looked for is unknown, not
   carried.
2. Write the request in a user's words, with the project state the run
   will see.
3. Write the key: the operations it implies, the roles it names, the
   moments it needs, and the evidence for each.
4. `python3 evaluation/tools/corpus.py --write-public` to regenerate the
   public half, then run the contract tests. The harness's own validator
   is the gate: a brief that leaks its key, or names an operation the
   engine does not have, is refused when it is added.
