# Fork-only engine patches

Place ordered patches required only by Ren in the engine-specific directory
(`filmcraft/` or `effectcraft/`). Each patch must include its rationale and
upstream file/commit anchor. Intake replays these patches against the candidate
source revision; adapters and Ren integration code belong in Ren-owned crates,
not here. Remove a patch when upstream contains its equivalent. No upstream PRs
are part of this workflow.
