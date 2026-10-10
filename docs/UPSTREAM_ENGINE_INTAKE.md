# FilmCraft and EffectCraft upstream intake

Ren tracks immutable source revisions in `library/tools/upstream_engines.json`.
The daemon observes upstream default-branch heads and release tags daily, then
runs the intake gate weekly after local-CI verification work is idle. An
expedited request uses that same gate. Runtime observations, candidate SHAs,
intake JSON records, and logs are stored in the local-CI state directory; the
pin status is visible with `ren ci status` or `ren ci upstream status`.

## Pins

The Ren driver crates own the pins: `FILMCRAFT_REPOSITORY` /
`FILMCRAFT_REVISION` in `native/ren-filmcraft/src/lib.rs` (and the
EffectCraft equivalents once that crate lands). The manifest mirrors those
constants, and qualification refuses before building anything when the two
drift apart. Upstream is always addressed by immutable commit SHA straight
from the storytold repos; intake clones that URL directly (shallow, deepened
only until the candidate SHA resolves) and verifies the checkout with
`git rev-parse` before building. No fork, no floating branch, no upstream PRs.

The qualified starting revisions are the source-built revisions measured in
the 2026-10-10 engine evaluation:

| Engine | Qualified source SHA | Upstream evidence |
|---|---|---|
| FilmCraft | `7bd76212629a067a9d1c5426d751de9faf4664b7` | `cargo build --release -p filmcraft-cli -j4`; `cargo test --release -p filmcraft-interchange` passed 62 tests. Headless export measured 225 frames with SSIM 0.980-0.982 against source decode; separate 413-frame H.264 performance run measured 30.30 s / 13.648 fps. |
| EffectCraft | `813c7c4650f0d4c805da620c8d5837c9600ad2e3` | `cargo build --release -p effectcraft-cli -j4`; `cargo test -p effectcraft-effects --lib` passed 426 tests. Headless export measured 75 RGBA frames in 0.28 s and a ProRes 4444 alpha render in 0.60 s. |

These measurements are historical qualification evidence, not a substitute
for the fixed, committed regression corpus below.

## Qualification gate

For each engine with a candidate newer than its qualified pin, intake builds
`--package filmcraft-cli` / `--package effectcraft-cli` at `-j4`, runs the
relevant upstream crate tests, replays the fork-only patch series, then runs
the committed headless render corpus
(`library/tools/upstream_corpus/corpus.json`, runner
`library/tools/local_ci/corpus.py`):

- `filmcraft-solids-roundtrip`: five committed solid stills placed
  back-to-back; each rendered mid-frame carries the still in a 16 px center
  crop (max 1 LSB drift, PSNR floor 40 dB) with only still color and
  letterbox black elsewhere.
- `filmcraft-stereo-unity`: the committed stereo WAV round-trips with both
  channels, duration within 0.05 s, and peak level within 0.5 dB.
- `effectcraft-alpha-title`: the driver's tiny title (96x64, 5 frames):
  exact frame count, RGBA on every frame, transparent first frame, visible
  last frame, deterministic re-render (PSNR floor 60 dB), and alpha extremes
  surviving the ProRes 4444 encode.
- `filmcraft-edit-50`: a fixed 50-op edit mix plus per-case timings and peak
  child RSS. Absolute budgets gate promotion; ratios against the prior passed
  pin are recorded as review findings, never silent.

The pin advances only when the build, the crate tests, and every corpus case
pass. Build, test, patch-replay, fixture, or fidelity failures refuse the
promotion and leave the prior qualified revision in place; the intake record
keeps both SHAs, the per-case measurements, the perf findings, and the log
path. Only engines with pending candidates are built.

Ren-specific patch series belong under
`library/tools/upstream_patches/<engine>/`; adapters and Ren integration code
belong in Ren-owned crates, not here. Upstream source is checked out detached
at an immutable SHA. Engine UI crates are never built: qualification builds
the CLI and named test packages only.

## Edition and license closure (Unit 8, measured 2026-10-10)

Both headless integrations are public components: `engine.filmcraft_cli`
and `engine.effectcraft_cli` in `THIRD_PARTY_NOTICES`, gated at runtime
through `library/tools/engine_edition.py` (`require_engine` before the
intake fetch/build in `upstream._qualify_engine` and before the corpus
drives either CLI). Both editions run the same Ren model and adapter
protocol; the public tree ships the adapter sources byte-identical and
contains no personal-only asset and no engine UI crate
(`tests/unit/context/test_product_editions.py`).

Dependency closure, measured over `cargo tree -p <cli>` at the pinned
revisions (FilmCraft CLI 0.5.0: 176 crates; EffectCraft CLI 0.7.0:
352 crates):

- All upstream workspace crates are MIT OR Apache-2.0 (workspace license).
- Registry crates are MIT/Apache-2.0/BSD/Zlib/Unlicense/Unicode-3.0/ISC,
  plus symphonia x13 MPL-2.0 (weak file-scoped copyleft, reached through
  audio decode; commercial use with notice), self_cell
  Apache-2.0 OR GPL-2.0-only (used under Apache-2.0), and
  epaint_default_fonts (MIT OR Apache-2.0) AND OFL-1.1 AND
  Ubuntu-font-1.0 (egui default fonts, EffectCraft closure only).
- No GPL/AGPL/LGPL-only, noncommercial, or proprietary dependency was
  found in either closure: nothing blocks public eligibility.
- Both headless binaries embed the upstream OFL-1.1 fonts (Inter x5,
  JetBrainsMono-Regular, NotoSerif-Regular via `include_bytes` in each
  engine's `crates/text`); CRAFT_FONTS is empty (Ren sets no
  `CRAFT_FONTS_DIR`). The FilmCraft whisper/download features stay off
  (Ren installs default features), so no model is downloaded or bundled.
- The ArtCraft name, wordmark, and logos in each repo's `docs/brand/`
  are trademarks, not open source (`docs/brand/LICENSE-brand.txt`); Ren
  packages no upstream source, docs, or marks.
- FilmCraft's CLI closure holds no desktop UI crate (no ui-egui, egui,
  eframe, winit, rfd, cpal, muda). EffectCraft's CLI links
  effectcraft-ui-egui (eframe/egui/winit/wgpu) for its headless
  `bench --ops` helper only; it has no window entry point and Ren invokes
  only run/render/info/mcp. Ren builds neither desktop app nor web bundle:
  the built package set is `library/tools/engine_edition.py`
  `INTAKE_PACKAGES`, disjoint from `UI_PACKAGES` by test.
