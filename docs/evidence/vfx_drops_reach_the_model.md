# Finding 34: a dropped VFX plan entry goes back to the model

Tests: `tests/unit/picture/test_vfx_drops_reach_the_model.py` (moved from its module docstring, 2026-10-02).

Finding 34: a dropped plan entry never goes back to the model.

On the scout's B8 first attempt both VFX entries (a `cut_in` with a
single `zoom` param, a `speed_ramp` with `speed` instead of `percent`)
were dropped with precise reasons (`no_readable_parameters`,
`not_a_speed_step`), 4.03 recorded `basis: every_entry_dropped` - and
the step stayed green. The contract-rejection retry path
(`post_bridge_retry`) carries a violation back to the model; a drop
does not, so a one-word slip silently removes the whole request.

The fix: on the first post-bridge pass, drops raise through the
existing retry path so the model can correct the slip. A later pass
ships whatever still resolves, with the remaining drops recorded -
never silently, never by inventing values.
