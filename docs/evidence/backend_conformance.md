# The two execution backends that answered differently

INFRA-005 (the punch list, 2026-10-20): "Establish backend conformance
tests. Run identical supported semantic operations against different
execution backends or controlled simulations. Compare resulting timing,
content, and preserved state. Distinguish equivalent operations from
merely similar operations. Make portability a verified property rather
than an assumption."

**The rule.** One request, one answer - on every execution backend - and
that sameness is MEASURED, never assumed. The same recorded request is
run through each backend and compared on CONTENT (stdout, stderr and the
exit code), on PRESERVED STATE (what the operation left behind in the
project it was about) and on TIMING (reported, never gated: the two sides
are allowed to differ in speed, and a speed difference is not a
disagreement). Two operations that are merely SIMILAR across backends are
not equivalent: equivalent means the same answer, the same state and the
same refusal. A backend that could not be run is UNMEASURED - never an
agreement, never a disagreement, and never zero.

## What happened

The native front door (`native/ren-cli`) serves `ren info`, `ren status`
and `ren check` with no Python process started; the Python CLI serves the
same three verbs. PR 3126 (PROD-001) counted SEVEN `native/ren-cli`
parity tests already failing on `main`, named the cause in one line -
"the native `ren check` answers PASS where the Python one refuses a
project with no footage" - and left them: "a native-mirror defect, not a
clean-install one, and untouched here". Nothing compared the two backends
outside the crate's own hand-written test, so a disagreement was only
visible to whoever happened to run `cargo test`.

The recorded request set found ELEVEN, on fourteen requests, in four
kinds:

1. **The refusal carried three fields of six.** The Python's refusal is
   the consequence, the KIND OF BLOCK that caused it, what, why, fix, the
   NEXT ACTION the caller can take, and - when the caller asked for
   machine-readable output - a `REN_RESULT` line carrying the same
   refusal as data. The native printed `ren: refused - <what/why/fix>` and
   nothing else: no kind of block, no next action, no data line. A host
   that reads the data line got nothing from the native door, and the two
   doors disagreed about the thing a host ACTS on.
2. **The check stopped one section early.** The Python's check ends with
   "Checking the Resolve target on the record..." and reports what the
   shadow store holds - the Resolve project, the timelines Ren has read.
   The native never reached it, so the two doors said different things
   about a project whose Resolve timeline had never been read.
3. **The mixed-resolution report spelled the same fact two ways.** The
   Python counts a resolution as a TUPLE and prints its repr, `(320,
   240)`; the native printed `320x240` - in the line a person reads to
   decide whether to re-encode their footage.
4. **A declared `source.footage_root` that is not a directory was
   reported as a missing `raw` folder.** The Python refuses with the
   declaration's own words - "which is not a directory. Refusing rather
   than falling back to `<project>/raw`: a typo that silently becomes
   'this project has no footage' is the refusal this declaration exists to
   prevent" - while the native ignored the declaration and named a folder
   the project does not use.

## The count, with the denominators

| measurement | before | after |
|---|---|---|
| recorded requests the two backends answered differently | 11 / 14 | 0 / 14 |
| the crate's own parity tests (`cargo test -p ren-cli`) | 7 passed, 7 failed (of 14) | 14 passed, 0 failed (of 14) |
| the recorded requests that completed, answered the same way | 3 of 8 | 8 of 8 |
| the recorded requests that ended in a refusal, answered the same way | 0 of 6 | 6 of 6 |
| the fields a refusal carries | 3 of 6 | 6 of 6 |

The timing is reported and never gated: on this Mac the native front door
answered one recorded request in 0.07 s (median) and the Python CLI in
1.5 s. A speed difference is not a disagreement - it is the reason the
native door exists.

## The guard

`library/tools/backend_conformance.py` is the check, and
`scripts/backend_conformance.py` runs it (exit 1 on a disagreement). The
recorded request set is `tests/fixtures/backend_conformance/`: a
descriptor per request and the project files it names, all text - the
footage is synthesized from a recorded lavfi source, so the fixture
carries no binary. Every path a back end reads - the projects root, the
two asset libraries, the per-user config directory, the state directory
the shadow store lives in - is pointed at a scratch directory, so no
request reaches a real project, Resolve, or a render. Both backends run
at the SAME scratch path (the path is in the output, so two directories
would differ in the very thing being compared), and the directory is
emptied before each run, so each backend starts from the same recorded
state. `tests/unit/tools/test_backend_conformance.py` pins the
comparison - an exit code, a prose difference and a state difference are
each a disagreement, and a backend that could not be run is unmeasured -
and runs the recorded set whenever a native binary is built.

The native side got the four fixes, not the expectations: the refusal
shape (`native/ren-cli/src/refusal.rs`, with the taxonomy's six kinds of
block and their next actions), the Resolve-target report
(`native/ren-cli/src/shadow.rs`, reading the shadow store the way
`library/tools/timeline_shadow.py` reads it), the resolution tuple's
spelling, and the declared-footage-root refusal.

## What it does not fix

A malformed brand template: the Python RAISES on a malformed declaration
- an unexpected error, a traceback, exit 1 - while the native refuses,
and the native now exits 1 and says the same thing, but a Python
traceback is not something the native can reproduce; the recorded set
does not cover it. The `REN_OUTPUT` default is a machine fact: the Python
turns the data line on when a chat harness is on PATH, and so does the
native now, but the recorded set pins `REN_OUTPUT` explicitly, because an
unset value makes the answer depend on what is installed. The shadow
store is machine state OUTSIDE the project - the Python opens a store
that is not there by CREATING it, the native only reads - so the
preserved-state comparison covers the project tree, which is what the
operation is about. The recorded set covers the three verbs the native
front door serves; every other verb execs the Python, so there is nothing
to compare.
