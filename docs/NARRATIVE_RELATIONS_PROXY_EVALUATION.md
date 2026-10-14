# Narrative relation proxy evaluation

VI-17 currently emits deterministic candidate hypotheses from aligned
`speech_sequence` passages. It does not invoke a VLM. A local VLM refinement
pass is intentionally deferred until a compatible model is already installed
and can be run under the heavy-work lock; this work downloads no model.

The labelled proxy set is
[`tests/fixtures/narrative_relations_proxy.json`](../tests/fixtures/narrative_relations_proxy.json).
It contains 10 short argument-like passages and 11 labelled relation pairs.
Against those labels, the current rules produce 11 candidates: 11 true
positives, 0 false positives and 0 false negatives. Relation precision is
1.00 and recall is 1.00 on this proxy fixture. The unit test computes both
metrics from the fixture labels.

These figures measure agreement with a small, rule-shaped proxy set; they do
not establish performance on real footage. Human-annotated argument footage
does not exist yet, so real-corpus precision and recall remain pending.
