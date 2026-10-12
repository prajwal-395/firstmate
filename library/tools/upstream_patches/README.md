# Fork-only engine patches

Place ordered patches required only by Ren in the engine-specific directory
(`filmcraft/` or `effectcraft/`). Each patch must include its rationale and
upstream file/commit anchor. Intake replays these patches against the candidate
source revision, and the runtime install replays the same series in the same
order when it builds the CLI Ren actually runs; both land in a cache keyed by
revision plus the patch-set key (`library/tools/filmcraft_patches.py`), so a
pre-patch binary is never reused. Adapters and Ren integration code belong in
Ren-owned crates, not here. Remove a patch when upstream contains its
equivalent. No upstream PRs are part of this workflow.
