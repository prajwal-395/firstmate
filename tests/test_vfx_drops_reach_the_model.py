"""Finding 34: on the first post-bridge pass a dropped VFX entry raises
through `post_bridge_retry` so the model can correct the slip; a later
pass (or a direct call with no retry path) ships what resolves, with the
drops recorded. History: `docs/evidence/vfx_drops_reach_the_model.md`.
"""
import io
import json
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.steps.step_4_03_plan_vfx import post_bridge as pb  # noqa: E402
from library.tools import post_bridge_retry  # noqa: E402


def _spine():
    return {"structure": [
        {"position": 1, "block_type": "speech", "clip_id": "clip_a",
         "source_start": 0.0, "source_end": 5.0,
         "timeline_start": 0.0, "timeline_end": 5.0,
         "word_timestamps": [], "alignment_method": "whisperx",
         "content": {}},
        {"position": 2, "block_type": "speech", "clip_id": "clip_a",
         "source_start": 5.0, "source_end": 10.0,
         "timeline_start": 5.0, "timeline_end": 10.0,
         "word_timestamps": [], "alignment_method": "whisperx",
         "content": {}},
    ]}


def _data(creative, **extra):
    data = {"a_roll_assignments": [],
            "timed_spine": _spine(),
            "frame_rate": 30.0,
            "vfx_creative": creative}
    data.update(extra)
    return data


def _run_main(data):
    stdin = io.StringIO(json.dumps(data))
    stdout = io.StringIO()
    stderr = io.StringIO()
    old_stdin = sys.stdin
    sys.stdin = stdin
    try:
        with redirect_stdout(stdout), redirect_stderr(stderr):
            pb.main()
    finally:
        sys.stdin = old_stdin
    return json.loads(stdout.getvalue()), stderr.getvalue()


def test_the_b8_slips_are_drops_with_reasons():
    """The evidence, pinned: `zoom` names nothing readable, `speed`
    is not a stepped segment."""
    dropped = []
    resolved = pb.resolve_vfx(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom": 1.15}, "rationale": "punch on quit"},
         {"target_block_position": 2, "effect_type": "speed_ramp",
          "params": {"speed": 200}, "rationale": "ramp in"}],
        _spine(), 30.0, dropped=dropped)
    assert resolved == []
    assert {d.reason for d in dropped} == {"no_readable_parameters",
                                          "not_a_speed_step"}


def test_drops_raise_on_the_first_pass_so_the_model_can_correct():
    """A first-pass answer that drops entries raises naming each one -
    which is what travels the post_bridge_retry path."""
    import pytest

    data = _data(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom": 1.15}, "rationale": "punch on quit"}],
        **{post_bridge_retry.ATTEMPT_KEY: 1})
    with pytest.raises(ValueError, match="no_readable_parameters"):
        _run_main(data)


def test_drops_ship_with_their_reasons_once_retried():
    """The retry already happened (the model kept the slip), or there is
    no retry path to travel (a direct call outside the runner - the replay
    bench, the manifest tests): the resolved plan ships and the drop is
    recorded, never silent."""
    creative = [{"target_block_position": 1, "effect_type": "cut_in",
                 "params": {"zoom": 1.15}, "rationale": "punch on quit"}]
    for extra in ({post_bridge_retry.ATTEMPT_KEY: 2}, {}):
        out, _ = _run_main(_data(creative, **extra))
        spec = out["enhancement_spec"]
        assert spec["visual_effects"] == []
        assert spec["planning_basis"]["basis"] == "every_entry_dropped"
        (drop,) = spec["planning_basis"]["dropped"]
        assert drop["reason"] == "no_readable_parameters"


def test_a_clean_plan_never_raises():
    data = _data(
        [{"target_block_position": 1, "effect_type": "cut_in",
          "params": {"zoom_start": 1.0, "zoom_mid": 1.15,
                     "zoom_end": 1.0},
          "rationale": "punch on quit"}],
        **{post_bridge_retry.ATTEMPT_KEY: 1})
    out, _ = _run_main(data)
    assert len(out["enhancement_spec"]["visual_effects"]) == 1
