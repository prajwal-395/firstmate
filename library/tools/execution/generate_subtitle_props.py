#!/usr/bin/env python3
"""
generate_subtitle_props.py — Generates frame-accurate subtitle props
for the Remotion SubtitleOverlay component.

This script:
1. Extracts audio from each A-roll source clip (just the used segment)
2. Runs WhisperX on each segment to get word-level timestamps
3. Maps source timestamps → timeline timestamps using the assembly manifest
4. Groups words into subtitle chunks (3-5 words each)
5. Outputs subtitle_props_4thwall.json for Remotion rendering

The key insight: word timestamps must come from the actual source audio
at the actual source in/out points, then be offset to timeline positions.
"""

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def extract_audio_segment(video_path: str, start: float, end: float, output_path: str):
    """Extract audio segment from video using ffmpeg."""
    subprocess.run([
        'ffmpeg', '-y', '-i', video_path,
        '-ss', str(start), '-to', str(end),
        '-vn', '-acodec', 'pcm_s16le', '-ar', '16000', '-ac', '1',
        output_path
    ], capture_output=True, timeout=30)


def transcribe_segment(audio_path: str) -> dict:
    """Run WhisperX on an audio segment, return word-level timestamps."""
    import whisperx

    device = "cpu"
    compute_type = "int8"

    # Load model
    model = whisperx.load_model("small", device, compute_type=compute_type)

    audio = whisperx.load_audio(audio_path)
    result = model.transcribe(audio, batch_size=4)

    # Align for word-level timestamps
    model_a, metadata = whisperx.load_align_model(language_code="en", device=device)
    result = whisperx.align(result["segments"], model_a, metadata, audio, device)

    return result


def build_subtitle_chunks(words: list, max_words: int = 4) -> list:
    """Group words into subtitle chunks of max_words each."""
    chunks = []
    current_chunk = []

    for word in words:
        current_chunk.append(word)
        if len(current_chunk) >= max_words:
            chunks.append({
                'text': ' '.join(w['word'].lower() for w in current_chunk),
                'start': current_chunk[0]['start'],
                'end': current_chunk[-1]['end'],
                '_words': list(current_chunk),  # preserve word objects
            })
            current_chunk = []

    # Remaining words
    if current_chunk:
        chunks.append({
            'text': ' '.join(w['word'].lower() for w in current_chunk),
            'start': current_chunk[0]['start'],
            'end': current_chunk[-1]['end'],
            '_words': list(current_chunk),
        })

    return chunks


def main():
    project_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    manifest_path = os.path.join(project_dir, 'pipeline_output', 'assembly_manifest.json')
    output_path = os.path.join(project_dir, 'video_testing', 'remotion_output', 'subtitle_props_4thwall.json')

    m = json.load(open(manifest_path))
    a_rolls = m['a_roll_assignments']
    fps = m.get('fps', 30)
    total_dur = m.get('duration_seconds', 60.0)

    print("═" * 60)
    print("  Subtitle Generator — Word-Level Alignment")
    print("═" * 60)
    print()

    all_subtitles = []

    with tempfile.TemporaryDirectory() as tmpdir:
        for i, ar in enumerate(a_rolls):
            clip_id = ar['clip_id']
            source_file = os.path.join(project_dir, ar['source_file'])
            src_in = ar['source_in']
            src_out = ar['source_out']
            tl_start = ar['timeline_start']
            tl_end = ar['timeline_end']
            tl_duration = tl_end - tl_start

            print(f"  [{i+1:2d}/10] {clip_id} src:{src_in:.1f}-{src_in + tl_duration:.1f}s → tl:{tl_start:.1f}-{tl_end:.1f}s")

            # Extract audio — only the portion that actually plays on timeline
            # The clip is TRIMMED (not speed-ramped), so only tl_duration seconds
            # of source audio starting at src_in are heard.
            audio_path = os.path.join(tmpdir, f"seg_{i:02d}.wav")
            actual_src_end = src_in + tl_duration
            extract_audio_segment(source_file, src_in, actual_src_end, audio_path)

            if not os.path.exists(audio_path) or os.path.getsize(audio_path) < 1000:
                print(f"          ⚠ Audio extraction failed, skipping")
                continue

            # Transcribe
            try:
                result = transcribe_segment(audio_path)
            except Exception as e:
                print(f"          ⚠ Transcription failed: {e}")
                continue

            # Collect words with source-relative timestamps
            words = []
            for seg in result.get('segments', []):
                for word in seg.get('words', []):
                    if 'start' in word and 'end' in word:
                        words.append(word)

            if not words:
                print(f"          ⚠ No words found")
                continue

            # Map 1:1 — word.start is seconds into the extracted audio,
            # which maps directly to tl_start + word.start on the timeline.
            # No speed factor. Clips are trimmed, not sped up.
            for word in words:
                word['timeline_start'] = tl_start + word['start']
                word['timeline_end'] = tl_start + word['end']

            # Group into subtitle chunks (using timeline-mapped timestamps)
            chunks = build_subtitle_chunks(words, max_words=4)

            for chunk in chunks:
                # Use the timeline-mapped timestamps from the words
                # chunk['start']/['end'] are segment-relative, so use word timeline times
                chunk_words = chunk.get('_words', [])

                # Build per-word frame timing
                word_frames = []
                for w in chunk_words:
                    w_start = max(w['timeline_start'], tl_start)
                    w_end = min(w['timeline_end'], tl_end)
                    w_start_frame = round(w_start * fps)
                    w_end_frame = round(w_end * fps)
                    if w_end_frame > w_start_frame:
                        word_frames.append({
                            'word': w['word'].lower(),
                            'startFrame': w_start_frame,
                            'endFrame': w_end_frame,
                        })

                if not word_frames:
                    continue

                start_frame = word_frames[0]['startFrame']
                end_frame = word_frames[-1]['endFrame']

                if end_frame > start_frame:
                    all_subtitles.append({
                        'text': chunk['text'],
                        'startFrame': start_frame,
                        'endFrame': end_frame,
                        'emphasisWords': [],
                        'words': word_frames,
                    })

            print(f"          ✓ {len(words)} words → {len(chunks)} chunks")

    # Sort by start frame
    all_subtitles.sort(key=lambda x: x['startFrame'])

    # Build final props
    props = {
        'subtitles': all_subtitles,
        'fps': fps,
        'width': 1080,
        'height': 1920,
        'durationInFrames': round(total_dur * fps),
    }

    with open(output_path, 'w') as f:
        json.dump(props, f, indent=2)

    print()
    print(f"  ✅ Generated {len(all_subtitles)} subtitle chunks")
    print(f"  Written to: {output_path}")
    print()

    # Show first few for verification
    print("  Preview:")
    for s in all_subtitles[:8]:
        start_s = s['startFrame'] / fps
        end_s = s['endFrame'] / fps
        print(f"    {start_s:5.2f}-{end_s:5.2f}s: \"{s['text']}\"")
    print("═" * 60)


if __name__ == "__main__":
    main()
