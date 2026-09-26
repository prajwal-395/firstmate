"""Montreal Forced Aligner in the `hybrid_transcription.Aligner` slot.

Why MFA is here, stated honestly because a wrong version of this will
otherwise get written into a docstring and outlive us. It was adopted
for SPEED - about 2.5x, one minute against 2.6 to time an 88-minute
episode, on under one CPU core instead of the GPU - and because the
aligner we run today places word starts late by a DIFFERENT amount per
speaker, +64.5 ms on one mic file and +35.9 ms on the other, which no
single subtracted constant can correct. It was NOT adopted for better
absolute accuracy: measured against 54 hand-placed reference onsets the
two aligners are statistically indistinguishable in error magnitude,
both raw and after de-biasing. Do not write "more accurate" anywhere.
The full evidence is `data/vep-try-mfa-instead-of-wav2vec2/report.md`.

The shape that worked, and that the numbers were measured on: one wav
+ one `.lab` per sentence window - the SAME windows the existing
aligner uses, so windowing cannot favour either side - then `mfa align
<corpus> english_us_arpa english_us_arpa <out>`, then TextGrid parsed
back to source time through the chunk-offset manifest. The acoustic
model version is PINNED (`shared_environment.MFA_ACOUSTIC_MODEL_VERSION`);
an unpinned model floats and the model is what places the boundaries.

Do not bother parallelising: MFA splits jobs by speaker, so a
single-speaker corpus gets one effective job and `--num_jobs 10`
measured identical to `--num_jobs 1`.

The two required pieces, both measured, both here
-----------------------------------------------
1. RETRY for transient empty chunks. About 0.9% of chunks produced no
   TextGrid, including mundane ones like "Absolutely.", and a rerun
   recovered 12 of 12 with sane timings. Transient, not
   text-dependent. Chunks still empty after the retry DECLINE the run
   rather than silently losing real words' timings.
2. DIGIT AND SYMBOL NORMALIZATION before alignment. Tokens like "20%"
   and "3.5" fall outside the pronunciation dictionary and are
   dropped. They are spelled out for the aligner's benefit ONLY - the
   transcript text the rest of the pipeline consumes is unchanged; the
   merge below writes the ORIGINAL tokens back onto the segments.

Also measured and worth knowing: out-of-vocabulary brand words are
FINE. ChatGPT, CRM and GEO all received timings through the
grapheme-to-phoneme fallback with no special handling. What actually
fails is a small named class - backchannel "Mm-hmm", hyphenated
compounds, possessive "AI's" - which MFA may not place. The hybrid
keeps the transcriber's per-word spans beside each alignment window, so
a dropped MFA token can keep its source timing. Only a token with no
source span is emitted untimed for the timeline transcript to
interpolate.

Where the decline goes
----------------------
Every failure of THIS aligner - environment absent, language not
covered, the `mfa` run itself failing, a chunk still empty after the
retry - raises `hybrid_transcription.FallbackRequired`, and since
2026-09-24 there is no wav2vec2 second chance behind it: that aligner
left with the whisperx pin. A lane or machine without MFA no longer
transcribes - the decline travels up to `transcribe_audio`, which
answers silence or refuses loudly. Nothing here raises anything else,
and nothing here falls through to a second transcriber: the full
WhisperX arm went the same day.

What adopting this costs
------------------------
MFA emits no per-word score, so words timed here carry no
`alignment_score` - the one per-row number the hybrid arm otherwise
publishes. That absence is recorded explicitly
(`transcript_confidence.ALIGNMENT_SCORE_ABSENT_MFA`), never left as a
blank column to be discovered. There is no second pass that would
produce the number: nothing `mfa align` writes carries one.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
import tempfile
import wave
from math import isfinite
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from library.tools import hybrid_transcription
from library.tools import shared_environment

# ── Decline reasons ────────────────────────────────────────────────
#
# These are `FallbackRequired` reasons, but they are NOT in
# `hybrid_transcription.TRIGGERS`: that tuple enumerates the
# transcriber-level conditions, and these are the aligner level below
# it. Since 2026-09-24 nothing catches them for a second attempt -
# `transcribe_audio` answers silence or propagates.

MFA_ENVIRONMENT_ABSENT = "mfa_environment_absent"
MFA_LANGUAGE_NOT_COVERED = "mfa_language_not_covered"
MFA_RUN_FAILED = "mfa_run_failed"
MFA_CHUNK_UNALIGNED = "mfa_chunk_unaligned"

MFA_COVERED_LANGUAGES = ("en", "eng", "english")
"""The only installed model is English. Anything else declines.

Tight on purpose: claiming a language MFA would align with a model
that is not downloaded is how confident nonsense gets timed.
"""

_MFA_TIMEOUT_SECONDS = 3600
"""One `mfa align` run over a speaker's whole rebuilt audio.

Measured marginal cost is ~5 ms per audio-second under ~35 s fixed
per run; an hour of audio is minutes. This is a backstop against a
hung subprocess, not a budget - and a timeout DECLINES like every
other failure here.
"""

_INTERVAL = re.compile(
    r"intervals \[\d+\]:\s+xmin = ([\d.eE+-]+)\s+xmax = ([\d.eE+-]+)"
    r'\s+text = "(.*?)"',
    re.DOTALL,
)
_TIER = re.compile(r"item \[\d+\]:")


def covers(language: str) -> bool:
    """Whether the installed MFA model can time `language`."""
    return (language or "").strip().lower() in MFA_COVERED_LANGUAGES


def align(segments: List[dict], language: str, audio_path: str) -> dict:
    """Time `segments` against `audio_path` with MFA.

    `segments` are the hybrid's alignment windows (`start`, `end`,
    `text` in audio-file time); the return is the same
    `{"segments": [...]}` shape whisperx used to return, with the
    ORIGINAL window text and tokens on it, stamped `"aligner": "mfa"`.
    Raises `FallbackRequired` for anything MFA cannot answer, which
    travels up to `transcribe_audio` - there is no second aligner.
    """
    usable, detail = shared_environment.mfa_available()
    if not usable:
        raise hybrid_transcription.FallbackRequired(MFA_ENVIRONMENT_ABSENT, detail)
    if not covers(language):
        raise hybrid_transcription.FallbackRequired(
            MFA_LANGUAGE_NOT_COVERED,
            f"MFA has no installed model for {language!r} "
            f"(installed: {', '.join(MFA_COVERED_LANGUAGES)}), so it "
            f"cannot place a word boundary in this audio.",
        )

    with tempfile.TemporaryDirectory(prefix="mfa_align_") as scratch:
        scratch_path = Path(scratch)
        audio = _read_audio(audio_path)
        corpus = scratch_path / "corpus"
        missing = _run_corpus(corpus, segments, audio, scratch_path / "out")
        if missing:
            retry = scratch_path / "retry_corpus"
            still_missing = _run_corpus(
                retry,
                [segments[index] for index in missing],
                audio,
                scratch_path / "retry_out",
                names=[f"chunk_{index:04d}" for index in missing],
            )
            if still_missing:
                texts = "; ".join(
                    repr((segments[index].get("text") or "").strip()[:60])
                    for index in still_missing[:5]
                )
                raise hybrid_transcription.FallbackRequired(
                    MFA_CHUNK_UNALIGNED,
                    f"{len(still_missing)} window(s) still produced no "
                    f"alignment after a retry ({texts}); the loss would "
                    f"be silent, so this run is refused instead.",
                )
            return _merge_all(
                segments,
                scratch_path,
                missing_names={
                    f"chunk_{index:04d}": (scratch_path / "retry_out")
                    for index in missing
                },
            )
        return _merge_all(segments, scratch_path)


def _run_corpus(
    corpus_dir: Path,
    windows: Sequence[dict],
    audio: dict,
    out_dir: Path,
    names: Optional[List[str]] = None,
) -> List[int]:
    """Write one wav+lab per window, align, return indices with nothing."""
    names = names or [f"chunk_{index:04d}" for index in range(len(windows))]
    corpus_dir.mkdir(parents=True, exist_ok=True)
    for name, window in zip(names, windows):
        _write_chunk(corpus_dir, name, window, audio)
    _run_mfa(corpus_dir, out_dir)
    return [
        index
        for index, name in enumerate(names)
        if not _chunk_words(out_dir / f"{name}.TextGrid")
    ]


# ── Audio in, chunks out ───────────────────────────────────────────


def _read_audio(audio_path: str) -> dict:
    """The whole audio file as frames, or a DECLINE, never a crash."""
    try:
        with wave.open(audio_path, "rb") as wav:
            params = wav.getparams()
            frames = wav.readframes(params.nframes)
    except (wave.Error, EOFError, OSError) as exc:
        raise hybrid_transcription.FallbackRequired(
            MFA_RUN_FAILED,
            f"could not read {audio_path} as wav ({exc}); MFA aligns "
            f"chunk files sliced from it, so this run is refused.",
        )
    return {"params": params, "frames": frames}


def _write_chunk(corpus_dir: Path, name: str, window: dict, audio: dict) -> None:
    """One window's audio plus its NORMALIZED text, beside each other."""
    params = audio["params"]
    rate = params.framerate
    total = len(audio["frames"]) // (params.sampwidth * params.nchannels)
    start = max(0, int(float(window["start"]) * rate))
    end = min(total, int(float(window["end"]) * rate))
    if end - start < int(rate * 0.1):
        end = min(total, start + int(rate * 0.1))
    width, channels = params.sampwidth, params.nchannels
    piece = audio["frames"][start * width * channels : end * width * channels]
    with wave.open(str(corpus_dir / f"{name}.wav"), "wb") as wav:
        wav.setparams(params)
        wav.writeframes(piece)
    (corpus_dir / f"{name}.lab").write_text(
        normalize_for_aligner(str(window.get("text") or "")) + "\n", encoding="utf-8"
    )


def _run_mfa(corpus_dir: Path, out_dir: Path) -> None:
    """One `mfa align` over the corpus. Failure DECLINES, never crashes."""
    binary = shared_environment.mfa_binary()
    cmd = [
        str(binary),
        "align",
        str(corpus_dir),
        shared_environment.MFA_DICTIONARY,
        shared_environment.MFA_ACOUSTIC_MODEL,
        str(out_dir),
        "--clean",
        "--overwrite",
    ]
    # The `mfa` entry point alone is not enough: alignment shells out
    # to openfst/kaldi binaries that live beside it in the env, so the
    # env's `bin` leads PATH. Measured: without this the run dies with
    # `Could not find 'fstcompile'`. An explicit `PIPELINE_MFA_BINARY`
    # elsewhere keeps working - its own directory simply leads instead.
    env = dict(os.environ)
    env["PATH"] = str(binary.parent) + os.pathsep + env.get("PATH", "")
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=_MFA_TIMEOUT_SECONDS,
            env=env,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise hybrid_transcription.FallbackRequired(
            MFA_RUN_FAILED,
            f"`mfa align` on {len(list(corpus_dir.glob('*.wav')))} "
            f"chunk(s) did not finish ({exc}); this run is refused.",
        )
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip()[-500:]
        raise hybrid_transcription.FallbackRequired(
            MFA_RUN_FAILED,
            f"`mfa align` on {len(list(corpus_dir.glob('*.wav')))} "
            f"chunk(s) exited {proc.returncode} ({tail}); "
            f"this run is refused.",
        )
    print(
        f"  aligned {len(list(corpus_dir.glob('*.wav')))} window(s) "
        f"with MFA ({shared_environment.MFA_ACOUSTIC_MODEL} "
        f"v{shared_environment.MFA_ACOUSTIC_MODEL_VERSION})",
        file=sys.stderr,
    )


# ── TextGrid back to source-time words ─────────────────────────────


def parse_textgrid(path: Path) -> List[Tuple[str, float, float]]:
    """A TextGrid's `words` tier as `(word, start, end)` in chunk time."""
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError:
        return []
    blocks = _TIER.split(text)
    words_block = next((block for block in blocks if 'name = "words"' in block), "")
    out = []
    for match in _INTERVAL.finditer(words_block):
        word = match.group(3).strip()
        if word:
            out.append((word, float(match.group(1)), float(match.group(2))))
    return out


def _chunk_words(textgrid: Path) -> List[Tuple[str, float, float]]:
    if not textgrid.is_file():
        return []
    return parse_textgrid(textgrid)


# ── Digit and symbol normalization ─────────────────────────────────
#
# Tokens like "20%" and "3.5" fall outside the pronunciation
# dictionary and are silently dropped. They are spelled out for the
# aligner's benefit ONLY: `normalization_map` keeps the original
# token beside its spelled-out phrase so the merge writes the
# ORIGINAL back. Nothing the pipeline consumes ever sees this text.

_ONES = [
    "zero",
    "one",
    "two",
    "three",
    "four",
    "five",
    "six",
    "seven",
    "eight",
    "nine",
    "ten",
    "eleven",
    "twelve",
    "thirteen",
    "fourteen",
    "fifteen",
    "sixteen",
    "seventeen",
    "eighteen",
    "nineteen",
]
_TENS = [
    "",
    "",
    "twenty",
    "thirty",
    "forty",
    "fifty",
    "sixty",
    "seventy",
    "eighty",
    "ninety",
]
_SCALES = [(10**9, "billion"), (10**6, "million"), (10**3, "thousand")]
_MAX_SPELLABLE = 10**12 - 1


def _spell_integer(value: int) -> Optional[str]:
    """`42` -> `"forty two"`. None when out of the spellable range."""
    if not 0 <= value <= _MAX_SPELLABLE:
        return None
    if value < 20:
        return _ONES[value]
    if value < 100:
        tens, rest = divmod(value, 10)
        return _TENS[tens] + ("" if rest == 0 else f" {_ONES[rest]}")
    for scale, name in _SCALES:
        if value >= scale:
            high, rest = divmod(value, scale)
            spelled = _spell_integer(high)
            if spelled is None:
                return None
            return (
                spelled + f" {name}" + ("" if rest == 0 else f" {_spell_integer(rest)}")
            )
    hundreds, rest = divmod(value, 100)
    return f"{_ONES[hundreds]} hundred" + (
        "" if rest == 0 else f" {_spell_integer(rest)}"
    )


def _spell_ordinal(value: int) -> Optional[str]:
    """`21` -> `"twenty first"`. None when out of range."""
    spelled = _spell_integer(value)
    if spelled is None:
        return None
    if 10 <= value % 100 <= 20:
        return spelled + "th"
    last = value % 10
    words = spelled.split()
    if last == 1:
        words[-1] = "first"
    elif last == 2:
        words[-1] = "second"
    elif last == 3:
        words[-1] = "third"
    else:
        return spelled + "th"
    if value >= 100 and len(words) == 1:
        # `spell_integer` never returns one word past a hundred, so
        # this is unreachable - kept rather than trusted.
        return spelled + "th"
    return " ".join(words)


def _spell_token(token: str) -> Optional[str]:
    """The spelled-out phrase for one digit/symbol token, or None."""
    core = token.strip().strip(".,?!;:\"'()[]")
    if not core:
        return None
    lowered = core.lower()
    for suffix, unit in (("%", "percent"),):
        if lowered.endswith(suffix) and len(lowered) > len(suffix):
            number = _spell_number(lowered[: -len(suffix)])
            return f"{number} {unit}" if number else None
    for symbol, unit in (("$", "dollars"),):
        if lowered.startswith(symbol) and len(lowered) > len(symbol):
            number = _spell_number(lowered[len(symbol) :])
            return f"{number} {unit}" if number else None
    ordinal = re.fullmatch(r"(\d+)(st|nd|rd|th)", lowered)
    if ordinal:
        return _spell_ordinal(int(ordinal.group(1)))
    return _spell_number(lowered)


def _spell_number(core: str) -> Optional[str]:
    """`20` -> `"twenty"`; `3.5` -> `"three point five"`; else None."""
    plain = core.replace(",", "")
    if re.fullmatch(r"\d+", plain):
        return _spell_integer(int(plain))
    decimal = re.fullmatch(r"(\d+)\.(\d+)", plain)
    if decimal:
        whole = _spell_integer(int(decimal.group(1)))
        frac = " ".join(_ONES[int(digit)] for digit in decimal.group(2))
        if whole is None:
            return None
        return f"{whole} point {frac}"
    return None


def normalization_map(text: str) -> List[Tuple[str, str]]:
    """Each whitespace token beside the phrase the aligner sees.

    A token with no digits that is not a symbol amount maps to itself;
    the transcript text is never rewritten, only accompanied.
    """
    pairs = []
    for token in text.split():
        spelled = _spell_token(token) if re.search(r"\d", token) else None
        pairs.append((token, spelled or token))
    return pairs


def normalize_for_aligner(text: str) -> str:
    """The `.lab` line: digit/symbol tokens spelled out, rest verbatim."""
    return " ".join(phrase for _, phrase in normalization_map(text))


# ── The merge: aligned words back onto the ORIGINAL tokens ─────────


def _comparable(word: str) -> str:
    return word.strip().strip(".,?!;:\"'()[]").lower()


def merge_window(
    text: str, aligned: Sequence[Tuple[str, float, float]], offset: float,
    source_words: Optional[Sequence[dict]] = None,
) -> List[dict]:
    """MFA's chunk-time words as the window's words, in ORIGINAL text.

    Each spelled-out phrase keeps a pointer to its original token; the
    alignment walks both sequences in order. If MFA dropped a token, its
    transcriber's own word span is the fallback; only a token with no
    source span is emitted untimed. MFA timings are shifted by the
    window's start in the source audio.
    """
    pairs = normalization_map(text)
    norm_words: List[Tuple[str, int]] = []
    for index, (_, phrase) in enumerate(pairs):
        for piece in phrase.split():
            norm_words.append((piece, index))

    starts: Dict[int, float] = {}
    ends: Dict[int, float] = {}
    cursor = 0
    current: Optional[int] = None
    for surface, start, end in aligned:
        wanted = _comparable(surface)
        found = next(
            (
                at
                for at in range(cursor, len(norm_words))
                if _comparable(norm_words[at][0]) == wanted
            ),
            None,
        )
        if found is None:
            # MFA timed something the transcript has no name for (a
            # tokenizer split, an `<unk>`). Its span extends the
            # current token rather than inventing a word the pipeline
            # would then caption.
            if current is not None:
                ends[current] = offset + end
            continue
        index = norm_words[found][1]
        starts.setdefault(index, offset + start)
        ends[index] = offset + end
        current = index
        cursor = found + 1

    source_spans: Dict[int, Tuple[float, float]] = {}
    if source_words is not None and len(source_words) == len(pairs):
        for index, ((token, _), source_word) in enumerate(
                zip(pairs, source_words)):
            if _comparable(str(source_word.get("word") or "")) != \
                    _comparable(token):
                continue
            try:
                start = float(source_word["start"])
                end = float(source_word["end"])
            except (KeyError, TypeError, ValueError):
                continue
            if isfinite(start) and isfinite(end) and end > start:
                source_spans[index] = (start, end)

    words = []
    for index, (token, _) in enumerate(pairs):
        if index in starts:
            words.append({"word": token, "start": starts[index], "end": ends[index]})
        elif index in source_spans:
            start, end = source_spans[index]
            words.append({"word": token, "start": start, "end": end,
                          "timed": True, "timing_source": "transcriber"})
        else:
            words.append({"word": token})
    return words


def _merge_all(
    segments: Sequence[dict],
    scratch: Path,
    missing_names: Optional[Dict[str, Path]] = None,
) -> dict:
    """Every window's TextGrid as one whisperx-shaped document."""
    missing_names = missing_names or {}
    out_segments = []
    for index, window in enumerate(segments):
        name = f"chunk_{index:04d}"
        out_dir = missing_names.get(name, scratch / "out")
        words = merge_window(
            str(window.get("text") or ""),
            _chunk_words(out_dir / f"{name}.TextGrid"),
            float(window["start"]),
            source_words=window.get("source_words"),
        )
        timed = [w for w in words if "start" in w and "end" in w]
        out_segments.append(
            {
                "start": timed[0]["start"] if timed else float(window["start"]),
                "end": timed[-1]["end"] if timed else float(window["end"]),
                "text": str(window.get("text") or ""),
                "words": words,
            }
        )
    return {"segments": out_segments, "aligner": hybrid_transcription.ALIGNER_MFA}
