"""The on-device transcriber's TEXT through this pipeline's own aligner.

What this is
------------
Two halves that were already in the repository, joined:

  * `library/tools/heard_speech.py` - the contained third-party
    transcriber, 275-344x realtime, which writes the WORDS but whose own
    timings move more than a frame for 67.7% of them (measured), and
  * `whisperx.load_align_model` / `whisperx.align` - the wav2vec2 forced
    aligner the pipeline has always used, which is what actually places
    a word boundary.

Handing the transcriber's text to the aligner takes the word-start error
against today's shipped transcript from **94.1ms to 25.6ms** and the
frame-level disagreement from **67.7% to 5.0%**, for **20-41x less wall
clock end to end**. Measured 2026-09-16 over 150.7 minutes of this
project's own mic audio:
`data/vep-voz-plus-wav2vec2-hybrid/report.md` and
`data/vep-push-the-alignment-floor/report.md`.

**What that number is, stated exactly, because the headline hides it.**
Every accuracy figure above is a DISTANCE FROM WhisperX's own output,
which was the reference because it is what the pipeline consumes today.
By construction the hybrid cannot beat it on that metric: the control
arm - WhisperX's own text through the same aligner - scores 10.9ms. So
the honest claim is **far faster and close enough**, never more
accurate. That is the whole reason the fallback below is real machinery
rather than a comment.

The window, and why it is these two numbers
-------------------------------------------
The aligner is given one window per run of words, and the window is
REBUILT from the transcriber's own word timings rather than taken from
its sentence spans, because a sentence span covers the silence a
sentence-final word was stretched across. Two dials, both settled by a
21-configuration sweep over a full 82-minute file
(`data/vep-push-the-alignment-floor/evidence/P5_finalists.json`):

  * `SILENCE_SPLIT_SECONDS = 1.0` - split a sentence wherever its own
    consecutive words are further apart than this. At 2.0s four
    over-long words survive across the two files; at 1.0s none does.
  * `WINDOW_PAD_SECONDS = 0.15` - widen each window by this at both
    ends. At a pad of zero, 8 one-word backchannels (`Yeah.` `Okay.`)
    fail alignment outright and are LOST; any pad at all recovers all 8.

Together they take the frame-level error from 5.84% to 5.01% and put
**8 lost backchannels, 8 failed segments and 7 over-long words all to
zero**, for 8% more alignment time. Shorter windows are worse on both
axes monotonically and cost MORE, so there is no cap: the sweep measured
that directly and it is the opposite of what was expected.

The fallback is real, and it is triggered by measurement
--------------------------------------------------------
`FallbackRequired` is raised - never a degraded return - and the caller
runs the full WhisperX path it ran before. Three conditions, each
detectable and each with a counted reason:

  1. **The language.** The transcriber covers 25 languages, its CLI does
     not enumerate which 25, and on one it does not cover it writes
     confident nonsense rather than an error. `da ear` answers the
     language question in 0.22s, and its answer is checked against the
     languages the ALIGNER has a model for. That is a narrower test than
     the one worth having and this is the honest statement of the gap:
     **membership of the transcriber's own 25 cannot be tested, because
     the vendor does not publish the list.** What IS tested is exact -
     an uncovered language means the hybrid cannot run at all.
  2. **A window that produced no words.** The aligner logs `backtrack
     failed, resorting to original` and emits an empty word list. Exact,
     free, and measured at 0 across 150.7 minutes under this window.
  3. **A word still over the clamp after alignment.** Measured at 0 under
     this window, against 33 in the transcriber's raw output.

Two more are not measurements of the audio but of the machine, and they
are kept apart on purpose: the transcriber is **not installed**, or it
is installed and **refused this file**. Measured on six seconds of
silence, it exits 1 with `no speech found`, and a record that called
that "unavailable" would send someone to check their PATH.

**Why zero tolerance on 2 and 3 rather than a rate.** A rate needs a
threshold and this pipeline does not invent one. Zero is not invented:
it is what this exact configuration measured over 150.7 minutes, so a
single occurrence says the audio is not the material the configuration
was proved on. The downside is bounded and worth writing down - falling
back costs the hybrid's own 5% on top of today's path, never less than
today and never much more.

What the hybrid CANNOT give back
---------------------------------
`avg_logprob`, the segment-level ASR confidence
`library/tools/transcript_confidence.py` exists to publish. faster-whisper
emits it; this transcriber emits nothing like it, and `whisperx.align`
only carries what it was handed. **The absence is stated, loudly, in
three places** - the transcript document's own `transcription` block,
`transcript_confidence.confidence_notice`, and the view a model reads -
because a null that reads as "confident" is exactly the hole that made
Reel 26's caption defect invisible.

What it restores instead is a per-WORD wav2vec2 `alignment_score`, which
is a different thing and is labelled as one: it says the characters fit
the audio, not that they are the right characters. Measured, a wrong
proper noun scores HIGHER than the right one. It is reported and nothing
routes on it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from library.tools import heard_speech

# ── Which arm answered ───────────────────────────────────────────────

ARM_HYBRID = "hybrid"
ARM_WHISPERX = "whisperx"

ASR_CONFIDENCE_ABSENT = "absent_by_construction"
"""What the hybrid arm records where `avg_logprob` would be.

Not `None`, and that is the whole point. A null on a confidence field
reads as "nobody has doubted this line", which is how Reel 26's caption
defect stayed invisible. This value says the number does not exist for
these words and no stand-in was derived; the sentence a reader gets is
`transcript_confidence.CONFIDENCE_ABSENT_HYBRID`.
"""


# ── The window, and the two dials that shape it ──────────────────────

SILENCE_SPLIT_SECONDS = 1.0
"""Split a sentence's words wherever the gap between two of them exceeds
this. MECHANICAL, not a creative floor (AGENTS.md 10.5): it is a bound
on where a measurement may be trusted, swept over 21 configurations."""

WINDOW_PAD_SECONDS = 0.15
"""Widen every alignment window by this at both ends."""

MAX_WORD_SECONDS = heard_speech.LONG_WORD_SECONDS
"""The clamp, spelled once and shared with the hearing pass: a word held
longer than this spans a silence rather than a syllable."""

MIN_WORD_SECONDS = 0.020
"""Roughly one frame at 30fps. A word shorter than this is a boundary."""


class FallbackRequired(RuntimeError):
    """The hybrid may not answer for this audio; run full WhisperX.

    Carries `reason` (a stable slug a record can be grouped by) and
    `detail` (what was measured, in words). Raised rather than returning
    a degraded transcript, because a caller that cannot tell "this is
    what was said" from "this is the best I could do" will write the
    second onto a timeline.
    """

    def __init__(self, reason: str, detail: str):
        super().__init__(f"{reason}: {detail}")
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class Aligner:
    """The forced aligner, injected so this module never imports whisperx.

    `covers(language)` answers whether an alignment model exists for a
    language; `align(segments, language, audio_path)` runs it and returns
    whisperx's own `{"segments": [...]}`. `library/tools/timeline_transcript.py`
    builds the real one; a test builds a fake one and needs no GPU, no
    model download and no transcriber installed.
    """

    covers: Callable[[str], bool]
    align: Callable[[List[dict], str, str], dict]


@dataclass
class HybridTranscription:
    """One hybrid pass: the aligner's output, and the account of it."""

    aligned: Dict[str, Any] = field(default_factory=dict)
    record: Dict[str, Any] = field(default_factory=dict)


# ── Building the alignment windows ───────────────────────────────────

def clamp_heard_words(words: Sequence[heard_speech.HeardWord]) -> List[dict]:
    """The transcriber's words with their unusable spans bounded.

    Three passes, in this order: a degenerate or sub-frame span is given
    `MIN_WORD_SECONDS`, a span over `MAX_WORD_SECONDS` is cut back to the
    median span of the words that are not over it, and an overlap with
    the next word is trimmed.

    This is WINDOW CONSTRUCTION HYGIENE on the TRANSCRIBER's words, and
    it is deliberately not the other thing that looks like it:
    `step_1_04_temporal_index._sanitize_word_boundaries` applies an
    equivalent pass to the ALIGNER's output words, which is a different
    input at a different stage. Unifying the two is the standing
    `vep-port-sanitize-word-boundaries-to-timeline-transcript` task and
    is not done here; naming both in one place is what this docstring is
    for.
    """
    spans = [{"word": w.word, "start": float(w.start), "end": float(w.end)}
             for w in words]
    if not spans:
        return []

    for span in spans:
        if span["end"] - span["start"] < MIN_WORD_SECONDS:
            span["end"] = round(span["start"] + MIN_WORD_SECONDS, 3)

    ordinary = sorted(span["end"] - span["start"] for span in spans
                      if MIN_WORD_SECONDS <= span["end"] - span["start"]
                      <= MAX_WORD_SECONDS)
    if ordinary:
        median = ordinary[len(ordinary) // 2]
        for span in spans:
            if span["end"] - span["start"] > MAX_WORD_SECONDS:
                span["end"] = round(span["start"] + median, 3)

    for here, following in zip(spans, spans[1:]):
        if here["end"] > following["start"]:
            here["end"] = following["start"]
    return spans


def _sentence_word_groups(spoken: heard_speech.HeardSpeech) -> List[List[dict]]:
    """The transcriber's words, grouped the way it grouped them.

    The transcriber publishes sentences and words as two flat lists with
    no key joining them, so a sentence claims as many words as its text
    has whitespace-separated tokens - which is how the sweep that settled
    the window did it, and it reproduces the word count exactly on this
    project's material. A file with no sentences at all is ONE group:
    the split below then does the whole job on its own.
    """
    spans = clamp_heard_words(spoken.words)
    if not spans:
        return []
    if not spoken.sentences:
        return [spans]

    groups: List[List[dict]] = []
    cursor = 0
    for sentence in spoken.sentences:
        tokens = len(sentence.text.split())
        own = spans[cursor:cursor + tokens]
        cursor += tokens
        if own:
            groups.append(own)
    if cursor < len(spans):
        # The transcriber's sentences did not claim every word. The
        # remainder is kept rather than dropped: losing speech silently
        # is the defect, and the split below gives it usable windows.
        groups.append(spans[cursor:])
    return groups


def alignment_windows(spoken: heard_speech.HeardSpeech,
                      silence_split: float = SILENCE_SPLIT_SECONDS,
                      pad: float = WINDOW_PAD_SECONDS) -> List[dict]:
    """The segment list handed to the aligner, as the sweep settled it.

    One window per run of words that are no further apart than
    `silence_split`, spanning that run's own first start and last end
    plus `pad` at each side, never starting before zero.
    """
    runs: List[List[dict]] = []
    for group in _sentence_word_groups(spoken):
        run = [group[0]]
        for span in group[1:]:
            if span["start"] - run[-1]["end"] > silence_split:
                runs.append(run)
                run = [span]
            else:
                run.append(span)
        runs.append(run)

    windows = []
    for run in runs:
        if not run:
            continue
        windows.append({
            "start": max(0.0, run[0]["start"] - pad),
            "end": run[-1]["end"] + pad,
            "text": " ".join(span["word"] for span in run).strip(),
        })
    return [window for window in windows if window["text"]]


# ── The triggers ─────────────────────────────────────────────────────

LANGUAGE_NOT_COVERED = "language_not_covered"
HEARD_NOTHING = "heard_nothing"
SEGMENT_PRODUCED_NO_WORDS = "segment_produced_no_words"
WORD_OVER_THE_CLAMP = "word_over_the_clamp"
TRANSCRIBER_UNAVAILABLE = "transcriber_unavailable"
TRANSCRIBER_REFUSED = "transcriber_refused"

TRIGGERS = (
    LANGUAGE_NOT_COVERED,
    HEARD_NOTHING,
    SEGMENT_PRODUCED_NO_WORDS,
    WORD_OVER_THE_CLAMP,
    TRANSCRIBER_UNAVAILABLE,
    TRANSCRIBER_REFUSED,
)
"""Every condition that routes this audio to the fallback, enumerated.

A trigger not in this tuple does not exist: `tests/test_hybrid_transcription.py`
asserts each one can actually fire, because a trigger that cannot is
worse than no trigger (AGENTS.md 10.4)."""


def _not_here_or_refused(failure: heard_speech.TranscriberUnavailable
                         ) -> FallbackRequired:
    """Tell "the transcriber is not installed" from "it refused this".

    The containment raises one exception for both, which is right for a
    caller that only needs to stop. This caller routes on the answer,
    and the two are different facts: one is a machine that is not set up
    and the other is a file this transcriber will not read. Found by
    running the seam on six seconds of silence, where the transcriber
    exits 1 saying `no speech found` and the record read
    `transcriber_unavailable` - which says nothing true.

    Asked of `executable()` rather than of the message text, because the
    message is the vendor's and nothing outside `heard_speech` may read
    one.
    """
    if heard_speech.executable():
        return FallbackRequired(TRANSCRIBER_REFUSED, str(failure))
    return FallbackRequired(TRANSCRIBER_UNAVAILABLE, str(failure))


def _check_alignment(aligned: dict) -> None:
    """Raise `FallbackRequired` for what the alignment itself measured."""
    segments = [s for s in aligned.get("segments") or [] if isinstance(s, dict)]

    for segment in segments:
        if not (segment.get("words") or []):
            raise FallbackRequired(
                SEGMENT_PRODUCED_NO_WORDS,
                f"forced alignment emitted no words for "
                f"{float(segment.get('start') or 0.0):.2f}-"
                f"{float(segment.get('end') or 0.0):.2f} "
                f"{(segment.get('text') or '').strip()[:80]!r}. This "
                f"window measured 0 failures over 150.7 minutes, so one "
                f"means this audio is not the material it was proved on.")

    for segment in segments:
        for word in segment.get("words") or []:
            if not isinstance(word, dict):
                continue
            try:
                span = float(word["end"]) - float(word["start"])
            except (KeyError, TypeError, ValueError):
                continue
            if span > MAX_WORD_SECONDS:
                raise FallbackRequired(
                    WORD_OVER_THE_CLAMP,
                    f"{str(word.get('word') or '').strip()!r} was aligned "
                    f"across {span:.2f}s, over the {MAX_WORD_SECONDS}s "
                    f"clamp. A word held that long spans a silence; this "
                    f"window measured 0 of them over 150.7 minutes.")


# ── The pass ─────────────────────────────────────────────────────────

def available() -> tuple:
    """`(usable, detail)` - whether the hybrid's transcriber half is here."""
    return heard_speech.available()


def transcribe_and_align(audio_path: str, aligner: Aligner,
                         *, label: str = "") -> HybridTranscription:
    """Hear `audio_path` with the transcriber and time it with the aligner.

    Raises `FallbackRequired` for any of `TRIGGERS`; returns a complete
    `HybridTranscription` otherwise. Nothing in between: there is no
    partial answer this function is willing to hand back.
    """
    try:
        spoken_language = heard_speech.identify_language(audio_path)
    except heard_speech.TranscriberUnavailable as absent:
        raise _not_here_or_refused(absent) from absent

    if not aligner.covers(spoken_language.language):
        raise FallbackRequired(
            LANGUAGE_NOT_COVERED,
            f"the language identifier heard {spoken_language.language!r} "
            f"and the forced aligner has no model for it, so the hybrid "
            f"cannot place a word boundary in this audio at all.")

    try:
        spoken = heard_speech.transcribe(audio_path)
    except heard_speech.TranscriberUnavailable as absent:
        raise _not_here_or_refused(absent) from absent

    windows = alignment_windows(spoken)
    if not windows:
        raise FallbackRequired(
            HEARD_NOTHING,
            f"the transcriber returned no words for "
            f"{label or audio_path}. Silence and a failed hearing are "
            f"the same object from here, so the question is asked again "
            f"of the path that can tell them apart.")

    aligned = aligner.align(windows, spoken_language.language, audio_path)
    _check_alignment(aligned)

    covered = sum(window["end"] - window["start"] for window in windows)
    return HybridTranscription(
        aligned=aligned,
        record={
            "arm": ARM_HYBRID,
            "transcriber": dict(spoken.engine),
            "language": spoken_language.as_dict(),
            "alignment_window": {
                "silence_split_seconds": SILENCE_SPLIT_SECONDS,
                "pad_seconds": WINDOW_PAD_SECONDS,
                "windows": len(windows),
                "covered_seconds": round(covered, 3),
            },
            "asr_confidence": ASR_CONFIDENCE_ABSENT,
        },
    )


def fallback_record(failure: FallbackRequired,
                    attempted: Optional[str] = None) -> Dict[str, Any]:
    """The account a fallback leaves behind, so a run can be read back."""
    record: Dict[str, Any] = {
        "arm": ARM_WHISPERX,
        "fell_back_because": {"trigger": failure.reason,
                              "detail": failure.detail},
    }
    if attempted:
        record["attempted_on"] = attempted
    return record
