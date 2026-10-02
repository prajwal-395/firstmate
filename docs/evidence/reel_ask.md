# `reel_ask` - test history

Narrative moved verbatim out of test module docstrings; the tests keep the invariant.

## `tests/unit/reels/test_gate_stills.py` module docstring (moved 2026-10-02)

```text
`reel.ask` writes what a pass-1 build writes, without the build.

The captain's standing complaint (2026-09-18): every reel is built
TWICE - pass 1 runs headless and its only surviving output is three
`llm_requests/*.json` asks, pass 2 runs with the answers. `reel.ask`
is the way to write those three asks without running a build to
produce them.

What this file proves, and how
------------------------------
`write_visual_asks` (`library/tools/reel_build.py`) is the ONE
spelling both callers use: the pass-1 loop calls it, and `reel.ask`
calls it. This file pins that the extraction changed nothing by
replaying the loop's PRE-EXTRACTION inline sequence - the exact lines
the loop ran before - beside `write_visual_asks` on identical inputs
and comparing the files:

- `reel_motion_NN.json` carries no timestamp, so it must match RAW,
  byte for byte.
- `reel_semantic_NN.json` and `reel_span_NN.json` carry a cosmetic
  `timestamp` re-stamp (`test_rebuild_determinism.py` names it), so
  they must match with ONLY that field normalised - and the test
  asserts the stamps DID differ, without which the normalisation
  would prove nothing.

The instrument is the file bytes under `pipeline_output/llm_requests/`,
the same distinction the finding demanded: measured from what the
writers wrote, never from spans between files on disk.

What this file does NOT claim
-----------------------------
The reel below is SYNTHETIC - a Reel 09-shaped moment, transcript
and master clip stand-ins, derived through the same functions a real
reel goes through - although a live captain's project exists on this
machine. A disposable lane must not verify against it: that would mean
running a throwaway pass-1 build holding the exclusive Resolve lease
on the captain's own open project, which is exactly the cost this
operation exists to remove. Production equivalence is structural (one
code path), and this file pins that the shared path performs the
pass-1 sequence exactly. A subtly different ask gets a subtly
different creative answer, so any future deviation here is the
finding, not a detail.
```
