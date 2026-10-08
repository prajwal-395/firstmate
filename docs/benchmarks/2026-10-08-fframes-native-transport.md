# FFrames native frame transport

## Setup

- Machine: Mac.attlocal.net, Darwin 25.3.0, arm64 (Apple Silicon)
- Base commit: `c92823f8efe165c4fd4d488c567b689bdd14a771`
- Candidate: working tree on `fm/vep-fframes-native-transport`, based on `c92823f8efe165c4fd4d488c567b689bdd14a771` (changes were uncommitted at measurement time)
- Workload: fixed `SubtitleOverlay` and `FullFrameCard` graphics from `docs/fframes-evidence/phase2-side-by-side/repro/props.py`, 1080x1920, 30 fps, 60 frames.
- Cold/warm: the baseline runs were sequential in one process and therefore include a cold first composition followed by a warm second composition. Candidate numbers will use the same order; they are one sample each, not a statistical benchmark.
- Command: `PATH="$PWD/native/target/release:$PATH" /Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh bin/vep docs/benchmarks/fframes_native_transport_bench.py`
- Parity command: `PATH="$PWD/native/target/release:$PATH" /Users/prajwal/.treehouse/firstmate-8bf1b0/3/firstmate/state/vep-verify-run.sh bin/vep docs/benchmarks/fframes_native_transport_parity.py`

## Baseline

Measured before the transport change using the checked-out base implementation and the release FFrames runtime. Values are end-to-end wall time, including rendering and encoding.

| Composition | Frames | End-to-end | Frames/s | Encoded bytes |
|---|---:|---:|---:|---:|
| SubtitleOverlay | 60 | 6.21 s | 9.66 | 1,586,647 |
| FullFrameCard | 60 | 7.43 s | 8.07 | 8,207,369 |

The old transparent path writes PNG frames, then Python opens and rewrites each frame for premultiplication before qtrle encoding. Its run did not retain intermediate PNG sizes or isolated encode time. The old opaque path streamed raw frames but created NumPy/Pillow buffers in Python for flattening. Baseline peak RSS was about 188 MiB from the child process high-water counter; this is not aggregate process-tree RSS.

## Candidate

Two verify-lock runs recorded end-to-end encode-complete time, encoded size, and sampled process-tree RSS (2026-10-08, second run after a docstring/dead-code cleanup with no behaviour change):

| Composition | Frames | End-to-end | Frames/s | Peak aggregate RSS | Encoded bytes | Intermediate frame files |
|---|---:|---:|---:|---:|---:|---:|
| SubtitleOverlay | 60 | 3.196 s (rerun 3.295 s) | 18.77 (rerun 18.21) | 272.5 MiB (rerun 275.7 MiB) | 1,586,647 | 0 |
| FullFrameCard | 60 | 6.263 s (rerun 6.362 s) | 9.58 (rerun 9.43) | 403.1 MiB (rerun 400.9 MiB) | 8,207,369 | 0 |

The measured wall time fell ~47% for SubtitleOverlay and ~14-16% for FullFrameCard; encoded file sizes were unchanged across all runs. The raw stream holds one RGBA frame plus native renderer and encoder buffers. Scratch disk during the run held only the final output (disk total equalled the encoded bytes: 1,586,647 and 8,207,369). The candidate's aggregate RSS sampler includes the Python process and descendants. The baseline's 188 MiB figure above is only the child-process high-water counter and is not directly comparable.

Renderer and ffmpeg overlap by design in the streamed path, so there is no independent encoder-only wall interval; the reported duration covers complete rendering through encoded output.

## Parity

`docs/benchmarks/fframes_native_transport_parity.py` (verify-lock, 2026-10-08) proves both paths against the code they replace:

- Overlay: the 60-frame `SubtitleOverlay` qtrle decodes byte-identical (`decoded_rgba_matches_legacy`) to the legacy PNG -> Python-premultiply -> qtrle path. Alpha is preserved (transparent pixels remain) and every output pixel satisfies RGB <= A (premultiplied). The legacy run wrote 60 PNG intermediates totalling 7,724,659 bytes before premultiplication and 3,332,661 after; the native path writes 0 intermediate files.
- Opaque: the native `--background #1A1A2E` flatten output is byte-identical to the Python reference equation `(src*A + ground*(255-A) + 127) // 255` with alpha forced to 255, over all 60 frames.

## Scope and limits

The FFrames adapter currently selects Metal on macOS and has no runtime CPU backend switch, so a CPU-vs-Metal timing is not available through this product path. The existing five-composition FFrames-vs-HyperFrames visual comparison is in `docs/fframes-evidence/phase2-side-by-side/README.md`; it measures visual parity, not full encode timing. This report measures the FFrames transport change only. The parity script compares decoded qtrle pixels against the former PNG path and native flattening against the former Python equation. qtrle remains the transparent carriage and ProRes 4444 remains the opaque card carriage.
