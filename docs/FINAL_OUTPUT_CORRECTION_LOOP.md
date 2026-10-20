# Final-output correction loop status

This lane closes the verification gap where a correction could be treated as
verified against the old export. `post_delivery_qa` now requires an AXI-landed
revision, a fresh export, and a full second pass of the deliverable detectors.
The receipt records the fresh export, target result, all detector results, and
any newly failing metric. A correction that is still awaiting translation, has
no fresh export, fails its target check, or introduces a regression remains
unresolved.

## QA-017 through QA-024

| Item | Existing work and result |
| --- | --- |
| QA-017 | `creative_qa.Finding` already types detector findings, and `EditRequest` types operations. The conversion from arbitrary QA findings to an operation-specific correction request is not implemented. A metric and span alone do not identify a safe timeline operation or target. |
| QA-018 | `EditRequest.compile_request` derives affected spans from an operation and base snapshot. No QA-to-operation planner selects the smallest valid timeline change. |
| QA-019 | `ren revise` prepares, routes and submits its `EditRequest` through `resolve_axi_core.submit_edit_request`; AXI applies the preservation invariant. The default QA route now accepts only a landed `ren revise` result as an applied correction. |
| QA-020 | `verify_render.run_scoped_recheck` and `render_qa.run_scoped_render_qa` already recheck dirty regions. Post-delivery QA currently re-exports the full deliverable after a landed correction; choosing a partial render requires a reliable mapping from the finding to render dependencies, which the current correction planner does not provide. |
| QA-021 | Implemented here: all original deterministic detectors are rerun on a fresh export. A newly failing metric is recorded as a regression and prevents a clean repair verdict. |
| QA-022 | `creative_qa.DEFAULT_REPAIR_BUDGET` and `post_delivery_qa._repair_once` already cap the loop at one correction attempt per delivered file. |
| QA-023 | `creative_qa` sends subjective findings to creator review; mechanical repair refuses findings outside its reversible allowlist. Failed or unmeasured attempts remain in the receipt. |
| QA-024 | `pipeline_output/review/creative_qa.json` already records the original findings, correction receipt, verification, questions, and unresolved errors. The re-export and regression outcomes now appear in that receipt. |

QA-017, QA-018, and the partial-render portion of QA-020 remain open. They need
a qualified planner that maps a measured defect to a stable target and a
production-enabled AXI operation, plus evidence that a section render covers
that operation's complete visual and audio dependencies. Guessing those links
would undermine the preservation and acceptance contracts already in place.

## Evidence still required

The live lane must verify a mechanical correction on a disposable, qualified
Resolve timeline: AXI commit and preservation receipt; fresh delivery export;
all detector results on the fresh export; one injected second-metric regression
remaining unresolved; and proof that untouched timeline state is unchanged.
No captain project, timeline, or media was used for this change.
