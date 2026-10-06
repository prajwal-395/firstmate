"""A dead section that reaches the render must not stay there.

The defect these tests name: `pacing.find_dead_sections` measures the
dead stretches and `propose_trims_for_dead_sections` turns each into a
measured trim - and at plan time that is where it stops. A dead section
the plan declines, or one never analysed, ships in the render. The
correction loop closes that on the as-built sequence: it applies the
head/tail trims, ripples the timeline, and re-measures with the
production analysis until the sequence is clean or the loop names why a
section cannot go.
"""

from __future__ import annotations

from library.tools.edit_correction import (
    EditCorrectionLoop,
    apply_verified_trims,
    correct_pacing,
)
from library.tools.correction_loop import (
    VERDICT_CANNOT_CORRECT,
    VERDICT_PASS,
)


def _block(position, tl_start, tl_end, clip, words):
    return {
        "position": position,
        "clip_id": clip,
        "source_start": 0.0,
        "source_end": tl_end - tl_start,
        "timeline_start": float(tl_start),
        "timeline_end": float(tl_end),
        "word_timestamps": [
            {"source_start": float(s), "source_end": float(e)}
            for s, e in words],
    }


def _static_silent_index(words, events=()):
    return {
        "motion_energy": {
            "values": [0.0] * 100,
            "sample_rate_hz": 10,
        },
        "speech_regions": [
            {"start": float(s), "end": float(e)} for s, e in words],
        "audio_events": [{"time": float(t)} for t in events],
    }


def _two_block_sequence():
    # Block 1 speaks at [1,2], block 2 at [16,17]: the 14s stretch
    # between them is dead against the sequence's own distribution.
    # The surviving pauses carry audio events, so the loop stops after
    # closing the real dead air instead of chasing 1s pauses.
    blocks = [
        _block(1, 0, 10, "c1", [(1, 2)]),
        _block(2, 10, 20, "c2", [(6, 7)]),
    ]
    indices = {
        "c1": _static_silent_index([(1, 2)], events=[0.5]),
        "c2": _static_silent_index([(6, 7)], events=[8.5]),
    }
    return blocks, indices


def test_the_loop_closes_the_dead_section_and_repasses():
    blocks, indices = _two_block_sequence()

    record = correct_pacing(blocks, indices)

    assert record["passed"] is True
    assert record["verdict"] == VERDICT_PASS
    assert [it["outcome"] for it in record["iterations"]] == [
        "corrected", "pass"]
    assert len(record["sections"]) == 2
    assert all(s["trim_applied"] for s in record["sections"])
    assert record["remaining"] == []
    assert len(record["applied_trims"]) == 2
    assert record["refused_trims"] == []


def test_the_trim_amounts_are_the_measured_dead_durations():
    # Block 1 loses its 8s tail, block 2 its 6s head - the dead
    # durations themselves, not a constant cut length.
    blocks, indices = _two_block_sequence()

    record = correct_pacing(blocks, indices)

    by_position = {t["block_position"]: t
                   for t in record["applied_trims"]}
    assert by_position[1]["trim_edge"] == "end"
    assert by_position[1]["trim_seconds"] == 8.0
    assert by_position[2]["trim_edge"] == "start"
    assert by_position[2]["trim_seconds"] == 6.0


def test_the_restructured_sequence_is_contiguous_and_keeps_its_words():
    blocks, indices = _two_block_sequence()

    loop = EditCorrectionLoop(blocks, indices)
    result = loop.run()

    assert result.passed
    restructured = loop.blocks
    assert [(b["timeline_start"], b["timeline_end"])
            for b in restructured] == [(0.0, 2.0), (2.0, 6.0)]
    # Words are source-anchored: they ride the ripple untouched.
    assert restructured[0]["word_timestamps"] == [
        {"source_start": 1.0, "source_end": 2.0}]
    assert restructured[1]["word_timestamps"] == [
        {"source_start": 6.0, "source_end": 7.0}]
    # And they still land inside their blocks' new spans.
    assert restructured[1]["source_start"] == 6.0


def test_a_mid_block_pause_names_the_split_it_needs():
    # The defect this names: "closing" a mid-block pause by trimming
    # either edge eats speech. The loop refuses and names the split it
    # does not do.
    blocks = [_block(1, 0, 10, "c1", [(1, 2), (8, 9)])]
    indices = {"c1": _static_silent_index([(1, 2), (8, 9)])}

    loop = EditCorrectionLoop(blocks, indices)
    result = loop.run()

    assert not result.passed
    assert result.verdict == VERDICT_CANNOT_CORRECT
    assert "split" in result.detail


def test_a_trim_that_would_eat_a_word_is_refused():
    blocks = [_block(1, 0, 10, "c1", [(4, 6)])]
    proposals = [{
        "timeline_start": 0.0,
        "timeline_end": 5.0,
        "block_position": 1,
        "trim_edge": "start",
        "trim_seconds": 5.0,
    }]

    _new, applied, refused = apply_verified_trims(blocks, proposals)

    assert applied == []
    assert len(refused) == 1
    assert "eat speech" in refused[0]["reason"]
    # The input blocks are untouched - the refusal changed nothing.
    assert blocks[0]["timeline_start"] == 0.0


def test_a_trim_that_would_delete_its_block_is_refused():
    blocks = [_block(1, 0, 10, "c1", [(1, 2)])]
    proposals = [{
        "timeline_start": 0.0,
        "timeline_end": 10.0,
        "block_position": 1,
        "trim_edge": "start",
        "trim_seconds": 10.0,
    }]

    _new, applied, refused = apply_verified_trims(blocks, proposals)

    assert applied == []
    assert "delete" in refused[0]["reason"]


def test_a_join_spanning_section_trims_both_sides():
    # The dead stretch straddles the block join: the outgoing block
    # gives up its tail, the incoming block its head, and the two meet.
    blocks, indices = _two_block_sequence()

    record = correct_pacing(blocks, indices)

    assert record["passed"] is True
    assert len(record["applied_trims"]) == 2
    edges = {t["block_position"]: t["trim_edge"]
             for t in record["applied_trims"]}
    assert edges == {1: "end", 2: "start"}


def test_an_unmeasured_sequence_passes_without_claiming_work():
    # No temporal indices: every gap is unmeasured, never dead. The
    # loop passes with no sections and no trims - not with a
    # restructure it never earned.
    blocks = [_block(1, 0, 10, "c1", [(1, 2)])]

    record = correct_pacing(blocks, {})

    assert record["passed"] is True
    assert record["sections"] == []
    assert record["applied_trims"] == []
    assert [it["outcome"] for it in record["iterations"]] == ["pass"]


def _stage_indices(tmp_path, indices):
    import json

    from library.tools.project_layout import Area, ProjectLayout

    for clip_id, document in indices.items():
        path = ProjectLayout(str(tmp_path)).read_path(
            Area.TEMPORAL_INDEX, f"{clip_id}.json")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document), encoding="utf-8")


class TestRecheckDeadSectionsCaller:
    def test_no_plan_time_sections_means_no_recheck(self, tmp_path):
        # The defect this names: a loop that runs with nothing to close
        # and reports a pass it never earned. Clean plan-time analysis
        # means no recheck at all.
        from library.steps.step_6_02_validate_output.bridge import (
            recheck_dead_sections,
        )

        blocks, _indices = _two_block_sequence()
        manifest = {"_spine_blocks": blocks, "dead_sections": []}

        assert recheck_dead_sections(
            manifest, str(tmp_path)) is None

    def test_missing_indices_are_named_not_guessed(self, tmp_path):
        # Plan-time sections outstanding but the indices are gone: the
        # loop cannot re-measure, and says so instead of passing.
        from library.steps.step_6_02_validate_output.bridge import (
            recheck_dead_sections,
        )

        blocks, _indices = _two_block_sequence()
        manifest = {
            "_spine_blocks": blocks,
            "dead_sections": [{"timeline_start": 2.0,
                               "timeline_end": 16.0}],
        }

        result = recheck_dead_sections(manifest, str(tmp_path))

        assert result is not None
        assert result.passed is False
        assert "temporal index" in result.detail

    def test_the_caller_closes_the_loop_on_the_built_spine(self, tmp_path):
        # End to end at the caller: the manifest spine plus the
        # project's own index files, closed with verified trims.
        from library.steps.step_6_02_validate_output.bridge import (
            recheck_dead_sections,
        )

        blocks, indices = _two_block_sequence()
        _stage_indices(tmp_path, indices)
        manifest = {
            "_spine_blocks": blocks,
            "dead_sections": [{"timeline_start": 2.0,
                               "timeline_end": 16.0}],
        }

        result = recheck_dead_sections(manifest, str(tmp_path))

        assert result is not None
        assert result.metric == "pacing_correction"
        assert result.passed is True
        record = result.value["recheck"]
        assert len(record["applied_trims"]) == 2
        assert record["remaining"] == []
