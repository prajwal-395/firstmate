"""What a rendered file actually SAYS, transcribed on the machine.

This module is a CONTAINMENT, and that is its whole design. Every fact
about the third-party transcriber this pipeline uses to hear a finished
render - the binary's name, its subcommand, its flags, the shape of its
JSON, its version, what "it is not installed" looks like - lives here
and nowhere else. `library/tools/reel_hearing.py` calls `transcribe`
and knows none of it.

Why a containment rather than an import
---------------------------------------
`desert-ant-cli` is version **0.1.1** and two weeks old at the time this
landed (measured 2026-09-16). It is fast enough to make hearing a reel
affordable at all - **344x realtime measured on this machine**, against
this project's recorded 1.40x for WhisperX, which is the entire reason
this pass exists - and it is young enough that the pipeline must be able
to drop it in an afternoon. The captain's ruling on that, carried into
this lane's brief: shell out to `da` the way we already shell out to
ffmpeg, behind ONE function, so ripping it out is a one-file change.

So: replacing the transcriber means rewriting `transcribe` to return the
same `HeardSpeech` and changing nothing else. `tests/test_heard_speech.py`
pins that containment by asserting no other module in the repository
names the binary.

Where its RAW timings may be used, and where they may not
---------------------------------------------------------
`transcribe` returns what the transcriber itself says, stretched words
and all. Measured on this project's own footage: 67.7% of those word
starts move more than a frame against WhisperX's forced alignment, it
stretches a sentence-final word across silence (13.44s, measured), and
it exposes no per-word confidence. None of that matters to a pass that
asks "does this render say roughly what the plan says, roughly where"
(`library/tools/reel_hearing.py`), and all of it matters to a step that
cuts on a word boundary.

So a timing decision never reads these words directly. It reads the
HYBRID - this transcriber's TEXT through the pipeline's own wav2vec2
forced aligner - which is `library/tools/hybrid_transcription.py`, and
which takes the same measurement to 25.6ms and 5.0%. This module's job
there is the same as here: it is the only place the vendor is named.

What it returns
---------------
`HeardSpeech`: the words with their reel-relative times, the full text,
and an `engine` block recording WHICH transcriber and WHICH version
answered - because a measurement that cannot say what made it cannot be
compared against a later one.

`anomalies` carries the transcriber's own structurally-detectable
failures (a word spanning a silence, a degenerate interval, a sub-frame
word). They are reported BESIDE the findings, never as findings: a
divergence caused by the transcriber mis-hearing is not a defect in the
edit, and a reader who cannot tell the two apart is being cried wolf at.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# ── The whole third-party surface, in one block ──────────────────────
#
# Everything below this comment and above `transcribe` is the vendor's,
# and nothing outside this module may name any of it.

BINARY = "da"
"""The desert-ant CLI. `da voz <file> --json --quiet` is the whole call."""

SUBCOMMAND = "voz"

EAR_SUBCOMMAND = "ear"
"""Its language identifier. `da ear <file> --json --quiet` is the call.

A SECOND subcommand, and the reason it is here rather than assumed
away: the transcriber covers 25 languages, its CLI does not enumerate
which 25, and handed one it does not cover it writes confident nonsense
rather than an error. Nothing in its transcript JSON names a language,
so the only way to ask the question is to ask this. Measured 0.22s per
file, and it reads an mp4 directly.
"""

FLAGS = ("--json", "--quiet")

VERSION_FLAG = "--version"

#: Its JSON keys, mapped once. A rename upstream is one edit here.
WORDS_KEY = "words"
WORD_TEXT_KEY = "text"
WORD_START_KEY = "start"
WORD_END_KEY = "end"
TEXT_KEY = "text"
SENTENCES_KEY = "sentences"
SENTENCE_TEXT_KEY = "text"
SENTENCE_START_KEY = "start"
SENTENCE_END_KEY = "end"
DURATION_KEY = "durationSec"
LOAD_KEY = "loadSec"
PROCESS_KEY = "processingSec"

#: `da ear --json`'s own keys.
LANGUAGE_KEY = "language"
LANGUAGE_CONFIDENCE_KEY = "confidence"

TIMEOUT_SECONDS = 600
"""Generous by two orders of magnitude.

A 45.9s reel measured 3.5s wall on this machine and 82 minutes of audio
measured 17.6s. This bound exists so a wedged subprocess cannot park a
lane, not to express an expectation about the cost.
"""

# ── Structural anomalies the transcriber is known to produce ─────────
#
# MECHANICAL bounds on a measurement, not creative floors (AGENTS.md
# 10.5): each is a value the transcriber cannot mean literally, and each
# was COUNTED on this project's own 150.7 minutes before being written
# down (33 words over 2.0s across two mic files; max 13.44s).

LONG_WORD_SECONDS = 2.0
"""A single word held longer than this spans a silence, not a syllable."""

SHORT_WORD_SECONDS = 0.04
"""Under a frame at 24fps: a boundary, not a duration."""


@dataclass(frozen=True)
class HeardWord:
    """One word the transcriber heard, in the file's own timebase."""

    word: str
    start: float
    end: float


@dataclass(frozen=True)
class HeardSentence:
    """One sentence the transcriber grouped its words into.

    Carried because the HYBRID needs it and the hearing pass does not:
    the aligner is handed one window per sentence, and the sentence is
    the only grouping the transcriber publishes. Its `start`/`end` are
    the transcriber's own and share every defect its word timings have -
    `hybrid_transcription` rebuilds the window from the WORDS and uses
    this only to say which words belong together.
    """

    text: str
    start: float
    end: float


@dataclass(frozen=True)
class HeardLanguage:
    """Which language the identifier says a file is spoken in."""

    language: str
    confidence: Optional[float] = None

    def as_dict(self) -> Dict[str, Any]:
        return {"language": self.language, "confidence": self.confidence}


@dataclass
class HeardSpeech:
    """Everything one transcription of one file says."""

    words: List[HeardWord] = field(default_factory=list)
    text: str = ""
    engine: Dict[str, Any] = field(default_factory=dict)
    anomalies: List[Dict[str, Any]] = field(default_factory=list)
    media_path: str = ""
    sentences: List[HeardSentence] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "media_path": self.media_path,
            "engine": dict(self.engine),
            "text": self.text,
            "word_count": len(self.words),
            "words": [{"word": w.word, "start": w.start, "end": w.end}
                      for w in self.words],
            "anomalies": list(self.anomalies),
        }


class TranscriberUnavailable(RuntimeError):
    """The transcriber is not installed, or refused to run.

    Raised rather than returning an empty transcription, because an
    empty hearing and a silent render are the same object otherwise -
    and "the render says nothing" is a finding this pass must never
    report when what actually happened is that nobody listened.
    """


def executable() -> Optional[str]:
    """Where the transcriber is, or None."""
    return shutil.which(BINARY)


_VERSION_CACHE: Dict[str, str] = {}


def version() -> str:
    """The transcriber's own version string, or "" when it cannot say.

    Cached per binary path: it is recorded on every hearing so a record
    can say what made it, and a batch pass over 31 reels must not pay a
    subprocess 31 times to ask a constant.
    """
    binary = executable()
    if not binary:
        return ""
    if binary in _VERSION_CACHE:
        return _VERSION_CACHE[binary]
    try:
        out = subprocess.run([binary, VERSION_FLAG], capture_output=True,
                             encoding="utf-8", check=False, timeout=30)
    except (OSError, subprocess.SubprocessError):
        return ""
    found = (out.stdout or "").strip().splitlines()[0] if out.stdout else ""
    _VERSION_CACHE[binary] = found
    return found


def available() -> tuple:
    """`(installed, detail)`. Detail says what is missing, by name."""
    binary = executable()
    if not binary:
        return False, (
            f"{BINARY!r} is not on PATH. Hearing a render needs the "
            f"on-device transcriber installed; see "
            f"library/tools/heard_speech.py for what it is and why it "
            f"is behind one function.")
    return True, f"{binary} ({version() or 'version unknown'})"


def _anomalies(words: List[HeardWord]) -> List[Dict[str, Any]]:
    """The transcriber's own structural failures, counted and named."""
    found: List[Dict[str, Any]] = []
    for word in words:
        span = word.end - word.start
        if span > LONG_WORD_SECONDS:
            found.append({"kind": "word_spans_a_silence", "word": word.word,
                          "start": word.start, "end": word.end,
                          "seconds": round(span, 3)})
        elif span <= 0.0:
            found.append({"kind": "degenerate_interval", "word": word.word,
                          "start": word.start, "end": word.end,
                          "seconds": round(span, 3)})
        elif span < SHORT_WORD_SECONDS:
            found.append({"kind": "sub_frame_word", "word": word.word,
                          "start": word.start, "end": word.end,
                          "seconds": round(span, 3)})
    return found


def read_payload(payload: Dict[str, Any], media_path: str = "") -> HeardSpeech:
    """Map the transcriber's JSON onto `HeardSpeech`.

    Split out from `transcribe` so a recorded payload can be replayed in
    a test without a subprocess and without the transcriber installed -
    the vendor's key names are still spelled exactly once, here.
    """
    words: List[HeardWord] = []
    for row in payload.get(WORDS_KEY) or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get(WORD_TEXT_KEY) or "")
        if not text.strip():
            continue
        try:
            start = float(row.get(WORD_START_KEY))
            end = float(row.get(WORD_END_KEY))
        except (TypeError, ValueError):
            continue
        words.append(HeardWord(word=text, start=start, end=end))
    words.sort(key=lambda w: (w.start, w.end))
    sentences: List[HeardSentence] = []
    for row in payload.get(SENTENCES_KEY) or []:
        if not isinstance(row, dict):
            continue
        text = str(row.get(SENTENCE_TEXT_KEY) or "")
        if not text.strip():
            continue
        try:
            start = float(row.get(SENTENCE_START_KEY))
            end = float(row.get(SENTENCE_END_KEY))
        except (TypeError, ValueError):
            continue
        sentences.append(HeardSentence(text=text, start=start, end=end))
    sentences.sort(key=lambda s: (s.start, s.end))
    return HeardSpeech(
        words=words,
        sentences=sentences,
        text=str(payload.get(TEXT_KEY) or ""),
        engine={
            "transcriber": BINARY,
            "subcommand": SUBCOMMAND,
            "version": version(),
            "duration_seconds": payload.get(DURATION_KEY),
            "load_seconds": payload.get(LOAD_KEY),
            "processing_seconds": payload.get(PROCESS_KEY),
        },
        anomalies=_anomalies(words),
        media_path=media_path,
    )


def transcribe(media_path: str,
               timeout: float = TIMEOUT_SECONDS) -> HeardSpeech:
    """Hear `media_path`. The ONE call that reaches the transcriber.

    Reads an mp4 directly - no ffmpeg pre-step - and returns words in
    the file's own timebase, which for a delivered reel is reel time.

    `encoding="utf-8"` rather than `text=True`: the locale codec decodes
    this repository's own UTF-8 wrong (AGENTS.md section 9).
    """
    binary = executable()
    if not binary:
        raise TranscriberUnavailable(available()[1])
    if not media_path or not os.path.isfile(media_path):
        raise TranscriberUnavailable(
            f"there is no file at {media_path!r} to hear. An absent "
            f"render is not a silent one.")
    try:
        out = subprocess.run([binary, SUBCOMMAND, media_path, *FLAGS],
                             capture_output=True, encoding="utf-8",
                             check=False, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        raise TranscriberUnavailable(
            f"{BINARY} {SUBCOMMAND} did not finish within {timeout}s on "
            f"{os.path.basename(media_path)}") from expired
    except OSError as failed:
        raise TranscriberUnavailable(
            f"{BINARY} {SUBCOMMAND} could not be run: {failed}") from failed
    if out.returncode != 0:
        raise TranscriberUnavailable(
            f"{BINARY} {SUBCOMMAND} exited {out.returncode}: "
            f"{(out.stderr or out.stdout or '').strip()[:400]}")
    try:
        payload = json.loads(out.stdout or "")
    except ValueError as unreadable:
        raise TranscriberUnavailable(
            f"{BINARY} {SUBCOMMAND} wrote something that is not JSON: "
            f"{unreadable}") from unreadable
    if not isinstance(payload, dict):
        raise TranscriberUnavailable(
            f"{BINARY} {SUBCOMMAND} wrote {type(payload).__name__}, not "
            f"an object")
    return read_payload(payload, media_path)


def identify_language(media_path: str,
                      timeout: float = TIMEOUT_SECONDS) -> HeardLanguage:
    """Which language `media_path` is spoken in. The ONE call to `da ear`.

    Raises `TranscriberUnavailable` for exactly the reasons `transcribe`
    does, and for the same reason: an unanswered language question and
    "this is English" must never arrive as the same object, because the
    caller uses the answer to decide whether it may transcribe at all.
    """
    binary = executable()
    if not binary:
        raise TranscriberUnavailable(available()[1])
    if not media_path or not os.path.isfile(media_path):
        raise TranscriberUnavailable(
            f"there is no file at {media_path!r} to identify.")
    try:
        out = subprocess.run([binary, EAR_SUBCOMMAND, media_path, *FLAGS],
                             capture_output=True, encoding="utf-8",
                             check=False, timeout=timeout)
    except subprocess.TimeoutExpired as expired:
        raise TranscriberUnavailable(
            f"{BINARY} {EAR_SUBCOMMAND} did not finish within {timeout}s "
            f"on {os.path.basename(media_path)}") from expired
    except OSError as failed:
        raise TranscriberUnavailable(
            f"{BINARY} {EAR_SUBCOMMAND} could not be run: {failed}") from failed
    if out.returncode != 0:
        raise TranscriberUnavailable(
            f"{BINARY} {EAR_SUBCOMMAND} exited {out.returncode}: "
            f"{(out.stderr or out.stdout or '').strip()[:400]}")
    try:
        payload = json.loads(out.stdout or "")
    except ValueError as unreadable:
        raise TranscriberUnavailable(
            f"{BINARY} {EAR_SUBCOMMAND} wrote something that is not JSON: "
            f"{unreadable}") from unreadable
    return read_language_payload(payload)


def read_language_payload(payload: Any) -> HeardLanguage:
    """Map the identifier's JSON onto `HeardLanguage`.

    Split out for the same reason `read_payload` is: a recorded payload
    replays in a test with no subprocess and no install, and the
    vendor's key names are still spelled exactly once.
    """
    if not isinstance(payload, dict):
        raise TranscriberUnavailable(
            f"{BINARY} {EAR_SUBCOMMAND} wrote {type(payload).__name__}, "
            f"not an object")
    language = str(payload.get(LANGUAGE_KEY) or "").strip()
    if not language:
        raise TranscriberUnavailable(
            f"{BINARY} {EAR_SUBCOMMAND} named no language. An unanswered "
            f"language question is not an answer of 'English'.")
    try:
        confidence = float(payload.get(LANGUAGE_CONFIDENCE_KEY))
    except (TypeError, ValueError):
        confidence = None
    return HeardLanguage(language=language, confidence=confidence)
