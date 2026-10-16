from library.tools import phrase_coherence as pc


def _words():
    return [
        {"word": "what", "source_start": 0.0, "source_end": 0.3},
        {"word": "did", "source_start": 0.4, "source_end": 0.6},
        {"word": "you", "source_start": 0.7, "source_end": 0.9},
        {"word": "learn?", "source_start": 1.0, "source_end": 1.4},
        {"word": "we", "source_start": 3.0, "source_end": 3.2},
        {"word": "shipped", "source_start": 3.3, "source_end": 3.7},
        {"word": "friday.", "source_start": 3.8, "source_end": 4.2},
    ]


def test_phrase_spans_split_on_pause_and_sentence():
    spans = pc.phrase_spans(_words())
    assert [(s["start_seconds"], s["end_seconds"], s["word_count"])
            for s in spans] == [(0.0, 1.4, 4), (3.0, 4.2, 3)]


def test_phrase_spans_empty_is_empty():
    assert pc.phrase_spans([]) == []


def test_pause_center_cuts_sit_in_gaps():
    cuts = pc.pause_center_cuts(_words(), min_pause_seconds=0.15)
    assert len(cuts) == 1
    cut = cuts[0]
    assert cut["position_seconds"] == 2.2
    assert cut["evidence"][0]["word"] == "learn?"
    assert cut["evidence"][1]["word"] == "we"


def test_pause_center_cuts_refuse_abutting_words():
    tight = [
        {"word": "a", "source_start": 0.0, "source_end": 0.2},
        {"word": "b", "source_start": 0.2, "source_end": 0.4},
    ]
    assert pc.pause_center_cuts(tight, min_pause_seconds=0.15) == []


def test_guard_refuses_isolated_word():
    verdict = pc.guard_coherent_span(3.0, 3.2, _words())
    assert verdict["status"] == "refused"
    assert verdict["reason"] == "isolated_word"


def test_guard_refuses_mid_word_edge():
    verdict = pc.guard_coherent_span(0.1, 1.4, _words())
    assert verdict["status"] == "refused"
    assert verdict["reason"] == "mid_word_start"


def test_guard_accepts_full_phrase():
    verdict = pc.guard_coherent_span(0.0, 1.4, _words())
    assert verdict["status"] == "ok"
    assert verdict["covered_words"] == 4


def test_question_answer_unit_keeps_question_with_answer():
    turns = [
        {"speaker": "host", "start_seconds": 0.0, "end_seconds": 1.4},
        {"speaker": "guest", "start_seconds": 3.0, "end_seconds": 4.2},
    ]
    units = pc.question_answer_units(turns, _words())
    assert len(units) == 1
    assert units[0]["question"]["speaker"] == "host"
    assert units[0]["answer"]["speaker"] == "guest"
    assert units[0]["gap_seconds"] == 1.6


def test_question_without_answer_yields_no_unit():
    turns = [{"speaker": "host", "start_seconds": 0.0,
              "end_seconds": 1.4}]
    assert pc.question_answer_units(turns, _words()) == []


def test_same_speaker_answer_is_not_a_unit():
    turns = [
        {"speaker": "host", "start_seconds": 0.0, "end_seconds": 1.4},
        {"speaker": "host", "start_seconds": 3.0, "end_seconds": 4.2},
    ]
    assert pc.question_answer_units(turns, _words()) == []
