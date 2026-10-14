from library.tools import mfa_align, source_memory, speech_measurements


def test_mfa_phone_tier_produces_phonemes_and_syllable_boundaries(tmp_path):
    grid = tmp_path / "one.TextGrid"
    grid.write_text('''File type = "ooTextFile"
Object class = "TextGrid"
item []:
    item [1]:
        class = "IntervalTier"
        name = "words"
        xmin = 0
        xmax = 0.5
        intervals [1]:
            xmin = 0
            xmax = 0.5
            text = "hello"
    item [2]:
        class = "IntervalTier"
        name = "phones"
        xmin = 0
        xmax = 0.5
        intervals [1]:
            xmin = 0
            xmax = 0.1
            text = "HH"
        intervals [2]:
            xmin = 0.1
            xmax = 0.3
            text = "AH0"
        intervals [3]:
            xmin = 0.3
            xmax = 0.4
            text = "L"
        intervals [4]:
            xmin = 0.4
            xmax = 0.5
            text = "OW1"
''', encoding="utf-8")

    assert mfa_align.parse_phone_tier(grid) == [
        ("HH", 0.0, 0.1), ("AH0", 0.1, 0.3),
        ("L", 0.3, 0.4), ("OW1", 0.4, 0.5),
    ]
    word = [{"word": "hello", "start": 2.0, "end": 2.5}]
    phonemes = mfa_align._phone_records(
        mfa_align.parse_phone_tier(grid), word, 2.0)
    syllables = mfa_align._syllable_records(phonemes)
    assert [(p["phone"], p["start"], p["end"]) for p in phonemes] == [
        ("HH", 2.0, 2.1), ("AH0", 2.1, 2.3),
        ("L", 2.3, 2.4), ("OW1", 2.4, 2.5),
    ]
    assert len(syllables) == 2
    assert all(p["boundary_uncertainty"]["status"] == "uncalibrated"
               for p in phonemes)

    scratch = tmp_path / "merge"
    output = scratch / "out"
    output.mkdir(parents=True)
    (output / "chunk_0000.TextGrid").write_text(
        grid.read_text(encoding="utf-8"), encoding="utf-8")
    aligned = mfa_align._merge_all(
        [{"start": 2.0, "end": 2.5, "text": "hello"}], scratch)
    assert len(aligned["segments"][0]["phonemes"]) == 4
    assert len(aligned["segments"][0]["syllables"]) == 2


def test_nonverbal_requires_explicit_annotation_and_never_trims():
    rows = speech_measurements.nonverbal_vocalizations(
        "[laughing] then words", 1.0, 1.6)
    assert rows == [{
        "kind": "laughter", "start": 1.0, "end": 1.6,
        "status": "candidate", "method": "explicit transcript annotation",
        "boundary_uncertainty": {"estimate_ms": None, "status": "uncalibrated"},
    }]
    assert speech_measurements.nonverbal_vocalizations("long pause", 1, 4) == []
    utterances = source_memory.utterances_from_aligned({"segments": [{
        "start": 2.0, "end": 2.7, "text": "[coughing]", "words": [],
    }]}, "hybrid-mfa")
    assert utterances[0]["vocalizations"][0]["kind"] == "cough"
    assert utterances[0]["words"] == []


def test_boundary_error_and_diarization_metrics_are_reported():
    assert speech_measurements.boundary_error([1.0, 2.0], [1.01, 1.98]) == {
        "status": "measured", "count": 2, "mae_ms": 15.0,
        "bias_ms": -5.0, "method": "ordered boundary comparison",
    }
    result = speech_measurements.diarization_der(
        [{"start": 0.0, "end": 3.0, "speaker": "a"}],
        [{"start": 0.0, "end": 3.0, "speaker": "b"}],
        collar_seconds=0,
    )
    assert result["status"] == "measured"
    assert result["der"] == 0.0  # speaker cluster labels are arbitrary
    swapped = speech_measurements.diarization_der(
        [{"start": 0.0, "end": 1.0, "speaker": "a"},
         {"start": 1.0, "end": 2.0, "speaker": "b"}],
        [{"start": 0.0, "end": 1.0, "speaker": "b"},
         {"start": 1.0, "end": 2.0, "speaker": "a"}],
        collar_seconds=0,
    )
    assert swapped["der"] == 0.0


def test_vi08_records_reach_m1_and_voice_quality_stays_measurement_only():
    utterances = source_memory.utterances_from_aligned({"segments": [{
        "text": "[laughing] hello", "words": [
            {"word": "hello", "start": 0.2, "end": 0.8}],
        "phonemes": [{"phone": "HH", "start": 0.2, "end": 0.3,
                      "word": "hello", "method": "MFA",
                      "boundary_uncertainty": {"status": "uncalibrated"}}],
        "syllables": [{"word": "hello", "start": 0.2, "end": 0.8}],
    }]}, "hybrid-mfa")
    assert utterances[0]["phonemes"][0]["method"] == "MFA"
    assert utterances[0]["syllables"][0]["word"] == "hello"
    assert utterances[0]["vocalizations"][0]["kind"] == "laughter"
    quality = speech_measurements.voice_quality_record(
        {"jitter": 0.01, "hnr_db": 14.0}, "praat-profile-1",
        start=0.2, end=0.8)
    assert quality["measurements"]["jitter"] == 0.01
    assert quality["interpretation"] == "none; acoustic measurements only"
    assert quality["boundary_uncertainty"]["status"] == "uncalibrated"
