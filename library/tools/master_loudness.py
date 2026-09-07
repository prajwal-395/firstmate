"""Mastering: bring a rendered master to the delivery loudness.

Step 6.02's P4 gate (`render_qa.measure_lufs`) has failed every run since
the baseline-craft checks landed (#155, 2026-08-25) with the same finding:
the master sits about 6 dB under the -14 LUFS delivery target (-20.96 on
the August end-to-end, -20.94 on the run of record, -19.68 on the Sep-03
replan). The finding is real - independent ffmpeg loudnorm reproduces the
gate's number to the digit - and the gate is right: a -19.68 LUFS master
plays audibly quieter than the feed around it. So this module fixes the
work, and touches the gate nowhere.

Why the mix cannot fix it. The mix (OTIO clip gains, owned by `audio_mix`)
sets the BALANCE between speech and bed, not the level of their sum. The
Sep-03 master needs +5.68 dB of makeup gain to reach target, and its true
peak is already -2.60 dBTP: that gain without a limiter lands at +3.08
dBTP, which fails the gate's other half. Gain plus a true-peak limiter is
mastering, not mixing, and no step performs it - which is why two weeks of
runs filed the failure under the 2026-08-15 "audio is out of scope" ruling
instead of fixing it. That ruling predates the pipeline owning the mix; it
does not cover a master the pipeline itself renders.

What this module does. Two-pass EBU R128 normalization (ffmpeg loudnorm,
`linear=true`) of theFILE the render step exported: pass one measures,
pass two applies the measured gain with the limiter set to the gate's own
ceiling minus headroom, and the output is re-measured. A file that still
fails is refused, never handed back - fail-closed, the way the gate is.
The picture stream is copied untouched; mastering is audio-only.

Proven on the real artefact. The Sep-03 master (`Pipeline_Edit_Replanned.mp4`,
-19.68 LUFS / -2.60 dBTP) normalizes to -14.14 LUFS / -1.43 dBTP, and the
gate's own `measure_lufs` flips from `passed=False severity=error` to
`passed=True`. Same instrument, both directions.

NOT WIRED. The call belongs in step 6.01 post-export, with 6.02 measuring
the normalized file - and that wiring needs a Resolve end-to-end this lane
cannot run (CI has no Resolve). Wiring it unwitnessed would be the defect
AGENTS.md 10.2 exists to prevent ("a capability is only real where the
renderer reads it"). The mechanism, its tests and its cost are landed here;
the one-line call plus a re-render is the priced remainder.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from typing import Any, Dict

try:
    from library.tools.render_qa import (
        DEFAULT_LUFS_TOLERANCE,
        DEFAULT_TRUE_PEAK_CEILING_DBTP,
    )
except ImportError:  # imported as a top-level module from library/tools
    from render_qa import (  # type: ignore[no-redef]
        DEFAULT_LUFS_TOLERANCE,
        DEFAULT_TRUE_PEAK_CEILING_DBTP,
    )

# The delivery target the P4 gate enforces. Kept as this module's own name
# so the gate file is never edited for mastering reasons; a test pins it
# against the gate's effective default, so either side drifting fails loudly
# instead of silently normalizing to a target the gate does not hold.
DELIVERY_LUFS_TARGET = -14.0

# The limiter is set this far BELOW the gate's true-peak ceiling. Measured:
# normalizing the Sep-03 master with the limiter exactly at the ceiling came
# back at -0.91 dBTP, 0.09 dB over - the lossy re-encode moves peaks the
# limiter already capped. 0.5 dB of headroom absorbs that with margin while
# leaving the master 0.5 dB hotter than the ceiling requires; it changes no
# gate, only where the mastering aims inside the passing band.
LIMITER_HEADROOM_DB = 0.5


@dataclass(frozen=True)
class MasteringResult:
    """What one normalization did, in the gate's own units."""

    input_path: str
    output_path: str
    already_compliant: bool
    input_i: float
    input_tp: float
    output_i: float
    output_tp: float


def _loudnorm_measure(path: str) -> Dict[str, float]:
    """Integrated loudness and true peak of a file, via ffmpeg.

    Raises on any failure or unparseable output: an unmeasured file is not
    a quiet file, and must never be treated as one.
    """
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-i", path,
         "-af", "loudnorm=print_format=json", "-f", "null", "-"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=300, check=False,
    )
    blob = ""
    in_json = False
    for line in proc.stderr.splitlines():
        if line.strip() == "{":
            in_json = True
        if in_json:
            blob += line + "\n"
        if line.strip() == "}":
            break
    try:
        data = json.loads(blob) if blob else {}
        return {
            "input_i": float(data["input_i"]),
            "input_tp": float(data["input_tp"]),
            "input_lra": float(data["input_lra"]),
            "input_thresh": float(data["input_thresh"]),
            "target_offset": float(data.get("target_offset", 0.0)),
        }
    except (ValueError, KeyError, TypeError) as exc:
        raise RuntimeError(
            f"could not measure loudness of {path}: {exc}") from exc


def _is_compliant(input_i: float, input_tp: float,
                  target_lufs: float = DELIVERY_LUFS_TARGET,
                  tolerance: float = DEFAULT_LUFS_TOLERANCE,
                  true_peak_ceiling: float = DEFAULT_TRUE_PEAK_CEILING_DBTP,
                  ) -> bool:
    """The gate's own verdict, in the gate's own units."""
    return (abs(input_i - target_lufs) <= tolerance
            and input_tp <= true_peak_ceiling)


def normalize_to_delivery(
    input_path: str,
    output_path: str,
    target_lufs: float = DELIVERY_LUFS_TARGET,
    tolerance: float = DEFAULT_LUFS_TOLERANCE,
    true_peak_ceiling: float = DEFAULT_TRUE_PEAK_CEILING_DBTP,
    audio_bitrate: str = "192k",
) -> MasteringResult:
    """Normalize a rendered master to the delivery loudness, true-peak-safe.

    The input is never modified. When it already complies it is copied
    byte-for-byte and reported `already_compliant`. Otherwise a measured
    two-pass normalization is applied and the output is re-measured; if it
    still fails the gate's verdict, nothing is handed back - RuntimeError.
    """
    if not os.path.exists(input_path):
        raise RuntimeError(f"master to normalize not found: {input_path}")
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise RuntimeError("output must differ from input: the master on "
                           "disk is evidence and is never overwritten")

    measured = _loudnorm_measure(input_path)
    if _is_compliant(measured["input_i"], measured["input_tp"],
                      target_lufs, tolerance, true_peak_ceiling):
        shutil.copyfile(input_path, output_path)
        after = _loudnorm_measure(output_path)
        return MasteringResult(
            input_path=input_path, output_path=output_path,
            already_compliant=True,
            input_i=measured["input_i"], input_tp=measured["input_tp"],
            output_i=after["input_i"], output_tp=after["input_tp"])

    limiter = true_peak_ceiling - LIMITER_HEADROOM_DB
    proc = subprocess.run(
        ["ffmpeg", "-nostdin", "-y", "-i", input_path,
         "-af",
         "loudnorm=I={}:TP={}:LRA=11"
         ":measured_I={}:measured_TP={}:measured_LRA={}"
         ":measured_thresh={}:offset={}:linear=true".format(
             target_lufs, limiter,
             measured["input_i"], measured["input_tp"],
             measured["input_lra"], measured["input_thresh"],
             measured["target_offset"]),
         "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate,
         output_path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=600, check=False,
    )
    if proc.returncode != 0 or not os.path.exists(output_path):
        raise RuntimeError(
            f"normalization pass failed for {input_path}: "
            f"{proc.stderr[-2000:]}")

    after = _loudnorm_measure(output_path)
    if not _is_compliant(after["input_i"], after["input_tp"],
                          target_lufs, tolerance, true_peak_ceiling):
        raise RuntimeError(
            f"normalized master still fails the delivery target: "
            f"LUFS {after['input_i']:.2f} (target {target_lufs:.1f} "
            f"+/-{tolerance:.1f}), true peak {after['input_tp']:+.2f} dBTP "
            f"(ceiling {true_peak_ceiling:.1f})")
    return MasteringResult(
        input_path=input_path, output_path=output_path,
        already_compliant=False,
        input_i=measured["input_i"], input_tp=measured["input_tp"],
        output_i=after["input_i"], output_tp=after["input_tp"])


def result_as_dict(result: MasteringResult) -> Dict[str, Any]:
    """A JSON-able account of one normalization, for step output."""
    return {
        "input_path": result.input_path,
        "output_path": result.output_path,
        "already_compliant": result.already_compliant,
        "input": {"lufs": result.input_i, "true_peak_dbtp": result.input_tp},
        "output": {"lufs": result.output_i,
                   "true_peak_dbtp": result.output_tp},
        "target": {"lufs": DELIVERY_LUFS_TARGET,
                   "tolerance": DEFAULT_LUFS_TOLERANCE,
                   "true_peak_ceiling": DEFAULT_TRUE_PEAK_CEILING_DBTP,
                   "limiter_headroom_db": LIMITER_HEADROOM_DB},
    }
