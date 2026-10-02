import math
import sys
import wave
from array import array

import pytest

pytestmark = pytest.mark.real_model

@pytest.fixture
def synthetic_wav(tmp_path):
    """Write stable harmonic audio without depending on a host TTS service."""
    wav_path = tmp_path / "harmonic-tone.wav"
    sample_rate = 16000
    samples = array(
        "h",
        (int(10000 * math.sin(2 * math.pi * 180 * frame / sample_rate))
         for frame in range(sample_rate * 12)),
    )
    if sys.byteorder != "little":
        samples.byteswap()
    with wave.open(str(wav_path), "wb") as audio:
        audio.setnchannels(1)
        audio.setsampwidth(2)
        audio.setframerate(sample_rate)
        audio.writeframes(samples.tobytes())
    return wav_path

def test_prosody_speaking_rate_comes_from_handed_regions(synthetic_wav,
                                                         tmp_path):
    """speaking_rate is computed from the speech_regions handed in, during a
    REAL parselmouth measurement - not a mocked one.

    Neither existing heavy test passes speech_regions, so analyze_prosody's
    speaking_rate branch runs only under mocks (test_speech_pipeline.py).
    Dropping that branch (speaking_rate=None) is invisible to the suite
    without this test. voice_quality is asserted for the same reason: the
    existing test never reads it, so a hollowed HNR ladder would pass.
    """
    from library.tools.analysis.speech_advanced_pipeline import analyze_speech_advanced

    first = ["hello", "there", "this", "is", "a", "test"]
    second = ["of", "the", "speech", "pipeline", "now"]
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
        audio_path=str(synthetic_wav),
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


def test_onnxruntime_is_never_loaded_by_this_tier():
    """Nothing in this tier loads onnxruntime, so its teardown cannot race.

    onnxruntime's telemetry threads abort the process at interpreter
    shutdown, after the tests have passed: on 2026-09-23 the real_model
    tier reported "1 passed then NATIVE CRASH (SIGABRT)" twice, both
    crash reports showing the 1DS WorkerThread dispatching an
    HTTP-response debug event (DebugEventSource::DispatchEvent ->
    recursive_mutex::lock() throws system_error -> terminate -> abort)
    while the main thread tears telemetry down
    (PosixTelemetry::Shutdown -> FlushAndTeardown).  The 2026-09-07
    conftest "fix" (eagerly import onnxruntime, then
    disable_telemetry_events()) did not hold: the disable call stops ORT
    event collection, not the SDK's own upload/debug path - and the eager
    import was itself the only thing loading
    onnxruntime_pybind11_state.so into this process, which measures with
    parselmouth only.

    This asserts the structural property the fix establishes: onnxruntime
    was never imported into this process.  Deterministic, no subprocess,
    no extra ML load - it fails the moment anything eagerly imports
    onnxruntime into the test process again.
    """
    assert "onnxruntime" not in sys.modules, (
        "onnxruntime was imported into the real_model test process - its "
        "telemetry threads SIGABRT at interpreter shutdown (2026-09-23: "
        "1 passed then NATIVE CRASH). This tier measures with parselmouth "
        "only; nothing here may load onnxruntime_pybind11_state.so."
    )
