# Product experience plan slices: live check

Checked 2026-10-08 against the nine slices in the captain's product-experience plan. Every live Resolve action was submitted to the shared broker as a `resolve.qualification_session` or `qualification.workflow` job. Each qualification session borrowed the exact `Ren Qualification` project and read back the captain's original project, timeline, and page on exit.

| Slice | Resolve surface | Result | Evidence |
|---|---|---|---|
| 1. Assignment interaction contract | None in the contract itself | Offline only, pass | Assignment interaction and artifact contract tests passed. |
| 2. Assignment lifecycle and autonomy | EditPatch apply and revision readback | Exercised live, pass for the edit seam | Round 1 applied the timeline build and Ren revision through EditPatch. The assignment lifecycle itself reached `delivered` in the qualification project. |
| 3. Proposal, comparison, and revision | Apply/revision and preservation | Exercised live, pass | Round 1 applied a later Ren revision and read back both video/audio placements plus the scripted human marker. |
| 4. Viewer-level creative QA | Final export inspection | Exercised live, pass | Round 1 `verify_render` passed all eight checks: loudness, black frames, freeze frames, silence under picture, resolution, frame rate, duration, and audio stream. |
| 5. Mixed human/agent reconciliation | Timeline shadow, move/trim, safe patch, conflict question | Broke and fixed locally; exact live acceptance remains open | The broker captured an observed generation on a disposable timeline. `reconcile_generations` returned a safe transform patch for the unchanged third clip and the expected creator question plus uncommitted `Ren proposal` when aimed at a changed clip. The live move/trim simulation exposed that a replacement can be returned without a correct timeline readback; the original could then be deleted. `resolve_axi._replace_item` now refuses that replacement before deleting the source, with a regression test. The requested live trim did not read back, so the complete move/trim acceptance remains open until Firstmate reloads the broker after merge and retests this guard. |
| 6. Persistent intent precedence | None | Offline only, pass | Intent model and scoped-intent tests passed. |
| 7. Reference collection | None | Offline only, pass | Reference collection tests passed. |
| 8. Evidence-informed briefing | None | Offline only, pass | Brief tests passed. |
| 9. First value, onboarding, and delivery receipt | Resolve export, render QA, assignment receipt | Exercised live, pass for round 1 | The broker rendered the 16-second qualification timeline. The final 1920x1080, 24 fps master passed render QA. In a second `resolve.qualification_session` job, an assignment script created a brief, advanced it through draft/review/approved, linked the broker timeline generation and render job, published a first-value footage map and complete delivery receipt, and transitioned it to `delivered`. Session job `b4a8a3d6fdeea3c8` read back `state=delivered`, `receipt_status=complete`, and `qc_passed=true`. The required two-round command then failed during round 2 analysis before Resolve, when the M2 frame lane could not find the shared frame directory for `clip_002`. |

## Validation

- Initial offline slice proof: 166 passed.
- Earlier changed-file selection: 508 passed before the main-branch merge.
- Post-fix dependent-test selection: 575 passed with `pytest -n 2`, through the firstmate `vep-verify-run.sh` lock helper. This selection included `tests/unit/resolve/test_resolve_axi.py` and its dependent Resolve, audio, caption, reel, and contract tests.
- `ruff check --config ruff-ci-gate.toml library/tools/resolve_axi.py tests/unit/resolve/test_resolve_axi.py` passed.
- `git diff --check` passed.
- The qualification round 1 broker receipts recorded successful edit construction, scripted human marker, Ren revision preservation, Resolve render, export, and final render QA. Its source copy SHA-256 was `9b5ec750c8dbddfd5a74c4e24d5d374f5f9880c1d3c787c6cf306770a01c30bc`.
- The round 2 analysis record identifies one failed source-memory frame lane row: `clip_002` could not find its shared `frames` directory. The workflow therefore reports failure despite round 1 completing. This analysis issue is separate from the Resolve render-call fix merged on main and from the mixed-edit replacement guard in this PR.
- The mixed-edit shadow comparison used timeline `Q Mixed Fresh 20261008T2006`, timeline id `f66fc18c-c20e-43d9-b39d-b2a4836ba7ad`, and generations 1 (approved) and 2 (observed). It attributed external generation 2, based the safe third-clip transform patch on generation 2, and returned the exact same-clip conflict question without applying the variant. The live trim failure means this is partial acceptance evidence, not a pass for the full recipe.
- The four `Q Mixed *` scratch timelines created for this acceptance attempt were deleted by exact name through a borrowed broker session and read back absent. The qualification workflow timeline used for export and delivery remained listed.
- Qualification sessions repeatedly restored and read back `Podcast (field test)`, timeline `Reel 01 - geo-is-comprehension-not-position`, and the `Edit` page.

The branch changes `library/tools/resolve_axi.py`, its focused regression test, and this report. The Resolve replacement guard is broker-served code; Firstmate must reload the broker after merge before the mixed-edit recipe can be retested against the fix.
