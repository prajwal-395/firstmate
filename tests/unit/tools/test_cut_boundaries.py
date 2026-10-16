"""Unit tests for library.tools.cut_boundaries (F04 edit-point refinement).

Synthetic word timings only - no footage, no models, no project reads.
"""

from library.tools import cut_boundaries


def _words(*spans):
    return [
        {"word": f"w{i}", "start": start, "end": end}
        for i, (start, end) in enumerate(spans)
    ]


def test_head_and_tail_pad_half_the_gap_into_silence():
    words = _words((10.0, 10.4), (11.0, 11.5), (12.0, 12.4))
    result = cut_boundaries.refine_cut(11.0, 11.5, words, {})
    # Head gap is 0.6 -> pad 0.15 (capped); tail gap is 0.5 -> pad 0.15.
    assert result["source_start"] == 11.0 - 0.15
    assert result["source_end"] == 11.5 + 0.15
    assert result["head"]["pad_applied"] == 0.15
    assert result["tail"]["pad_applied"] == 0.15
    assert result["head"]["flags"] == []
    assert result["tail"]["flags"] == []


def test_small_gap_pads_half_without_capping():
    words = _words((10.0, 10.4), (10.5, 10.9), (11.0, 11.4))
    result = cut_boundaries.refine_cut(10.5, 10.9, words, {})
    assert result["source_start"] == round(10.5 - 0.05, 3)
    assert result["source_end"] == round(10.9 + 0.05, 3)


def test_flush_edge_against_speech_flags_hard_cut():
    words = _words((10.0, 10.4), (10.4, 10.9), (10.9, 11.4))
    result = cut_boundaries.refine_cut(10.4, 10.9, words, {})
    assert result["head"]["pad_applied"] == 0.0
    assert result["tail"]["pad_applied"] == 0.0
    assert "hard_cut" in result["head"]["flags"]
    assert "hard_cut" in result["tail"]["flags"]


def test_mid_word_edge_is_not_moved_and_is_flagged():
    words = _words((10.0, 10.4), (11.0, 11.5))
    result = cut_boundaries.refine_cut(11.2, 11.5, words, {})
    assert result["source_start"] == 11.2
    assert result["head"]["mid_word"] is True
    assert "mid_word" in result["head"]["flags"]


def test_head_never_pads_before_zero_and_tail_obeys_duration():
    head = cut_boundaries.refine_edge(
        0.05, [(-1.0, -0.5), (0.05, 0.4)], "head", bound=0.0)
    assert head["edge"] == 0.0
    assert head["pad_applied"] == 0.05
    words = _words((9.5, 9.9), (10.5, 11.0))
    result = cut_boundaries.refine_cut(
        9.5, 9.9, words, {"clip_duration_seconds": 10.0}, max_pad=10.0)
    # Half of the 0.6 gap would reach 10.2; the source ends at 10.0.
    assert result["source_end"] == 10.0


def test_tail_with_no_following_word_measures_no_silence():
    words = _words((9.5, 9.9))
    result = cut_boundaries.refine_cut(9.5, 9.9, words, {})
    assert result["source_end"] == 9.9
    assert result["tail"]["gap_seconds"] is None


def test_action_crossing_and_truncated_action_are_flagged():
    words = _words((10.0, 10.4), (11.0, 11.5), (12.0, 12.4))
    evidence = {"action_windows": [
        {"start": 10.8, "end": 11.3, "label": "point"},
        {"start": 12.2, "end": 13.0, "label": "pickup"},
    ]}
    result = cut_boundaries.refine_cut(11.0, 12.4, words, evidence)
    assert "action_crossing" in result["head"]["flags"]
    assert "truncated_action" in result["span_flags"]
    assert result["truncated_actions"][0]["label"] == "pickup"


def test_motion_audio_scene_proximity_is_reported_per_edge():
    words = _words((10.0, 10.4), (11.0, 11.5))
    evidence = {
        "motion_peaks": [{"time": 11.05, "kind": "apex"}],
        "audio_events": [{"time": 11.45, "label": "Laughter"}],
        "scene_boundaries": [10.9],
    }
    result = cut_boundaries.refine_cut(11.0, 11.5, words, evidence)
    assert "motion_peak_near" in result["head"]["flags"]
    assert "scene_at_cut" in result["head"]["flags"]
    assert "audio_event_near" in result["tail"]["flags"]
    assert result["head"]["evidence_ids"] == []


def test_evidence_ids_travel_with_the_flags_that_used_them():
    words = _words((10.0, 10.4), (11.0, 11.5))
    evidence = {
        "motion_peaks": [
            {"time": 11.05, "kind": "apex", "evidence_id": "motion:1"}],
    }
    result = cut_boundaries.refine_cut(11.0, 11.5, words, evidence)
    assert result["head"]["evidence_ids"] == ["motion:1"]


def test_high_emphasis_word_boundary_is_reported():
    words = _words((10.0, 10.4), (11.0, 11.5))
    evidence = {"prosodic_words": [
        {"word": "important", "start": 11.0, "end": 11.5,
         "emphasis": 0.8},
    ]}
    result = cut_boundaries.refine_cut(11.0, 11.5, words, evidence)
    assert "prosody_peak_near" in result["head"]["flags"]
    assert result["head"]["prosodic_words_near"][0]["word"] == "important"


def test_absent_evidence_moves_nothing_and_is_named():
    words = _words((10.0, 10.4), (11.0, 11.5))
    result = cut_boundaries.refine_cut(11.0, 11.5, words, None)
    assert result["source_start"] == 11.0 - 0.15
    assert result["unmeasured"] == [
        "clip_duration", "action_windows", "motion_peaks",
        "audio_events", "scene_boundaries", "prosody",
    ]


def test_tiny_span_cannot_invert():
    words = _words((10.0, 10.4), (10.5, 10.55), (10.6, 11.0))
    result = cut_boundaries.refine_cut(10.5, 10.55, words, {},
                                       max_pad=10.0)
    assert result["source_end"] >= result["source_start"]


def test_flatten_words_reads_both_key_vocabularies_and_regions():
    mixed = [
        {"word": "a", "source_start": 3.0, "source_end": 3.4},
        {"words": [{"word": "b", "start": 1.0, "end": 1.4}]},
        {"word": "c", "start": 2.0, "end": 2.4},
        {"word": "broken"},
    ]
    assert cut_boundaries.flatten_words(mixed) == [
        (1.0, 1.4), (2.0, 2.4), (3.0, 3.4)]


# ── The reader: edit-point edges reach view:alignment ─────────────────
#
# `edit_point_report` is written on step 2.02's own output. Without a
# reader it is a measurement nobody took, so the edge columns ride
# view:alignment (the step that reviews the rough cut), joined by
# block. Synthetic reports only - no footage, no models.

def _sequence_with_reports():
    from library.tools import alignment_findings as af

    assert af is not None
    return {
        "speech_sequence": {
            "alignment_report": [
                {
                    "block": "Body[#1]", "clip_id": "clip_a",
                    "source_start": 10.0, "source_end": 12.0,
                    "leading_gap_seconds": 0.1,
                    "largest_gap_seconds": 0.3,
                    "voiced_fraction": 0.8, "anchors_considered": 2,
                    "event": "ok",
                },
            ],
            "edit_point_report": [
                {
                    "block": "Body[#1]", "clip_id": "clip_a",
                    "source_start": 9.9, "source_end": 12.1,
                    "head_pad_seconds": 0.1, "tail_pad_seconds": 0.1,
                    "head_gap_seconds": 0.2, "tail_gap_seconds": 0.2,
                    "head_flags": ["action_crossing"],
                    "tail_flags": ["hard_cut"],
                    "span_flags": [],
                    "unmeasured": ["prosody"],
                },
            ],
        },
    }


def test_edit_point_edge_columns_reach_the_alignment_view():
    from library.tools import context_views as cv

    view = cv.build_view("alignment", _sequence_with_reports())
    rows = view["alignment"]["passages"]
    assert len(rows) == 1
    assert rows[0]["head_flags"] == ["action_crossing"]
    assert rows[0]["tail_flags"] == ["hard_cut"]
    assert rows[0]["head_pad_seconds"] == 0.1
    assert rows[0]["unmeasured"] == ["prosody"]
    assert "head_flags / tail_flags" in view["alignment"]["legend"]


def test_alignment_view_without_edit_report_reads_as_before():
    from library.tools import context_views as cv

    data = _sequence_with_reports()
    del data["speech_sequence"]["edit_point_report"]
    view = cv.build_view("alignment", data)
    rows = view["alignment"]["passages"]
    assert len(rows) == 1
    assert "head_flags" not in rows[0]
    assert "head_flags / tail_flags" not in view["alignment"]["legend"]


def test_edit_only_blocks_join_with_unmeasured_insides():
    from library.tools import context_views as cv

    data = _sequence_with_reports()
    data["speech_sequence"]["edit_point_report"].append({
        "block": "Body[#2]", "clip_id": "clip_b",
        "source_start": 20.0, "source_end": 22.0,
        "head_pad_seconds": 0.0, "tail_pad_seconds": 0.0,
        "head_gap_seconds": 0.05, "tail_gap_seconds": 0.4,
        "head_flags": ["hard_cut"], "tail_flags": [],
        "span_flags": ["truncated_action"], "unmeasured": [],
    })
    view = cv.build_view("alignment", data)
    rows = {row["block"]: row for row in view["alignment"]["passages"]}
    assert rows["Body[#2]"]["largest_gap_seconds"] is None
    assert rows["Body[#2]"]["event"] == "unmeasured"
    assert rows["Body[#2]"]["span_flags"] == ["truncated_action"]


def test_raw_reports_stay_dropped_where_the_view_is_declared():
    import json
    from pathlib import Path

    root = Path(__file__).resolve().parents[3]
    for step in ("step_3_03_review_rough_cut", "step_2_05_mesh_spine"):
        manifest = json.loads(
            (root / "library" / "steps" / step / "manifest.json")
            .read_text(encoding="utf-8"))
        fields = manifest.get("context_fields") or manifest.get(
            "interface", {}).get("context_fields") or []
        assert "-speech_sequence.alignment_report" in fields
        assert "-speech_sequence.edit_point_report" in fields, (
            f"{step}: raw edit_point_report would reach the prompt "
            "beside the view that summarizes it")
