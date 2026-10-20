# AXI qualification evidence registry

Live qualification owners record one result per operation/backend pair using
`ren-axi-qualification-result/v1`, then import each completed report with:

```sh
python3 -m library.tools.axi_qualification --import-result result.json
```

The report includes the operation id, backend id, passed result, evidence
reference, Resolve version, project conditions, supported media types,
expected and actual state, preservation checks and limits, visual and audio
verification (including an explicit not-applicable reason where appropriate),
and recovery status. `library/tools/axi_qualification.py` records failures
without enabling them, rejects incomplete passing reports, and updates only
that operation/backend row in `library/tools/axi_qualification_records.json`.

The registry stores observations, not qualification decisions. The editing
algebra derives qualification and production enablement from a passing record
with all required evidence. Historical test references remain visible as
incomplete evidence until a result meets this schema. Regenerate the AXI
reference matrix after importing evidence with:

```sh
python3 -m library.tools.operations --emit-resolve-axi-references
```

That command prints generated references as JSON; write the returned file
contents to `.agents/skills/resolve-axi/references/`.
