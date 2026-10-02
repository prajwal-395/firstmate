"""Rung 7 (PA3.2/PA2.2): the plan's ASL windows beside delivered ASL.

PA3.2 - "average shot length around 2.5s in the opening 20s, then let
it relax to ~5s" - had no plan spelling and no measurement. Step 2.05
validates the windows; `library/tools/pacing.py` measures the built
picture against them into `compile_manifest`'s `pacing_report`,
report-only. Abutting clips share their edge frame, so one straight
cut is one cut, not two. A window naming a block the built spine does
not carry reports the miss instead of refusing - the picture is built
and the report is what says the target missed its address.
"""
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from library.tools.pacing import measure_pacing

FPS = 30.0


def _block(position, start_f, end_f):
    return {"position": position, "block_type": "speech",
            "timeline_start_frame": start_f,
            "timeline_end_frame": end_f,
            "timeline_start": start_f / FPS,
            "timeline_end": end_f / FPS}


def _clip(label, start_f, end_f):
    return {"label": label, "timeline_in_frame": start_f,
            "timeline_out_frame": end_f,
            "timeline_in": start_f / FPS, "timeline_out": end_f / FPS}


def test_delivered_asl_counts_each_cut_once_beside_either_target():
    """20 s, one mid cut shared by two abutting clips: 2 shots, ASL 10."""
    report = measure_pacing(
        [_block(1, 0, 300), _block(2, 300, 600)],
        [_clip("a", 0, 300), _clip("b", 300, 600)], [],
        [{"start_block": 1, "end_block": 2, "asl_seconds": 2.5}],
        FPS)
    (row,) = report
    assert row["cuts"] == 1
    assert row["window_seconds"] == 20.0
    assert row["delivered_asl_seconds"] == 10.0
    assert row["target_asl_seconds"] == 2.5

    # PA2.2's feel survives compile without being made numeric.
    report = measure_pacing(
        [_block(1, 0, 600)], [_clip("a", 0, 300), _clip("b", 300, 600)],
        [], [{"start_block": 1, "end_block": 1,
              "feel": "faster cuts as it builds"}], FPS)
    (row,) = report
    assert row["target_asl_seconds"] is None
    assert row["target_feel"] == "faster cuts as it builds"
    assert row["delivered_asl_seconds"] == 10.0


def test_b_roll_edges_are_shots_too():
    """A V2 cutaway over speech changes the visible shot: it cuts."""
    report = measure_pacing(
        [_block(1, 0, 600)],
        [_clip("a", 0, 600)],
        [_clip("v", 150, 450)],
        [{"start_block": 1, "end_block": 1, "asl_seconds": 5.0}],
        FPS)
    (row,) = report
    # Edges at 150 and 450: three shots over 20 s.
    assert row["cuts"] == 2
    assert row["delivered_asl_seconds"] == round(20.0 / 3, 3)

