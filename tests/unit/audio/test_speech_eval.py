from library.tools.speech_eval import score, score_batch


def test_scores_text_boundaries_speakers_and_cost_independently():
    ref = {"sample_id": "x", "words": [
        {"text": "Hello,", "start": 0.0, "end": .2, "speaker": "a"},
        {"text": "world", "start": .3, "end": .6, "speaker": "a"},
    ]}
    hyp = {"words": [
        {"text": "hello", "start": .01, "end": .21, "speaker": "a"},
        {"text": "word", "start": .31, "end": .61, "speaker": "b"},
    ]}
    result = score(ref, hyp, split="held_out", condition="clean",
                   audio_seconds=60, elapsed_seconds=30)
    assert (result["substitutions"], result["deletions"],
            result["insertions"]) == (1, 0, 0)
    assert result["wer"] == .5
    assert result["cer"] > 0
    assert result["boundary"]["start_median_abs_ms"] == 10
    assert result["speaker_attribution"]["accuracy"] == 1
    assert result["punctuation"]["status"] == "measured"
    assert result["punctuation"]["false_negatives"] == 1
    assert result["punctuation"]["f1"] == 0
    assert result["cost"]["seconds_per_audio_minute"] == 30


def test_punctuation_score_is_type_sensitive_and_only_uses_matched_words():
    ref = {"sample_id": "x", "words": [
        {"text": "Well,", "start": 0, "end": .2},
        {"text": "really?", "start": .3, "end": .6},
    ]}
    hyp = {"words": [
        {"text": "well;", "start": 0, "end": .2},
        {"text": "really?", "start": .3, "end": .6},
        {"text": "added.", "start": .7, "end": .8},
    ]}
    result = score(ref, hyp, split="calibration", condition="clean",
                   audio_seconds=1, elapsed_seconds=1)
    punctuation = result["punctuation"]
    assert punctuation["scored_tokens"] == 2
    assert punctuation["true_positives"] == 1
    assert punctuation["false_positives"] == 1
    assert punctuation["false_negatives"] == 1
    assert punctuation["f1"] == .5


def test_punctuation_is_unmeasured_without_exact_word_matches():
    ref = {"sample_id": "x", "words": [{"text": "Hello,", "start": 0,
                                            "end": .2}]}
    result = score(ref, {"words": [{"text": "goodbye!"}]},
                   split="held_out", condition="clean", audio_seconds=1,
                   elapsed_seconds=None)
    assert result["punctuation"]["status"] == "unmeasured"
    assert result["punctuation"]["f1"] is None


def test_abstention_is_not_counted_as_recognition_error():
    ref = {"sample_id": "x", "words": [{"text": "hello", "start": 0,
                                           "end": .2, "speaker": "a"}]}
    result = score(ref, {"status": "abstained", "reason": "unsupported"},
                   split="held_out", condition="noisy", audio_seconds=2,
                   elapsed_seconds=1)
    assert result["wer"] == 1
    assert result["failure_abstention"] == {
        "failed": False, "abstained": True, "reason": "unsupported"}
    assert result["boundary"]["matched_words"] == 0


def test_batch_aggregates_by_split_and_marks_missing_gold_times():
    ref = {"sample_id": "x", "words": [{"text": "word", "speaker": "a"}]}
    hyp = {"words": [{"text": "word", "speaker": "a", "start": 1, "end": 2}]}
    result = score_batch([{
        "reference": ref, "hypothesis": hyp, "split": "calibration",
        "condition": "unknown", "audio_seconds": 60, "elapsed_seconds": 3,
    }])
    group = result["summary_by_split_condition"][0]
    assert group["wer"] == 0
    assert group["cer"] == 0
    assert group["boundary_status"] == "unmeasured"
    assert group["seconds_per_audio_minute"] == 3


def test_cost_uses_audio_pass_duration_not_labeled_span_duration():
    result = score_batch([], [{
        "source_id": "calibration-source", "split": "calibration",
        "audio_seconds": 288, "elapsed_seconds": 24,
    }])
    cost = result["cost_by_split"][0]
    assert cost["seconds_per_audio_minute"] == 5
    assert cost["runs"] == 1
