"""The animated explainer: what it refuses, and that every refusal fires.

A gate that cannot fail is worse than no gate (AGENTS.md 10.4), so most
of this file is the refusals rather than the happy path.  Each one is
provoked by data a real reading could really produce.

`tests/test_explainer_reel_conformance.py` is the other half - F21,
which grades a built timeline against the plan the build recorded.
"""

from __future__ import annotations

import json

import pytest

from library.tools import explainer_plan as ex
from library.tools import motion_graphics_plan as mg
from library.tools import motion_graphics_vocabulary as vocab


# ── Lines a reel really plays ────────────────────────────────────────

def _line(at, says, words=None):
    line = {"at": at, "speaker": "Akshita", "says": says}
    if words is not None:
        line["words"] = words
    return line


def _timed(at, text, gap=0.4):
    """A line whose words carry their own reel seconds."""
    words = []
    second = at
    for token in text.split(" "):
        words.append({"word": token, "at": round(second, 3)})
        second += gap
    return _line(at, text, words)


LINES = [
    _timed(10.0, "AI is going to see your LinkedIn your Crunchbase"),
    _timed(14.0, "Reddit threads your Instagram press mentions"),
]

PARTS = [
    {"part": "LinkedIn", "quote": "your LinkedIn"},
    {"part": "Crunchbase", "quote": "your Crunchbase"},
    {"part": "Reddit", "quote": "Reddit threads"},
]

DECLARATION = {
    "element": "list_build",
    "band": "above",
    "anchor": "bottom_left",
    "hold_seconds": 2.0,
    "colour": "#FFB8D4",
    "type_role": "supporting",
}


# ── The roster subset ────────────────────────────────────────────────


# ── Anchoring ────────────────────────────────────────────────────────

def test_a_stage_is_anchored_to_the_word_its_quote_begins_on():
    out = ex.anchor_stages(PARTS, LINES, reel_seconds=30.0)
    assert not out.refused
    assert [s.text for s in out.stages] == ["LinkedIn", "Crunchbase",
                                            "Reddit"]
    assert all(s.precision == ex.WORD for s in out.stages)
    # "your LinkedIn" begins on the sixth word of the first line.
    assert out.stages[0].at_seconds == pytest.approx(10.0 + 5 * 0.4)
    assert out.stages[1].at_seconds == pytest.approx(10.0 + 7 * 0.4)
    assert out.stages[2].at_seconds == pytest.approx(14.0)


def test_a_quote_the_reel_does_not_say_is_refused_as_ungrounded():
    out = ex.anchor_stages(
        [{"part": "Facebook", "quote": "your Facebook page"}],
        LINES, reel_seconds=30.0)
    assert not out.stages
    assert out.refused[0]["reason"] == ex.UNGROUNDED


def test_parts_the_reel_says_in_another_order_are_refused():
    """The SPEECH is the order, not the list. An explainer built the
    other way would reveal part three while part one is being said."""
    out = ex.anchor_stages(
        [{"part": "Reddit", "quote": "Reddit threads"},
         {"part": "LinkedIn", "quote": "your LinkedIn"}],
        LINES, reel_seconds=30.0)
    assert [s.text for s in out.stages] == ["Reddit"]
    assert out.refused[0]["reason"] == ex.OUT_OF_ORDER


def test_a_stage_past_the_end_of_the_reel_is_refused():
    out = ex.anchor_stages(PARTS, LINES, reel_seconds=11.0)
    reasons = {r["reason"] for r in out.refused}
    assert ex.OFF_THE_END in reasons


# ── The declaration ──────────────────────────────────────────────────


@pytest.mark.parametrize("field,value,message", [
    ("element", "", "names no `element`"),
    ("element", "quote_card", "an element that stages"),
    ("band", "", "band"),
    ("band", "beside", "band"),
    ("anchor", "nowhere", "anchor"),
    ("hold_seconds", None, "hold_seconds"),
    ("hold_seconds", -1.0, "cannot be negative"),
    ("colour_role", "chartreuse", "colour_role"),
    ("type_role", "enormous", "type_role"),
    ("entrance", "explode", "entrance"),
    ("exit", "implode", "exit"),
])
def test_a_malformed_declaration_raises_by_name(field, value, message):
    """RAISES rather than being dropped: a declaration that vanishes
    into a log line is how the 4th Wall end card survived four months."""
    declaration = dict(DECLARATION)
    declaration[field] = value
    with pytest.raises(ex.ExplainerError, match=message):
        ex.normalise_declaration(declaration)


# ── The plan ─────────────────────────────────────────────────────────

def test_the_plan_is_one_entry_whose_runs_are_the_stages():
    """ONE entry, not one per stage: the staging is INSIDE the element,
    so an entry per stage would draw three separate lists."""
    anchored = ex.anchor_stages(PARTS, LINES, reel_seconds=30.0)
    entries = ex.plan_entries(anchored, ex.normalise_declaration(DECLARATION))
    assert len(entries) == 1
    assert [run["text"] for run in entries[0]["copy"]] == [
        "LinkedIn", "Crunchbase", "Reddit"]


# ── The picture bands ────────────────────────────────────────────────


# ── Authoring, end to end ────────────────────────────────────────────

JUDGEMENT = {"readings": [{"reel": 7, "claim_parts": PARTS}]}


def test_authoring_from_a_judgement_produces_a_planned_explainer():
    plan = ex.author_explainer("Reel 07", 7, 30.0, JUDGEMENT, DECLARATION,
                               lines=LINES)
    assert plan.basis == ex.PLANNED
    assert len(plan.entries) == 1
    assert len(plan.anchored.stages) == 3


def test_a_project_that_declares_nothing_gets_nothing_and_says_which():
    plan = ex.author_explainer("Reel 07", 7, 30.0, JUDGEMENT, None)
    assert plan.basis == ex.NOT_DECLARED
    assert plan.entries == []


def test_a_reel_whose_claim_has_no_parts_says_that_rather_than_nothing():
    """`[]` is the honest answer for most reels and is NOT the same as
    'nobody was asked'. `BASES` keeps them apart."""
    plan = ex.author_explainer("Reel 07", 7, 30.0,
                               {"readings": [{"reel": 7}]}, DECLARATION)
    assert plan.basis == ex.NO_PARTS


def test_a_reel_whose_every_stage_is_refused_says_that(capsys):
    plan = ex.author_explainer(
        "Reel 07", 7, 30.0,
        {"readings": [{"reel": 7,
                       "claim_parts": [{"part": "X", "quote": "not said"}]}]},
        DECLARATION, lines=LINES)
    assert plan.basis == ex.ALL_REFUSED
    assert plan.anchored.refused


def test_every_basis_is_in_the_enumeration():
    for basis in (ex.NOT_DECLARED, ex.NO_PARTS, ex.ALL_REFUSED,
                  ex.NOTHING_TO_DRAW, ex.PLANNED):
        assert basis in ex.BASES


def test_parts_for_reel_reads_the_reel_it_was_asked_for():
    judgement = {"readings": [{"reel": 1, "claim_parts": [{"part": "a"}]},
                              {"reel": 7, "claim_parts": PARTS}]}
    assert ex.parts_for_reel(judgement, 7) == PARTS
    assert ex.parts_for_reel(judgement, 99) == []
    assert ex.parts_for_reel(None, 7) == []


# ── The recorded plan ────────────────────────────────────────────────

def test_the_build_records_every_reel_including_the_empty_ones(tmp_path):
    plans = [
        ex.ExplainerPlan(reel_name="Reel 01", declared=False,
                         basis=ex.NOT_DECLARED),
        ex.ExplainerPlan(reel_name="Reel 07", declared=True,
                         basis=ex.PLANNED,
                         segments=[{"overlay_path": "/x.mov",
                                    "timeline_start": 1.0,
                                    "timeline_end": 3.0,
                                    "total_frames": 48,
                                    "elements": ["list_build"]}]),
    ]
    ex.write_plans(str(tmp_path), plans)
    back = ex.read_plans(str(tmp_path))
    assert len(back["plans"]) == 2
    assert ex.plan_for_reel(back, "Reel 07")["segments"][0]["total_frames"] == 48
    assert ex.plan_for_reel(back, "Reel 01")["segments"] == []
    assert ex.plan_for_reel(back, "Reel 99") is None


def test_reading_plans_from_a_project_that_has_none_is_empty(tmp_path):
    assert ex.read_plans(str(tmp_path)) == {}


def test_a_segment_name_carries_its_reel():
    """AGENTS.md 5: prefix an overlay filename with its context. Two
    reels writing one filename is one reel's graphic on another reel's
    timeline."""
    first = ex.segment_name("Reel 01 - a-slug", 0)
    second = ex.segment_name("Reel 02 - a-slug", 0)
    assert first != second
    assert first.startswith(ex.RENDER_PREFIX)


# ── The one field, in the one contract ───────────────────────────────

def test_claim_parts_is_in_the_judge_contract_and_is_optional():
    from library.tools.reel_quality_bar import READING_FIELDS, READING_SCHEMA
    field = [f for f in READING_FIELDS if f.name == ex.CLAIM_PARTS_KEY]
    assert field, "claim_parts is not in the judge's ask"
    assert field[0].required is False
    assert field[0].grounding == "contains"
    assert ex.CLAIM_PARTS_KEY in READING_SCHEMA["readings"][0]


def test_an_ungrounded_claim_part_refuses_the_whole_reading():
    """The quote is not evidence FOR the part - it is the only thing
    that puts the part in time. An ungrounded part cannot be drawn at
    all, so the reading carrying it is refused."""
    from library.tools.reel_quality_bar import check_reading
    words = "AI is going to see your LinkedIn your Crunchbase"
    ungrounded, _ = check_reading(
        {"reel": 1, "claim_quote": "your LinkedIn",
         "opening_quote": "AI", "closing_quote": "your Crunchbase",
         "assumes_known": [],
         "claim_parts": [{"part": "Facebook", "quote": "your Facebook"}]},
        words)
    assert any("claim_parts" in u for u in ungrounded)


def test_a_claim_part_with_no_quote_refuses_the_reading():
    from library.tools.reel_quality_bar import check_reading
    ungrounded, _ = check_reading(
        {"reel": 1, "claim_quote": "your LinkedIn",
         "opening_quote": "AI", "closing_quote": "your LinkedIn",
         "assumes_known": [],
         "claim_parts": [{"part": "LinkedIn", "quote": ""}]},
        "AI is going to see your LinkedIn")
    assert any("no quote" in u for u in ungrounded)


def test_a_grounded_claim_part_does_not_refuse_the_reading():
    from library.tools.reel_quality_bar import check_reading
    ungrounded, _ = check_reading(
        {"reel": 1, "claim_quote": "your LinkedIn",
         "opening_quote": "AI", "closing_quote": "your LinkedIn",
         "assumes_known": [],
         "claim_parts": [{"part": "LinkedIn", "quote": "your LinkedIn"}]},
        "AI is going to see your LinkedIn")
    assert ungrounded == []


def test_a_reading_with_no_claim_parts_still_grounds():
    """OPTIONAL, and every reading written before the field existed
    must keep grounding or the field breaks the captain's data."""
    from library.tools.reel_quality_bar import check_reading
    ungrounded, _ = check_reading(
        {"reel": 1, "claim_quote": "your LinkedIn",
         "opening_quote": "AI", "closing_quote": "your LinkedIn",
         "assumes_known": []},
        "AI is going to see your LinkedIn")
    assert ungrounded == []


def test_the_ask_naming_claim_parts_is_still_uncontaminated():
    """Adding a field must not hand the judge the answer sheet."""
    from library.tools.reel_quality_bar import (
        READING_SCHEMA, assert_ask_is_uncontaminated)
    assert_ask_is_uncontaminated(json.dumps(READING_SCHEMA), "schema")
    for field in __import__(
            "library.tools.reel_quality_bar", fromlist=["x"]).READING_FIELDS:
        assert_ask_is_uncontaminated(field.asks, field.name)


# ── Words, and where they may go ─────────────────────────────────────

def test_played_speech_carries_no_words_unless_asked():
    """Word timings do not reach a prompt (AGENTS.md 10.1), and the
    judge's `lines` table is a prompt."""
    import inspect

    from library.tools.reel_quality_bar import played_speech
    signature = inspect.signature(played_speech)
    assert signature.parameters["with_words"].default is False


def test_an_omitted_optional_quote_does_not_refuse_the_reading():
    """The defect this test names: `normalise(None)` was the word
    "none", so a judge that answered an optional field by leaving the
    key out - rather than sending an empty string - had its whole
    reading refused.  A gate that fails correct output (AGENTS.md
    10.4), and it never fired only because every reading on disk
    happens to carry the key."""
    from library.tools.reel_quality_bar import normalise
    assert normalise(None) == ""


# ── Looking at what was drawn ────────────────────────────────────────

def _alpha_clip(path, width, height, rect, frames=4):
    """A ProRes 4444 clip with alpha, opaque in exactly `rect`.

    Built with ffmpeg, which the CI runner installs (AGENTS.md 9): 27
    library files shell out to it and every audio and video measurement
    path skipped itself without it.

    An OPAQUE box padded onto a transparent canvas rather than a
    `drawbox` over one - `drawbox` blends into the existing alpha and
    leaves the plane transparent, which produces a clip that measures as
    empty and would make this whole test read as a pass for the wrong
    reason.
    """
    import subprocess
    left, top, right, bottom = rect
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-v", "error", "-f", "lavfi", "-i",
         f"color=c=white:s={right - left}x{bottom - top}:d=1",
         "-vf", (f"format=rgba,pad={width}:{height}:{left}:{top}"
                 f":color=0x00000000"),
         "-frames:v", str(frames), "-c:v", "prores_ks",
         "-profile:v", "4444", "-pix_fmt", "yuva444p10le", "-y", str(path)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        check=False)
    return result.returncode == 0


def test_measure_render_finds_the_ink_it_was_given(tmp_path):
    clip = tmp_path / "ink.mov"
    if not _alpha_clip(clip, 1080, 1920, (90, 300, 500, 600)):
        raise AssertionError(
            "ffmpeg could not build an alpha clip; the CI runner installs "
            "ffmpeg (AGENTS.md 9) and this measurement is meaningless "
            "without it")
    measured = ex.measure_render(str(clip))
    assert measured["frame"] == [1080, 1920]
    assert measured["ink_pixels"] > 0
    # ProRes is chroma-subsampled 4:4:4 but the alpha plane is exact;
    # allow one row of codec rounding at each edge rather than pretending
    # a video codec is lossless.
    top, bottom = measured["rows"]
    assert abs(top - 300) <= 2 and abs(bottom - 599) <= 2
    assert measured["touches_frame_edge"] == []


def test_measure_render_sees_ink_at_the_frame_edge(tmp_path):
    clip = tmp_path / "clipped.mov"
    if not _alpha_clip(clip, 1080, 1920, (0, 0, 400, 500)):
        raise AssertionError("ffmpeg could not build an alpha clip")
    measured = ex.measure_render(str(clip))
    assert "top" in measured["touches_frame_edge"]
    assert "left" in measured["touches_frame_edge"]


def test_measure_render_refuses_a_file_it_cannot_read(tmp_path):
    missing = tmp_path / "nothing.mov"
    with __import__("pytest").raises(ex.ExplainerError, match="could not read"):
        ex.measure_render(str(missing))


def _bands():
    from library.tools.safe_area import safe_area_for_frame
    return ex.picture_bands((0, 656, 1080, 1264), 1080, 1920,
                            safe_area_for_frame(1080, 1920))


def test_ink_at_the_frame_edge_is_an_error():
    """A graphic laid out inside a 90px left inset cannot legitimately
    reach column zero, so ink there means the frame cut it off - and the
    file is a valid picture of the right size that nothing downstream
    can tell apart."""
    findings = ex.render_findings(
        {"ink_pixels": 10, "rows": [0, 657], "cols": [90, 560],
         "touches_frame_edge": ["top"], "frame": [1080, 1920]},
        _bands(), {"band": "above"})
    errors = [f for f in findings if f["severity"] == "error"]
    assert errors and errors[0]["code"] == ex.FRAME_EDGE_CLIPPED


def test_a_row_of_shadow_past_the_band_is_a_warning_and_not_an_error():
    """The boundary is shared with the picture and a text shadow falls
    across it. A check that failed on one row would fail correct output
    (AGENTS.md 10.4). Measured on the real render of reel 21: ink rows
    320..657 against a band of 120..656."""
    findings = ex.render_findings(
        {"ink_pixels": 61778, "rows": [320, 657], "cols": [90, 560],
         "touches_frame_edge": [], "frame": [1080, 1920]},
        _bands(), {"band": "above"})
    assert [f["severity"] for f in findings] == ["warning"]
    assert "1 row(s) below" in findings[0]["message"]


def test_ink_inside_the_band_says_nothing_at_all():
    findings = ex.render_findings(
        {"ink_pixels": 500, "rows": [320, 600], "cols": [90, 560],
         "touches_frame_edge": [], "frame": [1080, 1920]},
        _bands(), {"band": "above"})
    assert findings == []


def test_a_render_that_draws_nothing_is_an_error():
    """AGENTS.md 10.2: an overlay that draws nothing is not rendered."""
    findings = ex.render_findings(
        {"ink_pixels": 0, "rows": None, "cols": None,
         "touches_frame_edge": [], "frame": [1080, 1920]},
        _bands(), {"band": "above"})
    assert findings[0]["severity"] == "error"
    assert findings[0]["code"] == ex.NOTHING_TO_DRAW
