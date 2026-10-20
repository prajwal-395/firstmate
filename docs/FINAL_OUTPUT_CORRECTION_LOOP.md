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
| QA-017 | Implemented as a typed `CorrectionRequest` projection in `qa_correction_request`: each failing finding gets a request, including explicit `needs_plan` and `creator_review` states when no safe candidate exists. |
| QA-018 | Implemented for supplied typed candidates: each is compiled against the same base snapshot, candidates outside the measured finding span are rejected, and the unique minimum affected-frame scope is selected. Equal minima are `ambiguous`, never chosen by input order. The planner does not invent operation targets. |
| QA-019 | `ren revise` prepares, routes and submits its `EditRequest` through `resolve_axi_core.submit_edit_request`; AXI applies the preservation invariant. The default QA route now accepts only a landed `ren revise` result as an applied correction. |
| QA-020 | Post-delivery correction now requires an injected affected-section inspection callback and records its evidence and full-export decision before re-export. Missing inspection or export callbacks refuse before mutation. A production Resolve adapter is not wired by the CLI; the live lane must qualify one against a disposable timeline. Existing `verify_render.run_scoped_recheck` and `render_qa.run_scoped_render_qa` provide dirty-region detector paths. |
| QA-021 | Implemented here: all original deterministic detectors are rerun on a fresh export. A newly failing metric is recorded as a regression and prevents a clean repair verdict. |
| QA-022 | `creative_qa.DEFAULT_REPAIR_BUDGET` and `post_delivery_qa._repair_once` already cap the loop at one correction attempt per delivered file. |
| QA-023 | `creative_qa` sends subjective findings to creator review; mechanical repair refuses findings outside its reversible allowlist. Failed or unmeasured attempts remain in the receipt. |
| QA-024 | `pipeline_output/review/creative_qa.json` already records the original findings, correction receipt, verification, questions, and unresolved errors. The re-export and regression outcomes now appear in that receipt. |

The typed planner remains proposal-only: the creator or a qualified planner
must supply typed `EditRequest` candidates and a pinned base snapshot. This
lane does not map prose measurements to operation targets or execute a
selected proposal through a new path. The production CLI does not yet provide
the affected-section inspection callback, so its default route safely leaves
mechanical corrections unresolved instead of editing without QA-020 evidence.

## Evidence still required

The live lane must wire and verify an affected-section inspector on a
disposable, qualified Resolve timeline, including the callback evidence and
decision; verify a selected typed candidate executes through AXI with the
preservation receipt; inspect the affected section before deciding on the full
export; then verify all detector results on any required fresh export, one
injected second-metric regression remaining unresolved, and untouched timeline
state. No captain project, timeline, or media was used for this change.
