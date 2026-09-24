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

@pytest.mark.heavy
@pytest.mark.heavy_ml
def test_prosody_speaking_rate_comes_from_handed_regions(hello_wav, tmp_path):
    """speaking_rate is computed from the speech_regions handed in, during a
    REAL parselmouth measurement - not a mocked one.

    Neither existing heavy test passes speech_regions, so analyze_prosody's
    speaking_rate branch runs only under mocks (test_speech_pipeline.py).
    Dropping that branch (speaking_rate=None) is invisible to the suite
    without this test. voice_quality is asserted for the same reason: the
    existing test never reads it, so a hollowed HNR ladder would pass.
    """
    from library.tools.analysis.speech_advanced_pipeline import analyze_speech_advanced

    first = "hello there this is a test".split()
    second = "of the speech pipeline now".split()
    regions = [
        {"start": 0.0, "end": 5.0,
         "words": [{"word": w, "start": 0.1 * i, "end": 0.1 * i + 0.09}
                   for i, w in enumerate(first)]},
        {"start": 6.0, "end": 10.0,
         "words": [{"word": w, "start": 6.0 + 0.1 * i, "end": 6.0 + 0.1 * i + 0.09}
                   for i, w in enumerate(second)]},
    ]

    out_dir = tmp_path / "out"
    out_dir.mkdir()

    result = analyze_speech_advanced(
        audio_path=str(hello_wav),
        speech_regions=regions,
        output_dir=str(out_dir),
        clip_id="clip_rate",
    )

    prosody = result["prosody"]
    assert prosody.get("method") == "parselmouth-praat"

    rate = prosody.get("speaking_rate")
    assert rate is not None, "speaking_rate missing although regions were handed in"
    total_words = len(first) + len(second)
    total_seconds = 5.0 + 4.0
    assert rate["total_words"] == total_words
    assert rate["total_speech_seconds"] == total_seconds
    assert rate["words_per_minute"] == round(total_words / total_seconds * 60, 1)

    vq = prosody.get("voice_quality")
    assert vq is not None, "voice_quality missing from a real measurement"
    # Issue #263: HNR is reported as a number with no vocal-register
    # label beside it - on field recordings the number measures the
    # environment as much as the voice.
    assert "quality_assessment" not in vq, (
        f"register label reappeared: {vq!r}")
    hnr = vq.get("hnr_db")
    assert hnr is None or isinstance(hnr, float), f"hnr_db is not measured: {hnr!r}"
