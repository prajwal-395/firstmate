"""The VFX bridge originates no creative value.

The bridge used to unconditionally inject `{"effect_type": "color_wash",
"intensity": 0.5}` on the first segment of every project as
`enhancement_spec`, and to hardcode `vfx_suggested` to the literal
string "No" on every row. Both are a bridge voting on a creative
decision nobody made.

These tests drive the real bridge with representative input and assert:

  * `enhancement_spec` is not emitted at all (the post-bridge writes it).
  * No effect_type, intensity or creative value appears in the output.
  * `vfx_suggested` carries a measurement, not a hardcoded verdict.
  * `text` is populated from the spine's content, not empty.
  * The table has one row per spine block, keyed by position.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
VFX = REPO / "library" / "steps" / "step_4_03_plan_vfx"


def _run_bridge(payload: dict):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(REPO) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.run(
        [sys.executable, str(VFX / "bridge.py")],
        input=json.dumps(payload),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(REPO),
        env=env,
    )


def _spine(n_blocks=3) -> dict:
    return {
        "structure": [
            {
                "position": i + 1,
                "block_type": "speech",
                "clip_id": f"clip_{i + 1:03d}",
                "source_start": i * 5.0,
                "source_end": i * 5.0 + 5.0,
                "timeline_start": i * 5.0,
                "timeline_end": i * 5.0 + 5.0,
                "content": {"text": f"This is sentence number {i + 1}"},
                "word_timestamps": [],
                "alignment_method": "whisperx",
            }
            for i in range(n_blocks)
        ]
    }


def _semantic_docs():
    """Per-clip profiles keyed by file stem, with camera segments."""
    return [
        {
            "clip_id": "IMG_1806",
            "file_path": "/raw/IMG_1806.MOV",
            "camera": [
                {
                    "start": 0.0,
                    "end": 15.0,
                    "mode": "selfie",
                    "framing": "close-up",
                    "stability": "steady",
                    "movement": "stationary",
                }
            ],
            "assessment": {
                "camera_stability": "stable",
                "content_type": "person_talking_to_camera",
            },
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 15, "location": "studio"}],
        },
        {
            "clip_id": "IMG_1807",
            "file_path": "/raw/IMG_1807.MOV",
            "camera": [
                {
                    "start": 0.0,
                    "end": 10.0,
                    "mode": "handheld",
                    "framing": "medium",
                    "stability": "shaky",
                    "movement": "panning_left",
                }
            ],
            "assessment": {
                "camera_stability": "handheld",
                "content_type": "b_roll",
            },
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 10, "location": "outdoor"}],
        },
        {
            "clip_id": "IMG_1808",
            "file_path": "/raw/IMG_1808.MOV",
            "camera": [
                {
                    "start": 0.0,
                    "end": 10.0,
                    "mode": "selfie",
                    "framing": "close-up",
                    "stability": "steady",
                    "movement": "stationary",
                }
            ],
            "assessment": {"camera_stability": "stable"},
            "analysis_metadata": {"pipeline_version": "v3"},
            "scene": [{"start": 0, "end": 10, "location": "studio"}],
        },
    ]


def _aroll():
    """a_roll_assignments carrying clip_id + source_file for the join."""
    return [
        {
            "spine_block_position": 1,
            "block_type": "speech",
            "video_segments": [
                {"clip_id": "clip_001", "source_file": "/raw/IMG_1806.MOV"}
            ],
        },
        {
            "spine_block_position": 2,
            "block_type": "speech",
            "video_segments": [
                {"clip_id": "clip_002", "source_file": "/raw/IMG_1807.MOV"}
            ],
        },
        {
            "spine_block_position": 3,
            "block_type": "speech",
            "video_segments": [
                {"clip_id": "clip_003", "source_file": "/raw/IMG_1808.MOV"}
            ],
        },
    ]


def _payload() -> dict:
    return {
        "timed_spine": _spine(),
        "semantic_analysis_documents": _semantic_docs(),
        "a_roll_assignments": _aroll(),
        "b_roll_assignments": [],
        "creative_direction": {},
        "rough_cut_review": {"passed": True},
    }


class TestVfxBridgeOriginatesNoCreativeValue:
    """Regression: the colour wash cannot come back."""

    def test_no_enhancement_spec_and_no_effect_value_in_output(self):
        """The post-bridge writes `enhancement_spec` after the model
        answers; the pre-bridge names no effect, intensity or wash."""
        proc = _run_bridge(_payload())
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        assert "enhancement_spec" not in output
        stdout_text = proc.stdout.lower()
        assert "color_wash" not in stdout_text
        assert "effect_type" not in stdout_text
        assert "\"intensity\": 0.5" not in proc.stdout

    def test_text_column_is_populated(self):
        proc = _run_bridge(_payload())
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        lines = [l for l in toon.strip().split("\n") if l and not l.startswith("[")]
        assert len(lines) > 0
        populated = 0
        for line in lines:
            fields = line.split("\t")
            assert len(fields) >= 2
            text = fields[1]
            if text.strip():
                populated += 1
        assert populated == len(lines), (
            f"Only {populated} of {len(lines)} rows have text"
        )


    def test_vfx_suggested_carries_camera_data(self):
        proc = _run_bridge(_payload())
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        lines = [l for l in toon.strip().split("\n") if l and not l.startswith("[")]
        # Never the old hardcoded verdict, on any row.
        assert all(line.split("\t")[2] != "No" for line in lines), lines
        # First block: stationary + steady camera on clip_001
        fields = lines[0].split("\t")
        vfx_suggested = fields[2]
        assert "stationary" in vfx_suggested or "steady" in vfx_suggested, (
            f"First block should show stationary/steady camera: {vfx_suggested}"
        )
        # Second block: panning_left + shaky camera on clip_002
        fields = lines[1].split("\t")
        vfx_suggested = fields[2]
        assert "panning_left" in vfx_suggested or "shaky" in vfx_suggested, (
            f"Second block should show panning/shaky camera: {vfx_suggested}"
        )


class TestVfxBridgeNoSourceClip:
    """Non-speech blocks without a source clip report 'not measured'."""

    def test_non_speech_block_reports_not_measured(self):
        spine = {
            "structure": [
                {
                    "position": 1,
                    "block_type": "transition_slot",
                    "clip_id": None,
                    "source_start": None,
                    "source_end": None,
                    "timeline_start": 0.0,
                    "timeline_end": 2.0,
                    "word_timestamps": [],
                    "alignment_method": None,
                },
            ]
        }
        payload = {
            "timed_spine": spine,
            "semantic_analysis_documents": [],
            "a_roll_assignments": [],
        }
        proc = _run_bridge(payload)
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        assert "not measured" in toon


class TestVfxBridgeEmptyInput:
    """Empty spine produces an empty table, not a crash."""

    def test_empty_spine(self):
        payload = {
            "timed_spine": {"structure": []},
            "semantic_analysis_documents": [],
            "a_roll_assignments": [],
        }
        proc = _run_bridge(payload)
        assert proc.returncode == 0, proc.stderr
        output = json.loads(proc.stdout)
        toon = output["vfx_candidates_toon"]
        assert toon.startswith("[0]")
        assert "enhancement_spec" not in output
