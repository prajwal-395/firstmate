# ren-effectcraft

Ren-owned, headless adapter for EffectCraft. It accepts versioned `Request` values and returns a
versioned `Receipt`; `.ecproj` files are temporary engine projections. It has no dependency on any
EffectCraft UI crate and never opens the desktop application.

`PINNED_EFFECTCRAFT_REV` is the qualified immutable upstream revision until the upstream intake
lane lands its pin manifest. `Driver::resolve` honors `REN_EFFECTCRAFT_CLI`, otherwise it clones that
revision into `~/.cache/ren/effectcraft/<sha>/source` and builds only `effectcraft-cli`. The CLI must
report version 0.7.0 and its Git checkout must match the pinned SHA. A standalone binary override
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
