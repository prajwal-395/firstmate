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

What this module does. Measure, then apply ONE static gain and a
lookahead peak limiter run at 4x the sample rate (so it catches inter-sample
peaks), set below the gate's ceiling. The encoded output is re-measured; if
it misses either target, another pass corrects the gain and limiter from that
measured miss.

Why not loudnorm's second pass. `loudnorm ... linear=true` silently falls
back to DYNAMIC mode whenever the linear gain would push the measured true
peak over its target, which is every master this pipeline renders (every K2
eval master: -21 LUFS at about 0 dBTP). Dynamic mode rides the level over
3-second windows, so it undoes the mix it was handed: the per-block bed
levels and the word-gap recovery `otio_mix` keyframed are pulled back
together. A static gain keeps the mix; the limiter only touches peaks. Every pass starts from
the original Resolve export. A file that still fails is refused, never
handed back - fail-closed, the way the gate is. The picture stream is copied
untouched; mastering is audio-only.

Proven on the real artefact. The K2 MX3.1 eval master (-21.11 LUFS /
-0.13 dBTP, 2026-09-25) masters in one pass to -16.27 LUFS / -1.12 dBTP
against its declared -16, and to -14.49 / -1.09 at the default -14; every
block's bed level moves by the same +5.0 dB and its measured word-gap
recovery is unchanged to 0.3 dB. The loudnorm pass it replaces reported
`linear=true` on that file and ran dynamic.

Wired after Resolve export in step 6.01 and in the explicit reel-delivery
path. Both keep the Resolve render in project scratch, write the measured
delivery master to exports, and return that mastered path for the existing
export QA and sidecar verification to read. The input and output readings
stay on the render report.
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
        DEFAULT_LUFS_TARGET,
        DEFAULT_TRUE_PEAK_CEILING_DBTP,
    )
except ImportError:  # imported as a top-level module from library/tools
    from render_qa import (  # type: ignore[no-redef]
        DEFAULT_LUFS_TOLERANCE,
        DEFAULT_LUFS_TARGET,
        DEFAULT_TRUE_PEAK_CEILING_DBTP,
    )

# The default delivery target the P4 gate enforces. A request may declare a
# different integrated-loudness target; this remains the fallback when it
# does not. A test pins it against the gate's effective default, so either
# side drifting fails loudly instead of silently normalizing to a target the
# gate does not hold.
DELIVERY_LUFS_TARGET = DEFAULT_LUFS_TARGET

# Initial limiter margin below the gate's true-peak ceiling. The encoded file
# is measured after every pass because AAC can overshoot this requested value
# by more than a fixed margin; retries then use the observed overshoot.
LIMITER_HEADROOM_DB = 0.5
MAX_NORMALIZATION_ATTEMPTS = 5
# The limiter runs oversampled so it holds TRUE peak, not sample peak.
LIMITER_OVERSAMPLING = 4
NORMALIZATION_TYPE = "static_gain_true_peak_limiter"
RETRY_PEAK_MARGIN_DB = 0.5


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
    target_lufs: float = DELIVERY_LUFS_TARGET
    tolerance: float = DEFAULT_LUFS_TOLERANCE
    true_peak_ceiling: float = DEFAULT_TRUE_PEAK_CEILING_DBTP
    normalization_attempts: int = 0
    final_limiter_target: float = DEFAULT_TRUE_PEAK_CEILING_DBTP


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


def _audio_sample_rate(path: str) -> int:
    """The first audio stream's sample rate; the limiter returns to it."""
    proc = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate", "-of", "csv=p=0", path],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60, check=False,
    )
    try:
        return int(proc.stdout.strip().splitlines()[0])
    except (IndexError, ValueError) as exc:
        raise RuntimeError(
            f"could not read the audio sample rate of {path}: "
            f"{proc.stderr[-500:]}") from exc


def static_gain_filter(gain_db: float, limiter_target_dbtp: float,
                       sample_rate: int) -> str:
    """One static gain, then a true-peak-safe limiter, back at the input rate.

    `level=false` stops alimiter re-normalizing its output to the limit
    (its default), and `latency=true` removes its lookahead delay so the
    sound stays in sync with the copied picture.
    """
    limit = 10 ** (limiter_target_dbtp / 20.0)
    return (f"volume={gain_db:.2f}dB,"
            f"aresample={sample_rate * LIMITER_OVERSAMPLING},"
            f"alimiter=limit={limit:.6f}:level=false:latency=true,"
            f"aresample={sample_rate}")


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
    two-pass normalization is applied and the output is re-measured. If the
    lossy encode misses either target, the next pass adjusts its internal
    targets based on that measured miss. Every pass starts from the Resolve
    render, never a prior lossy encode. Nothing is handed back until the
    encoded output itself passes both measurements.
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
        if not _is_compliant(after["input_i"], after["input_tp"],
                              target_lufs, tolerance, true_peak_ceiling):
            raise RuntimeError(
                f"copied master no longer meets the delivery target: "
                f"LUFS {after['input_i']:.2f} (target {target_lufs:.1f} "
                f"+/-{tolerance:.1f}), true peak "
                f"{after['input_tp']:+.2f} dBTP "
                f"(ceiling {true_peak_ceiling:.1f})")
        return MasteringResult(
            input_path=input_path, output_path=output_path,
            already_compliant=True,
            input_i=measured["input_i"], input_tp=measured["input_tp"],
            output_i=after["input_i"], output_tp=after["input_tp"],
            target_lufs=target_lufs, tolerance=tolerance,
            true_peak_ceiling=true_peak_ceiling,
            normalization_attempts=0,
            final_limiter_target=true_peak_ceiling)

    limiter_target = true_peak_ceiling - LIMITER_HEADROOM_DB
    gain_db = target_lufs - measured["input_i"]
    sample_rate = _audio_sample_rate(input_path)
    after: Dict[str, float] = {}
    completed_attempts = 0
    for attempt in range(1, MAX_NORMALIZATION_ATTEMPTS + 1):
        proc = subprocess.run(
            ["ffmpeg", "-nostdin", "-y", "-i", input_path,
             "-af", static_gain_filter(gain_db, limiter_target, sample_rate),
             "-c:v", "copy", "-c:a", "aac", "-b:a", audio_bitrate,
             output_path],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=600, check=False,
        )
        if proc.returncode != 0 or not os.path.exists(output_path):
            raise RuntimeError(
                f"normalization pass {attempt} failed for {input_path}: "
                f"{proc.stderr[-2000:]}")

        # The encoded file is the delivery artifact. A filter's requested
        # target says nothing about the AAC output's measured true peak.
        after = _loudnorm_measure(output_path)
        completed_attempts = attempt
        if _is_compliant(after["input_i"], after["input_tp"],
                          target_lufs, tolerance, true_peak_ceiling):
            break

        loudness_error = target_lufs - after["input_i"]
        gain_db += max(-3.0, min(3.0, loudness_error))
        peak_overshoot = after["input_tp"] - true_peak_ceiling
        if peak_overshoot > 0:
            limiter_target -= peak_overshoot + RETRY_PEAK_MARGIN_DB
    else:
        raise RuntimeError(
            f"normalized master still fails after {completed_attempts} "
            f"measured passes: LUFS {after['input_i']:.2f} "
            f"(target {target_lufs:.1f} +/-{tolerance:.1f}), true peak "
            f"{after['input_tp']:+.2f} dBTP "
            f"(ceiling {true_peak_ceiling:.1f}); final limiter target "
            f"{limiter_target:.2f} dBTP")
    return MasteringResult(
        input_path=input_path, output_path=output_path,
        already_compliant=False,
        input_i=measured["input_i"], input_tp=measured["input_tp"],
        output_i=after["input_i"], output_tp=after["input_tp"],
        target_lufs=target_lufs, tolerance=tolerance,
        true_peak_ceiling=true_peak_ceiling,
        normalization_attempts=completed_attempts,
        final_limiter_target=limiter_target)


def result_as_dict(result: MasteringResult) -> Dict[str, Any]:
    """A JSON-able account of one normalization, for step output."""
    return {
        "input_path": result.input_path,
        "output_path": result.output_path,
        "already_compliant": result.already_compliant,
        "input": {"lufs": result.input_i, "true_peak_dbtp": result.input_tp},
        "output": {"lufs": result.output_i,
                   "true_peak_dbtp": result.output_tp},
        "target": {"lufs": result.target_lufs,
                   "tolerance": result.tolerance,
                   "true_peak_ceiling": result.true_peak_ceiling,
                   "limiter_headroom_db": LIMITER_HEADROOM_DB},
        "normalization": {
            "measured_attempts": result.normalization_attempts,
            "final_limiter_target_dbtp": result.final_limiter_target,
            "type": NORMALIZATION_TYPE,
        },
    }


def master_render_report(render_report: Dict[str, Any],
                         output_path: str,
                         target_lufs: float = DELIVERY_LUFS_TARGET,
                         true_peak_ceiling: float =
                         DEFAULT_TRUE_PEAK_CEILING_DBTP) -> Dict[str, Any]:
    """Master a Resolve render and return its report pointed at the export.

    The Resolve file stays at its scratch path as evidence. The returned
    `output_path` is the measured delivery master, with both measurements
    recorded; callers hand off this returned path.
    """
    raw_path = render_report.get("output_path")
    if not isinstance(raw_path, str) or not raw_path:
        raise RuntimeError("Resolve render report has no output_path")
    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    result = normalize_to_delivery(
        raw_path, output_path, target_lufs=target_lufs,
        true_peak_ceiling=true_peak_ceiling)
    report = dict(render_report)
    report["raw_output_path"] = raw_path
    report["output_path"] = result.output_path
    report["mastering"] = result_as_dict(result)
    report["size_bytes"] = os.stat(result.output_path).st_size
    return report
