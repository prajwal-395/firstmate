"""FI-02: the footage producer inventory cannot drift from the lane table.

Defect named: `docs/FOOTAGE_PRODUCER_INVENTORY.md` lists every measurement
producer; `footage_analysis.LANES` is the code that runs them. A lane added
to one without the other reads as "no producer here" (or documents one that
never runs). This test fails on `origin/main` (no inventory exists there)
and passes once every lane in `LANE_NAMES` appears in the doc with a
producer path and a reader list.

Hermetic: reads one doc file and imports the lane table (module-level
imports only - no ffmpeg, no model, no Resolve, no network). Clears no
environment markers; depends on none.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOC = REPO_ROOT / "docs" / "FOOTAGE_PRODUCER_INVENTORY.md"

ROSTER_START = "<!-- FI02-LANE-ROSTER-START"
ROSTER_END = "FI02-LANE-ROSTER-END -->"


def _doc_text() -> str:
    assert DOC.is_file(), (
        f"{DOC.name} does not exist: FI-02's producer inventory was never written"
    )
    return DOC.read_text(encoding="utf-8")


def _roster(text: str) -> list[str]:
    start = text.index(ROSTER_START)
    end = text.index(ROSTER_END)
    assert start < end, "lane roster markers are inverted"
    return [
        line.strip()
        for line in text[start + len(ROSTER_START) : end].splitlines()
        if line.strip() and not line.strip().startswith("<")
    ]


def test_lane_roster_matches_lane_names():
    from library.tools.footage_analysis import LANE_NAMES

    roster = _roster(_doc_text())
    assert roster == list(LANE_NAMES), (
        f"inventory roster drifted from LANE_NAMES: "
        f"doc-only={sorted(set(roster) - set(LANE_NAMES))} "
        f"code-only={sorted(set(LANE_NAMES) - set(roster))}"
    )


def test_every_lane_has_producer_path_and_reader_list():
    from library.tools.footage_analysis import LANE_NAMES

    text = _doc_text()
    rows = [
        line
        for line in text.splitlines()
        if line.startswith("| `") and line.count("|") >= 7
    ]
    by_lane = {}
    for row in rows:
        cells = [cell.strip() for cell in row.strip().strip("|").split("|")]
        lane_cell = cells[0]
        match = re.match(r"`([^`]+)`", lane_cell)
        if match:
            by_lane[match.group(1)] = cells
    missing = [name for name in LANE_NAMES if name not in by_lane]
    assert not missing, f"lanes with no inventory row: {missing}"
    for name in LANE_NAMES:
        cells = by_lane[name]
        producer, readers = cells[2], cells[5]
        assert "library/" in producer, f"lane {name!r} names no producer code path"
        assert readers and readers != "-", f"lane {name!r} names no readers"
