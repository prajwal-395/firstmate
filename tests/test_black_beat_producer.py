"""The black-beat escape hatch: a planner can declare a deliberate hold on
black, and compile_manifest will accept it.  An undeclared gap still fails.

The captain ruled both gaps and no-gaps have their place, but a gap must be
deliberate and defensible.  PR #80 landed the consumer in compile_manifest;
this file tests the producer side - that the planner can emit the
declaration, that it passes the spine contract and the coverage assertion,
and that every malformed or missing declaration still hard-fails.

Uses real captured run data from `tests/fixtures/captured_run/` so the
tests exercise the full data flow, not just synthetic shapes.

Both gates that judge black are covered: `compile_manifest` on the
manifest and `render_qa` on the rendered file.  They read one bound and
one declaration helper from the spine contract, so a beat that survives
compilation cannot be failed after a full render.
"""

import copy
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from library.tools.spine_contract import (
    MAX_DECLARED_BLACK_BEAT_SECONDS,
    SpineContractError,
    declared_black_beat_ranges,
    validate_spine_blocks,
)
from library.tools.render_qa import detect_black_frames, run_full_render_qa
from library.steps.step_5_04_compile_manifest.step import (
    _assert_timeline_fully_covered,
    _video_coverage_gaps,
)
from library.steps.step_2_05_mesh_spine.post_bridge import enrich_spine
from library.steps.step_6_02_validate_output.step import _declared_black_beats


# ─── Helpers ──────────────────────────────────────────────────────────

def _make_spine_block(position, block_type, start, end, **extra):
    """A minimal spine block that satisfies the contract."""
    block = {
        "position": position,
        "block_type": block_type,
        "clip_id": None,
        "source_start": None,
        "source_end": None,
        "timeline_start": start,
        "timeline_end": end,
        "word_timestamps": [],
        "alignment_method": None,
    }
    block.update(extra)
    return block


def _manifest_with_spine(v1, spine_blocks, duration=10.0, fps=30.0, v2=()):
    """A manifest whose _spine_blocks are set from the given blocks."""
    def clips(spans, prefix):
        return [
            {"timeline_in": s, "timeline_out": e,
             "timeline_in_frame": int(round(s * fps)),
             "timeline_out_frame": int(round(e * fps)),
             "label": f"{prefix}_{i}"}
            for i, (s, e) in enumerate(spans)
        ]
    return {
        "project": {"frame_rate": fps, "duration_seconds": duration},
        "tracks": {
            "V1": {"label": "A-Roll", "clips": clips(v1, "aroll")},
            "V2": {"label": "B-Roll", "clips": clips(v2, "broll")},
        },
        "_spine_blocks": spine_blocks,
    }


# ─── Spine contract validation ────────────────────────────────────────

class TestSpineContractBlackBeatValidation:
    """The spine contract gate catches malformed declarations at emit time."""

    def test_valid_declaration_passes(self):
        blocks = [_make_spine_block(
            1, "transition_slot", 4.0, 5.0,
            intentional_black_beat=True,
            black_beat_reason="hold on black before the tonal shift",
        )]
        # Should not raise
        validate_spine_blocks(blocks)

    def test_declaration_on_speech_block_fails(self):
        blocks = [_make_spine_block(
            1, "speech", 4.0, 5.0,
            clip_id="clip_001",
            source_start=10.0, source_end=11.0,
            word_timestamps=[{"word": "test", "source_start": 10.0,
                              "source_end": 10.5}],
            alignment_method="whisperx",
            intentional_black_beat=True,
            black_beat_reason="dramatic pause",
        )]
        with pytest.raises(SpineContractError,
                           match="speech block declares intentional_black_beat"):
            validate_spine_blocks(blocks)

    def test_declaration_on_hook_block_fails(self):
        blocks = [_make_spine_block(
            "hook", "hook", 0.0, 1.0,
            clip_id="clip_001",
            source_start=0.0, source_end=1.0,
            word_timestamps=[{"word": "hey", "source_start": 0.0,
                              "source_end": 0.5}],
            alignment_method="whisperx",
            intentional_black_beat=True,
            black_beat_reason="dramatic pause",
        )]
        with pytest.raises(SpineContractError,
                           match="speech block declares intentional_black_beat"):
            validate_spine_blocks(blocks)

    def test_declaration_without_reason_fails(self):
        blocks = [_make_spine_block(
            1, "transition_slot", 4.0, 5.0,
            intentional_black_beat=True,
        )]
        with pytest.raises(SpineContractError,
                           match="black_beat_reason is missing or empty"):
            validate_spine_blocks(blocks)

    def test_declaration_with_empty_reason_fails(self):
        blocks = [_make_spine_block(
            1, "transition_slot", 4.0, 5.0,
            intentional_black_beat=True,
            black_beat_reason="   ",
        )]
        with pytest.raises(SpineContractError,
                           match="black_beat_reason is missing or empty"):
            validate_spine_blocks(blocks)

    def test_block_without_declaration_passes(self):
        """Normal blocks with no black beat keys still pass."""
        blocks = [_make_spine_block(1, "transition_slot", 4.0, 5.0)]
        validate_spine_blocks(blocks)

    def test_false_flag_is_not_treated_as_declaration(self):
        """intentional_black_beat=False is not a declaration."""
        blocks = [_make_spine_block(
            1, "transition_slot", 4.0, 5.0,
            intentional_black_beat=False,
        )]
        # False is falsy so the validation gate skips it
        validate_spine_blocks(blocks)


# ─── Post-bridge passthrough ──────────────────────────────────────────

class TestPostBridgeBlackBeatPassthrough:
    """The post_bridge's shallow copy carries the declaration through."""

    def _minimal_spine_with_beat(self):
        return {
            "structure": [
                {
                    "position": 1,
                    "block_type": "transition_slot",
                    "duration_seconds": 2.0,
                    "music_behavior": "prominent",
                    "visual_note": "B-roll cutaway",
                    "content": None,
                    "intentional_black_beat": True,
                    "black_beat_reason": "silence before the reveal",
                },
            ],
        }

    def test_declared_beat_survives_enrichment(self):
        spine = self._minimal_spine_with_beat()
        result = enrich_spine(spine, {}, {}, {"project_config": {"target_duration_seconds": 2.0}})
        blocks = result["audio_spine"]["structure"]
        assert len(blocks) == 1
        assert blocks[0]["intentional_black_beat"] is True
        assert blocks[0]["black_beat_reason"] == "silence before the reveal"

    def test_normal_block_has_no_beat_keys(self):
        spine = {
            "structure": [
                {
                    "position": 1,
                    "block_type": "transition_slot",
                    "duration_seconds": 2.0,
                    "music_behavior": "prominent",
                    "visual_note": "B-roll cutaway",
                    "content": None,
                },
            ],
        }
        result = enrich_spine(spine, {}, {}, {"project_config": {"target_duration_seconds": 2.0}})
        blocks = result["audio_spine"]["structure"]
        assert "intentional_black_beat" not in blocks[0]
        assert "black_beat_reason" not in blocks[0]


# ─── End-to-end: declared beat vs undeclared gap ──────────────────────

class TestEndToEndBlackBeat:
    """Prove both directions against the coverage assertion."""

    def test_declared_beat_passes_coverage_check(self):
        """A gap covered by a declared black beat passes compilation."""
        spine = [_make_spine_block(
            1, "transition_slot", 3.5, 5.0,
            intentional_black_beat=True,
            black_beat_reason="hold on black before the turn",
        )]
        # V1 has a 0.4s gap from 4.0 to 4.4, inside the spine block
        manifest = _manifest_with_spine(
            v1=[(0.0, 4.0), (4.4, 10.0)],
            spine_blocks=[{
                "position": 1,
                "timeline_start": 3.5,
                "timeline_end": 5.0,
                "block_type": "transition_slot",
                "music_behavior": "full",
                "intentional_black_beat": True,
                "black_beat_reason": "hold on black before the turn",
            }],
        )
        # Should have a gap
        gaps = _video_coverage_gaps(manifest)
        assert len(gaps) == 1
        assert gaps[0] == pytest.approx((4.0, 4.4))
        # But the assertion should pass because the beat is declared
        _assert_timeline_fully_covered(manifest)

    def test_undeclared_gap_same_size_still_fails(self):
        """The same gap without a declaration hard-fails."""
        manifest = _manifest_with_spine(
            v1=[(0.0, 4.0), (4.4, 10.0)],
            spine_blocks=[{
                "position": 1,
                "timeline_start": 3.5,
                "timeline_end": 5.0,
                "block_type": "transition_slot",
                "music_behavior": "full",
                # No intentional_black_beat
            }],
        )
        with pytest.raises(ValueError,
                           match="no spine block declares"):
            _assert_timeline_fully_covered(manifest)


# ─── The render gate honours the same ruling ──────────────────────────

def _blackdetect_stderr(*segments):
    """ffmpeg stderr for the given (start, end) black segments."""
    return "\n".join(
        f"[blackdetect @ 0x123] black_start:{s} black_end:{e} "
        f"black_duration:{e - s:.3f}"
        for s, e in segments
    )


class TestRenderQADeclaredBeats:
    """The render gate must judge black by the same ruling as the manifest
    gate - otherwise a beat that survives compilation burns a full render
    and then fails at the last step."""

    def _detect(self, stderr, **kwargs):
        with patch("subprocess.run",
                   return_value=MagicMock(stderr=stderr, returncode=0)):
            return detect_black_frames("dummy.mp4", **kwargs)

    def test_declared_beat_at_the_maximum_passes(self):
        """A beat of exactly MAX_DECLARED_BLACK_BEAT_SECONDS - the length
        the handoff documents and compile_manifest accepts - passes."""
        res = self._detect(
            _blackdetect_stderr((24.5, 24.5 + MAX_DECLARED_BLACK_BEAT_SECONDS)),
            declared_beats=[(24.259, 26.259)],
        )
        assert res.passed
        assert res.value[0]["declared"] is True

    def test_declared_beat_reported_a_frame_wide_still_passes(self):
        """blackdetect reports whole frames, so the segment can run a
        frame past the planned gap; that is still the declared beat."""
        res = self._detect(
            _blackdetect_stderr((24.492, 25.025)),
            declared_beats=[(24.259, 26.259)],
        )
        assert res.passed

    def test_undeclared_black_still_fails(self):
        """The other direction: black nobody declared is still a defect."""
        res = self._detect(
            _blackdetect_stderr((4.0, 4.4)),
            declared_beats=[(24.259, 26.259)],
        )
        assert not res.passed
        assert res.severity == "error"
        assert res.value[0]["declared"] is False
        assert "undeclared" in res.detail

    def test_black_longer_than_a_beat_inside_a_declaration_fails(self):
        """A declaration excuses a beat, not a hole: black that outruns
        the bound fails even inside the declared block."""
        res = self._detect(
            _blackdetect_stderr((24.4, 26.0)),
            declared_beats=[(24.259, 26.259)],
        )
        assert not res.passed

    def test_black_straddling_a_declaration_edge_fails(self):
        """A beat must sit inside the block that declared it."""
        res = self._detect(
            _blackdetect_stderr((24.0, 24.4)),
            declared_beats=[(24.259, 26.259)],
        )
        assert not res.passed

    def test_no_declarations_means_all_black_fails(self):
        """Baseline: with nothing declared the check is unchanged."""
        res = self._detect(_blackdetect_stderr((24.4, 24.8)))
        assert not res.passed

    def test_clean_render_passes(self):
        res = self._detect("", declared_beats=[(24.259, 26.259)])
        assert res.passed
        assert res.value == []

    def test_run_full_render_qa_forwards_declared_beats(self):
        with patch("library.tools.render_qa.detect_black_frames") as detect:
            with patch("library.tools.render_qa.measure_lufs"), \
                 patch("library.tools.render_qa.detect_freeze_frames"), \
                 patch("library.tools.render_qa.analyze_color_histogram"), \
                 patch("library.tools.render_qa.verify_resolution"), \
                 patch("library.tools.render_qa.verify_framerate"), \
                 patch("library.tools.render_qa.verify_audio_streams"):
                run_full_render_qa("dummy.mp4",
                                   declared_black_beats=[(1.0, 2.0)])
        detect.assert_called_once_with("dummy.mp4",
                                       declared_beats=[(1.0, 2.0)])


class TestDeclaredBeatRanges:
    """Only a declaration the spine gate would have accepted is honoured."""

    def test_valid_declaration_yields_a_range(self):
        blocks = [_make_spine_block(
            1, "transition_slot", 24.259, 26.259,
            intentional_black_beat=True,
            black_beat_reason="hold on black before the turn",
        )]
        assert declared_black_beat_ranges(blocks) == [(24.259, 26.259)]

    def test_undeclared_block_yields_nothing(self):
        blocks = [_make_spine_block(1, "transition_slot", 24.259, 26.259)]
        assert declared_black_beat_ranges(blocks) == []

    def test_declaration_without_reason_yields_nothing(self):
        blocks = [_make_spine_block(
            1, "transition_slot", 24.259, 26.259,
            intentional_black_beat=True,
            black_beat_reason="   ",
        )]
        assert declared_black_beat_ranges(blocks) == []

    def test_speech_declaration_yields_nothing(self):
        blocks = [_make_spine_block(
            2, "speech", 10.741, 22.678,
            intentional_black_beat=True,
            black_beat_reason="dramatic pause",
        )]
        assert declared_black_beat_ranges(blocks) == []

    def test_validate_output_reads_the_manifest_spine(self):
        manifest = {"_spine_blocks": [
            {"position": 1, "block_type": "transition_slot",
             "timeline_start": 24.259, "timeline_end": 26.259,
             "intentional_black_beat": True,
             "black_beat_reason": "hold on black before the turn"},
            {"position": 2, "block_type": "speech",
             "timeline_start": 26.259, "timeline_end": 29.759},
        ]}
        assert _declared_black_beats(manifest) == [(24.259, 26.259)]

    def test_validate_output_without_a_spine_declares_nothing(self):
        assert _declared_black_beats({}) == []


# ─── End-to-end with real captured run data ───────────────────────────

FIXTURES = Path(__file__).parent / "fixtures" / "captured_run"


@pytest.fixture(scope="module")
def captured_run():
    """The spine and manifest spine blocks from the real captured run.

    Committed under `tests/fixtures/captured_run/` - the convention this
    repo already uses for run-derived fixtures - so these tests exercise
    real planner output on every machine and on CI, not only where the
    project directory happens to live.
    """
    with open(FIXTURES / "black_beat_spine.json") as f:
        return json.load(f)


def _first_declarable_block(spine_blocks):
    """A non-speech block long enough to hold a beat, or skip.

    A block shorter than the bound cannot contain a gap the declaration
    would excuse, and building one anyway inverts the test's V1 spans.
    """
    for block in spine_blocks:
        if block.get("block_type") in ("speech", "hook"):
            continue
        if (block["timeline_end"] - block["timeline_start"]
                >= MAX_DECLARED_BLACK_BEAT_SECONDS):
            return block
    pytest.skip("captured run has no non-speech block long enough for a beat")


def _manifest_with_gap_in(block, spine_blocks, project):
    """A manifest whose only picture hole is a 0.3s gap inside `block`."""
    middle = (block["timeline_start"] + block["timeline_end"]) / 2
    gap_start, gap_end = middle - 0.15, middle + 0.15
    duration = project["duration_seconds"]
    return _manifest_with_spine(
        v1=[(0.0, gap_start), (gap_end, duration)],
        spine_blocks=spine_blocks,
        duration=duration,
        fps=project["frame_rate"],
    ), (gap_start, gap_end)


class TestCapturedRunBlackBeat:
    """Verify the feature against real data from the captured run.

    That run has no intentional black beats - the feature did not exist
    when it was captured - so it is the honest baseline for both
    directions: declare on one of its blocks and the hole is excused,
    leave it undeclared and the same hole hard-fails.
    """

    def test_captured_spine_has_no_declarations(self, captured_run):
        """Baseline: the captured run never declared a black beat."""
        for block in captured_run["timed_spine_structure"]:
            assert "intentional_black_beat" not in block, (
                f"block {block.get('position')} has an unexpected "
                f"intentional_black_beat declaration"
            )

    def test_captured_spine_satisfies_the_contract(self, captured_run):
        validate_spine_blocks(captured_run["timed_spine_structure"])

    def test_adding_declaration_to_captured_block_passes_spine_contract(
        self, captured_run
    ):
        structure = copy.deepcopy(captured_run["timed_spine_structure"])
        slot = next(
            b for b in structure if b["block_type"] == "transition_slot"
        )
        slot["intentional_black_beat"] = True
        slot["black_beat_reason"] = "silence before the next section"
        validate_spine_blocks(structure)

    def test_adding_declaration_to_speech_block_fails_spine_contract(
        self, captured_run
    ):
        structure = copy.deepcopy(captured_run["timed_spine_structure"])
        speech = next(b for b in structure if b["block_type"] == "speech")
        speech["intentional_black_beat"] = True
        speech["black_beat_reason"] = "dramatic pause"
        with pytest.raises(SpineContractError,
                           match="speech block declares intentional_black_beat"):
            validate_spine_blocks(structure)

    def test_captured_manifest_gap_with_declaration_passes(self, captured_run):
        """A gap inside a declared block passes compilation, and the render
        gate excuses the black it produces."""
        spine_blocks = copy.deepcopy(captured_run["spine_blocks"])
        target = _first_declarable_block(spine_blocks)
        target["intentional_black_beat"] = True
        target["black_beat_reason"] = "breath before the next section"

        manifest, (gap_start, gap_end) = _manifest_with_gap_in(
            target, spine_blocks, captured_run["project"]
        )

        gaps = _video_coverage_gaps(manifest)
        assert len(gaps) == 1
        _assert_timeline_fully_covered(manifest)

        with patch("subprocess.run", return_value=MagicMock(
                stderr=_blackdetect_stderr((gap_start, gap_end)),
                returncode=0)):
            res = detect_black_frames(
                "dummy.mp4",
                declared_beats=_declared_black_beats(manifest),
            )
        assert res.passed

    def test_captured_manifest_gap_without_declaration_fails(
        self, captured_run
    ):
        """The same gap without a declaration hard-fails at compilation,
        and the render gate fails the same black - proving the escape
        hatch is not a universal gap pass."""
        spine_blocks = copy.deepcopy(captured_run["spine_blocks"])
        target = _first_declarable_block(spine_blocks)
        target.pop("intentional_black_beat", None)
        target.pop("black_beat_reason", None)

        manifest, (gap_start, gap_end) = _manifest_with_gap_in(
            target, spine_blocks, captured_run["project"]
        )

        with pytest.raises(ValueError, match="no spine block declares"):
            _assert_timeline_fully_covered(manifest)

        with patch("subprocess.run", return_value=MagicMock(
                stderr=_blackdetect_stderr((gap_start, gap_end)),
                returncode=0)):
            res = detect_black_frames(
                "dummy.mp4",
                declared_beats=_declared_black_beats(manifest),
            )
        assert not res.passed
