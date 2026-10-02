# `library.tools.treatment_verify` - the PowerCrop diagnosis

Moved from the module docstring of `tests/unit/reels/test_treatment_verify.py` (2026-10-02).

Proven on PowerCrop (the tv_power Crop): the captain found by eye that the
powercrop nodes mis-frame the a-roll and that removing them makes things look
right. The pipeline never looked - `apply_fusion_comps` and `comp_builder`
contained no visual verification of any kind.

`library/tools/treatment_verify.py` is the always-on deterministic half: it
builds the comp with and without the treatment, evaluates the treatment's own
splines over the frames the timeline really renders, and reports the
before/after difference. The measurable part gates (AGENTS.md 10.4); the
model's reading is recorded, never enforced.

Frame-measured diagnosis (first in /tmp/powercrop_sim, since reproduced in the
tests without Resolve - the comp builder is Resolve-free by design):

- the switch-ON head opens fully and holds neutral: EXONERATED as drawn.
- the switch-OFF tail on a 24000/1001 reel timeline cut from 30 fps pool
  footage keyed its animation at source frames 72..90 while the timeline
  played 0..71: every spline flat-neutral over everything rendered, 0 of 72
  frames changed. The reel never turned off and nothing said so. The
  `played_frames` clamp in `build_effect_comp` is the fix
  (`test_reel_tail_draws_with_a_played_horizon`); a test that characterized
  the unclamped output was removed on 2026-10-02.
- a head on a clip shorter than its own animation never reaches neutral: the
  whole clip plays collapsed.
- `BezierSpline.sampled()`'s docstring once said end_frame+N while the code
  keys the frame; it caused one wrong diagnosis (read as a type confusion at
  effects.py:809). `test_sampled_hold_after_is_a_frame_not_an_offset` pins the
  implementation.

## Finding 5: 4.03's gate could not verify anything 4.03 plans

Moved from the module docstring of `tests/unit/reels/test_treatment_verify.py` (2026-10-02).

On the scout's B1 run (FR3.3) every plan_vfx answer - even an empty plan -
failed with "declared gating skill(s) verify_treatment never ran", and the
skill itself only knew tv_power_head/tv_power_tail, so a planned slow_zoom_in
failed it with "UnknownTreatment: treatment 'slow_zoom_in'".

A gate that fails correct output is no coverage (AGENTS.md 10.4). The gate
must verify what 4.03 actually plans (drift via verify_drift, switch animation
via verify_treatment) and scope itself to the treatments it knows: an empty
plan, or one naming only effects with no deterministic verifier (stabilize,
speed_ramp, screen_shake), needs no receipt.
