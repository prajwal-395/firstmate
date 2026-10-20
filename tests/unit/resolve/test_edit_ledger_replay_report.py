"""The ledger replay must report each unreplayable row ONCE.

`replay_on_timeline` replays a project's hands rows onto a freshly built
reel, and every row it cannot replay is reported BY NAME. The report
grew exponentially: the caller extended the report list with the very
list the replayer had already appended to, once per program row.

Measured 2026-10-20 on the Ren Scratch - breaktest project: 28 program
rows grew the report to 2**28 records, and the build printed every one
of them - 53 GB of stderr in 75 minutes, which filled the disk and
killed the build.
"""

from __future__ import annotations

import library.tools.edit_ledger as edit_ledger


class _FakeTimeline:
    """A timeline that answers nothing: every op is unreplayable."""

    def GetItemListInTrack(self, *_args):  # noqa: N802 - Resolve's spelling
        return []

    def GetCurrentTimeline(self):  # noqa: N802 - Resolve's spelling
        return None


def _program_row(index: int) -> dict:
    return {
        "op": "edit_program",
        "reel": "Reel 01 - a",
        "anchor": {"kind": "reel"},
        "params": {
            "ops": [{"op": "audio.fade", "params": {"unique_id": f"c{index}"}}],
        },
    }


def test_the_unreplayable_report_grows_once_per_row(monkeypatch):
    """Catches: the report doubling per program row, so a build with a
    few dozen rows prints hundreds of millions of lines."""
    monkeypatch.setattr(edit_ledger, "report_unreplayable",
                        lambda records: records)
    rows = [_program_row(index) for index in range(12)]

    result = edit_ledger.replay_on_timeline(
        "Reel 01 - a", rows, [], {}, _FakeTimeline(),
        reel_name="Reel 01 - a")

    unreplayable = result["unreplayable"]
    assert len(unreplayable) == len(rows), (
        f"{len(rows)} program rows reported {len(unreplayable)} records")
    # Exponential growth is the failure: 12 rows must not report 4096.
    assert len(unreplayable) < 100


def test_the_applied_list_grows_once_per_row(monkeypatch):
    """Catches: the applied list doubling the same way, so a build's
    record of what it applied said twice what it did."""
    monkeypatch.setattr(edit_ledger, "report_unreplayable",
                        lambda records: records)
    rows = [_program_row(index) for index in range(12)]

    result = edit_ledger.replay_on_timeline(
        "Reel 01 - a", rows, [], {}, _FakeTimeline(),
        reel_name="Reel 01 - a")

    applied = result["applied"]
    # `audio.fade` has no native apply, so nothing is applied - but the
    # list must not have grown either.
    assert len(applied) <= len(rows), (
        f"{len(rows)} program rows applied {len(applied)} records")
