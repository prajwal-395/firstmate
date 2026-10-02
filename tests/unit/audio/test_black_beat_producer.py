"""The black-beat escape hatch: a planner can declare a deliberate hold on
black, and compile_manifest will accept it.  An undeclared gap still fails.

A gap must be deliberate and defensible: the planner can emit the
declaration, it passes the spine contract and the coverage assertion, and
every malformed or missing declaration still hard-fails.

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

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT))

from library.tools.spine_contract import (
    MAX_DECLARED_BLACK_BEAT_SECONDS,
    SpineContractError,
    validate_spine_blocks,
)
from library.tools.render_qa import detect_black_frames
from library.steps.step_5_04_compile_manifest.step import (
    _assert_timeline_fully_covered,
    _video_coverage_gaps,
)
from library.steps.step_2_05_mesh_spine.post_bridge import enrich_spine
from library.steps.step_6_02_validate_output.bridge import _declared_black_beats


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

    @pytest.mark.parametrize("case", [
        "valid_declaration",
        "false_flag_is_not_a_declaration",
        "captured_block_with_declaration",
    ])
    def test_valid_black_beat_shapes_pass(self, case, captured_run):
        """Accepted shapes. The captured spine's undeclared blocks ride
        along in the last case, so an undeclared block is covered too."""
        if case == "valid_declaration":
            blocks = [_make_spine_block(
                1, "transition_slot", 4.0, 5.0,
                intentional_black_beat=True,
                black_beat_reason="hold on black before the tonal shift",
            )]
        elif case == "false_flag_is_not_a_declaration":
            blocks = [_make_spine_block(
                1, "transition_slot", 4.0, 5.0,
                intentional_black_beat=False,
            )]
        else:
            blocks = copy.deepcopy(captured_run["timed_spine_structure"])
            slot = next(
                b for b in blocks if b["block_type"] == "transition_slot"
            )
            slot["intentional_black_beat"] = True
            slot["black_beat_reason"] = "silence before the next section"
        # Should not raise
        validate_spine_blocks(blocks)

    def test_each_malformed_declaration_fails_by_name(self):
        """A declaration on speech, and one without a reason, both refuse."""
        on_speech = _make_spine_block(
            1, "speech", 4.0, 5.0,
            clip_id="clip_001",
            source_start=10.0, source_end=11.0,
            word_timestamps=[{"word": "test", "source_start": 10.0,
                              "source_end": 10.5}],
            alignment_method="whisperx",
            intentional_black_beat=True,
            black_beat_reason="dramatic pause",
        )
        no_reason = _make_spine_block(
            1, "transition_slot", 4.0, 5.0, intentional_black_beat=True)
        for block, match in (
            (on_speech, "speech block declares intentional_black_beat"),
            (no_reason, "black_beat_reason is missing or empty"),
        ):
            with pytest.raises(SpineContractError, match=match):
                validate_spine_blocks([block])


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

    def test_render_gate_judges_black_by_the_declaration(self):
        """Each row: blackdetect segments against one declared beat."""
        declared = [(24.259, 26.259)]
        rows = [
            # exactly the bound compile_manifest accepts
            ((24.5, 24.5 + MAX_DECLARED_BLACK_BEAT_SECONDS), True),
            # blackdetect reports whole frames: a frame wide still passes
            ((24.492, 25.025), True),
            # black nobody declared
            ((4.0, 4.4), False),
            # longer than a beat, even inside the declaration
            ((24.4, 26.0), False),
            # straddling the declaration's edge
            ((24.0, 24.4), False),
        ]
        for segment, passes in rows:
            res = self._detect(_blackdetect_stderr(segment),
                               declared_beats=declared)
            assert res.passed is passes, (segment, res.detail)
            if segment == (4.0, 4.4):
                assert res.severity == "error"
                assert res.value[0]["declared"] is False
                assert "undeclared" in res.detail
            if passes and segment[0] == 24.5:
                assert res.value[0]["declared"] is True


# ─── End-to-end with real captured run data ───────────────────────────

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "captured_run"


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
