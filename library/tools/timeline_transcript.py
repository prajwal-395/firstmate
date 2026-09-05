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

What is NOT decided here
------------------------
Nothing about which moments are interesting.  This module measures what
was said and where; choosing what matters is taste and belongs to a
model (AGENTS.md 10.5).  See `library/tools/reel_proposal.py`.

    python3 -m library.tools.timeline_transcript <project_folder> --write

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


def to_source_time(clip, timeline_time: float) -> float:
    """A timeline second, expressed in the SOURCE file's own timebase."""
    return clip.source_in + (timeline_time - clip.timeline_start)


# ── Transcribing ─────────────────────────────────────────────────────

def transcribe_audio(audio_path: Path, model_size: str = "large-v3",
                     beam_size: int = 5) -> dict:
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

    print(f"  transcribing with faster-whisper ({model_size}, int8, cpu)...",
          file=sys.stderr)
    model = WhisperModel(model_size, device="cpu", compute_type="int8")
    raw_segments, info = model.transcribe(
        str(audio_path), beam_size=beam_size, vad_filter=True,
        word_timestamps=False)
    segments = [{"start": s.start, "end": s.end, "text": s.text}
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


def segments_for_speaker(aligned: dict, speaker: Optional[str],
                         clips: Sequence) -> List[SpokenSegment]:
    """WhisperX output as `SpokenSegment`s, bound to the footage."""
    out: List[SpokenSegment] = []
    for segment in aligned.get("segments", []):
        text = (segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment["start"])
        end = float(segment["end"])
        clip = attribute_to_clip(start, end, clips)
        out.append(SpokenSegment(
            speaker=speaker,
            text=text,
            timeline_start=start,
            timeline_end=end,
            source_file=clip.source_file if clip else None,
            source_start=to_source_time(clip, start) if clip else None,
            source_end=to_source_time(clip, end) if clip else None,
            resolve_item_id=clip.resolve_item_id if clip else None,
            words=tuple(interpolate_untimed_words(segment.get("words") or [])),
        ))
    return out


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
        "segments": [s.as_dict() for s in merged],
    }


# ── CLI ──────────────────────────────────────────────────────────────

def build_and_transcribe(project_folder: str, snapshot,
                         model_size: str = "large-v3",
                         only_speakers: Optional[Iterable[str]] = None) -> dict:
    """The whole job: rebuild each speaker's audio, transcribe, bind back."""
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    scratch = Path(layout.write_dir(Area.SCRATCH)) / "timeline_transcript"
    cache_dir = scratch / "spans"
    scratch.mkdir(parents=True, exist_ok=True)

    by_speaker: Dict[Optional[str], List] = {}
    for clip in snapshot.picture_clips():
        by_speaker.setdefault(clip.speaker, []).append(clip)

    wanted = set(only_speakers) if only_speakers else None
    per_speaker: Dict[Optional[str], List[SpokenSegment]] = {}

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
        aligned = transcribe_audio(audio_path, model_size=model_size)
        segments = segments_for_speaker(aligned, speaker, clips)
        print(f"[{speaker}] {len(segments)} spoken segments", file=sys.stderr)
        per_speaker[speaker] = segments

    return transcript_document(snapshot, merge_speakers(per_speaker))


def main(argv=None) -> int:
    import argparse
    from library.tools import timeline_ingest
    from library.tools.project_layout import Area, ProjectLayout

    parser = argparse.ArgumentParser(
        description="Transcribe what a Resolve timeline says, per speaker.")
    parser.add_argument("project_folder")
    parser.add_argument("--model", default="large-v3")
    parser.add_argument("--speaker", action="append", default=[],
                        help="only this speaker; repeatable")
    parser.add_argument("--out", default="",
                        help="where to write; default is the project's scratch")
    args = parser.parse_args(argv)

    project_name, timeline_name = timeline_ingest._names_from(args.project_folder)
    snapshot, _p, _t = timeline_ingest.connect(project_name, timeline_name)
    print(f"{snapshot.timeline_name!r}: {len(snapshot.picture_clips())} "
          f"picture clips, speakers {snapshot.speakers()}", file=sys.stderr)

    document = build_and_transcribe(args.project_folder, snapshot,
                                    model_size=args.model,
                                    only_speakers=args.speaker or None)

    out = Path(args.out) if args.out else (
        Path(ProjectLayout(args.project_folder).write_dir(Area.SCRATCH))
        / "timeline_transcript" / "transcript.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(document, indent=2), encoding="utf-8")
    print(f"\nwrote {document['segment_count']} segments -> {out}",
          file=sys.stderr)
    if document["segments_straddling_a_cut"]:
        print(f"  {document['segments_straddling_a_cut']} segments straddle a "
              f"cut and carry no source binding", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
