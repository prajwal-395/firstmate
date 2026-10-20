"""AGENT-014: one read of what an execution actually did.

Every write path already captures the state before and the state after -
`edit_patch` re-reads the live timeline under the lease, the AXI path
verifies against one fresh read, a touch-up records the inventory it
started from, the undo journal records both sides. The capture was
universal; the READER was missing. The data sat in five places, and an
agent asking "did my edit land, and what did it change?" had to know
which of them to ask.

These tests name the defects:

- the report says what the RECORDED state shows, and names which
  generation it read - the head is the timeline as of its `verified_at`,
  and a report that hid that would over-claim;
- a timeline with no recorded generation is UNREADABLE, not "unchanged":
  a state that cannot be established is not a state that did not change;
- the first generation is a FIRST READ, not a change of nothing - there
  is no "before" to compare against;
- the diff is `timeline_shadow.diff`, so the report cannot disagree with
  the round-diff and promotion differ about what a row holds;
- a write that changed nothing says UNCHANGED, because the shadow
  records a patch's own row even when the state did not move.
"""
from __future__ import annotations

from library.tools import edit_state_report as report
from library.tools import timeline_shadow as shadow


def _clip(unique_id="clip-1", name="a.mov", record_in=0, record_out=100):
    return {"unique_id": unique_id, "name": name, "track_type": "video",
            "track_index": 1, "track_name": "V1", "record_in": record_in,
            "record_out": record_out, "duration": record_out - record_in,
            "source_in_frame": 10, "source_out_frame": 110,
            "source_file": f"/m/{name}", "media_type": "video"}


def _snapshot(*clips):
    return {"timeline": "Reel 29", "project": "Podcast", "fps": 30.0,
            "tracks": [{"type": "video", "index": 1, "name": "V1",
                        "clips": list(clips)}],
            "markers": {"notes": []}}


def _store(tmp_path):
    return shadow.ShadowStore(tmp_path / "shadow.db")


def _record(store, snapshot, *, source=shadow.OBSERVED, expected_head=0):
    return store.record(project="Podcast", timeline_id="tl-1",
                        timeline_name="Reel 29", snapshot=snapshot,
                        source=source, expected_head=expected_head)


def test_a_timeline_with_no_recorded_state_is_unreadable(tmp_path):
    # Defect: "no recorded state" read as "nothing changed", which is a
    # claim about a state nobody has established.
    store = _store(tmp_path)
    result = report.state_report("Podcast", "Reel 29", store=store)
    assert result["verdict"] == report.UNREADABLE
    assert result["changed"] is False
    assert result["reason"]


def test_a_timeline_the_registry_does_not_hold_is_unreadable(tmp_path):
    store = _store(tmp_path)
    _record(store, _snapshot(_clip()))
    result = report.state_report("Podcast", "No Such Reel", store=store)
    assert result["verdict"] == report.UNREADABLE
    assert "No Such Reel" in result["reason"]


def test_the_first_generation_is_a_first_read_not_a_change_of_nothing(tmp_path):
    store = _store(tmp_path)
    _record(store, _snapshot(_clip()))
    result = report.state_report("Podcast", "Reel 29", store=store)
    assert result["verdict"] == report.FIRST_READ
    assert result["before"] is None
    assert result["changed"] is False
    assert result["after"]["generation"] == 1


def test_a_write_that_changed_something_says_what_changed(tmp_path):
    store = _store(tmp_path)
    _record(store, _snapshot(_clip()), expected_head=0)
    _record(store, _snapshot(_clip(), _clip("clip-2", "b.mov", 100, 200)),
            source=shadow.PATCH, expected_head=1)
    result = report.state_report("Podcast", "Reel 29", store=store)
    assert result["verdict"] == report.CHANGED
    assert result["changed"] is True
    assert result["before"]["generation"] == 1
    assert result["after"]["generation"] == 2
    assert result["after"]["source"] == shadow.PATCH
    # The diff is the shadow's own, so the report cannot disagree with
    # the round-diff and promotion differ about what a row holds.
    assert result["diff"] == shadow.diff(_snapshot(_clip()),
                                          _snapshot(_clip(),
                                                    _clip("clip-2", "b.mov",
                                                          100, 200)))


def test_a_write_that_changed_nothing_says_unchanged(tmp_path):
    # The shadow records a patch's own row even when the state did not
    # move, so "the write landed and changed nothing" is a real answer
    # and not the same as "nothing was recorded".
    store = _store(tmp_path)
    _record(store, _snapshot(_clip()), expected_head=0)
    _record(store, _snapshot(_clip()), source=shadow.PATCH, expected_head=1)
    result = report.state_report("Podcast", "Reel 29", store=store)
    assert result["verdict"] == report.UNCHANGED
    assert result["changed"] is False
    assert result["diff"] is None


def test_the_report_names_the_generation_it_read(tmp_path):
    # The head is the timeline as of its `verified_at`; a report that
    # hid that would over-claim.
    store = _store(tmp_path)
    _record(store, _snapshot(_clip()), expected_head=0)
    _record(store, _snapshot(_clip(), _clip("clip-2", "b.mov", 100, 200)),
            source=shadow.PATCH, expected_head=1)
    result = report.state_report("Podcast", "Reel 29", store=store)
    assert result["after"]["generation"] == 2
    assert result["after"]["verified_at"] > 0
    assert result["after"]["hash"] != result["before"]["hash"]


def test_a_generation_that_does_not_exist_is_unreadable(tmp_path):
    store = _store(tmp_path)
    _record(store, _snapshot(_clip()))
    result = report.state_report("Podcast", "Reel 29", generation=9,
                                 store=store)
    assert result["verdict"] == report.UNREADABLE
    assert "9" in result["reason"]


def test_the_front_door_prints_the_report(capsys, tmp_path):
    import json

    store = _store(tmp_path)
    _record(store, _snapshot(_clip()), expected_head=0)
    _record(store, _snapshot(_clip(), _clip("clip-2", "b.mov", 100, 200)),
            source=shadow.PATCH, expected_head=1)
    assert shadow._main(["--db", str(tmp_path / "shadow.db"), "state",
                         "--project", "Podcast",
                         "--timeline", "Reel 29"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == report.CHANGED
    assert payload["after"]["generation"] == 2


def test_an_unrecorded_timeline_prints_the_report_rather_than_refusing(
        capsys, tmp_path):
    import json

    assert shadow._main(["--db", str(tmp_path / "shadow.db"), "state",
                         "--project", "Podcast",
                         "--timeline", "Reel 29"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["verdict"] == report.UNREADABLE
