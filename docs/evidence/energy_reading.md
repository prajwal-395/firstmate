# One reading of target_energy

Tests: `tests/unit/picture/test_pacing.py`.

Project 001's creative direction chose `target_energy: "building"`
deliberately - its own rationale said cutting the piece as high-energy
would "fight the source". Two readers then bucketed it differently and
only one of them acted:

* `transition_selector` did not match it, so scene-change cuts stayed
  hard cuts;
* `step_5_03_creative_cohesion.map_energy` substring-matched it to
  "high", demanded transitions under 500 ms and >=10 SFX per minute, and
  cut three `defocus` transitions from 500 ms to 333 ms.

Those four cohesion thresholds are now GONE - not re-bucketed.  "a high
energy edit holds every transition under 500 ms" and "carries at least 10
SFX per minute" are creative values the step chose (AGENTS.md 10.5), so
`map_energy` and the checks it fed are deleted and 5.03 reports the
counts instead of judging them.  What remains here is the vocabulary
itself, which still reaches the picture through `transition_selector`.

The reconciliation is deliberate and recorded in
`library/tools/energy_reading.py`: "building" names a TRAJECTORY, not a
level, and reading a trajectory as its endpoint discards the very
distinction the direction was drawing. The transition selector's
vocabulary is adopted whole, because it is the reading whose result
reaches the picture and the one that was right on the only project
anyone has measured.

The test module also carried an unused reader registry (`KNOWN_READERS`, `PHRASE_VERDICTS`, `EVERY_READER`) left from the removed every-reader-agrees test; it was deleted in the 2026-10 suite halving.
