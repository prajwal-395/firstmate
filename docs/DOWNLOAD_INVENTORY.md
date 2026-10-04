# Download inventory: every executable and model Ren fetches

Every executable or model Ren fetches at install or run time, with the
pin, the integrity check, and the licence **as stated by upstream**.
Written for the commercial-licence lane to reuse: the licence column
states only what upstream says and where it says it, never a
commercial-use determination.

Conventions: a **PINNED** row names an exact version whose bytes are
verified before use; a mismatch REFUSES (the install deletes the bad
bytes first, a runtime loader raises rather than loading). A
**FLOATING** row is an honest gap: what moves, and what still needs a
pin. Measured 2026-10-04 unless a row says otherwise.

The pins live once, in `library/tools/shared_environment.py` (plus
`HEAD_SHA256` beside `HEAD_URL` in `library/tools/frame_ranker.py`);
the install scripts ASK for them rather than carrying copies.

## Install-time executables (scripts/)

| Name | Source URL | Version | Hash | Licence (upstream statement) | Check |
|---|---|---|---|---|---|
| micromamba (osx-arm64) | `https://micro.mamba.pm/api/micromamba/osx-arm64/2.9.0` | 2.9.0 | sha256 `500f5074feb8d02c4296ef9921c3650ed2874171805a9fbb8fbb53896433646b` | BSD-3-Clause (mamba-org/mamba LICENSE, QuantStack) | sha256 verified before unpack; mismatch deleted, never executed |
| micromamba (linux-64) | `https://micro.mamba.pm/api/micromamba/linux-64/2.9.0` | 2.9.0 | sha256 `8761c382127e6363bd9e0a2451aa3ef90d071a79133f736e2f759a3bf13040dd` | BSD-3-Clause, as above | as above |
| deep-filter binary (aarch64-apple-darwin) | `https://github.com/Rikorose/DeepFilterNet/releases/download/v0.5.6/deep-filter-0.5.6-aarch64-apple-darwin` | 0.5.6 | sha256 `4601e7f4e4c03e59a4c5b5000216ef3add3e808799cfccd95e14e83ea4611081` | MIT OR Apache-2.0 (LICENSE-MIT / LICENSE-APACHE upstream, Rikorose/DeepFilterNet) | sha256 verified on fetch AND on reuse; mismatch refuses (fresh bytes deleted, existing left in place unrun) |
| montreal-forced-aligner (conda-forge) | conda-forge channel, via the pinned micromamba | 3.4.2 exact (`montreal-forced-aligner=3.4.2`) | conda repodata hashes, verified by micromamba/mamba on download | MIT (MontrealCorpusTools/Montreal-Forced-Aligner LICENSE) | exact spec; solver cannot float it |
| PANNs checkpoint `Cnn14_DecisionLevelMax_mAP=0.385.pth` | `https://zenodo.org/api/records/3987831/files/Cnn14_DecisionLevelMax_mAP%3D0.385.pth/content` | record 3987831 (327,428,481 bytes) | md5 `70539c43c18b6a289b3199c503a82c5a` (Zenodo-provided) | CC-BY-4.0 (the Zenodo record licence) | md5 verified on fetch and on reuse; mismatch refuses |
| LAION aesthetic head `sa_0_4_vit_l_14_linear.pth` | `https://github.com/LAION-AI/aesthetic-predictor/raw/main/sa_0_4_vit_l_14_linear.pth` | 4,071 bytes (unchanged upstream since 2022) | sha256 `2cd4e60f4f24ae3bcd57b847b13c1f3ba27edc28cc1a7f9ce74ee9f421243cba` | MIT (LAION-AI/aesthetic-predictor LICENSE, 2022) | sha256 verified on fetch AND on reuse; mismatch deleted and refuses |

## Install-time models (scripts/)

| Name | Source | Version | Hash | Licence (upstream statement) | Check |
|---|---|---|---|---|---|
| MFA acoustic `english_us_arpa` | MFA model repo (GitHub releases, MontrealCorpusTools/mfa-models) | v3.0.0 (newest tag) | no upstream checksum; version pin is the anchor, fetched over TLS | CC BY 4.0 (`acoustic/english/us_arpa/v3.0.0/meta.json` + LICENSE) | `--version v3.0.0` (verbatim canonical form MFA resolves) |
| MFA dictionary `english_us_arpa` | as above | v3.0.0 (newest tag) | as above | CC BY 4.0 (`dictionary/english/us_arpa/v3.0.0/meta.json`) | `--version v3.0.0` |
| MFA G2P `english_us_arpa` | as above | v2.0.0a (newest tag; no v3 exists for this model) | as above | CC BY 4.0 (`g2p/english/us_arpa/v2.0.0a/meta.json`) | `--version v2.0.0a` |
| ECAPA `speechbrain/spkrec-ecapa-voxceleb` | HuggingFace, `snapshot_download` at revision | commit `0f99f2d0ebe89ac095bcc5903c4dd8f72b367286` | Hub transfer integrity (TLS + Hub hash check on download) | recipe Apache-2.0 (speechbrain); weights trained on VoxCeleb, whose dataset terms govern them | pinned revision; required-files check (`hyperparams.yaml` + `embedding_model.ckpt`) refuses without |
| insightface `buffalo_l` | insightface's own downloader into its own cache | FLOATING - no version or pin (licence lane owns replace/relicence) | none | non-commercial research use (deepinsight/insightface; see `scripts/install_insightface.sh` header) | required-files check only (`det_10g.onnx` + `w600k_r50.onnx`) |
| DeepFilterNet weights (~100 MB) | fetched by the `deep-filter` binary itself on first use (inside the install's smoke run) | follows the pinned 0.5.6 binary release | none independent | MIT OR Apache-2.0, as the binary | binary pin covers the release; weights float inside it - residual gap, stated here |

## Run-time model downloads (first use, via Hub/loader TLS)

Fetched by the ML libraries on first use into the HuggingFace/torch
caches, over TLS with the Hub's own transfer integrity. No hand-rolled
hash: the pin for each is its model id (exact strings below), and only
ECAPA additionally pins a commit today. Floating ids are a stated
residual, not a silent one.

| Name | Model id | Licence (upstream statement) | Used by |
|---|---|---|---|
| Gemma vision | `mlx-community/gemma-4-12b-it-4bit` | Gemma Terms of Use (Google); community conversion by mlx-community | `library/tools/vision_model.py`, `analysis/vision_pipeline_v3.py` |
| SAM 2 | `facebook/sam2.1-hiera-small` | upstream statement to be recorded by the licence lane | `analysis/object_segmentation.py` |
| CLIP | `openai/clip-vit-large-patch14` | MIT (openai/CLIP) | `analysis/footage_frames.py`, `frame_ranker.py` |
| MiniLM | `sentence-transformers/all-MiniLM-L6-v2` | Apache-2.0 (sentence-transformers) | `analysis/footage_query.py`, `analysis/sfx_pipeline.py` |
| Audio Flamingo Next | `nvidia/audio-flamingo-next-captioner-hf` | upstream statement to be recorded by the licence lane (needs HF_TOKEN) | `analysis/sfx_pipeline.py` |
| beat_this weights | via `beat-this==1.1.0` (PyPI) | MIT code AND MIT weights (CPJKU/beat_this README) | `analysis/music_pipeline.py` |
| All-In-One weights | `huggingface.co/taejunkim/allinone` via `all-in-one-infer==3.1.0` | MIT code, MIT weights (upstream) | `analysis/music_pipeline.py` |
| Demucs `htdemucs` | via `demucs-infer==4.2.2` (PyPI) | MIT code and models (Demucs README) | `analysis/music_pipeline.py` |
| EasyOCR models | via `easyocr>=1.7` (PyPI) | Apache-2.0 (JaidedAI/EasyOCR); per-model weights various, to be recorded by the licence lane | step 1.07 `ocr_extraction` |

## Package managers (mechanism, not per-file hashes)

| Name | Pin story | Integrity mechanism |
|---|---|---|
| pip / uv | `requirements.txt` floors + `requirements/lock/macos-arm64-py312.txt` generated lock with per-file `--hash` | `uv pip sync` verifies every file against the lock hashes |
| npm | `remotion-subtitles/package.json` exact versions + `package-lock.json` + `npm ci` into a lockfile-keyed store | npm verifies `integrity` (sha512) from the lockfile; store keyed by sha256 of the lockfile, so a stale tree is unreachable |
| yt-dlp | floor `>=2026.08.19` in requirements + version reported on every run (`music_search.py`) | pip lock hash, as above |
| conda-forge (MFA env) | exact `montreal-forced-aligner=3.4.2` | repodata hashes verified by the solver |

## System preconditions (not fetched by Ren)

| Name | Story |
|---|---|
| ffmpeg / ffprobe | Homebrew (`brew install ffmpeg`); version unpinned, system-managed. Every shell-out refuses with a named error when absent, never a traceback. |
| Node.js 18+ | Homebrew; version unpinned, system-managed. Same refusal discipline. |
| `da voz` (desert-ant CLI) | External transcriber on PATH; resolved via `shutil.which`, refused by name when absent (`heard_speech.py`). |
| DaVinci Resolve Studio | External application; scripting precondition checked, never downloaded. |

## Vendored (no download at all)

| Name | Location | Licence record |
|---|---|---|
| Montserrat variable font | `remotion-subtitles/public/fonts/Montserrat-Variable.ttf` | SIL OFL 1.1 (`public/fonts/OFL-Montserrat.txt`) |
| GSAP 3.14.2 | `hyperframes/vendor/gsap.min.js` | GSAP Standard License (header in file) |

## What the audit claimed, and what held

Against origin/main (audit made against `d32c657`):

1. **"PANNs is hash checked" - HELD.** `install_panns.sh` verifies the Zenodo md5 on fetch and on reuse and refuses on mismatch. Unchanged.
2. **"DeepFilterNet downloads a binary without checksum" - HELD, now fixed.** `install_deepfilternet.sh` curled the release asset with no check. It now verifies the pinned sha256 on fetch and on reuse and refuses on mismatch.
3. **"MFA downloads the current latest micromamba" - HELD, now fixed.** `install_mfa.sh` fetched `/latest`. It now fetches the versioned 2.9.0 URL and verifies the per-platform sha256 before unpacking.
4. **Adjacent float found while pinning MFA: the conda spec (`3.4.*`) and the dictionary/G2P models floated, and the pinned acoustic download never worked.** The acoustic `--version` passed `vv3.0.0` (the constant already carries the `v`; MFA's own downloader only tolerates a *missing* `v`, never a doubled one, and raises `RemoteModelVersionNotFoundError` otherwise). All three models now pass verbatim canonical versions (`v3.0.0` / `v3.0.0` / `v2.0.0a`, each the newest tag on MontrealCorpusTools/mfa-models), and the conda spec is exact (`3.4.2`, newest 3.4.x on conda-forge).
