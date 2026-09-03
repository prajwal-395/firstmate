"""The measured usable picture reaches the cutaway choice.

The captain marked 001 at 00:00:42:06: *"why does this part of the clip
keep getting recommended as broll? like its a zoomed up bit of the dash of
my car and doesn't show anything actually."*

Every candidate window was its span's HEAD, and `usable_overlap` was
collapsed to `> 0.0`.  The marked window scored 0.0909 and was selected
anyway, on a clip that is 83% usable.

The other half of that defect - `usable_ranges` reading empty on B-roll -
is in `test_usable_ranges.py::TestRule2IsAROllOnly`, beside the rule it
gates.  Shapes are 001's.  No test reaches a real project.
"""

from library.tools.cutaway_window import choose_window


def _doc(usable, blocks, method="deterministic_v1"):
    return {"assessment": {"usable_ranges": usable,
                           "usable_ranges_method": method},
            "blocks": [{"start": s, "end": e, "visual": v}
                       for s, e, v in blocks]}


def test_window_moves_off_an_unusable_span_head():
    """IMG_1811's shape: 83% usable, and the unusable part is the head."""
    usable = [[2.0, 2.8], [3.6, 7.2], [8.2, 22.87]]
    doc = _doc(usable, [(0.0, 10.0, "a street with a traffic light")])

    choice = choose_window("the street with traffic", doc, {}, 22.87, 2.2)

    assert choice.video_in != 0.0, (
        "the span head is 91% outside the measured usable ranges - this is "
        "the window the captain marked"
    )
    assert any(s <= choice.video_in and choice.video_out <= e
               for s, e in usable)


def test_an_unmeasured_clip_is_not_moved():
    """`measured_usable_ranges` returns None for any method that is not
    `deterministic_v1`, so this path is reachable - and moving a window on a
    measurement that does not exist is the taste fabrication AGENTS.md
    forbids."""
    doc = _doc(None, [(0.0, 10.0, "a street")], method="unmeasured")

    choice = choose_window("a street", doc, {}, 20.0, 2.0)

    assert choice.video_in == 0.0
    assert all(row["placed_by"] == "span_head" for row in choice.candidates)


def test_usability_breaks_a_tie_the_words_cannot():
    """Two spans, one description, different measured usability."""
    doc = _doc([[5.0, 10.0]], [(0.0, 5.0, "a parked car"),
                               (5.0, 10.0, "a parked car")])

    choice = choose_window("a parked car", doc, {}, 10.0, 2.0)

    assert choice.video_in >= 5.0, (
        "where the words cannot discriminate, the span with more measured "
        "usable picture must win over the earliest one"
    )


def test_the_words_still_outrank_usability():
    """`moment_match` is first. Usability only breaks its ties."""
    # The matching span is the LESS usable one. The words still win.
    doc = _doc([[0.0, 1.0], [5.0, 10.0]],
               [(0.0, 5.0, "a red bicycle by a wall"),
                (5.0, 10.0, "an empty pavement")])

    choice = choose_window("a red bicycle", doc, {}, 10.0, 2.0)

    assert choice.video_in < 5.0
    assert choice.basis.startswith("moment_match")
