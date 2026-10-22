# Module evidence

A module's docstring states its CURRENT contract: what holds, what the
names mean, how to reach it, which tests pin it. How each rule was found -
the dated measurements, the incident, the captain's words, what a ruling
superseded - lives here, one file per module, so the contract is not buried
in the story (punch list 17).

Incident evidence that backs an AGENTS.md rule stays in
[`RULE_EVIDENCE.md`](../RULE_EVIDENCE.md); this directory holds the history
behind a single module's docstring.

Each file was moved verbatim from its module. Where a file and the code
disagree, the code and its docstring win.

A module's history may be split across more than one file when a lane
lands later: one file per lane, so two lanes never conflict on the same
paragraph. `portable_evidence.md` and `portable_evidence_m3b.md` are the
M1 transcript lane and the M3b identity lane of one module.
