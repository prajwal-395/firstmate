"""The aligner's own report reaches a reader.

`alignment_report` is written on step 2.02's output and carries, per
passage, the leading gap, the largest silence BETWEEN two words, the
voiced fraction and how many anchors the search ranked. Two steps are
routed it and both drop it by name; nothing else has ever opened it. On
project 001's run of record body[0] - "today is march 25th, 2026." -
shipped with a 1.169s silence inside a 2.982s block, `voiced_fraction`
0.474, the worst of eight, and no step, no gate and no report said so.

It ORDERS and REPORTS. AGENTS.md section 6's "there is no gap threshold
and no voiced-fraction band" is unchanged, and this file pins that: no
number here decides anything.
"""

import json
from pathlib import Path

from library.tools.alignment_findings import (
    ALIGNMENT_LEGEND,
    passage_rows,
    summary_lines,
)
from library.tools.context_views import build_view


# The eight records 001's run of record really wrote, trimmed to the
# keys this module reads.
REPORT = [
    {"block": "Hook", "clip_id": "clip_011", "event": "ok",
     "source_start": 0.836, "source_end": 3.234,
     "leading_gap_seconds": 0.02, "largest_gap_seconds": 0.08,
     "voiced_fraction": 0.852, "anchors_considered": 26},
    {"block": "Body[#1]", "clip_id": "clip_011", "event": "ok",
     "source_start": 9.699, "source_end": 12.681,
     "leading_gap_seconds": 0.02, "largest_gap_seconds": 1.169,
     "voiced_fraction": 0.474, "anchors_considered": 2},
    {"block": "Body[#2]", "clip_id": "clip_017", "event": "reanchored",
     "source_start": 45.441, "source_end": 55.59,
     "leading_gap_seconds": 0.038, "largest_gap_seconds": 1.072,
     "voiced_fraction": 0.579, "anchors_considered": 4},
]


def test_the_worst_internal_gap_comes_first():
    rows = passage_rows(REPORT)
    assert [r["block"] for r in rows] == ["Body[#1]", "Body[#2]", "Hook"]
    assert rows[0]["largest_gap_seconds"] == 1.169
    assert rows[0]["duration_seconds"] == 2.982
    assert rows[0]["voiced_fraction"] == 0.474


def test_an_unmeasured_passage_keeps_its_row_and_says_so():
    rows = passage_rows([{"block": "Body[#1]", "clip_id": "clip_009"}])
    assert len(rows) == 1
    assert rows[0]["largest_gap_seconds"] is None
    assert rows[0]["voiced_fraction"] is None
    assert "unmeasured" in "\n".join(summary_lines(
        [{"block": "Body[#1]", "clip_id": "clip_009"}]))


def test_nothing_measured_prints_nothing():
    assert summary_lines([]) == []
    assert summary_lines(None) == []
    assert passage_rows(None) == []


def test_the_summary_states_the_share_of_the_block():
    lines = "\n".join(summary_lines(REPORT))
    assert "1.169s = 39% of the block" in lines
    assert "No threshold fires" in lines


def test_the_view_carries_the_legend_and_every_passage():
    view = build_view("alignment", {
        "speech_sequence": {"alignment_report": REPORT}})["alignment"]
    assert "largest_gap_seconds" in view["legend"]
    assert len(view["passages"]) == 3
    assert view["passages"][0]["largest_gap_seconds"] == 1.169


def test_the_view_is_empty_when_there_is_no_report():
    assert build_view("alignment", {"speech_sequence": {}}) == {}
    assert build_view("alignment", {}) == {}


def test_the_legend_says_what_a_key_is_and_never_what_to_conclude():
    """A legend defines; it does not instruct (AGENTS.md 10.1)."""
    text = " ".join(ALIGNMENT_LEGEND.values()).lower()
    for verdict in ("should ", "must ", "too long", "too short", "reject",
                    "fail", "prefer "):
        assert verdict not in text, verdict


def test_review_rough_cut_declares_the_view_and_the_input_it_reads():
    manifest = json.loads((
        Path(__file__).resolve().parents[1] / "library" / "steps"
        / "step_3_03_review_rough_cut" / "manifest.json").read_text())
    assert "view:alignment" in manifest["context_fields"]
    # The view is a READING of the report; the raw structure stays
    # dropped, so the two never ship together (AGENTS.md 10.1).
    assert "-speech_sequence.alignment_report" in manifest["context_fields"]
    assert "speech_sequence" in {
        i["name"] for i in manifest["interface"]["inputs"]}


def test_the_run_summary_reads_it():
    """The reader is in the runner, after `status` is decided."""
    source = (Path(__file__).resolve().parents[1] / "library" / "processes"
              / "edit_video" / "run_pipeline.py").read_text()
    assert "alignment_findings" in source
    assert '"passage_alignment": alignment_summary' in source
    # Reading is not gating: the block must come after the status line.
    assert source.index("status = ") < source.index("alignment_findings")
