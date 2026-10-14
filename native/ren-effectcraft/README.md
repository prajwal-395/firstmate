# ren-effectcraft

Ren-owned, headless adapter for EffectCraft. It accepts versioned `Request` values and returns a
versioned `Receipt`; `.ecproj` files are temporary engine projections. It has no dependency on any
EffectCraft UI crate and never opens the desktop application.

`PINNED_EFFECTCRAFT_REV` is the qualified immutable upstream revision until the upstream intake
lane lands its pin manifest. `Driver::resolve` honors `REN_EFFECTCRAFT_CLI`, otherwise it clones that
revision into `~/.cache/ren/effectcraft/<sha>/source` and builds only `effectcraft-cli`. The CLI must
report version 0.9.2 and its Git checkout must match the pinned SHA. A standalone binary override
also requires `REN_EFFECTCRAFT_REV` to declare that exact SHA. Effect support and parameter identifiers/types are read at startup from the
headless CLI's `list_effects` registry; unknown effect ids, parameters and JSON value kinds refuse.

Requests map to the pinned CLI's command vocabulary for comp creation, footage import/layering,
text/shape/footage layers, keyframes, masks, track mattes (`layer.setTrackMatte`, with image
sequences conformed to the comp clock through `file.interpretFootage`), effects and rendering.
PNG sequence renders require a `#` frame-number pattern and are checked for dimensions and alpha.
ProRes 4444 renders use the RGBA channel request and are checked with `ffprobe` for an alpha pixel
format before a receipt is returned. Fixture coverage runs the real pinned CLI against a short
animated alpha title, a committed checkerboard image, a per-frame track-matte sequence, and the
Project Model E2E fixture's person mask against its own A-roll.

GPU execution: each render carries a `gpu` preference (`cpu` by default, `auto` opt-in).
`auto` passes `--gpu` to the pinned CLI and falls back to the CPU with the reason recorded when
no GPU adapter is usable; `gpu` requires the GPU and refuses rather than rendering silently on
the CPU. Every render receipt records the compositor the engine itself reports (`render.backend`'s
`active`: `backend`, plus `adapter` and `backend_note`) - the request flag alone proves nothing,
because a headless session without `--gpu` renders on the CPU even when the project reads Mercury
GPU Acceleration. The CPU is the default on speed, not fidelity: on 1080x1920x48 of coast-broll
footage under a grade (`ec.color.ociocdl`) plus the heaviest mapped family, `blur.gaussian`, the
GPU agrees within 1 LSB (max channel delta 1 across all 48 frames, SSIM Y 1.000000, PSNR 102.7 dB
average with worst frame 100.5 dB) but runs 4.1x slower (1.1 s CPU vs 4.5 s auto wall on Apple M5
Metal, upload/readback dominating); see `tests/headless.rs::gpu_and_cpu_agree_on_treatment_pixels`
for the small-range gate that keeps `auto` honest.
