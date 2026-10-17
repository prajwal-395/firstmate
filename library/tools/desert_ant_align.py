"""Desert Ant Align, run against the pipeline's own words.

AUD-007..009 benchmark Align against MFA as the word-boundary refiner
for the SAME transcript on the SAME audio. The comparison inputs are
Voz's (or the reference's) words; Align moves their boundaries, MFA
re-places them, and the benchmark scores both against blind agent
labels. This module runs Align and reports what it did. It compares
nothing - the comparison lives in `library/tools/asr_benchmark.py`.

Two behaviors the caller must not mistake:

- An unsupported language is a PASSTHROUGH, not an error: the SDK
  returns the input words with their original times and `refined: false`
  on each. This module reports `support.supported: False` next to those
  words instead of raising, so AUD-011's "no silent no-op" rule holds by
  construction - but the caller still has to READ the flag.
- A word past the audio edge, or one whose corrected range would end
  before it starts, keeps its input times with `refined: false`. That
  fallback checks structure, not accuracy: a plausible-but-wrong
  correction still lands, and spoken numbers are the SDK's stated weak
  case. Per-word validation against labels is the benchmark's job.

The SDK is Node-only and is never a pipeline dependency: all of it sits
behind `node library/tools/align_refine.mjs`, resolved through
`package_dir` (an `npm i @desert-ant-labs/align` tree the lane installs
in scratch, never in the repo). Without node, without the package, or
when loading fails, the call refuses with `AlignUnavailable` - Align
never silently stands in for MFA.
"""

from __future__ import annotations

import json
import os
import shutil
import struct
import subprocess
import tempfile
import wave
from pathlib import Path
from typing import Any, Dict, List, Optional

ALIGNER_NAME = "desert-ant-align"

DRIVER_FILENAME = "align_refine.mjs"

TIMEOUT_SECONDS = 600


class AlignUnavailable(RuntimeError):
    """Align cannot answer: no runtime, no package, or a failed run.

    An unanswered refinement and a perfectly-timed transcript must never
    arrive as the same object, so the refusal raises.
    """


def driver_path() -> Path:
    """The Node driver beside this module."""
    return Path(__file__).with_name(DRIVER_FILENAME)


def _node_binary(node: Optional[str] = None) -> str:
    binary = node or shutil.which("node")
    if not binary:
        raise AlignUnavailable(
            "node is not on PATH, so Desert Ant Align cannot run. "
            "Install node and `npm i @desert-ant-labs/align` in scratch.")
    return binary


def _resolve_package_dir(package_dir: Optional[str]) -> str:
    if package_dir:
        resolved = package_dir
    else:
        env_dir = os.environ.get("DESERT_ANT_ALIGN_PACKAGE")
        if env_dir:
            resolved = env_dir
        else:
            raise AlignUnavailable(
                "no Align package directory: pass package_dir or set "
                "DESERT_ANT_ALIGN_PACKAGE to an `npm i "
                "@desert-ant-labs/align` tree.")
    entry = Path(resolved) / "node.js"
    if not entry.is_file():
        raise AlignUnavailable(
            f"{resolved} is not an Align package tree (no node.js entry).")
    return resolved


def _run_driver(command: List[str], timeout: float) -> str:
    """Run the Node driver, returning its stdout verbatim.

    `languages` prints its report on stdout; `refine` prints nothing
    there and leaves the result in the `--out` file instead. Parsing
    belongs to the caller that expects JSON, so a quiet-but-successful
    refinement is never misread as an unreadable answer.
    """
    try:
        out = subprocess.run(command, capture_output=True,
                             encoding="utf-8", check=False,
                             timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        raise AlignUnavailable(
            f"align driver did not finish within {timeout}s") from expired
    except OSError as failed:
        raise AlignUnavailable(
            f"align driver could not be run: {failed}") from failed
    if out.returncode != 0:
        raise AlignUnavailable(
            f"align driver exited {out.returncode}: "
            f"{(out.stderr or out.stdout or '').strip()[:400]}")
    return out.stdout or ""


def supported_languages(*, node: Optional[str] = None,
                         package_dir: Optional[str] = None,
                         timeout: float = TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Ask the SDK which language codes it refines. No model load."""
    binary = _node_binary(node)
    resolved = _resolve_package_dir(package_dir)
    stdout = _run_driver(
        [binary, str(driver_path()), "languages",
         "--package-dir", resolved], timeout)
    try:
        report = json.loads(stdout)
    except ValueError as unreadable:
        raise AlignUnavailable(
            f"align driver wrote something that is not JSON: {unreadable}"
        ) from unreadable
    languages = report.get("languages")
    if not isinstance(languages, list) or not languages:
        raise AlignUnavailable(
            f"align driver named no languages: {report!r:.200}")
    return {"languages": [str(code) for code in languages],
            "sdk_version": str(report.get("sdkVersion") or "")}


def _read_mono_f32(wav_path: str) -> tuple:
    """Read a WAV as mono float32 samples plus its rate.

    Multi-channel input is mixed down by averaging, because the refiner
    takes one waveform and channel choice is a measurement decision the
    benchmark records, not a default this module hides.
    """
    try:
        with wave.open(wav_path, "rb") as audio:
            channels = audio.getnchannels()
            width = audio.getsampwidth()
            rate = audio.getframerate()
            frames = audio.readframes(audio.getnframes())
    except (OSError, EOFError, wave.Error) as unreadable:
        raise AlignUnavailable(
            f"could not read {os.path.basename(wav_path)}: {unreadable}"
        ) from unreadable
    if width == 2:
        fmt = "<" + "h" * (len(frames) // 2)
        raw = [value / 32768.0 for value in struct.unpack(fmt, frames)]
    elif width == 4:
        fmt = "<" + "i" * (len(frames) // 4)
        raw = [value / 2147483648.0 for value in struct.unpack(fmt, frames)]
    else:
        raise AlignUnavailable(
            f"{os.path.basename(wav_path)} has {width * 8}-bit samples; "
            f"the driver path reads 16- and 32-bit PCM.")
    if channels > 1:
        mono = [sum(raw[i:i + channels]) / channels
                for i in range(0, len(raw), channels)]
    else:
        mono = raw
    return mono, rate


def _write_f32(path: str, samples: List[float]) -> None:
    with open(path, "wb") as stream:
        stream.write(struct.pack("<" + "f" * len(samples), *samples))


def refine_words(words: List[Dict[str, Any]], wav_path: str, *,
                 language: str,
                 node: Optional[str] = None,
                 package_dir: Optional[str] = None,
                 model_dir: Optional[str] = None,
                 timeout: float = TIMEOUT_SECONDS) -> Dict[str, Any]:
    """Refine `words` against `wav_path` in `language`.

    `words` carry at least `text`, `start`, `end` seconds; every extra
    key round-trips through the SDK untouched and comes back on the
    refined row. Returns the refined rows plus a `support` block naming
    whether the language was refined or passed through, and an `sdk`
    block naming the package version that answered.
    """
    if not words:
        raise AlignUnavailable("there are no words to refine.")
    if not language or not language.strip():
        raise AlignUnavailable(
            "no language was named. Align passes unsupported languages "
            "through silently, so refining without one is refused here.")
    if not wav_path or not os.path.isfile(wav_path):
        raise AlignUnavailable(f"there is no file at {wav_path!r} to refine "
                               f"against.")
    binary = _node_binary(node)
    resolved = _resolve_package_dir(package_dir)
    samples, rate = _read_mono_f32(wav_path)
    if not samples:
        raise AlignUnavailable(
            f"{os.path.basename(wav_path)} decoded to no samples.")
    with tempfile.TemporaryDirectory(prefix="align-refine-") as scratch:
        pcm_path = os.path.join(scratch, "audio.f32")
        words_path = os.path.join(scratch, "words.json")
        out_path = os.path.join(scratch, "refined.json")
        _write_f32(pcm_path, samples)
        with open(words_path, "w", encoding="utf-8") as stream:
            json.dump(words, stream)
        command = [binary, str(driver_path()), "refine",
                   "--package-dir", resolved,
                   "--pcm", pcm_path, "--rate", str(rate),
                   "--words", words_path, "--out", out_path,
                   "--language", language.strip()]
        if model_dir:
            command += ["--model-dir", model_dir]
        _run_driver(command, timeout)
        try:
            with open(out_path, encoding="utf-8") as stream:
                result = json.load(stream)
        except (OSError, ValueError) as unreadable:
            raise AlignUnavailable(
                f"align driver left no readable result: {unreadable}"
            ) from unreadable
    refined = result.get("words")
    if not isinstance(refined, list):
        raise AlignUnavailable(
            f"align driver returned no word list: {result!r:.200}")
    supported = bool(result.get("supported"))
    return {
        "words": refined,
        "support": {"language": language.strip(), "supported": supported,
                    "passthrough": not supported},
        "sdk": {"aligner": ALIGNER_NAME,
                "sdk_version": str(result.get("sdkVersion") or ""),
                "model_dir": model_dir or "",
                "downloaded": bool(result.get("downloaded"))},
    }
