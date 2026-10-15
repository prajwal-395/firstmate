# Query evidence routing check (2026-10-15)

This check measures whether deterministic query interpretation retains the
evidence lane named by the frozen evaluation family. It does not score the
rank order or interval recall of a retrieval backend.

| Queries | Evidence lane included before | After | Change |
|---|---:|---:|---:|
| EVAL-02 visual, 8 queries | 8/8 | 8/8 | 0 |
| EVAL speech, 19 queries | 19/19 | 19/19 | 0 |

The visual rows are from `library/tools/visual_retrieval_heldout_set.json`;
speech rows are the `speech` family in
`library/tools/retrieval_eval_set.json`. The route score is the fraction whose
anchor set contains video/still/description for visual queries or transcript
for speech queries. Before uses the previous single-label router; after uses
the multi-intent router. The speech set caught two lexical false positives
while developing the deterministic motion/action cues; both are retained in
the general route, which includes transcript.

No retrieval floor or model setting was selected or changed from these
queries. EVAL-02 was read once as held-out evidence and was not used to tune a
setting. The existing visual retrieval backend and its pinned interval scores
are unchanged by this routing-only patch. Focused behavior tests also pin the
combined speech/motion/action route and prove unrelated audio anchors are
excluded from that request.
