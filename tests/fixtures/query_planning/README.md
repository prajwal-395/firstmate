"""R03 measurement corpus: one row per query, hand-recorded expectations.

Each row names the query and the deterministic filters it states. A
filter slot's expectation is the tokens the extracted value must
contain (lists), an exact number or range (ordinal, time_range), or a
track/role assertion (person). An ABSENT expectation means the slot
must stay empty: over-extraction fails the row exactly as a miss does.

Sources: the 32 M0 questions from
docs/benchmarks/2026-10-22-targeted-reasoning.json (5 shapes asked of
each source's own records, with per-source times), the 16 recorded
editorial queries in library/tools/editorial_retrieval_set.json, and
the R03 acceptance query.
"""
