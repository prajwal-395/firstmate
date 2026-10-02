# Reels build witnesses - `report_layer_coherence` and the informational sweep

Moved from the module docstring of `tests/unit/reels/test_reels_build_witnesses.py` (2026-10-02).

Two wirings, both print-only and neither a gate:

1. `report_layer_coherence` runs `layer_coherence` on the reels build - one
   import, one call, beside the prebuild census and the divergence survey. It
   used to run only on `edit_video`, while the timelines it never consulted
   were the ones the captain reviews.

2. `sweep_all_reels_informational` grades EVERY reel timeline beside the scoped
   refusing gate. The gate stays scoped on purpose (a whole-project gate failed
   clean single-reel builds on timelines they never touched - PR #658); the
   sweep restores the detection ("did this round disturb a reel I did not
   touch") without restoring the false refusals. A sweep that raised on exit 1
   would be the whole-project refusal PR #658 removed, wearing a new name.
