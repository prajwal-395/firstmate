# The answer that did not say which resolution it was reached at

Code: `library/tools/coarse_report.py`, `library/tools/evidence_fidelity.py`,
`library/tools/analysis/footage_frames.py`.
Tests: `tests/unit/picture/test_coarse_report.py`,
`tests/unit/picture/test_evidence_fidelity.py`.
Measurement: `docs/benchmarks/2026-10-22-progressive-fidelity-second-call.md`.

**The rule.** An answer says the RESOLUTION it was reached at, and the
way up to the next one. A coarse answer is KEPT, so the escalation
computes the detailed tier from the ranking it already has instead of
ranking the index again. One ladder: `library/tools/fidelity.py`.

**What happened.** The punchlist of 2026-10-20 asked for progressive
fidelity (INFRA-002): "permit multiple resolutions of the same source
evidence - coarse indexes, detailed observations, and original media",
"let a model request higher-fidelity information only when needed",
"preserve provenance as information moves between representations".
PR 3166 built the ladder and reached two lanes with it - the frame lane
and the retrieval service - and left two gaps, both named in its own
report:

- **The typed Project Model path answered without declaring its
  resolution.** `ren evidence` answered "the model holds this record,
  over this coverage" and "the model holds nothing" in the same voice.
  A scene observation and a relation read the same in the answer, and
  they are not the same kind of claim: one says what the source shows,
  the other says what the model concluded from what it saw.

- **The coarse answer was not persisted, so a second-call escalation
  re-paid the ranking.** The frame lane's coarse answer was printed
  and gone. A caller who asked for the cheap answer first and the
  verified answer second paid for the ranking TWICE, and the cost of
  the question was decided by how the caller split it into commands.

**The fix.** The typed path declares its resolution: a claim about a
span of the source is at the `detailed` tier and carries the way up to
the media behind it; a claim about the model's own structure - a
relation, an identity link, a trace of what the model believes - is at
the `coarse` tier. The tier follows the CLAIM, and not the record's
epistemic class: a transcribed word and a relation are both
inferences, and neither axis is the ladder's.

The coarse answer is now kept beside the index it was made from, in
the project's own scratch area, and it is found again by its IDENTITY:
the query, the policy the search ran under, and the fingerprint of the
index it ranked. A stored answer the index has moved on from is
REFUSED, not reused - the ranking was made over frames that are no
longer the ones on disk.

**What the fix cost.** `ren search` is a read verb, and keeping the
coarse answer writes into the project's scratch. The write is derived
data, beside the index, safe to delete at any moment; deleting it
costs one ranking and never an answer that cannot be had again. A
caller that does not want it says so: `--no-coarse-store`.

**What was left undone.** The `original` tier of the ladder is
reachable rather than answered at: a detailed answer names the
recording and the span, and no lane yet ANSWERS a question at that
tier. The typed path's `original` escalation costs nothing because it
is a pointer, not a measurement - the pixels behind the finding are
still one `ffmpeg` away from any reader who wants them.
