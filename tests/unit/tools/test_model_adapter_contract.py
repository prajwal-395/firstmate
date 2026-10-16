from fractions import Fraction

import pytest

from library.tools.model_adapter_contract import (
    CAPABILITY_INPUTS,
    AdapterResult,
    CapabilityInput,
    ComputeBudget,
    Confidence,
    ContractError,
    CoordinateFrame,
    EvidenceInterval,
    FrameRange,
    NormalizedDetection,
    NormalizedMask,
    RawArtifact,
    SampleRange,
    SourceAudioSamples,
    SourceClock,
    SourceVideoFrame,
    TimeMap,
    input_fingerprint,
    interval_from_frames,
    interval_from_samples,
    read_audio_samples,
    read_video_frames,
    run_adapter,
)


def _video_request(**changes):
    values = {
        "capability": "visual.detect",
        "source_id": "asset-001",
        "source_sha256": "0" * 64,
        "source_clock": SourceClock("source-001", 1000, "asset-001", frame_rate_num=24000,
                                     frame_rate_den=1001),
        "frame_range": FrameRange(100, 180),
        "coordinates": CoordinateFrame(
            1920, 1080, crop_xywh=(100, 50, 1000, 500),
            model_width=640, model_height=320, scale_x=0.64, scale_y=0.64,
            pad_left=0, pad_top=0),
        "prompt": "laptop",
    }
    values.update(changes)
    return CapabilityInput(**values)


def test_video_request_uses_authoritative_half_open_source_frames_and_crop_transform():
    request = _video_request()

    assert request.frame_range == FrameRange(100, 180)
    assert request.coordinates.source_box((64, 32, 320, 160)) == pytest.approx(
        (0.1041666667, 0.0925925926, 0.3125, 0.2777777778))


def test_variable_frame_rate_request_uses_explicit_authoritative_ticks():
    request = _video_request(
        source_clock=SourceClock("vfr", 1000, "asset-001"),
        source_intervals=(EvidenceInterval("vfr", 4021, 7430, 100, 180),))

    assert request.source_intervals == (
        EvidenceInterval("vfr", 4021, 7430, 100, 180),)
    with pytest.raises(ContractError, match="authoritative source_intervals"):
        _video_request(source_clock=SourceClock("vfr", 1000, "asset-001"))


def test_audio_request_requires_sample_clock_and_sample_range():
    request = CapabilityInput(
        capability="speech.transcribe", source_id="audio-1",
        source_sha256="1" * 64,
        source_clock=SourceClock("audio-clock", 48000, "audio-1"),
        sample_range=SampleRange(48000, 96000, 48000))

    assert request.sample_range.start == 48000
    with pytest.raises(ContractError, match="frame range or sample range"):
        CapabilityInput("speech.transcribe", "audio-1", "1" * 64, request.source_clock)


def test_registered_capabilities_require_their_modalities_and_prompts():
    assert len(CAPABILITY_INPUTS) == 20
    with pytest.raises(ContractError, match="requires a prompt"):
        _video_request(capability="video.locate_event", prompt=None)
    with pytest.raises(ContractError, match="audiovisual requests need"):
        CapabilityInput(
            capability="audio.active_speaker", source_id="muxed", source_sha256="2" * 64,
            source_clock=SourceClock("video", 1000, "muxed", frame_rate_num=24, frame_rate_den=1),
            frame_range=FrameRange(0, 24), sample_range=SampleRange(0, 48000, 48000),
            coordinates=CoordinateFrame(1920, 1080))

    transform = TimeMap("audio-clock", "video-clock", 0, 5000, 0, 5000,
                        Fraction(1))
    request = CapabilityInput(
        capability="audio.active_speaker", source_id="muxed",
        source_sha256="2" * 64,
        source_clock=SourceClock("video-clock", 1000, "muxed",
                                 frame_rate_num=24000, frame_rate_den=1001),
        sample_clock=SourceClock("audio-clock", 1000, "muxed"),
        clock_transforms=(transform,),
        frame_range=FrameRange(24, 48),
        sample_range=SampleRange(48000, 96000, 48000),
        coordinates=CoordinateFrame(1920, 1080))
    assert request.clock_transforms == (transform,)


def test_source_clock_must_belong_to_the_requested_asset():
    with pytest.raises(ContractError, match="must belong to the requested"):
        CapabilityInput(
            capability="speech.transcribe", source_id="audio-1",
            source_sha256="1" * 64, source_clock=SourceClock("clock", 48000, "other"),
            sample_range=SampleRange(0, 48000, 48000))


def test_frame_and_sample_ranges_convert_to_explicit_source_ticks():
    video_clock = SourceClock("video", 1000, "video-asset", frame_rate_num=24000,
                              frame_rate_den=1001)
    audio_clock = SourceClock("audio", 1000, "audio-asset")

    assert interval_from_frames(video_clock, FrameRange(24, 48)) == EvidenceInterval(
        "video", 1001, 2002, 24, 48)
    assert interval_from_samples(
        audio_clock, SampleRange(48000, 96000, 48000)) == EvidenceInterval(
            "audio", 1000, 2000, start_sample=48000, end_sample=96000)


def test_declared_compute_budget_refuses_oversized_source_ranges():
    with pytest.raises(ContractError, match="exceeds max_frames"):
        _video_request(budget=ComputeBudget(max_frames=12))


def test_media_reader_preserves_source_positions_and_refuses_bad_frames():
    class Media:
        def __init__(self, rows):
            self.rows = rows

        def read_video_frames(self, request):
            return self.rows

        def read_audio_samples(self, request):
            return SourceAudioSamples(request.sample_range, [0.0])

    request = _video_request()
    expected = (SourceVideoFrame(100, object(), 1920, 1080),
                SourceVideoFrame(110, object(), 1920, 1080))
    assert read_video_frames(Media(expected), request) == expected

    with pytest.raises(ContractError, match="source ordered"):
        read_video_frames(Media((expected[1], expected[0])), request)
    with pytest.raises(ContractError, match="outside the request"):
        read_video_frames(Media((SourceVideoFrame(180, object(), 1920, 1080),)), request)


def test_audio_media_reader_must_return_exact_sample_span():
    class Media:
        def read_video_frames(self, request):
            return ()

        def read_audio_samples(self, request):
            return SourceAudioSamples(SampleRange(0, 10, 48000), [0.0])

    request = CapabilityInput(
        capability="speech.transcribe", source_id="audio-1",
        source_sha256="1" * 64,
        source_clock=SourceClock("audio-clock", 48000, "audio-1"),
        sample_range=SampleRange(48000, 96000, 48000))
    with pytest.raises(ContractError, match="disagrees with the request"):
        read_audio_samples(Media(), request)


def test_input_fingerprint_is_stable_and_tracks_source_or_preprocessor_revision():
    request = _video_request()

    assert input_fingerprint(request, "decode-v1") == input_fingerprint(request, "decode-v1")
    assert input_fingerprint(request, "decode-v1") != input_fingerprint(request, "decode-v2")
    assert input_fingerprint(
        _video_request(source_sha256="f" * 64), "decode-v1") != input_fingerprint(
            request, "decode-v1")


def test_normalized_result_preserves_uncalibrated_score_and_raw_artifact():
    result = AdapterResult(
        status="completed", supported_features=frozenset({"boxes"}),
        detections=(NormalizedDetection(
            "laptop", (0.1, 0.2, 0.6, 0.8),
            Confidence(0.91, "uncalibrated_score"),
            EvidenceInterval("source-001", 4170, 7508, 100, 180)),),
        raw_output=RawArtifact("artifacts/raw-output.json", "a" * 64))

    assert result.detections[0].confidence.raw_score == 0.91
    assert result.detections[0].confidence.calibrated_probability is None
    assert result.raw_output.uri.endswith("raw-output.json")


def test_probability_requires_explicit_calibration_provenance():
    with pytest.raises(ContractError, match="calibrated value"):
        Confidence(0.9, "calibrated_probability")
    with pytest.raises(ContractError, match="calibration_id"):
        Confidence(0.9, "uncalibrated_score", calibrated_probability=0.9)


def test_feature_gaps_are_explicit_and_adapter_cannot_claim_undeclared_features():
    class BoxesOnly:
        adapter_id = "detector-v1"
        capability = "visual.detect"
        model_revision = "publisher-sha-1"
        supported_features = frozenset({"boxes"})

        def infer(self, request, model, media):
            return AdapterResult(
                status="completed", supported_features=frozenset({"boxes"}),
                unsupported_features=frozenset({"masks"}),
                raw_output=RawArtifact("raw.json", "b" * 64))

    assert run_adapter(BoxesOnly(), _video_request(), object(), object()).unsupported_features == {"masks"}

    class LyingAdapter(BoxesOnly):
        def infer(self, request, model, media):
            return AdapterResult(
                status="completed", supported_features=frozenset({"boxes", "masks"}),
                raw_output=RawArtifact("raw.json", "b" * 64))

    with pytest.raises(ContractError, match="undeclared features"):
        run_adapter(LyingAdapter(), _video_request(), object(), object())


@pytest.mark.parametrize("status,failure", [
    ("failed", "model_unavailable"), ("failed", "out_of_memory"),
    ("failed", "unsupported_input"), ("failed", "low_confidence"),
])
def test_failure_causes_are_structured(status, failure):
    assert AdapterResult(status=status, failure=failure).failure == failure


def test_completed_absence_is_not_a_failure():
    assert AdapterResult(
        status="no_evidence", detail="searched full requested interval",
        raw_output=RawArtifact("raw.json", "d" * 64)).status == "no_evidence"


def test_model_space_box_must_map_inside_source():
    coordinates = _video_request().coordinates
    with pytest.raises(ContractError, match="outside the source"):
        coordinates.source_box((-1000, 0, 500, 200))


def test_adapter_cannot_emit_evidence_on_another_clock_or_outside_request():
    class WrongClock:
        adapter_id = "detector-v1"
        capability = "visual.detect"
        model_revision = "publisher-sha-1"
        supported_features = frozenset({"intervals"})

        def infer(self, request, model, media):
            return AdapterResult(
                status="completed", supported_features=frozenset({"intervals"}),
                intervals=(EvidenceInterval("foreign-clock", 1, 2),),
                raw_output=RawArtifact("raw.json", "c" * 64))

    with pytest.raises(ContractError, match="other than the requested"):
        run_adapter(WrongClock(), _video_request(), object(), object())


def test_adapter_masks_use_the_oriented_source_display_dimensions():
    class MaskAdapter:
        adapter_id = "segmenter-v1"
        capability = "visual.segment"
        model_revision = "publisher-sha-1"
        supported_features = frozenset({"masks"})

        def infer(self, request, model, media):
            return AdapterResult(
                status="completed", supported_features=frozenset({"masks"}),
                masks=(NormalizedMask("mask.rle", 640, 320, "rle"),),
                raw_output=RawArtifact("raw.json", "e" * 64))

    with pytest.raises(ContractError, match="mask dimensions"):
        run_adapter(MaskAdapter(), _video_request(capability="visual.segment"),
                    object(), object())
