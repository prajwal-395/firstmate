import subprocess
import sys

import pytest


@pytest.fixture
def hello_wav(tmp_path):
    wav_path = tmp_path / "hello.wav"
    raw_path = tmp_path / "raw_hello.wav"
    aiff_path = tmp_path / "hello.aiff"
    
    test_sentence = "Hello there, this is a test of the speech detection pipeline. We need a long enough sentence so that the voice activity detector can recognize this as human speech."
    
    if sys.platform == "darwin":
        subprocess.run(["say", "-o", str(aiff_path), test_sentence], check=True)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(aiff_path),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)
        ], check=True, capture_output=True)
    else:
        subprocess.run(["espeak", "-w", str(raw_path), test_sentence], check=True)
        subprocess.run([
            "ffmpeg", "-y", "-i", str(raw_path),
            "-ar", "16000", "-ac", "1", "-c:a", "pcm_s16le", str(wav_path)
        ], check=True, capture_output=True)
        
    return wav_path

@pytest.mark.heavy_ml
def test_prosody_produces_real_measurement(hello_wav, tmp_path):
    """Prosody analysis must produce a real measurement using parselmouth,
    not a hollow profile, when given a real speech file.
    """
    from library.tools.analysis.speech_advanced_pipeline import analyze_speech_advanced
    
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    
    result = analyze_speech_advanced(
        audio_path=str(hello_wav),
        output_dir=str(out_dir),
        clip_id="clip_hello"
    )
    
    assert result["clip_id"] == "clip_hello"
    assert "prosody" in result
    prosody = result["prosody"]
    assert prosody.get("method") == "parselmouth-praat", f"Unexpected method: {prosody.get('method')}"
    
    # Must contain real measurements
    assert "pitch_stats" in prosody
    assert "mean_f0_hz" in prosody["pitch_stats"]
    assert "intensity_contour_50ms" in prosody
    assert len(prosody["intensity_contour_50ms"]) > 0

@pytest.mark.heavy_ml
def test_transcription_produces_real_timed_words(hello_wav, tmp_path):
    """Transcription must produce timed words using whisperx when given a real speech file."""
    from library.steps.step_1_04_temporal_index.step import detect_speech_regions
    
    regions = detect_speech_regions(
        audio_path=str(hello_wav),
        output_dir=str(tmp_path),
        onsets=[],
        whisper_model_size="tiny"
    )
    
    assert len(regions) > 0, "No speech regions detected"
    words = regions[0].get("words", [])
    assert len(words) > 0, "No timed words produced"
    
    first_word = words[0]
    assert "word" in first_word
    assert "start" in first_word
    assert "end" in first_word
