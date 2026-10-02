"""What changed between round 3 and round 4, in the captain's terms.

The payoff of the version object, and the reason the versioning
question mattered: without it a round is SUPERSEDED - the new reel is
there and the old one is gone - and with it a round is REVIEWABLE.

Each test fails if its mechanism is removed:

- the diff itself, over two STORED row snapshots, off disk;
- a reel not rebuilt in a round reported as such rather than as a loss;
- the re-render classification, without which every round diff is
  mostly noise (every overlay filename carries a content digest, so a
  rebuild that changed nothing lands 34 new names at 34 identical
  spans) - and noise is how a real change goes unread;
- the guard's own row vocabulary, so a row named in a round diff and a
  row named in a promotion refusal are spelled the same way;
- a missing round raising rather than diffing against nothing.
"""
import pytest

from library.tools.versions import rounds


def _row(name, items):
    return {"media_type": "video", "index": 1, "name": name,
            "items": list(items),
            "count": len(items),
            "frames": sum(item["duration"] for item in items)}


def _item(name, start, duration):
    return {"name": name, "start": start, "end": start + duration,
            "duration": duration}


def _rounds(tmp_path, earlier_reels, later_reels):
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    rounds.write_rounds(str(project), {
        "format": rounds.ROUNDS_FORMAT,
        "rounds": [
            {"round": 2, "opened_at": "2026-09-11T03:08Z",
             "opened_by": ["a"], "reels_asked": ["Reel 01"],
             "why": "opened by the captain's feedback",
             "reels": earlier_reels},
            {"round": 3, "opened_at": "2026-09-11T22:51Z",
             "opened_by": ["b"], "reels_asked": ["Reel 09"],
             "why": "opened by the captain's feedback",
             "reels": later_reels},
        ]})
    return project


def _reel(rows, when="2026-09-12T01:28Z"):
    return {"promoted_at": when, "built_at": when, "built_with": "abc",
            "rows": rows, "source": rounds.SOURCE_STAMPED}


def test_a_row_gained_or_lost_is_named_in_the_guards_vocabulary(tmp_path):
    """The real shape of the 2026-09-12 round on the captain's project:
    a tail logo card arrived on the Akshita row of six reels."""
    earlier = {"Reel 01": _reel({"video:Akshita": _row(
        "Akshita", [_item("LC4930.MXF", 0, 684)])})}
    later = {"Reel 01": _reel({"video:Akshita": _row(
        "Akshita", [_item("LC4930.MXF", 0, 684),
                    _item("logo_reveal.mov", 1274, 71)])})}
    project = _rounds(tmp_path, earlier, later)

    diff = rounds.diff_rounds(str(project), 2, 3)
    rendered = rounds.render_diff(diff)
    assert "logo_reveal.mov" in rendered
    assert "3 -> 4 item(s)" not in rendered      # 1 -> 2 here
    assert "1 -> 2 item(s)" in rendered
    changed = diff["reels"]["Reel 01"]["changed"]
    assert [row["key"] for row in changed] == ["video:Akshita"]
    assert changed[0]["gained"][0]["name"] == "logo_reveal.mov"

    # A whole row gone is named as gone, in the promote guard's own
    # vocabulary (the V5 'Semantic' row shape).
    earlier = {"Reel 01": _reel({
        "video:Semantic": _row("Semantic", [_item("s.mov", 0, 40)])})}
    project = _rounds(tmp_path / "gone", earlier, {"Reel 01": _reel({})})
    diff = rounds.diff_rounds(str(project), 2, 3)
    entry = diff["reels"]["Reel 01"]
    assert entry["changed"][0]["state"] == rounds.LOST_ROW
    assert "the row is gone" in rounds.render_diff(diff)


def test_overlays_re_rendered_at_identical_spans_are_not_a_change(
        tmp_path):
    """Every caption `.mov` carries a content digest in its name, so a
    rebuild that changed nothing lands 34 new filenames at 34 identical
    spans. Remove this and the change that matters is buried under
    them - which is the failure mode this classification exists for."""
    earlier = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item(f"sub_{n}_aaaa.mov", n * 100, 50)
                      for n in range(15)])})}
    later = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item(f"sub_{n}_bbbb.mov", n * 100, 50)
                      for n in range(15)])})}
    project = _rounds(tmp_path, earlier, later)

    diff = rounds.diff_rounds(str(project), 2, 3)
    entry = diff["reels"]["Reel 01"]
    assert entry["changed"] == []
    assert [row["key"] for row in entry["rerendered"]] == [
        "video:Subtitles"]
    rendered = rounds.render_diff(diff)
    assert "rebuilt, and nothing moved" in rendered
    assert "re-rendered at identical spans" in rendered
    # And it is a MEASUREMENT of the spans, not a reading of the
    # filenames: nothing here parses a naming scheme.
    assert "sub_0_bbbb.mov" not in rendered

    # The bound on the classification: one span differs and the row is
    # a change again.
    earlier = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item("a.mov", 0, 50), _item("b.mov", 50, 50)])})}
    later = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item("a.mov", 0, 50), _item("b.mov", 60, 50)])})}
    project = _rounds(tmp_path / "moved", earlier, later)

    entry = rounds.diff_rounds(str(project), 2, 3)["reels"]["Reel 01"]
    assert [row["key"] for row in entry["changed"]] == ["video:Subtitles"]
    assert entry["rerendered"] == []


def test_a_reel_not_rebuilt_in_the_round_says_so(tmp_path):
    """A real answer - "round 3 did not touch Reel 13" - not a gap, and
    certainly not a loss."""
    earlier = {"Reel 13": _reel({"video:Akshita": _row(
        "Akshita", [_item("a.mov", 0, 10)])})}
    project = _rounds(tmp_path, earlier, {})
    diff = rounds.diff_rounds(str(project), 2, 3)
    assert diff["reels"]["Reel 13"]["state"] == "not rebuilt in this round"
    assert "not rebuilt in round 3" in rounds.render_diff(diff)




def test_a_missing_round_raises_rather_than_diffing_against_nothing(
        tmp_path):
    """An empty side reads exactly like a round that removed
    everything."""
    project = _rounds(tmp_path, {}, {})
    with pytest.raises(ValueError) as refused:
        rounds.diff_rounds(str(project), 2, 9)
    assert "not recorded" in str(refused.value)


