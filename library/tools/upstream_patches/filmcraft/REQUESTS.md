# FilmCraft upstream requests (tracked notes, not patches)

Engine-side asks too big, too behavioral, or too upstream-owned for a fork
patch. Each entry records the measured pin behavior, the ask, and who should
carry it. Intake replays only `*.patch` in this directory, so this file is
inert to patch replay. No upstream PRs are part of this workflow (see
`docs/UPSTREAM_ENGINE_INTAKE.md`); the pin-move lane re-probes open asks at
each new HEAD.

## 1. Variable speed as a first-class retime (open)

Raised by audit G6 (native headless gap audit, 2026-10-10). Measured at the
pinned revision `7bd7621` (CLI 0.5.0) on 2026-10-10.

### What the pin does (measured, not assumed)

- `clip.speedDuration` params: `{"clips":[id]?,"speed":percent=100,
  "reverse":bool,"ripple":bool,"interpolation":"frameSampling|frameBlending|
  opticalFlow"?}` (from `commands --json`).
- There is NO per-piece / multi-speed parameter: one call sets one speed.
  A stepped ramp still needs razor points plus one constant retime per
  piece - Ren's `expand_retime_ramp` (`native/ren-filmcraft/src/adapter.rs`).
- Supporting link mechanics measured while proving the workaround:
  `timeline.razor` severs the A/V link on later pieces (distinct video/audio
  link ids; first half keeps the shared id); a retime over severed ids
  ripples followers once PER ID instead of once; `clip.link` is a toggle
  (mixed links clear to none with `{"linked":false}`, unlinked clips gain a
  shared id with `{"linked":true}`, an intact pair unlinks); `ripple:false`
  sets speed flags without changing any span, so it cannot extend audio.
  The workaround re-links each severed piece (clear, then link) ahead of its
  retime.
- Reverse is WIRED, not asked: Ren plans the engine's `reverse` flag for
  whole clips and ramp pieces, with a frame-exact equivalence proof plus a
  render-level direction proof (reversed-frame PSNR beats frame-for-frame by
  10+ dB). Whole-ramp reversal (reversed piece order) keeps refusing with
  `reverse_not_supported` - the expansion plays pieces in timeline order.

### The ask

A first-class stepped / curved speed parameter on `clip.speedDuration`
(per-segment speeds plus per-segment interpolation in one call), or
documented confirmation that razor-plus-per-piece is the intended permanent
route. Until answered, Ren keeps the expansion workaround and its
equivalence proofs (`ren_retime_ramp_expands_to_razors_plus_constant_retimes`,
`ren_retime_ramp_with_reversed_piece_matches_model_frame_exact`,
`ren_reverse_plays_backwards_frame_exact`).

### Handoff

- Pin-move lane: re-probe at the new HEAD whether any multi-speed parameter
  appeared; update or close this entry with the outcome.
- No code in Ren depends on this file; it is a tracking note only.
