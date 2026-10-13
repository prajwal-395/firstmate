# Reel 22 native speed: measured bottleneck and parity boundary (2026-10-13)

## Finding

The slow part is FilmCraft's ingest of the two 4K MXF A-roll sources, not
Ren's planning, placement, overlays, or H.264 encode. The existing end-to-end
profile measured 389.4 s for the 1183-frame A-roll-only export and 492.5 s for
the 1255-frame export with overlays. Importing and compositing overlays alone
was fast (the overlay-only case rendered at 129.6 fps). A 120-frame 4K MXF
range took 179.8 s; the same range from a 1080x1920 ProRes pre-conform took
5.7 s after a 9 s transcode. The speed profile attributes roughly 150-200 s
per MXF to open/index/seek, with deeper seeks measured at 428.4 s for 121
frames. Its source and commands are recorded in
`library/tools/filmcraft_preconform.py` and the linked speed-profile report
(`data/vep-fc-native-speed-profile/report.md`).

The later static-transform Reel 22 pass completed planning, 33 media imports,
placements, 34 static parameters, and a 1255-frame H.264 export in 129 s wall,
653 s CPU, and 3.97 GB peak RSS. Its execution receipt reports 92.558 s for the
FilmCraft ProRes base export (1255 frames, 13.559 fps); the remaining 36.4 s of
wall time includes the rest of the pass, including the final H.264 treatment
encode. This is the current-pipeline phase split available from the fixture.
The earlier 492.5 s full export on FilmCraft 0.5.0 and this 129 s pass on the
current pin are not a controlled before/after pair, so the difference cannot
be credited to a code change.

## Current-pin pre-conform experiment

The AFTER model was re-executed with `--preconform --preconform-size
1080x1920`, with all cache and output paths in a disposable worktree copy of
the project. The model placed the same 33 assets and applied the same static
transforms, grade and frame treatment. Two A-roll sources were proxied. Their
cold-cache transcodes took 22.15 s and 60.01 s; the FilmCraft ProRes base
export took 60.835 s for 1255 frames (20.629 fps), compared with 92.558 s
(13.559 fps) for the step 1 native export. Thus the render phase was 34.3%
shorter, while proxy creation plus rendering took 142.995 s before the final
H.264 treatment encode. A warm cache avoids repeating the transcodes, but
does not change the picture comparison below.

The same three references and frame counts as step 1 were measured, along
with direct comparison to its AFTER render:

| comparison | candidate SSIM-Y | candidate PSNR-y | step 1 AFTER SSIM-Y | step 1 AFTER PSNR-y |
|---|---:|---:|---:|---:|
| candidate vs deliverable (1183 frames) | 0.5174 | 16.79 dB | 0.6664 | 20.36 dB |
| candidate vs fresh Resolve (1254 frames) | 0.4376 | 14.31 dB | 0.5037 | 14.23 dB |
| candidate vs step 1 AFTER (1255 frames) | 0.7626 | 22.57 dB | — | — |

Candidate SSIM-Y by consecutive 200-frame segment was 0.537/0.520/0.511/
0.511/0.509/0.516 against the deliverable, and 0.423/0.415/0.409/0.412/
0.419/0.446/0.817 against fresh Resolve (the final shorter segment is the
endcard). Against the step 1 AFTER render, the corresponding segments were
0.753/0.749/0.747/0.747/0.747/0.767/1.000. The same f100, f300 and f329
stills were extracted for the candidate, step 1 AFTER, fresh Resolve and
deliverable. The framewise logs and stills were generated in scratch and are
not committed.

Pre-conform improves the cached FilmCraft render phase, but does not satisfy
the output-preservation requirement: its deliverable SSIM-Y is 0.149 lower
than step 1 AFTER, and its cold-cache proxy work makes the measured total
longer. No parity-safe speed fix was established. The dominant cost remains
FilmCraft's MXF ingest; the faster proxy path is unsuitable for the default
render under this lane's fidelity constraint.

## Parity decision

The measured speed workaround is `--preconform`, which replaces source pixels
with edit-resolution ProRes pixels. The current-pin Reel 22 comparison above
confirms that it is lossy beyond the lane's output-preservation requirement.
The existing CLI keeps it opt-in. Enabling it by default or splitting the
reel into segments would violate the lane's output-parity requirement or
repeat the measured MXF open/scan cost. Hardware encoding already uses
VideoToolbox automatically; its measured contribution is about 10%. GPU
rendering Auto showed no framehash or speed gain on this content.

No parity-safe optimization is supported by these measurements in the fork.
The dominant cost is inside the pinned FilmCraft media ingest path. The
measured proxy workaround remains available as an explicit, lossy opt-in;
closing the default path's speed gap requires an upstream ingest improvement
or a separately approved fidelity qualification of pre-conform.

## Reel 22 fidelity baseline

There was no speed code change, so this lane has no distinct after render.
For context, step 1's same-reference measurements (before = its pre-static
render; after = its static-transform render) are reproduced here:

| comparison | before SSIM-Y | after SSIM-Y | before PSNR-y | after PSNR-y |
|---|---:|---:|---:|---:|
| native vs deliverable (1183 frames) | 0.5265 | 0.6664 | 12.88 dB | 20.36 dB |
| native vs fresh Resolve (1254 frames) | 0.6186 | 0.5037 | 15.29 dB | 14.23 dB |

These are not a speed-lane before/after comparison. They establish the exact
render whose output this lane was required to preserve. The measured per-200f
segments and reference stills are in
[`2026-10-13-native-static-transforms-reel22.md`](2026-10-13-native-static-transforms-reel22.md).
