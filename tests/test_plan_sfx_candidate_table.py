"""`sfx_candidates_toon` carries rows, and they come from the spine.

Regression #223: the table arrived with zero rows, built from an input no
DAG edge carries and keyed by a name the answer cannot use (AGENTS.md
10.1). History: docs/evidence/sfx.md.
"""
from library.steps.step_4_04_plan_sfx.bridge import (
    build_sfx_candidates,
)


def _spine():
    """Three blocks in project 001's own shape.

    A speech block whose source range covers two measured energy peaks,
    a second covering none, and a transition slot with no source clip -
    the three answers the transient column has to be able to give.
    """
    return {
        "structure": [
            {
                "position": "hook",
                "block_type": "hook",
                "clip_id": "clip_011",
                "source_start": 0.836,
                "source_end": 3.234,
                "timeline_start": 0.0,
                "timeline_end": 2.4,
                "visual_note": "Open cold on his face.",
                "content": {
                    "text": "i can feel the silent judgment.",
                    "clip_id": "clip_011",
                },
            },
            {
                "position": 1,
                "block_type": "transition_slot",
                "clip_id": None,
                "source_start": None,
                "source_end": None,
                "timeline_start": 2.4,
                "timeline_end": 5.4,
                "visual_note": "The breath after the hook.",
                "content": None,
            },
            {
                "position": 2,
                "block_type": "speech",
                "clip_id": "clip_011",
                "source_start": 9.699,
                "source_end": 12.681,
                "timeline_start": 5.4,
                "timeline_end": 8.38,
                "visual_note": "Back on him.",
                "content": {
                    "text": "today is march 25th, 2026.",
                    "clip_id": "clip_011",
                },
            },
        ]
    }


def _temporal():
    return [
        {
            "clip_id": "clip_011",
            "energy_curve": {
                "sample_rate_hz": 30,
                "peak_times": [7.833, 9.867, 10.8, 12.6, 14.2],
            },
            "onset_times": [0.836, 1.045],
        }
    ]


def _inputs(**overrides):
    payload = {
        "timed_spine": _spine(),
        "temporal_event_indices": _temporal(),
    }
    payload.update(overrides)
    return payload


# ── The rows ──────────────────────────────────────────────────────────


def test_segment_id_is_the_position_the_answer_has_to_name():
    """The step's `llm_outputs` schema asks for `spine_block_position`.

    A table keyed by anything else names identifiers the model's answer
    cannot use, which is what `segment_id` off an A-roll slot would have
    been even had the input been routed.
    """
    payload = _inputs()
    assert "a_roll_assignments" not in payload  # what the runner hands it
    rows = build_sfx_candidates(payload)
    assert [r["segment_id"] for r in rows] == ["hook", 1, 2]


def test_a_speech_block_carries_its_line_and_a_slot_carries_its_note():
    rows = build_sfx_candidates(_inputs())
    assert rows[0]["text"] == "i can feel the silent judgment."
    assert rows[1]["text"] == "The breath after the hook."
    assert all(r["text"] for r in rows), (
        f"a row came out with no summary text: {rows}"
    )


# ── The transient column ──────────────────────────────────────────────


def test_the_transient_column_is_measured_not_a_constant(monkeypatch):
    # The bridge reads per-clip index FILES; hand it the fixture's indices.
    monkeypatch.setattr(
        'library.steps.step_4_04_plan_sfx.bridge._temporal_lookup',
        lambda data: {t['clip_id']: t
                      for t in data.get('temporal_event_indices', [])})
    """It was the literal string "No" on every row it built.

    Now it counts the energy peaks step 1.04 measured inside the
    block's own source range: none over the hook's 0.836-3.234, because
    the peaks on this clip start at 7.833, and three over 9.699-12.681.
    """
    rows = build_sfx_candidates(_inputs())
    assert rows[0]["action_sfx_suggested"] == "0 audio transients"
    assert rows[2]["action_sfx_suggested"] == "3 audio transients"


def test_a_block_with_no_source_clip_says_it_was_not_measured():
    """State the absence; never report it as a measured zero.

    A non-speech block names no `clip_id` on the SPINE, and this used to
    stop there - `not measured (no source clip)` on 5 of 001's 13 rows,
    the rows a whoosh would go on, while `b_roll_assignments` named the
    covering cutaway in the same prompt. With no cutaway over it there
    is genuinely nothing, and the cell says both halves.
    """
    rows = build_sfx_candidates(_inputs())
    assert rows[1]["action_sfx_suggested"] == (
        "not measured (no source clip and no cutaway over it)")


def test_a_covered_block_names_the_cutaway_and_says_it_plays_silent():
    """The cutaway is placed `video_only`; its own audio is never heard.

    So the honest cell is not "N transients" either - counting them
    would describe a sound nobody hears. See
    library/tools/broll_coverage.py.
    """
    rows = build_sfx_candidates(_inputs(b_roll_assignments=[{
        "spine_block_position": 1, "clip_id": "clip_004",
        "source_file": "/raw/IMG_1809.MOV",
        "video_in": 0.0, "video_out": 3.0,
    }]))
    assert rows[1]["action_sfx_suggested"] == (
        "covered by clip_004, video only - the cutaway's own audio is "
        "never heard")
