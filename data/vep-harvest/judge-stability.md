# Is the blind judging stable? Two independent readers, byte-identical request

Reader 1 is the run of record (answered llm_requests/judge_reels.json).
Reader 2 is a control: a separate agent, never saw reader 1's answer, the
selector's argument, this repository, the project folder or any report.
Request md5 a7a5e399d45bbc02f4c0ab0d139f8cee, identical for both.

|                                  | reader 1 | reader 2 |
|----------------------------------|----------|----------|
| readings returned                | 31       | 31       |
| readings REFUSED (quote not found)| 0       | 0        |
| coherence not_followable         | 31       | 31       |
| coherence followable             | 0        | 0        |
| value delivers                   | 30       | 30       |
| value delivers_nothing           | 1        | 1        |
| which reel delivers nothing      | 22       | 22       |
| rank 1                           | reel 1   | reel 1   |

STABLE: every derived verdict reproduces, including WHICH reel is the one
that hands a listener nothing. Two readers who never met picked reel 22
independently.

NOT STABLE: the ordering below the top. Only 4 of 10 reels appear in both
top tens; mean |rank delta| 6.1, max 23. That is what
reel_quality_bar/passage_engagement already say - "compare ranks only near
the top" - measured rather than asserted. Rank 1 agreed.

CONCLUSION: 31/31 not_followable is NOT a reader artefact. It reproduces.
So the coherence OBSERVATIONS are about the material. What it does not do
is DISCRIMINATE: a column that is constant across a batch carries no
information about that batch, exactly as FIRST_MEASUREMENT recorded for
`value` on the previous batch (25 delivers, 0 otherwise).
