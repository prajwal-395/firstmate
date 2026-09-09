"""What the timeline SAYS, per speaker, without touching Resolve.

The gap this closes
-------------------
`library/tools/timeline_ingest.py` reads which clip plays when.  It does
not read what is said, and a reel cannot be proposed from a cut list.

Why the audio is REBUILT rather than rendered
---------------------------------------------
The obvious route, and the one the captain's own one-off
`export_audio.py` took, is to solo a track in Resolve and render it.
That works, and it is rejected here for a reason that is not style:
`SetTrackEnable` is a WRITE to the captain's live project.  It is
restored afterwards, but a crash between the two leaves their timeline
muted, and "we only changed it back" is not a property this pipeline
should rely on for a real client deliverable.

Everything a render would produce is already derivable.  Each timeline
clip carries its source file and the exact seconds inside it, so a
speaker's timeline audio can be REBUILT from the source media:

    silence(gap) + span + silence(gap) + span + ...

laid end to end in timeline order.  The result is sample-for-sample what
that track plays, the transcript's timings come out in TIMELINE time with
no rebasing, and Resolve is never opened.  It also runs with Resolve
closed, which a render cannot.

Why per-SPAN caching, and not per-speaker
-----------------------------------------
The captain asked for this by name:

    "it is important for us to be able to recursively tag the positioning
     of a raw clip to the ground truth ... which also helps to be able to
     reindex specific portions of clips (whether it was because it was
     previously not indexed because the clip was not being used and now
     is or if we just need to reindex to correct a mistake)"

So an extracted span is cached under a key derived from the SPAN ITSELF -
source file, in point, out point - and not from its position in the cut.
Three consequences, all of them the point:

- a clip that moves on the timeline is not re-extracted, because the
  audio did not change,
- a clip that was unused and becomes used finds its span already cached
  if some other clip had used it,
- and re-indexing one portion invalidates exactly that span.

`AUDIO_CACHE_KEYS` is the enumeration of what the key is made of.  A key
that included the timeline position would defeat all three.

The row is not the atom - the WORD is
-------------------------------------
A Whisper row often joins two utterances that sit on two different
clips, and one that does carries no single source binding.  Every such
row used to be emitted unbound, and `reel_spine` drops those, so the
reel played the speech and nothing captioned it.

The words inside the row already answer the question the row cannot:
they carry their own aligned times and they land on real clips.  So a
row that does not sit inside one clip is SPLIT at its own word
boundaries, one bound segment per clip.  `segments_for_speaker` holds
the measurement and the reason; `clip_of_word` is the test, and it is
`attribute_to_clip`'s own containment rule with no tolerance added.

What is NOT decided here
------------------------
Nothing about which moments are interesting.  This module measures what
was said and where; choosing what matters is taste and belongs to a
model (AGENTS.md 10.5).  See `library/tools/reel_proposal.py`.

    python3 -m library.tools.timeline_transcript <project_folder>

`tests/test_timeline_transcript.py`.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence

AUDIO_CACHE_KEYS = ("source_file", "source_in", "source_out",
                    "sample_rate", "channels")
"""What an extracted span's cache key is made of. Complete, and
deliberately free of anything about WHERE the span sits in the cut."""

SAMPLE_RATE = 16000
"""What Whisper wants. Resampling later would be a second lossy step."""

CHANNELS = 1

_SILENCE_CODEC = ("-f", "lavfi", "-i",
                  f"anullsrc=r={SAMPLE_RATE}:cl=mono")

WAV_HEADER_BYTES = 44
BYTES_PER_SECOND = SAMPLE_RATE * CHANNELS * 2   # pcm_s16le


def _expected_bytes(duration: float) -> int:
    """Roughly what a wav of `duration` should weigh.

    Used as a FLOOR on extraction, because ffmpeg exits 0 having written
    a header and nothing else when asked for a span that starts past the
    end of the file. A return code is not evidence that audio came out.
    """
    return WAV_HEADER_BYTES + int(duration * BYTES_PER_SECOND)


class TimelineTranscriptError(RuntimeError):
    """The transcript could not be produced honestly."""


@dataclass(frozen=True)
class SpokenSegment:
    """One stretch of speech, in TIMELINE time, bound to its source."""

    speaker: Optional[str]
    text: str
    timeline_start: float
    timeline_end: float

    source_file: Optional[str]
    source_start: Optional[float]
    source_end: Optional[float]
    resolve_item_id: Optional[str]
    """Which timeline clip this speech came out of. None when a segment
    straddles a cut - see `attribute_to_clip`, which says so rather than
    picking one."""

    words: tuple = ()

    read_from_words: bool = False
    """This row did not sit inside one clip, so it was re-read from its
    own word timings - see `segments_for_speaker`. NAMED rather than
    folded in silently: a reader asking why a row's timings are not the
    ones Whisper emitted gets the answer on the row."""

    avg_logprob: Optional[float] = None
    """The TRANSCRIBER's own confidence in the words on this row, exactly
    as `faster_whisper` emitted it and `whisperx.align` carried it.

    `None` on a transcript written before it was kept, and on a row the
    aligner produced without one. Never derived, never defaulted:
    `library/tools/transcript_confidence.py` holds the account of what
    the absence cost and why no stand-in is computed for it."""

    def as_dict(self) -> dict:
        body = asdict(self)
        body["words"] = list(self.words)
        return body


# ── Rebuilding a speaker's timeline audio ────────────────────────────

def _run(cmd: Sequence[str], what: str) -> None:
    proc = subprocess.run(list(cmd), capture_output=True,
                          encoding="utf-8", errors="replace", check=False)
    if proc.returncode != 0:
        raise TimelineTranscriptError(
            f"{what} failed ({proc.returncode}): "
            f"{(proc.stderr or '').strip()[-400:]}")


def span_cache_key(source_file: str, source_in: float,
                   source_out: float) -> str:
    """A stable name for one extracted span.

    Milliseconds, so a float that round-trips through JSON does not
    produce a second copy of the same audio.
    """
    canonical = "|".join((
        f"source_file={os.path.abspath(source_file)}",
        f"source_in={int(round(source_in * 1000))}",
        f"source_out={int(round(source_out * 1000))}",
        f"sample_rate={SAMPLE_RATE}",
        f"channels={CHANNELS}",
    ))
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:16]
    stem = Path(source_file).stem
    return f"{stem}_{int(round(source_in * 1000))}_{digest}"


def extract_span(source_file: str, source_in: float, source_out: float,
                 cache_dir: Path) -> Path:
    """One span of source audio as a mono 16k wav, cached.

    `-ss` before `-i` seeks by keyframe on video, but audio decode is
    exact, and the explicit `-t` bounds the duration rather than trusting
    the seek to land.
    """
    cache_dir.mkdir(parents=True, exist_ok=True)
    out = cache_dir / f"{span_cache_key(source_file, source_in, source_out)}.wav"
    duration = source_out - source_in
    if duration <= 0:
        raise TimelineTranscriptError(
            f"span {source_in}..{source_out} of {source_file} is not a range")
    # A cached file is trusted only if it holds roughly the audio it is
    # supposed to. `st_size > 44` was the guard, and it let through the
    # 98-byte header-only wavs that ffmpeg writes - exiting 0 - when the
    # requested span starts past the end of the file. Ten of those
    # cached silently, shortening the rebuilt track and shifting every
    # later timing. See MIN_SPAN_BYTES.
    if out.exists() and out.stat().st_size >= _expected_bytes(duration) * 0.5:
        return out
    tmp = out.with_suffix(".partial.wav")
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
          "-ss", f"{source_in:.6f}", "-i", source_file,
          "-t", f"{duration:.6f}",
          "-vn", "-ac", str(CHANNELS), "-ar", str(SAMPLE_RATE),
          "-c:a", "pcm_s16le", str(tmp)],
         f"extracting {Path(source_file).name} {source_in:.2f}-{source_out:.2f}s")
    produced = tmp.stat().st_size if tmp.exists() else 0
    if produced < _expected_bytes(duration) * 0.5:
        tmp.unlink(missing_ok=True)
        raise TimelineTranscriptError(
            f"extracting {os.path.basename(source_file)} "
            f"{source_in:.2f}-{source_out:.2f}s produced {produced} bytes, "
            f"not the ~{_expected_bytes(duration)} a {duration:.2f}s span "
            f"needs. ffmpeg exits 0 when the span starts past the end of "
            f"the file, so a zero return code is not evidence that audio "
            f"came out. Check the range against the media - see "
            f"timeline_ingest.verify_against_media.")
    tmp.replace(out)
    return out


def _silence(duration: float, cache_dir: Path) -> Path:
    """A silence wav of `duration`, cached by rounded milliseconds."""
    millis = max(int(round(duration * 1000)), 1)
    out = cache_dir / f"_silence_{millis}.wav"
    if out.exists() and out.stat().st_size > 44:
        return out
    tmp = out.with_suffix(".partial.wav")
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
          *_SILENCE_CODEC, "-t", f"{millis / 1000:.6f}",
          "-c:a", "pcm_s16le", str(tmp)],
         f"generating {millis}ms of silence")
    tmp.replace(out)
    return out


def build_speaker_audio(clips: Sequence, out_path: Path,
                        cache_dir: Path,
                        progress=None) -> Path:
    """One speaker's whole timeline audio, rebuilt from source spans.

    `clips` are `timeline_ingest.TimelineClip`s on ONE track, so they
    cannot overlap; they are laid end to end with the timeline's own gaps
    as silence.  The result begins at timeline 0, so a transcript of it
    is already in timeline time.
    """
    ordered = sorted(clips, key=lambda c: c.timeline_start)
    if not ordered:
        raise TimelineTranscriptError("no clips to build audio from")
    # Before anything writes into it: the first piece is usually the
    # head silence, and `_silence` is not the one that creates this.
    cache_dir.mkdir(parents=True, exist_ok=True)

    pieces: List[Path] = []
    cursor = 0.0
    for index, clip in enumerate(ordered):
        gap = clip.timeline_start - cursor
        if gap > 0.0005:
            pieces.append(_silence(gap, cache_dir))
        # `clip.source_out` is the PLAYED end - in-point plus timeline
        # duration - so a span is exactly as long as the slot it fills
        # and the track telescopes to the timeline exactly. Extracting
        # GetSourceEndFrame()'s length instead drifts a frame on every
        # clip where the two disagree, which is 50 of 167 here.
        pieces.append(extract_span(clip.source_file, clip.source_in,
                                   clip.source_out, cache_dir))
        cursor = clip.timeline_end
        if progress:
            progress(index + 1, len(ordered))

    listing = cache_dir / f"{out_path.stem}_concat.txt"
    listing.write_text(
        "".join(f"file '{p.as_posix()}'\n" for p in pieces), encoding="utf-8")

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _run(["ffmpeg", "-nostdin", "-y", "-loglevel", "error",
          "-f", "concat", "-safe", "0", "-i", str(listing),
          "-c:a", "pcm_s16le", "-ar", str(SAMPLE_RATE), "-ac", str(CHANNELS),
          str(out_path)],
         f"concatenating {len(pieces)} pieces into {out_path.name}")
    return out_path


# ── Binding speech back to the footage it came from ──────────────────

def attribute_to_clip(start: float, end: float, clips: Sequence):
    """Which timeline clip a stretch of speech sits in, or None.

    None when the stretch straddles a cut.  Saying "I cannot tell" is
    the honest answer: a segment spanning two clips came from two places
    in the raw footage, and naming either one would be a false ground
    truth.  Callers report the None rather than guessing.
    """
    midpoint = (start + end) / 2.0
    for clip in clips:
        if clip.timeline_start <= midpoint <= clip.timeline_end:
            if clip.timeline_start <= start and end <= clip.timeline_end:
                return clip
            return None
    return None


def clip_of_word(word: dict, clips: Sequence):
    """The clip one WORD sits on, or None.

    The same containment question `attribute_to_clip` asks of a whole
    stretch, asked of the atom that carries its own measured time.  No
    tolerance is added and none is needed: `build_speaker_audio` lays
    this speaker's clips end to end with the timeline's own gaps as
    SILENCE, so a word whose midpoint falls between two clips was
    aligned against silence and sits on no clip at all.
    """
    midpoint = (float(word["start"]) + float(word["end"])) / 2.0
    for clip in clips:
        if clip.timeline_start <= midpoint <= clip.timeline_end:
            return clip
    return None


def clip_runs(words: Sequence[dict], clips: Sequence) -> list[tuple]:
    """A row's words grouped into maximal runs that share one clip.

    Returns `[(clip_or_None, [word, ...]), ...]` in word order.  Two
    runs mean the row crosses a cut BETWEEN two of its own words, which
    is the case `attribute_to_clip` can only answer with None.
    """
    runs: list[tuple] = []
    for word in words:
        clip = clip_of_word(word, clips)
        if runs and runs[-1][0] is clip:
            runs[-1][1].append(word)
            continue
        runs.append((clip, [word]))
    return runs


def to_source_time(clip, timeline_time: float) -> float:
    """A timeline second, expressed in the SOURCE file's own timebase."""
    return clip.source_in + (timeline_time - clip.timeline_start)


# ── Transcribing ─────────────────────────────────────────────────────

def transcribe_audio(audio_path: Path, model_size: str = "large-v3",
                     beam_size: int = 5, initial_prompt: str | None = None,
                     hotwords: str | None = None) -> dict:
    """One speaker's rebuilt timeline audio, transcribed and aligned.

    TRANSCRIPTION goes through `faster_whisper` directly, and ALIGNMENT
    through `whisperx.align`.  Step 1.04 calls `whisperx.load_model`,
    which does both at once, and that call is BROKEN in this
    environment - measured 2026-09-04, two independent version skews:

        whisperx.load_model(...)
          -> TranscriptionOptions.__init__() missing 2 required
             positional arguments: 'multilingual' and 'hotwords'
             (whisperx builds its option dict for an older faster_whisper)

        and once that is supplied via asr_options=:
          -> Inference.__init__() got an unexpected keyword argument
             'use_auth_token'
             (whisperx's VAD passes an argument this pyannote.audio
             dropped)

    Only `load_model` is affected.  `load_align_model` and `align` touch
    neither faster_whisper's options nor pyannote, so the alignment half
    - which is the part worth having, and the reason this repo uses
    WhisperX at all - still works, on MPS where available.
    `faster_whisper`'s own `vad_filter` replaces the VAD chunking that
    `load_model` would have provided.

    **Step 1.04 makes the identical `load_model` call and will fail the
    same way.**  It is not changed here: this task's scope excludes
    indexing the raw footage, and rewiring a step this cannot exercise
    would be worse than reporting it.
    """
    try:
        import torchaudio
        if not hasattr(torchaudio, "set_audio_backend"):
            torchaudio.set_audio_backend = lambda x: None
        if not hasattr(torchaudio, "get_audio_backend"):
            torchaudio.get_audio_backend = lambda: "soundfile"
    except ImportError:
        pass

    import whisperx
    from faster_whisper import WhisperModel

    # Lazy, like every other `library.*` import in this module: it is
    # also a command-line entry point, and a top-level import puts the
    # repository root on the critical path of `--help`.
    from library.tools.transcript_confidence import AVG_LOGPROB

    print(f"  transcribing with faster-whisper ({model_size}, int8, cpu)...",
          file=sys.stderr)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    # Decode-time bias toward the project's recorded corrections
    # (`library/tools/transcript_corrections.py`). Both are HINTS the
    # decoder may honour, not guarantees - the deterministic
    # post-transcription pass in `build_and_transcribe` is the
    # guarantee, and it runs whether or not biasing held. An empty
    # prompt is never sent: a call with nothing to ask is not made.
    transcribe_kwargs = {"beam_size": beam_size, "vad_filter": True,
                         "word_timestamps": False}
    if initial_prompt:
        transcribe_kwargs["initial_prompt"] = initial_prompt
    if hotwords:
        transcribe_kwargs["hotwords"] = hotwords
    raw_segments, info = model.transcribe(
        str(audio_path), **transcribe_kwargs)
    # `avg_logprob` is the transcriber's own confidence in the words it
    # just wrote, and it was being thrown away HERE - rebuilt out of
    # three keys and handed to the aligner without it. It cost a reel 15
    # seconds: a selector run hit a line that reads as Hangul in the
    # middle of an English sentence, could not tell a garbled READING
    # from garbled AUDIO, and ended the reel early to keep the damaged
    # line outside the span. `library/tools/transcript_confidence.py`
    # holds that account and why this is the one number carried.
    #
    # `whisperx.align` copies it onto every aligned segment and through
    # the groupby that merges subsegments, so it survives to
    # `segments_for_speaker` with nothing re-attaching it.
    segments = [{"start": s.start, "end": s.end, "text": s.text,
                 AVG_LOGPROB: s.avg_logprob}
                for s in raw_segments]
    print(f"  {len(segments)} segments, language {info.language}",
          file=sys.stderr)
    if not segments:
        return {"segments": [], "language": info.language}

    import torch
    device = "mps" if torch.backends.mps.is_available() else "cpu"
    print(f"  aligning word timings on {device}...", file=sys.stderr)
    audio = whisperx.load_audio(str(audio_path))
    align_model, metadata = whisperx.load_align_model(
        language_code=info.language, device=device)
    try:
        return whisperx.align(segments, align_model, metadata, audio, device,
                              return_char_alignments=False)
    except Exception as exc:                # pragma: no cover - device path
        print(f"  alignment on {device} failed ({exc}); retrying on cpu",
              file=sys.stderr)
        align_model, metadata = whisperx.load_align_model(
            language_code=info.language, device="cpu")
        return whisperx.align(segments, align_model, metadata, audio, "cpu",
                              return_char_alignments=False)


def interpolate_untimed_words(words: List[dict]) -> List[dict]:
    """Give a word with no timing one from its neighbours.

    WhisperX returns words it could not align with no `start`/`end`.
    Dropping them - which the project's one-off
    `generate_podcast_subtitles.py` did silently - loses real speech from
    the caption.  `place_subtitles.py` interpolated instead, and that is
    the behaviour worth keeping.
    """
    out: List[dict] = []
    for index, word in enumerate(words):
        if "start" in word and "end" in word:
            out.append({"word": word.get("word", ""),
                        "start": float(word["start"]),
                        "end": float(word["end"]),
                        "timed": True})
            continue
        previous = next((words[j]["end"] for j in range(index - 1, -1, -1)
                         if "end" in words[j]), None)
        following = next((words[j]["start"] for j in range(index + 1, len(words))
                          if "start" in words[j]), None)
        if previous is None or following is None:
            continue
        start = previous + 0.05
        end = min(start + max((following - previous) * 0.4, 0.15),
                  max(following - 0.02, start + 0.02))
        out.append({"word": word.get("word", ""), "start": start,
                    "end": end, "timed": False})
    return out


def _bound(speaker, text, clip, start, end, words,
           read_from_words=False, avg_logprob=None) -> SpokenSegment:
    return SpokenSegment(
        speaker=speaker,
        text=text,
        timeline_start=start,
        timeline_end=end,
        source_file=clip.source_file if clip else None,
        source_start=to_source_time(clip, start) if clip else None,
        source_end=to_source_time(clip, end) if clip else None,
        resolve_item_id=clip.resolve_item_id if clip else None,
        words=tuple(words),
        read_from_words=read_from_words,
        avg_logprob=avg_logprob,
    )


def segments_for_speaker(aligned: dict, speaker: Optional[str],
                         clips: Sequence) -> List[SpokenSegment]:
    """WhisperX output as `SpokenSegment`s, bound to the footage.

    A row that sits wholly inside one clip is emitted exactly as it
    arrived.  A row that does NOT is re-read from its own WORDS, and
    that is the whole of the difference.

    Why, measured on the captain's field test 2026-09-06
    ----------------------------------------------------
    61 of 875 rows carried no binding, and `reel_spine` drops those - so
    the reel plays the speech and nothing writes it.  Reel 05 loses
    about 9 seconds of Craig that way, on rows 204 and 206.

    Read the artefact and the rows are not what "straddles a cut"
    suggests.  Row 204 runs 609.38..615.34 across a cut at 614.0/614.8,
    and **no word of it crosses that cut**: eighteen words end at
    613.875 on the clip before it and three begin at 614.818 on the clip
    after.  Row 206 runs 615.54..636.20 over a 14.4-second stretch where
    Craig has no clip at all, and carries no word in the middle of it -
    two words at 615.5 on one clip, sixteen from 631.1 on another.  What
    the row envelope spans is Whisper joining two utterances; what the
    WORDS span is real speech on real clips.

    Across all 61 rows: 977 words, of which **958 sit squarely on a clip
    of their own speaker** and 19 do not.  Nine of the 19 are single
    words the aligner stretched across the silence - one 'starting' runs
    62.6 seconds - and it is exactly that stretch that puts their
    midpoint in the gap.  They stay unbound, which is the honest answer
    and the same one as before.

    So the row is not the atom.  The WORD is, it carries its own
    measured time, and asking the clip question of it recovers speech
    that was being thrown away.  Nothing here widens what counts as a
    match: `clip_of_word` is `attribute_to_clip`'s own containment test
    with no tolerance added.

    A row that already binds is left ALONE
    --------------------------------------
    Deliberately, and not for caution.  Measured over the same 875
    rows, re-reading the 814 bound ones from their words would move
    exactly 4 of them - and in all 4 the only word that moves is the
    FIRST, by 0.05-0.12s, onto the clip that ends where this one starts.
    That buys nothing and costs a one-word caption card on reels the
    captain has already approved.  `attribute_to_clip` returning a clip
    means the question is already answered; only the None needs asking
    again.
    """
    from library.tools.transcript_confidence import line_confidence

    out: List[SpokenSegment] = []
    for segment in aligned.get("segments", []):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        words = interpolate_untimed_words(segment.get("words") or [])
        confidence = line_confidence(segment)

        clip = attribute_to_clip(start, end, clips)
        if clip is not None or not words:
            out.append(_bound(speaker, text, clip, start, end, words,
                              avg_logprob=confidence))
            continue

        out.extend(read_from_words(speaker, text, words, clips,
                                   avg_logprob=confidence))
    return out


def read_from_words(speaker, text: str, words: Sequence[dict],
                    clips: Sequence, avg_logprob=None) -> List[SpokenSegment]:
    """One unbound row, split at its own word boundaries.

    Factored out of `segments_for_speaker` so `rebind_document` can apply
    the SAME rule to a transcript already on disk rather than a second
    implementation of it.  There is one re-read in this module and both
    callers reach it.

    Every piece keeps the parent row's `avg_logprob`, because it is the
    transcriber's confidence in the DECODE that produced these words and
    the split does not re-hear any of them.  A reader who wants to know
    the row was cut up is told by `read_from_words` on the same row.
    """
    out: list[SpokenSegment] = []
    for run_clip, run_words in clip_runs(words, clips):
        # Every timing on a re-read row comes from the words, because
        # the row's own envelope is the thing that was wrong.  A run
        # that is the whole row keeps the row's text verbatim; a run
        # that is part of one is named by the words it carries, which
        # reproduces the row text on 860 of the 862 rows that have
        # words at all.
        whole = len(run_words) == len(words)
        out.append(_bound(
            speaker,
            text if whole else " ".join(w["word"] for w in run_words).strip(),
            run_clip,
            float(run_words[0]["start"]),
            float(run_words[-1]["end"]),
            run_words,
            read_from_words=True,
            avg_logprob=avg_logprob))
    return out


def rebind_document(document: dict, snapshot,
                    project_folder: str | None = None) -> dict:
    """Re-ask the clip question of a transcript ALREADY ON DISK.

    The words are not re-heard.  Every word timing in the returned
    document is the one WhisperX produced, byte for byte; the only thing
    that changes is which clip a row is bound to, and a row that already
    binds is not touched at all.

    Why this exists, measured 2026-09-06
    ------------------------------------
    The word-level re-read landed in `segments_for_speaker`, which runs
    when a transcript is PRODUCED.  The field test's transcript was
    written 29.6 hours earlier, and `reel_build` reads that file rather
    than re-transcribing, so reel 03 was built with the old binding and
    played 1.5 seconds of Craig - "search didn't change" and "the links
    in the bio" - with nothing written over them.  A file on disk is not
    a measurement (AGENTS.md 10.3), and a fix that only reaches new
    transcripts does not reach any project that already has one.

    Re-transcribing would reach it and costs a WhisperX pass over every
    speaker, and it would move words on rows the captain has already
    approved captions for.  Nothing about the repair needs new words:
    the 977 words of the 61 unbound rows are already in the file, and
    `clip_of_word` is a containment test against the timeline's own clip
    list.  So this re-runs the binding and nothing else.

    The rule is `segments_for_speaker`'s, unchanged and not restated: a
    row with a `resolve_item_id` is left ALONE, a row with no words is
    left alone, and anything else goes through `read_from_words`.
    """
    by_speaker: dict[Optional[str], list] = {}
    for clip in snapshot.picture_clips():
        by_speaker.setdefault(clip.speaker, []).append(clip)

    per_speaker: dict[Optional[str], list[SpokenSegment]] = {}
    for row in document.get("segments", []):
        segment = SpokenSegment(**{**row, "words": tuple(row.get("words") or ())})
        clips = by_speaker.get(segment.speaker)
        if (segment.resolve_item_id is not None or not segment.words
                or not clips):
            per_speaker.setdefault(segment.speaker, []).append(segment)
            continue
        per_speaker.setdefault(segment.speaker, []).extend(
            read_from_words(segment.speaker, segment.text,
                            list(segment.words), clips,
                            avg_logprob=segment.avg_logprob))

    rebound = transcript_document(snapshot, merge_speakers(per_speaker))
    # A rebound transcript SAYS it is one.  The words came from the run
    # named here; the binding came from this machine's clip list, and a
    # reader comparing two transcripts of one timeline needs to know
    # which half of each was produced when.
    rebound["measurement"] = (
        document.get("measurement", "") + " Clip binding RE-DERIVED from "
        "the timeline's own clip list without re-hearing the audio "
        "(`timeline_transcript.rebind_document`); every word timing is "
        "the one the transcribe pass produced.")
    # A transcript already on disk gets the same guarantee a fresh one
    # gets: corrections recorded since it was written apply here, so a
    # rebind - not a re-transcription - is enough to carry them.
    if project_folder:
        from library.tools import transcript_corrections
        transcript_corrections.apply_to_document(rebound, project_folder)
    return rebound


def merge_speakers(per_speaker: Dict[Optional[str], List[SpokenSegment]]
                   ) -> List[SpokenSegment]:
    """Every speaker's segments in one timeline-ordered conversation."""
    everything: List[SpokenSegment] = []
    for segments in per_speaker.values():
        everything.extend(segments)
    everything.sort(key=lambda s: (s.timeline_start, s.timeline_end))
    return everything


def transcript_document(snapshot, merged: List[SpokenSegment]) -> dict:
    """The whole transcript, as written to disk."""
    unbound = sum(1 for s in merged if s.resolve_item_id is None)
    read_from_words = sum(1 for s in merged if s.read_from_words)
    rebound = sum(1 for s in merged
                  if s.read_from_words and s.resolve_item_id is not None)
    with_confidence = sum(1 for s in merged if s.avg_logprob is not None)
    # The speakers who ACTUALLY SPEAK, from the segments themselves -
    # not `snapshot.speakers()`, which is the TRACK ROSTER and lists
    # `Akshita CH1` and `Craig CH1` beside the two real people. Four keys
    # where two belong is how a per-speaker styling bug gets in: the
    # style lookup keys off speaker identity, and a name nothing speaks
    # under is a style nobody ever sees applied.
    speaking = []
    for segment in merged:
        if segment.speaker and segment.speaker not in speaking:
            speaking.append(segment.speaker)
    video_ranges = [(c.timeline_start, c.timeline_end) for c in snapshot.clips if c.track_type == "video"]
    video_ranges.sort()
    merged_video = []
    for s, e in video_ranges:
        if not merged_video:
            merged_video.append([s, e])
        else:
            if s <= merged_video[-1][1]:
                merged_video[-1][1] = max(merged_video[-1][1], e)
            else:
                merged_video.append([s, e])
                
    picture_holes = []
    cursor = 0.0
    for s, e in merged_video:
        if s > cursor + 0.04:
            picture_holes.append([round(cursor, 3), round(s, 3)])
        cursor = max(cursor, e)
    
    return {
        "derived_from": {
            "project": snapshot.project_name,
            "timeline": snapshot.timeline_name,
            "fps": snapshot.fps,
            "duration_seconds": snapshot.duration,
            "picture_holes": picture_holes,
        },
        "measurement": (
            "Speech transcribed by WhisperX from audio REBUILT out of the "
            "source spans the timeline plays. Resolve was not opened and "
            "nothing was rendered. Times are timeline time."),
        "speakers": speaking,
        "segment_count": len(merged),
        "segments_straddling_a_cut": unbound,
        # A row whose envelope crossed a cut is re-read from its own
        # words (`segments_for_speaker`); these two say how much of the
        # transcript that produced and how much of it came back BOUND.
        # Without them the repair is invisible in its own output.
        "segments_read_from_words": read_from_words,
        "segments_rebound_from_words": rebound,
        # How many rows carry the TRANSCRIBER's own confidence in their
        # words. Said on the document rather than left to be counted,
        # because zero and "nobody looked" read the same from outside -
        # and zero is what every transcript written before #587 holds.
        "segments_with_asr_confidence": with_confidence,
        "segments": [s.as_dict() for s in merged],
    }


# ── Where it lands ───────────────────────────────────────────────────

TRANSCRIPT_FILENAME = "transcript.json"
SCRATCH_SUBDIR = "timeline_transcript"


def transcript_path(project_folder) -> Path:
    """Where this module's output lands, spelled ONCE.

    The same shape as `reel_proposal.PROPOSAL_FILENAME`, and for the same
    reason: the writer below, `run_pipeline.gather_step_inputs`,
    `reel_proposal.write_from_step_output`, `reel_build` twice and the
    requirement that refuses when it is absent all name one file, and
    five of the six composed the path themselves.  A reader who wants to
    know where the transcript lives should find one answer.
    """
    from library.tools.project_layout import Area, ProjectLayout

    return Path(ProjectLayout(str(project_folder)).read_path(
        Area.SCRATCH, SCRATCH_SUBDIR, TRANSCRIPT_FILENAME))


# ── CLI ──────────────────────────────────────────────────────────────

def build_and_transcribe(project_folder: str, snapshot,
                         model_size: str = "large-v3",
                         only_speakers: Optional[Iterable[str]] = None) -> dict:
    """The whole job: rebuild each speaker's audio, transcribe, bind back."""
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    scratch = Path(layout.write_dir(Area.SCRATCH)) / SCRATCH_SUBDIR
    cache_dir = scratch / "spans"
    scratch.mkdir(parents=True, exist_ok=True)

    by_speaker: dict[Optional[str], list] = {}
    for clip in snapshot.picture_clips():
        by_speaker.setdefault(clip.speaker, []).append(clip)

    wanted = set(only_speakers) if only_speakers else None
    per_speaker: Dict[Optional[str], List[SpokenSegment]] = {}

    # Corrections recorded against this project bias the decoder AND
    # are enforced after it (`library/tools/transcript_corrections.py`:
    # biasing is a hint, the post pass is the guarantee).
    from library.tools import transcript_corrections
    initial_prompt, hotwords = transcript_corrections.bias_strings(
        project_folder)

    for speaker, clips in by_speaker.items():
        if wanted and speaker not in wanted:
            continue
        label = (speaker or "unnamed").lower().replace(" ", "_")
        audio_path = scratch / f"{label}.wav"
        print(f"\n[{speaker}] rebuilding {len(clips)} spans -> {audio_path.name}",
              file=sys.stderr)

        def _progress(done, total, _label=label):
            if done % 20 == 0 or done == total:
                print(f"    {_label}: {done}/{total} spans", file=sys.stderr)

        build_speaker_audio(clips, audio_path, cache_dir, progress=_progress)
        aligned = transcribe_audio(audio_path, model_size=model_size,
                                   initial_prompt=initial_prompt or None,
                                   hotwords=hotwords or None)
        segments = segments_for_speaker(aligned, speaker, clips)
        print(f"[{speaker}] {len(segments)} spoken segments", file=sys.stderr)
        per_speaker[speaker] = segments

    document = transcript_document(snapshot, merge_speakers(per_speaker))
    # The guarantee half: respell at the root, before anything
    # downstream reads it. Downstream consumers need no changes - they
    # read corrected words because corrected words are what is here.
    correction_report = transcript_corrections.apply_to_document(
        document, project_folder)
    if correction_report["replacements"]:
        print(f"  transcript corrections applied: "
              f"{correction_report['replacements']} replacement(s) in "
              f"{correction_report['segments_touched']} segment(s) "
              f"{[a['id'] for a in correction_report['applied'] if a['replacements']]}",
              file=sys.stderr)
    return document


def main(argv=None) -> int:
    import argparse
    from library.tools import timeline_ingest

    parser = argparse.ArgumentParser(
        description="Transcribe what a Resolve timeline says, per speaker.")
    parser.add_argument("project_folder")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--speaker", action="append", default=[],
                        help="only this speaker; repeatable")
    parser.add_argument("--out", default="",
                        help="where to write; default is the project's scratch")
    parser.add_argument(
        "--rebind", action="store_true",
        help="do not transcribe: re-derive the CLIP BINDING of the "
             "transcript already on disk, keeping every word timing it "
             "carries. For a project transcribed before the word-level "
             "re-read landed - see `rebind_document`")
    args = parser.parse_args(argv)

    project_name, timeline_name = timeline_ingest.resolve_binding(args.project_folder)
    snapshot, _p, _t = timeline_ingest.connect(project_name, timeline_name)
    print(f"{snapshot.timeline_name!r}: {len(snapshot.picture_clips())} "
          f"picture clips, speakers {snapshot.speakers()}", file=sys.stderr)

    if args.rebind:
        existing = transcript_path(args.project_folder)
        if not existing.is_file():
            print(f"--rebind needs a transcript to re-bind and {existing} "
                  f"does not exist. Run without --rebind first.",
                  file=sys.stderr)
            return 1
        before = json.loads(existing.read_text(encoding="utf-8"))
        print(f"re-binding {before.get('segment_count')} segments from "
              f"{existing} - no audio is read", file=sys.stderr)
        document = rebind_document(before, snapshot,
                                   project_folder=args.project_folder)
        print(f"  straddling a cut: "
              f"{before.get('segments_straddling_a_cut')} -> "
              f"{document['segments_straddling_a_cut']}", file=sys.stderr)
    else:
        document = build_and_transcribe(args.project_folder, snapshot,
                                        model_size=args.model,
                                        only_speakers=args.speaker or None)

    out = (Path(args.out) if args.out
           else transcript_path(args.project_folder))
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"\nwrote {document['segment_count']} segments -> {out}",
          file=sys.stderr)
    if document["segments_read_from_words"]:
        print(f"  {document['segments_read_from_words']} segment(s) came from "
              f"rows re-read at word level, {document['segments_rebound_from_words']} "
              f"of them bound to a clip", file=sys.stderr)
    if document["segments_straddling_a_cut"]:
        print(f"  {document['segments_straddling_a_cut']} segments carry no "
              f"source binding even at word level", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
