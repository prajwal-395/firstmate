"""mix_correction.py - close the loop on a bed that masks the dialogue.

P3 (`render_qa.measure_speech_above_bed`) judges every speech-bearing
window's measured margin against the separation the plan DECLARED for it
(`music_automation[].separation_target_db`, decided per run by step 5.02
over the bed's and the speech's own measurements).  A window whose margin
misses its declared target fails the build - and nothing moves the bed
and re-checks.  A masked line ships behind a failing number.

This module closes that loop.  `compute_duck_deepening` is the measured
correction: the shortfall between the declared separation and the
measured margin, in dB, which is exactly how much deeper the bed must go
under that window's speech.  `MixCorrectionLoop` iterates measure ->
correct -> apply -> re-measure through the generic
`correction_loop.CorrectionLoop`, recording each iteration and the
verdict.

The loop is CLOSED, not simulated, in three moves.  First, the deepened
level is written onto the automation entry the mix reads
(`audio_mix.music_automation[].target_level_db`, which `otio_mix`
delivers to the timeline and the build refuses to guess at) - the
mutation lands on the manifest dict the pipeline holds, so the post-step
state dump carries it to the next build.  The declared
`separation_target_db` is never touched: the correction meets the
intent, it does not move the goalposts.  Second, the mix is re-rendered
offline with the correction applied (`rerender_remix`: the fitted bed
contribution is scaled down by the deepening and the residual - the
measured everything-else - is added back) and the re-measure runs the
PRODUCTION P3 chain on the re-rendered file, so the loop's pass and the
gate's pass are the same code on samples, not arithmetic agreeing with
itself.  Third, the captain's named music-over-speech spans are never
corrected: an overridden window is recorded as skipped with its
override's id, the way P3 records the exemption rather than silently
skipping it.
"""

from __future__ import annotations

import os
import tempfile
import wave
from typing import Any, Callable, List, Optional

from library.tools.correction_loop import CorrectionLoop


def compute_duck_deepening(margin_db: float,
                           required_margin_db: float) -> float:
    """How many dB deeper the bed must go under this window's speech.

    The shortfall between the separation the plan declared and the
    margin the render measured.  Lowering the bed's contribution by
    exactly the shortfall restores the declared separation, because the
    fit is linear in the bed.  Always non-negative: a window already
    meeting its target needs no deepening.
    """
    return max(0.0, float(required_margin_db) - float(margin_db))


class MixCorrectionLoop(CorrectionLoop):
    """Iterate one window's bed level until it meets its declared margin.

    Holds the measured margin, the required margin and the applied
    deepening, and runs the generic loop: measure the shortfall, deepen
    by it, re-measure.  The production caller supplies `measure` that
    reads the re-rendered mix; the dB arithmetic and the iteration
    discipline are the same, and are what the fixtures here pin.
    """

    def __init__(self, margin_db: float, required_margin_db: float, *,
                 max_iterations: int = 3):
        self._margin = float(margin_db)
        self._required = float(required_margin_db)
        self._deepening = 0.0
        super().__init__(
            measure=self._measure,
            correct=self._correct,
            apply=self._apply,
            max_iterations=max_iterations,
            name="dialogue_masking",
        )

    def _measure(self) -> dict:
        return {
            "passed": self._margin >= self._required,
            "margin_db": self._margin,
            "required_margin_db": self._required,
            "deepening_db": self._deepening,
        }

    def _correct(self, measurement: dict) -> Optional[dict]:
        import math

        margin = measurement["margin_db"]
        required = measurement["required_margin_db"]
        if not (math.isfinite(margin) and math.isfinite(required)):
            measurement["cannot_correct_reason"] = (
                f"a margin of {margin} dB cannot be deepened toward "
                f"{required} dB - the measurement is not a number")
            return None
        shortfall = compute_duck_deepening(margin, required)
        if shortfall <= 0:
            return None
        return {"deepen_db": shortfall}

    def _apply(self, correction: dict) -> None:
        self._deepening += correction["deepen_db"]
        self._margin += correction["deepen_db"]

    @property
    def deepening_db(self) -> float:
        """Total dB the bed has been lowered under this window's speech."""
        return self._deepening


def rerender_remix(master_path: str, music_path: str, offset_seconds: float,
                   start: float, end: float, extra_duck_db: float,
                   sample_rate: int = 48000) -> Optional[str]:
    """Re-render the mix with the bed lowered by `extra_duck_db` here.

    Decodes the master and the music file, fits the bed's contribution
    over the window with the production fit (`_fit_music`), scales that
    contribution down by the deepening, and adds the residual - the
    measured everything-else, speech included - back.  The window is
    spliced into an otherwise untouched copy of the master and written
    as a temp WAV.  Returns its path, or None when either file cannot
    be decoded or the window cannot be fitted - the loop then says so
    instead of claiming a sample re-measure it never ran.
    """
    import numpy as np

    from library.tools.render_qa import (
        _decode_mono,
        _fit_music,
    )

    if extra_duck_db <= 0:
        return None
    try:
        mix = _decode_mono(master_path, sample_rate).astype(np.float64)
        music = _decode_mono(music_path, sample_rate).astype(np.float64)
    except Exception:  # noqa: BLE001 - an undecodable file reports, never raises
        return None
    if mix.size == 0 or music.size == 0:
        return None
    a = int(start * sample_rate)
    b = int(end * sample_rate)
    ma = int((start + float(offset_seconds)) * sample_rate)
    mb = int((end + float(offset_seconds)) * sample_rate)
    window_mix = mix[a:min(b, mix.size)]
    window_music = music[ma:min(mb, music.size)]
    n = min(window_mix.size, window_music.size)
    if n <= 0:
        return None
    window_mix, window_music = window_mix[:n], window_music[:n]
    fit = _fit_music(window_mix, window_music, sample_rate)
    if fit is None:
        return None
    residual = window_mix - fit["gain"] * window_music
    scale = 10.0 ** (-float(extra_duck_db) / 20.0)
    corrected = residual + fit["gain"] * scale * window_music
    out = mix.copy()
    out[a:a + n] = corrected
    try:
        fd, path = tempfile.mkstemp(prefix="mix_rerender_", suffix=".wav")
        os.close(fd)
        peak = float(np.max(np.abs(out))) or 1.0
        pcm = np.clip(out / peak, -1.0, 1.0)
        with wave.open(path, "wb") as handle:
            handle.setnchannels(1)
            handle.setsampwidth(2)
            handle.setframerate(sample_rate)
            handle.writeframes((pcm * 32767.0).astype("<i2").tobytes())
        return path
    except Exception:  # noqa: BLE001 - an unwritable scratch dir reports, never raises
        return None


def remeasure_remix(remix_path: str, music_path: str,
                    automation: List[dict], offset_seconds: float,
                    start: float, end: float,
                    sample_rate: int = 48000,
                    spine_blocks: Optional[List[dict]] = None,
                    ) -> Optional[dict]:
    """Re-run the production P3 chain on the re-rendered mix.

    The SAME `measure_speech_above_bed` the gate runs, over the corrected
    automation, returning the corrected window's own row - so a pass here
    is the gate's pass, not the loop's arithmetic agreeing with itself.
    `spine_blocks` is threaded through untouched: without it P3 judges
    nothing (only speech-bearing blocks are judged), and an unjudged
    re-measure would fail every correction it just made.  None when the
    re-render cannot be measured.
    """
    from library.tools.render_qa import measure_speech_above_bed

    result = measure_speech_above_bed(
        remix_path, music_path, automation, float(offset_seconds),
        spine_blocks=spine_blocks, sample_rate=sample_rate)
    value = result.value
    if not isinstance(value, dict):
        return None
    for row in value.get("windows") or []:
        if not isinstance(row, dict):
            continue
        if (abs(float(row.get("timeline_start", -1.0)) - start) <= 0.001
                and abs(float(row.get("timeline_end", -1.0)) - end) <= 0.001):
            return row
    return None


def correct_masking(
        master_path: str,
        music_path: str,
        automation: List[dict],
        offset_seconds: float,
        *,
        p3_value: Optional[dict] = None,
        write_levels: Optional[Callable[[int, float], None]] = None,
        sample_rate: int = 48000,
        spine_blocks: Optional[List[dict]] = None,
        max_iterations: int = 3) -> List[dict]:
    """Close the loop on every window whose bed masks the dialogue.

    For each speech-bearing window the P3 result reports missing its
    DECLARED separation target, deepen its `target_level_db` by the
    measured shortfall, re-render the mix with the correction applied
    and re-measure on the re-rendered samples, until the window meets
    its target or the loop names why it cannot.  Returns one record per
    failing window carrying the loop's verdict, its full iteration
    history and the sample re-measure.

    `p3_value` is the `measure_speech_above_bed` VALUE dict - the
    `RenderQAResult.value` the validate bridge passes straight through -
    whose `windows` rows carry the margins.  Only windows judged
    against a declared separation target are corrected: a window on the
    clip-gain fallback has no margin anybody chose to miss, a
    music-over-speech override is the captain's explicit instruction
    and is recorded as skipped with its override's id, never corrected.

    `write_levels` is called with the automation entry's index and its
    deepened `target_level_db`; it writes the level where the mix reads
    it (`audio_mix.music_automation[].target_level_db`).  When the mix
    cannot be re-rendered the record says `remeasured: False` and keeps
    the loop verdict - an arithmetic pass is reported as one, never as
    a sample pass.
    """
    records: List[dict] = []
    windows = (p3_value or {}).get("windows") if isinstance(
        p3_value, dict) else None
    if not windows:
        return records

    for row in windows:
        if not isinstance(row, dict):
            continue
        start = float(row.get("timeline_start", 0.0))
        end = float(row.get("timeline_end", 0.0))
        if not row.get("judged") or row.get("meets_plan", True):
            continue
        if row.get("music_over_speech_override"):
            records.append({
                "window": [start, end],
                "verdict": "skipped_override",
                "passed": True,
                "detail": (f"the bed is deliberately over the voice here "
                           f"({row['music_over_speech_override']}) - the "
                           f"captain's instruction stands, the bed is not "
                           f"touched"),
                "iterations": [],
                "deepening_db": 0.0,
                "corrected_level_db": None,
                "remeasured": False,
            })
            continue
        if (row.get("required_margin_basis")
                != "declared_separation_target"):
            if row.get("music_behavior") == "silent":
                records.append({
                    "window": [start, end],
                    "verdict": "skipped_silent",
                    "passed": row.get("meets_plan", True),
                    "detail": ("a silent window is judged on the bed's "
                               "audibility, not on a declared separation - "
                               "nothing to deepen toward"),
                    "iterations": [],
                    "deepening_db": 0.0,
                    "corrected_level_db": None,
                    "remeasured": False,
                })
            continue
        required = float(row["required_margin_db"])
        margin = float(row["margin_db"])

        index = None
        for i, entry in enumerate(automation or []):
            if not isinstance(entry, dict):
                continue
            try:
                es = float(entry.get("timeline_start", -1.0))
                ee = float(entry.get("timeline_end", -1.0))
            except (TypeError, ValueError):
                continue
            if abs(es - start) <= 0.001 and abs(ee - end) <= 0.001:
                index = i
                break
        if index is None:
            continue
        try:
            old_level = float(automation[index].get("target_level_db"))
        except (TypeError, ValueError):
            continue

        # Iterate on SAMPLES, not just arithmetic: each attempt deepens
        # by the shortfall the last re-measure reported, so sample noise
        # (quantization, the splice) converges instead of failing a
        # window the arithmetic closed by hundredths of a dB.
        current_margin = margin
        deepening = 0.0
        new_level = None
        iterations: List[dict] = []
        remix_path = None
        remeasured = None
        verdict, passed, detail = "cannot_correct", False, ""
        for _attempt in range(max(1, max_iterations)):
            loop = MixCorrectionLoop(current_margin, required,
                                     max_iterations=max_iterations)
            result = loop.run()
            iterations.extend(result.as_dict()["iterations"])
            if not result.passed:
                verdict, passed = result.verdict, False
                detail = result.detail
                break
            deepening += loop.deepening_db
            new_level = old_level - deepening
            if write_levels is not None:
                write_levels(index, new_level)

            corrected = [dict(e) if isinstance(e, dict) else e
                         for e in (automation or [])]
            if isinstance(corrected[index], dict):
                corrected[index] = dict(corrected[index])
                corrected[index]["target_level_db"] = new_level

            remix_path = rerender_remix(
                master_path, music_path, offset_seconds, start, end,
                deepening, sample_rate)
            if remix_path is None:
                verdict, passed = result.verdict, True
                detail = (result.detail + " - the mix could not be "
                          "re-rendered, so this pass is the loop's "
                          "arithmetic, not samples")
                remeasured = None
                break
            remeasured = remeasure_remix(
                remix_path, music_path, corrected, offset_seconds,
                start, end, sample_rate, spine_blocks)
            iterations.append({
                "outcome": ("pass" if (remeasured or {}).get(
                    "meets_plan", False) else "corrected"),
                "measurement": {
                    "passed": bool((remeasured or {}).get(
                        "meets_plan", False)),
                    "margin_db": (remeasured or {}).get("margin_db"),
                    "required_margin_db": (
                        (remeasured or {}).get("required_margin_db")),
                    "deepening_db": deepening,
                },
                "correction": None,
            })
            if remeasured is not None and remeasured.get(
                    "meets_plan", False):
                verdict, passed = result.verdict, True
                detail = result.detail
                break
            if remeasured is None:
                verdict, passed = result.verdict, True
                detail = (result.detail + " - the re-rendered mix could "
                          "not be measured, so this pass is the loop's "
                          "arithmetic, not samples")
                break
            current_margin = float(remeasured.get("margin_db"))
        else:
            verdict, passed = "cannot_correct", False
            detail = (f"the re-rendered mix still misses the declared "
                      f"separation ({(remeasured or {}).get('margin_db')} "
                      f"dB vs {(remeasured or {}).get('required_margin_db')} "
                      f"dB) after {max_iterations} re-render(s) - the "
                      f"arithmetic pass did not survive samples")

        records.append({
            "window": [start, end],
            "verdict": verdict,
            "passed": passed,
            "detail": detail,
            "iterations": iterations,
            "deepening_db": deepening,
            "corrected_level_db": new_level,
            "remeasured": remeasured is not None,
            "remix_path": remix_path,
            "remeasured_margin_db": (remeasured or {}).get("margin_db"),
            "remeasured_required_margin_db": (
                (remeasured or {}).get("required_margin_db")),
        })
    return records
