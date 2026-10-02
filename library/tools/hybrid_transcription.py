"""The on-device transcriber's TEXT through this pipeline's own aligner.

What this is
------------
Two halves that were already in the repository, joined:

  * `library/tools/heard_speech.py` - the contained third-party
    transcriber, 275-344x realtime, which writes the WORDS but whose own
    timings move more than a frame for 67.7% of them (measured), and
  * `library/tools/mfa_align.py` - the MFA forced aligner, which is
    what actually places a word boundary. Until 2026-09-24 this half
    was whisperx's wav2vec2 aligner; it left with the whisperx pin
    (huggingface-hub disjoint with gemma4-capable mlx-vlm), and MFA -
    until then the preferred aligner with wav2vec2 behind it - is now
    the only one.

Handing the transcriber's text to the aligner takes the word-start error
against today's shipped transcript from **94.1ms to 25.6ms** and the
frame-level disagreement from **67.7% to 5.0%**, for **20-41x less wall
clock end to end**. Measured 2026-09-16 over 150.7 minutes of this
project's own mic audio:
`data/vep-voz-plus-wav2vec2-hybrid/report.md` and
`data/vep-push-the-alignment-floor/report.md`.

**What that number is, stated exactly, because the headline hides it.**
Every accuracy figure above is a DISTANCE FROM WhisperX's own output,
which was the reference because it is what the pipeline consumed
until 2026-09-24. By construction the hybrid cannot beat it on that
metric: the control arm - WhisperX's own text through the same
aligner - scored 10.9ms. So the honest claim is **far faster and
close enough**, never more accurate. The control arm left with
whisperx and cannot be re-run; the figures stand as the recorded
basis, not as a live comparison.

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

The refusal is real, and it is triggered by measurement
--------------------------------------------------------
`FallbackRequired` is raised - never a degraded return - and since
2026-09-24 there is no fallback transcriber left to run: the
full-WhisperX arm went with the whisperx pin. What the refusal becomes
is the SEAM's decision (`timeline_transcript.transcribe_audio`) -
silence is returned empty and attributed, anything else propagates.
Two conditions refuse a hybrid result:

  1. **The language.** Voz writes text but not its language, so `da ear`
     identifies a speech window selected from Voz's word timings. This
     keeps silence or a music lead-in from deciding which MFA model is
     used. An uncovered language still refuses rather than getting
     forced through an English aligner.
  2. **A window that produced no words.** The aligner logs `backtrack
     failed, resorting to original` and emits an empty word list. Under
     MFA a window no pass can align is recovered on a widened span
     first, and only when that fails too does the window keep voz's
     own timings marked as unaligned - a measured alignment failure
     refuses the file only past `mfa_align.MAX_UNALIGNED_FRACTION`,
     rather than publishing a hollow transcript or dropping the file
     over one line.

An MFA word that runs over the shared clamp is corrected at the same
boundary used by temporal indexing and the reel transcript path. It no
longer rejects every other window in its batch.

Two more are not measurements of the audio but of the machine, and they
are kept apart on purpose: the transcriber is **not installed**, or it
is installed and **refused this file**. Measured on six seconds of
silence, it exits 1 with `no speech found`, and a record that called
that "unavailable" would send someone to check their PATH.

**Why the refusal is a rate, and where the threshold lives.**
A rate needs a threshold and this pipeline does not invent one - so
the threshold is stated where it is enforced, as
`mfa_align.MAX_UNALIGNED_FRACTION` (5%, with a floor of one tolerated
window: a single unaligned window never refuses a file). Zero was the
old rule because zero is what this exact configuration measured over
150.7 minutes; 2026-10-01 measured the first counterexample, one
disfluent window in 1,025 failing deterministically at corpus scale
while aligning alone, which is a rare event rather than unproved
material. What stays refused: an uncovered language, a run that heard
nothing, and a file whose unaligned windows exceed the fraction after
the widened recovery pass - genuinely broad failure, not one line.
With no fallback transcriber left, a refusal on speech-bearing audio
is a loud failure rather than a slower answer, and the downside is
bounded the same way: the file that keeps a few voz-timed windows
says so on its record (`transcriber_timed_words`).

What the hybrid CANNOT give back
---------------------------------
`avg_logprob`, the segment-level ASR confidence
`library/tools/transcript_confidence.py` exists to publish. faster-whisper
emitted it; this transcriber emits nothing like it. **The absence is stated, loudly, in
three places** - the transcript document's own `transcription` block,
`transcript_confidence.confidence_notice`, and the view a model reads -
because a null that reads as "confident" is exactly the hole that made
Reel 26's caption defect invisible.

What the wav2vec2 aligner restored instead was a per-WORD
`alignment_score`, which is a different thing and is labelled as one:
it says the characters fit the audio, not that they are the right
characters. Measured, a wrong proper noun scores HIGHER than the right
one. It was reported and nothing routed on it. MFA, the only aligner
since 2026-09-24 (`library/tools/mfa_align.py`), emits no per-word
score, so a run it timed publishes no per-row number at all -
`transcript_confidence
.ALIGNMENT_SCORE_ABSENT_MFA` is the sentence that says so, and adopting
MFA costs that column. That is the real trade and it is stated here
rather than left for whoever wonders why it emptied.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

from library.tools import heard_speech

# ── Which arm answered ───────────────────────────────────────────────

ARM_HYBRID = "hybrid"
ARM_WHISPERX = "whisperx"
"""Kept as record vocabulary for transcripts written before 2026-09-24:
no new transcript can carry the second arm, but old ones still read."""

ALIGNER_MFA = "mfa"
ALIGNER_WAV2VEC2 = "wav2vec2"
"""Which forced aligner placed a run's word boundaries.

Recorded on the hybrid's record under `aligner`, the same way `arm`
says which transcriber wrote the words: every accuracy number in this
programme is only interpretable if you know which instrument produced
it, and there were two. MFA was preferred where its environment was
present with the wav2vec2 aligner behind it; since 2026-09-24 MFA is
the only one. The second value stays readable for old records.
"""

ASR_CONFIDENCE_ABSENT = "absent_by_construction"
"""What the hybrid arm records where `avg_logprob` would be.

Not `None`, and that is the whole point. A null on a confidence field
reads as "nobody has doubted this line", which is how Reel 26's caption
defect stayed invisible. This value says the number does not exist for
these words and no stand-in was derived; the sentence a reader gets is
`transcript_confidence.CONFIDENCE_ABSENT_HYBRID`.
"""

TRANSCRIBER_TIMING_SOURCE = "transcriber"
"""The mark on a word whose timing is the transcriber's own, not MFA's.

`mfa_align` writes it when a window no pass can align keeps voz's spans
rather than refusing the file. The hybrid's record counts such words
(`transcriber_timed_words`) so a run says how much of it MFA never
placed. Spelled once, here: the writer and every reader must agree on
the value, and a bare string in two modules is how they stop agreeing.
"""


# ── The window, and the two dials that shape it ──────────────────────

SILENCE_SPLIT_SECONDS = 1.0
"""Split a sentence's words wherever the gap between two of them exceeds
this. MECHANICAL, not a creative floor (AGENTS.md 10.5): it is a bound
on where a measurement may be trusted, swept over 21 configurations."""

WINDOW_PAD_SECONDS = 0.15
"""Widen every alignment window by this at both ends."""

LANGUAGE_PROBE_SECONDS = 8.0
"""Maximum speech sample used for language identification."""

MAX_WORD_SECONDS = heard_speech.LONG_WORD_SECONDS
"""The clamp, spelled once and shared with the hearing pass: a word held
longer than this spans a silence rather than a syllable."""

MIN_WORD_SECONDS = 0.020
"""Roughly one frame at 30fps. A word shorter than this is a boundary."""


class FallbackRequired(RuntimeError):
    """The hybrid may not answer for this audio; nothing else will either.

    Carries `reason` (a stable slug a record can be grouped by) and
    `detail` (what was measured, in words). Raised rather than returning
    a degraded transcript, because a caller that cannot tell "this is
    what was said" from "this is the best I could do" will write the
    second onto a timeline. Until 2026-09-24 the caller ran full
    WhisperX on this exception; with that arm removed, the seam answers
    silence or propagates - see `timeline_transcript.transcribe_audio`.
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
    `library/tools/word_boundaries.py` applies the shared pass to the
    ALIGNER's output words, serving both `step_1_04_temporal_index` and
    `timeline_transcript.segments_for_speaker`. This one stays separate
    because its input is different (transcriber words, before any
    aligner has run) and its fallback is different (no median, no
    clamp, rather than the shared 0.3s) - folding it in would trade one
    shared clamp for one clamp with two modes, which is the same defect
    with fresher paint.
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
            "source_words": [
                {"word": span["word"], "start": span["start"],
                 "end": span["end"]}
                for span in run
            ],
        })
    return [window for window in windows if window["text"]]


def language_probe_span(windows: Sequence[dict]) -> tuple[float, float]:
    """Select a representative speech window for language detection.

    The most continuous run carries the most speech and least silence.
    Long runs are sampled around their center, away from a clip lead-in.
    """
    if not windows:
        raise ValueError("language identification needs a speech window")
    window = max(
        windows,
        key=lambda item: (item["end"] - item["start"],
                          len(item["source_words"])),
    )
    span = window["end"] - window["start"]
    duration = min(span, LANGUAGE_PROBE_SECONDS)
    start = window["start"] + max(0.0, (span - duration) / 2.0)
    return start, duration


def identify_speech_language(audio_path: str,
                             windows: Sequence[dict]
                             ) -> heard_speech.HeardLanguage:
    """Identify language from transcribed speech, not the file lead-in."""
    start, duration = language_probe_span(windows)
    try:
        return heard_speech.identify_language(
            audio_path,
            sample_start_seconds=start,
            sample_duration_seconds=duration,
        )
    except heard_speech.TranscriberUnavailable as absent:
        raise _not_here_or_refused(absent) from absent


# ── The triggers ─────────────────────────────────────────────────────

LANGUAGE_NOT_COVERED = "language_not_covered"
HEARD_NOTHING = "heard_nothing"
SEGMENT_PRODUCED_NO_WORDS = "segment_produced_no_words"
TRANSCRIBER_UNAVAILABLE = "transcriber_unavailable"
TRANSCRIBER_REFUSED = "transcriber_refused"

TRIGGERS = (
    LANGUAGE_NOT_COVERED,
    HEARD_NOTHING,
    SEGMENT_PRODUCED_NO_WORDS,
    TRANSCRIBER_UNAVAILABLE,
    TRANSCRIBER_REFUSED,
)
"""Every condition that routes this audio to the fallback, enumerated.

A trigger not in this tuple does not exist: `tests/unit/audio/test_hybrid_transcription.py`
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
    """Reject empty MFA output and sanitize stretched word boundaries."""
    from library.tools.word_boundaries import sanitize_word_boundaries

    segments = [s for s in aligned.get("segments") or [] if isinstance(s, dict)]

    for segment in segments:
        words = segment.get("words") or []
        if not words:
            raise FallbackRequired(
                SEGMENT_PRODUCED_NO_WORDS,
                f"forced alignment emitted no words for "
                f"{float(segment.get('start') or 0.0):.2f}-"
                f"{float(segment.get('end') or 0.0):.2f} "
                f"{(segment.get('text') or '').strip()[:80]!r}. This "
                f"window measured 0 failures over 150.7 minutes, so one "
                f"means this audio is not the material it was proved on.")
        timed_run = []
        for word in words:
            try:
                start = float(word["start"])
                end = float(word["end"])
            except (KeyError, TypeError, ValueError):
                if timed_run:
                    sanitize_word_boundaries(timed_run)
                    timed_run = []
                continue
            word["start"] = start
            word["end"] = end
            timed_run.append(word)
        if timed_run:
            sanitize_word_boundaries(timed_run)


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

    spoken_language = identify_speech_language(audio_path, windows)
    if not aligner.covers(spoken_language.language):
        raise FallbackRequired(
            LANGUAGE_NOT_COVERED,
            f"the speech-window identifier heard "
            f"{spoken_language.language!r} "
            f"(confidence {spoken_language.confidence!r}) and the forced "
            f"aligner has no model for it, so the hybrid cannot place a "
            f"word boundary in this audio at all.")

    aligned = aligner.align(windows, spoken_language.language, audio_path)
    _check_alignment(aligned)

    return HybridTranscription(
        aligned=aligned,
        record=hybrid_record(aligned, spoken, spoken_language, windows))


def hybrid_record(aligned: dict, spoken: heard_speech.HeardSpeech,
                  spoken_language: heard_speech.HeardLanguage,
                  windows: Sequence[dict]) -> Dict[str, Any]:
    """The account of one hybrid hearing of one audio file.

    One builder, two callers: `transcribe_and_align` (one file, one
    aligner run) and step 1.04's batched run (many files, one aligner
    run, split back per file), so a transcript measured either way is
    recorded in the same shape (`library/tools/transcript_measurement.py`).
    """
    covered = sum(window["end"] - window["start"] for window in windows)
    fallback_words = 0
    fallback_windows = 0
    for segment in aligned.get("segments") or []:
        if not isinstance(segment, dict):
            continue
        hits = sum(
            1 for word in segment.get("words") or []
            if isinstance(word, dict)
            and word.get("timing_source") == TRANSCRIBER_TIMING_SOURCE)
        fallback_words += hits
        fallback_windows += 1 if hits else 0
    return {
        "arm": ARM_HYBRID,
        # The instrument that placed these boundaries. An aligner
        # STAMPS its own document (`aligner` beside `segments`);
        # this copies the stamp onto the record so a run can be
        # read back. Anything unstamped predates the second
        # aligner, when wav2vec2 placed every boundary there was.
        "aligner": aligned.get("aligner", ALIGNER_WAV2VEC2),
        "transcriber": dict(spoken.engine),
        "language": spoken_language.as_dict(),
        "alignment_window": {
            "silence_split_seconds": SILENCE_SPLIT_SECONDS,
            "pad_seconds": WINDOW_PAD_SECONDS,
            "windows": len(windows),
            "covered_seconds": round(covered, 3),
            # Words whose timing is voz's own because no MFA pass
            # could align their window (`timing_source` on the
            # word). Zero on a clean run; counted, never hidden,
            # because an MFA-placed boundary and a voz-placed one
            # are different measurements.
            "transcriber_timed_windows": fallback_windows,
            "transcriber_timed_words": fallback_words,
        },
        "asr_confidence": ASR_CONFIDENCE_ABSENT,
    }


def fallback_record(failure: FallbackRequired,
                    attempted: Optional[str] = None) -> Dict[str, Any]:
    """The account a refusal leaves behind, so a run can be read back.

    Until 2026-09-24 this was the account a fallback run left behind;
    now it travels on the empty answer (`transcribe_audio` returns no
    segments for audio the transcriber heard nothing in) so the silence
    is attributed rather than silent."""
    record: Dict[str, Any] = {
        # No arm answered: "none" is step_1_04's own UNTRANSCRIBED
        # vocabulary, and no aligner timed anything. The trigger below
        # is the whole account of why.
        "arm": "none",
        "aligner": None,
        "fell_back_because": {"trigger": failure.reason,
                              "detail": failure.detail},
    }
    if attempted:
        record["attempted_on"] = attempted
    return record
