#!/usr/bin/env python3
"""Offline A/B render: Reel 15 program mix vs per-speaker ISO route.

Puts the ISO trade in the captain's ears. No Resolve writes, no reel
rebuild: the clip map below was read from the live timeline
("Reel 15 - the-3d-nail-art-salon-beats-the-chains", project
"Podcast (field test)") on 2026-09-19 with library/tools/reel_read.py,
and this script re-synthesises exactly what the timeline plays from
the placed source ranges with ffmpeg.

Route A: audio stream 0:1 (mix CH1) of each source file, unmodified.
Route B: audio stream 0:3 (ISO CH3) plus the per-file gain measured on
speech spans (200 ms windows where the ISO exceeds -40 dBFS):
LC4932 +10.8 dB, LCATL0013 +5.2 dB.

Overlapping tracks are summed (amix normalize=0), as the timeline
plays them. Nothing is denoised or levelled beyond the baked gains.

Usage:
    python3 scripts/render_iso_ab_reel15.py <footage_dir> <out_dir>
Writes the two FULL renders plus the 9-15 s pause excerpt pair and a
manifest.json. <footage_dir> holds the podcast's MXF sources; the
2026-09-19 renders went to the project's pipeline_output/review/iso-ab-reel15.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

FOOTAGE_ROOT = Path(".")  # set from argv by main()

FPS_NUM, FPS_DEN = 24000, 1001


def f2s(frame: int) -> float:
    return frame * FPS_DEN / FPS_NUM


# (record_in, record_out, source file, src_in, src_out, route-B gain dB)
CLIPS = [
    (326, 619, "LC4932.MXF", 65207, 65500, 10.8),
    (619, 732, "LC4932.MXF", 65644, 65757, 10.8),
    (732, 1043, "LC4932.MXF", 67138, 67449, 10.8),
    (1043, 1231, "LC4932.MXF", 12129, 12317, 10.8),
    (0, 326, "LCATL0013.MXF", 63493, 63818, 5.2),
    (326, 619, "LCATL0013.MXF", 65117, 65410, 5.2),
]

TOTAL_S = f2s(1231)
EXCERPT_START, EXCERPT_LEN = 9.0, 6.0


def build_route(route: str, out_path: Path) -> None:
    inputs: list[str] = []
    filters: list[str] = []
    labels: list[str] = []
    for i, (rec_in, _rec_out, name, src_in, src_out, gain) in enumerate(CLIPS):
        start = f2s(src_in)
        dur = f2s(src_out) - start
        stream = 1 if route == "A" else 3
        inputs += [
            "-ss",
            f"{start:.6f}",
            "-t",
            f"{dur:.6f}",
            "-i",
            str(FOOTAGE_ROOT / name),
        ]
        effect = f"volume={gain:+.1f}dB" if route == "B" else "anull"
        delay_ms = round(f2s(rec_in) * 1000)
        filters.append(
            f"[{i}:a:{stream - 1}]{effect},"
            f"adelay={delay_ms}|{delay_ms},"
            f"apad=whole_dur={TOTAL_S:.6f}[d{i}]"
        )
        labels.append(f"[d{i}]")
    graph = (
        ";".join(filters)
        + ";"
        + "".join(labels)
        + f"amix=inputs={len(labels)}:normalize=0:"
        "duration=longest:dropout_transition=0[m]"
    )
    cmd = (
        ["ffmpeg", "-hide_banner", "-nostats", "-y"]
        + inputs
        + [
            "-filter_complex",
            graph,
            "-map",
            "[m]",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ]
    )
    result = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg route {route} failed: {result.stderr[-800:]}")


def cut_excerpt(full: Path, out_path: Path) -> None:
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostats",
            "-y",
            "-ss",
            f"{EXCERPT_START:.1f}",
            "-t",
            f"{EXCERPT_LEN:.1f}",
            "-i",
            str(full),
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=300,
    )
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg excerpt failed: {result.stderr[-800:]}")


def main(argv: list[str]) -> int:
    global FOOTAGE_ROOT
    if len(argv) != 3:
        print(__doc__.split("Usage:")[1].split("Writes")[0].strip(),
              file=sys.stderr)
        return 2
    FOOTAGE_ROOT = Path(argv[1]).expanduser()
    out = Path(argv[2]).expanduser()
    out.mkdir(parents=True, exist_ok=True)
    full_a = out / "reel15_A_program-mix_FULL.wav"
    full_b = out / "reel15_B_speaker-iso_gain-matched_FULL.wav"
    exc_a = out / "reel15_A_program-mix_PAUSE-EXCERPT.wav"
    exc_b = out / "reel15_B_speaker-iso_gain-matched_PAUSE-EXCERPT.wav"
    build_route("A", full_a)
    build_route("B", full_b)
    cut_excerpt(full_a, exc_a)
    cut_excerpt(full_b, exc_b)
    (out / "manifest.json").write_text(
        json.dumps(
            {
                "procedure": __doc__.splitlines()[1],
                "routeA": "mix CH1, unmodified",
                "routeB": "ISO CH3, LC4932 +10.8 dB, LCATL0013 +5.2 dB",
                "clips": [list(c) for c in CLIPS],
            },
            indent=1,
        ),
        encoding="utf-8",
    )
    print(f"wrote A/B full + excerpt to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
