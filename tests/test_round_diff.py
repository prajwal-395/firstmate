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

from library.tools import round_diff
from library.tools import round_version as rv


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
    rv.write_rounds(str(project), {
        "format": rv.ROUNDS_FORMAT,
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
            "rows": rows, "source": rv.SOURCE_STAMPED}


def test_a_card_added_to_a_row_is_named_with_its_span(tmp_path):
    """The real shape of the 2026-09-12 round on the captain's project:
    a tail logo card arrived on the Akshita row of six reels."""
    earlier = {"Reel 01": _reel({"video:Akshita": _row(
        "Akshita", [_item("LC4930.MXF", 0, 684)])})}
    later = {"Reel 01": _reel({"video:Akshita": _row(
        "Akshita", [_item("LC4930.MXF", 0, 684),
                    _item("logo_reveal.mov", 1274, 71)])})}
    project = _rounds(tmp_path, earlier, later)

    diff = round_diff.diff_rounds(str(project), 2, 3)
    rendered = round_diff.render(diff)
    assert "logo_reveal.mov" in rendered
    assert "3 -> 4 item(s)" not in rendered      # 1 -> 2 here
    assert "1 -> 2 item(s)" in rendered
    changed = diff["reels"]["Reel 01"]["changed"]
    assert [row["key"] for row in changed] == ["video:Akshita"]
    assert changed[0]["gained"][0]["name"] == "logo_reveal.mov"


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

    diff = round_diff.diff_rounds(str(project), 2, 3)
    entry = diff["reels"]["Reel 01"]
    assert entry["changed"] == []
    assert [row["key"] for row in entry["rerendered"]] == [
        "video:Subtitles"]
    rendered = round_diff.render(diff)
    assert "rebuilt, and nothing moved" in rendered
    assert "re-rendered at identical spans" in rendered
    # And it is a MEASUREMENT of the spans, not a reading of the
    # filenames: nothing here parses a naming scheme.
    assert "sub_0_bbbb.mov" not in rendered


def test_a_row_that_really_moved_is_not_read_as_a_re_render(tmp_path):
    """The bound on the classification above: one span differs and the
    row is a change again."""
    earlier = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item("a.mov", 0, 50), _item("b.mov", 50, 50)])})}
    later = {"Reel 01": _reel({"video:Subtitles": _row(
        "Subtitles", [_item("a.mov", 0, 50), _item("b.mov", 60, 50)])})}
    project = _rounds(tmp_path, earlier, later)

    entry = round_diff.diff_rounds(str(project), 2, 3)["reels"]["Reel 01"]
    assert [row["key"] for row in entry["changed"]] == ["video:Subtitles"]
    assert entry["rerendered"] == []


def test_a_reel_not_rebuilt_in_the_round_says_so(tmp_path):
    """A real answer - "round 3 did not touch Reel 13" - not a gap, and
    certainly not a loss."""
    earlier = {"Reel 13": _reel({"video:Akshita": _row(
        "Akshita", [_item("a.mov", 0, 10)])})}
    project = _rounds(tmp_path, earlier, {})
    diff = round_diff.diff_rounds(str(project), 2, 3)
    assert diff["reels"]["Reel 13"]["state"] == "not rebuilt in this round"
    assert "not rebuilt in round 3" in round_diff.render(diff)




def test_a_whole_row_gone_is_named_as_gone(tmp_path):
    """The V5 'Semantic' row vanishing is the shape the promote guard
    was built for; a round diff reports it the same way and in the same
    vocabulary."""
    earlier = {"Reel 01": _reel({
        "video:Semantic": _row("Semantic", [_item("s.mov", 0, 40)])})}
    later = {"Reel 01": _reel({})}
    project = _rounds(tmp_path, earlier, later)
    entry = round_diff.diff_rounds(str(project), 2, 3)["reels"]["Reel 01"]
    assert entry["changed"][0]["state"] == round_diff.LOST_ROW
    assert "the row is gone" in round_diff.render(
        round_diff.diff_rounds(str(project), 2, 3))




def test_a_missing_round_raises_rather_than_diffing_against_nothing(
        tmp_path):
    """An empty side reads exactly like a round that removed
    everything."""
    project = _rounds(tmp_path, {}, {})
    with pytest.raises(ValueError) as refused:
        round_diff.diff_rounds(str(project), 2, 9)
    assert "not recorded" in str(refused.value)


