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
the 2026-10-10 engine evaluation. That table is the historical baseline;
the current pins live in `library/tools/upstream_engines.json`, mirrored
from the driver crates, and each promotion appends an intake record below.

| Engine | Qualified source SHA | Upstream evidence |
|---|---|---|
| FilmCraft | `7bd76212629a067a9d1c5426d751de9faf4664b7` | `cargo build --release -p filmcraft-cli -j4`; `cargo test --release -p filmcraft-interchange` passed 62 tests. Headless export measured 225 frames with SSIM 0.980-0.982 against source decode; separate 413-frame H.264 performance run measured 30.30 s / 13.648 fps. |
| EffectCraft | `813c7c4650f0d4c805da620c8d5837c9600ad2e3` | `cargo build --release -p effectcraft-cli -j4`; `cargo test -p effectcraft-effects --lib` passed 426 tests. Headless export measured 75 RGBA frames in 0.28 s and a ProRes 4444 alpha render in 0.60 s. |

These measurements are historical qualification evidence, not a substitute
for the fixed, committed regression corpus below.

## Intake record: 2026-10-10 FilmCraft `7bd7621` -> `98ba9ac`

Upstream `main` advanced 256 commits (`7bd76212629a067a9d1c5426d751de9faf4664b7`
to `98ba9acbcedcbb630ba2a07b782176b66835edd2`, head merge PR #421 "GPU export
rendering on by default"), also carrying TTS narration (schema v14), AAF
fixes, and anamorphic PAR. The daily poll ran 06:52 UTC and correctly
recorded the pin: the new head landed 12:35 UTC, after the poll, so no poll
change was needed. EffectCraft `main` was still at its pin
(`813c7c4650f0d4c805da620c8d5837c9600ad2e3`); it was not intaken.

Gate (all at the candidate, Apple M5 / 24 GB):

- `cargo build --release -p filmcraft-cli -j4`: clean.
- `cargo test --release -p filmcraft-interchange -p filmcraft-golden -j4`:
  99 passed, 0 failed.
- Patch replay: `library/tools/upstream_patches/filmcraft/` is empty; nothing
  to replay.
- Committed render corpus (`corpus_version 2026-10-10.1`): passed at the
  candidate with fidelity identical to the old pin (solids center diff 0 on
  all five, stereo level -0.001 dB, `filmcraft-edit-50` 1.07 s / 103.5 MB
  vs 1.069 s / 103.3 MB at the old pin, ratio 1.00). This run exposed a
  corpus harness bug, not an engine change: the headless still render is
  1920x1080 RGBA while the fixtures are 64x36 RGB, so the center-crop
  comparison refused on buffer length at BOTH pins. Fixed in
  `library/tools/local_ci/corpus.py` (`rgb_crop`: compare color, refuse any
  non-opaque alpha) with the fake CLI runner now emitting the real RGBA
  shape; both pins render fully opaque.
- Adapter suites against the candidate CLI (`cargo test -p ren-filmcraft`):
  26 unit + 10 `adapter_qualification` + 4 `pinned_cli`, all passed.
- `ren qualification pm-e2e --no-resolve`: passed (ingest, identity,
  filmcraft head-trim 12..48 with 36-frame measured render and frame map,
  effectcraft, swapback plan, failure injection; `resolve_live` skipped).

Behaviour deltas the adapter absorbs (no papering; each verified live):

- Project schema 13 -> 14 (v13->v14 is a no-op narrations migration).
  `FILMCRAFT_PROJECT_SCHEMA` moved to 14; `--version` still prints
  `filmcraft-cli 0.5.0`.
- `sequence.inspect` gained a top-level `transitionSelection` key; the
  response contract allows it (still not required).
- Transition authority unchanged: the candidate's
  `exec effects.list '{"kind":"VideoTransition"}'` reports exactly the 112
  ids checked in by PR #2261, with byte-identical entries - no removals.
- All three measured defects reproduce unchanged, so every compensation
  stands: a V1-only razor still leaves linked audio (video 1->2 items,
  audio stays 1); the -318-frame ripple-out trim still answers
  `delta -752215464000` ticks and lands at duration 1, tick-identical at
  both pins; a move to the same tick target reads back the same
  `startFrame` at both pins.

Export wall time, standard 4K H.264 render with GPU export Auto
(`file.exportMedia` `{format h264, hardwareEncoding auto, wait true}` over
a 3840x2160/24fps/96-frame `testsrc2` fixture on a 3840x2160 sequence,
export exec timed, run under verify slot 2 with the daemon's Tier-1 checks
active on slot 0):

| Pin | Rep walls (s) | Median wall (s) | Median fps | Output |
|---|---|---|---|---|
| `7bd7621` (old) | 3.69, 3.69, 3.58 (+4.87 setup run) | 3.69 | 26.0 | 3840x2160, 96 frames, 10143827 B |
| `98ba9ac` (new) | 3.23, 3.98, 5.14 (+5.69/4.22 setup runs) | 3.98 | 24.1 | 3840x2160, 96 frames, 10143791 B |

Both pins emit byte-near-identical encodes (36 B apart) at exact 4K
dimensions and 96 frames; medians differ by ~8% with overlapping ranges
under shared-machine noise, and the engine-internal `render_fps` agrees
(~23 both). No systematic export regression; the anamorphic PAR work did
not move output geometry.

Pin moved in every place it lives: `FILMCRAFT_REVISION` and
`FILMCRAFT_PROJECT_SCHEMA` in `native/ren-filmcraft/src/lib.rs` (sole
source), `library/tools/upstream_engines.json`
(`qualified_sha`/`last_seen_upstream_sha`), `FILMCRAFT_REVISION` in
`library/tools/project_model_qualification.py`, the FilmCraft pin in
`THIRD_PARTY_NOTICES` (closure re-measured at the candidate: 270 crates,
all MIT/Apache-2.0/BSD/Zlib/Unlicense/0BSD/Unicode-3.0/ISC plus symphonia
x16 MPL-2.0; no UI crates, no GPL/AGPL/LGPL-only), and the
`test_daily_poll_records_new_commit_and_release_without_moving_pin`
assertion now reads the manifest instead of a hardcoded SHA. The
`qualification-receipts.json` fixture keeps the 09:41 UTC full-run record
at the old pin as history; it refreshes on the next full (`--resolve`) run.

## Intake record: 2026-10-11 FilmCraft `98ba9ac` -> `0800a8b`

Upstream `main` advanced 284 commits (`98ba9acbcedcbb630ba2a07b782176b66835edd2`
to `0800a8b0aa4a703ba42172f415b45a61f8d8a5e1`, `v0.6.0-4-g0800a8b`),
carrying the headless `gpuRendering:auto` panic fix (`d215d0e`), schema
v15 (no-op `notes` migration), the morph_cut optical-flow change (#805)
and the Lumetri vignette-roundness change (#719). EffectCraft `main` was
not intaken (FilmCraft first, alone).

Gate (all at the candidate, captain's Mac):

- `cargo build --release -p filmcraft-cli -j4`: clean (3m15s);
  `--version` prints `filmcraft-cli 0.6.0`.
- `cargo test --release -p filmcraft-interchange -p filmcraft-golden -j4`:
  70 + 33 passed, 0 failed.
- Patch replay: `library/tools/upstream_patches/filmcraft/` holds no
  `*.patch`; nothing to replay.
- Committed render corpus (`corpus_version 2026-10-10.1`): passed at the
  candidate with fidelity identical to the old pin (solids center diff 0
  on all five, stereo level -0.001 dB; pin-vs-candidate outputs
  pixel-identical, PSNR inf). `filmcraft-edit-50`: 0.772 s wall,
  102.8 MB peak RSS (budgets 600 s / 8192 MB; no ratio findings).
- Adapter suites against the candidate CLI (`cargo test -p ren-filmcraft`):
  67 unit + 24 `adapter_qualification` + 3 `mono_to_dual` + 6 `pinned_cli`,
  all passed - after one Ren-side absorption: `sequence.inspect` gained a
  top-level `gapSelection` key, and the response contract now allows it
  (same class as the earlier `transitionSelection` tolerance).
- `ren qualification pm-e2e --no-resolve`: passed (all five failure
  injections refuse by name).

Behaviour deltas the adapter absorbs:

- Project schema 14 -> 15 (`v14_to_v15` is a no-op notes migration).
  `FILMCRAFT_PROJECT_SCHEMA` moved to 15.
- `sequence.inspect` gained `gapSelection` (allowed, not required).
- Transition authority unchanged: the candidate's
  `exec effects.list '{"kind":"VideoTransition"}'` output is byte-identical
  to the pin's (112 ids, same entry set) - no file change.
- `clip.speedDuration` still single-speed (REQUESTS.md ask #1 stays open).
- `describe file.exportMedia` still omits `gpuRendering`, and the response
  carries `job/path/result` with no `gpu_render_stats` (asks #1 and #3
  stay open; ask #2 closed - see GPU below).

Intended pixel changes, measured pin-vs-candidate (not corpus cases -
the committed corpus is pixel-identical):

- morph_cut transition (textured 12-frame blend): frames inside the blend
  differ strongly (PSNR ~24.5-26 dB, SSIM ~0.977-0.987, max delta 254);
  frames outside it identical. Optical-flow morph confirmed.
- Lumetri vignette with nonzero roundness (amount -3.0, roundness +100):
  max delta 148, PSNR 16.5 dB, SSIM 0.893. Roundness honored vs ignored.
- Ren plans reach neither by default: `morph_cut` renders only when a
  model edit explicitly names it (nothing in the pipeline emits it; the
  omitted effect is the engine default), and Ren never sends vignette
  params (only `lumetri.setInputLut`).
- MXF span re-probe (LCATL0013.MXF, 24-frame 0-1 s export): import
  12.7 s vs 12.6 s, export 18.8 s vs 19.7 s - no ingest change, so
  pre-conform stays (ask #2 stays open). Pixels moved slightly
  (max delta 27, PSNR 50.4 dB, SSIM Y 0.997; frame 0 identical, frames
  1-23 drift), consistent with the open-GOP leading-picture fix.

GPU re-probe (captain 2026-10-11: acceptable when visually
indistinguishable; bar SSIM >= 0.995 and worst-frame PSNR >= 45 dB):

- `gpuRendering:auto` through `exec file.exportMedia` no longer panics
  (rc=0); the `export` subcommand behaves the same. The shipped CLI
  compiles wgpu with no backend (metal/vulkan/dx12/gles are
  dev-dependencies only), so `auto` falls back to the CPU reference path
  on every platform with this build.
- GPU-vs-CPU on the same 96 effects-heavy frames (1080p, lumetri +
  morph_cut): SSIM Y 1.000000, avg PSNR inf, worst-frame PSNR inf, max
  per-pixel delta 0 - the bar is exceeded trivially because no GPU path
  runs. Timing equal (`off` 3.90/3.76 s vs `auto` 3.78/3.88 s per 96
  frames; outputs byte-identical across reps).
- Ren lifted the `auto` refusal but keeps the `off` default everywhere
  (fidelity rule - PR #421 parity-tolerance statement still governs real
  GPUs); switching on stays the captain's call. Side-by-side stills
  (CPU left, GPU-auto right): `sbs-frame12/50/70.png` beside this record's
  probe log (intake scratch, retained on the captain's Mac).
- `gpuRendering` default kept `off` in the adapter, the qualification
  export, and the corpus.

Pin moved in every place it lives: `FILMCRAFT_REVISION`,
`FILMCRAFT_CLI_VERSION` ("0.6.0") and `FILMCRAFT_PROJECT_SCHEMA` (15) in
`native/ren-filmcraft/src/lib.rs` (sole source),
`library/tools/upstream_engines.json`
(`qualified_sha`/`last_seen_upstream_sha`), `FILMCRAFT_REVISION` /
`FILMCRAFT_VERSION` in `library/tools/project_model_qualification.py` and
`library/tools/project_model_cli.py`, the FilmCraft pin in
`THIRD_PARTY_NOTICES` (closure re-measured at the candidate: 267 unique
packages, symphonia x16 MPL-2.0, no GPL/AGPL/LGPL-only, no UI crates),
plus the `gapSelection` tolerance and the `auto`-refusal lift with their
unit tests.

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
