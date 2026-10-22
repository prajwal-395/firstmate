# the-answer-that-did-not-say-what-it-was-measured-at

This entry was moved here, UNCHANGED, on 2026-10-22.  `docs/RULE_EVIDENCE.md`
is an append-only file that every lane writes to at its end, so a lane that
lands its evidence there collides with every other lane that lands at the
same time, and the collision is resolved by hand.  A lane's evidence belongs
in its OWN file in this directory, and the text is the text.

## the-answer-that-did-not-say-what-it-was-measured-at

**The rule.** An answer about footage says WHICH RESOLUTION answered it, and
an answer that stops at the coarse resolution carries the escalation that
raises it: what the next resolution would ESTABLISH, and what it is MEASURED
to cost.  The ladder is one enumeration - `library/tools/fidelity.py` - and
every answer about the picture reads its tiers from there.

**What happened.** Measured 2026-10-21 on the real podcast reel (seven camera
files, 3840x2160 MXF at 23.976 fps, 1092 sampled frames in the built CLIP
index) with the frame lane, `library/tools/analysis/footage_frames.py`.

The lane has two halves that make DIFFERENT claims.  The first ranks the
sampled frames against the query - a RANKING, which says where to look.  The
second puts each candidate shot to the local vision model - a VERDICT, which
says the shot establishes the query.  The second half is not a refinement of
the first, and it cost 81.0 s per candidate on this machine (ten candidates,
810 s of model time, `library/tools/span_verification.py`'s
`MEASURED_SECONDS_PER_INTERVAL_VERDICT`).

The lane ran the second half BEFORE it said anything.  So:

- a caller who wanted to know WHERE TO LOOK paid for a verdict it never
  read - 1023 s where the ranking alone is 31 s;
- the answer said nothing about which half produced it.  The candidates
  carried `verified: false` and the verdict carried `verified: true`, but
  the answer as a whole said neither, so a caller that read the JSON could
  not tell a place to look from a place that was looked at;
- there was no way up.  The escalation - "the candidate shots are put to the
  local vision model" - was a boolean (`--no-temporal`) rather than a
  request, so a caller could not ask for the higher resolution and could
  not find out what it would cost before it bought it;
- and the escalation, run as a second call, RE-RANKED the index: the coarse
  answer's ranking was thrown away and paid for again.

**The fix.**  `library/tools/fidelity.py` owns the ladder - `coarse` (a
ranking: where to look), `detailed` (a measurement: that the candidate
establishes the claim), `original` (the media at the frame the finding
names).  The frame lane's answer carries a `fidelity` block that names the
resolution it was reached at and what that resolution does NOT establish,
and a coarse answer carries an `escalation` block naming the request, what
it would establish, and its measured cost.

`FrameIndex.verify_ranges` computes the detailed tier FROM a coarse answer:
the escalation reuses the coarse answer's candidates and its policy, so it
does not re-rank the index and does not pay for the ranking again.  The
detailed answer's provenance names the coarse answer it came from, and
every window - coarse or verified - points at the ORIGINAL media behind it.

**Measured.** `docs/benchmarks/2026-10-21-progressive-fidelity.md` carries
the run: the before arm (the verified tier, one call) against the after arm
(the coarse tier, then the escalation), on three planning questions over the
real reel, with the arms in separate processes and each with its own cold
verdict store, and the two arms' final answers compared window by window and
verdict by verdict.

Defect-naming tests: `tests/unit/tools/test_fidelity.py`,
`tests/unit/picture/test_progressive_fidelity.py`.
