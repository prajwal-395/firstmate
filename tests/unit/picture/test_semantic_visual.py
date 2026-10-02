"""A visual lands because of what is being SAID, on the word that says it.

The SUBJECT is the model's free text and the engine never reads it to
decide (`test_the_subject_is_inert`); the visual lands ON its anchor
phrase, found by SEARCH in the measured word timings, or is refused by
name. Rationale and the captain's ask: `docs/evidence/semantic_visual.md`.
"""
import os
import sys
import pytest
import json
import re


PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import motion_graphics_plan as mgp
from library.tools import semantic_visual as sv

FPS = 30
# The fixture words ride at real timeline seconds (the reel_09 "money"
# moment sits past the twenty-minute mark), so the test timeline must
# contain them: a 60-second clock would drop every anchored entry as
# outside_the_timeline, which is the resolver working, not failing.
DURATION = 1300.0

# Words in TIMELINE seconds, the shape `collect_word_windows` produces:
# "like huge ad campaigns and lots of money but"
WORDS = [
    {"word": "like", "start": 1209.0, "end": 1209.3},
    {"word": "huge", "start": 1209.4, "end": 1209.7},
    {"word": "ad", "start": 1209.72, "end": 1209.85},
    {"word": "campaigns", "start": 1209.92, "end": 1210.461},
    {"word": "and", "start": 1210.801, "end": 1210.921},
    {"word": "lots", "start": 1210.981, "end": 1211.181},
    {"word": "of", "start": 1211.221, "end": 1211.281},
    {"word": "money", "start": 1211.361, "end": 1211.621},
    {"word": "but", "start": 1212.161, "end": 1212.522},
]


def money_entry(**kw):
    base = {
        "element": "subject_emblem",
        "subject": "money - paid advertising budgets",
        "anchor_phrase": "lots of money",
        "hold_seconds": 2.0,
        "anchor": "middle_right",
        "copy": {"display": "$", "supporting": "ad budgets"},
        "color": "#F5C518",
    }
    base.update(kw)
    return base


def resolve(plan, words=WORDS, duration=DURATION):
    return mgp.resolve_plan(plan, timeline_duration=duration, fps=FPS,
                            palette_roles={},
                            word_windows=words)


# ── 1. the window lands on the word ───────────────────────────────────

def test_the_emblem_lands_on_its_word_window():
    """`lots of money` spans 1210.981-1211.621; the moment starts there."""
    start, end = sv.find_phrase_window(WORDS, "lots of money")
    assert start == pytest.approx(1210.981)
    assert end == pytest.approx(1211.621)
    # The search ignores case and punctuation.
    start, _ = sv.find_phrase_window(WORDS, "Lots Of Money,")
    assert start == pytest.approx(1210.981)


def test_an_unlandable_anchor_refuses_by_name():
    """Never said, an untimed anchor word, no word timings at all: each
    refuses by reason rather than guessing a window."""
    untimed = [dict(w) for w in WORDS]
    untimed[7] = {"word": "money", "start": None, "end": None}
    for words, phrase, reason in [
            (WORDS, "crypto fortune", "anchor_phrase_not_found"),
            (untimed, "lots of money", "anchor_word_untimed"),
            ([], "money", "no_word_timings_to_anchor_against")]:
        with pytest.raises(sv.SemanticVisualError) as exc:
            sv.find_phrase_window(words, phrase)
        assert exc.value.reason == reason


# ── 2. the subject is the model's, and the engine never reads it ──────

def test_the_subject_is_inert():
    """Same phrase, different subjects: identical timing and geometry.

    If the engine consulted the subject - a keyword table by any other
    name - "money" and "potatoes" would resolve differently. They do not:
    the subject travels onto the moment as provenance and nothing reads
    it to decide.
    """
    first = resolve([money_entry()])
    second = resolve([money_entry(subject="potatoes - a root vegetable")])
    assert first.moments and second.moments
    a, b = dict(first.moments[0]), dict(second.moments[0])
    a.pop("subject"), b.pop("subject")
    assert a == b


# ── 3. the plan honours the anchor ─────────────────────────────────────

def test_resolve_plan_lands_an_anchored_entry_on_its_words():
    resolved = resolve([money_entry()])
    assert resolved.basis == mgp.ELEMENTS_PLANNED
    moment = resolved.moments[0]
    assert moment["timeline_start"] == pytest.approx(1210.981, abs=0.034)
    assert moment["timeline_end"] == pytest.approx(1210.981 + 2.0, abs=0.034)
    assert moment["timing_basis"] == "word_window:lots of money"
    assert moment["subject"] == "money - paid advertising budgets"


def test_two_timings_is_ambiguous_and_refused():
    """An entry naming BOTH an anchor phrase and explicit seconds claims
    two timings. The engine does not pick one - choosing would be the
    chooser (AGENTS.md 10.5) - so the entry is dropped as conflicting."""
    resolved = resolve([money_entry(start_seconds=5.0, duration_seconds=1.0)])
    assert not resolved.moments
    assert resolved.dropped[0].reason == "conflicting_timing"


# ── 5. spine words reach the resolver in timeline seconds ──────────────

def test_collect_word_windows_maps_source_onto_the_timeline():
    """Spine word timings ride in SOURCE seconds (AGENTS.md 6); the block
    carries both clocks, so the resolver reads timeline seconds."""
    spine = {"structure": [{
        "clip_id": "clip_001",
        "timeline_start": 100.0,
        "source_start": 50.0,
        "word_timestamps": [
            {"word": "lots", "start": 60.981, "end": 61.181},
            {"word": "of", "start": 61.221, "end": 61.281},
            {"word": "money", "start": 61.361, "end": 61.621},
        ],
    }]}
    windows = sv.collect_word_windows(spine)
    assert [(w["word"], round(w["start"], 3)) for w in windows] == [
        ("lots", 110.981), ("of", 111.221), ("money", 111.361)]
    start, end = sv.find_phrase_window(windows, "lots of money")
    assert start == pytest.approx(110.981)
    assert end == pytest.approx(111.361 + 0.26)
    # A block with no timeline clock contributes nothing.
    assert sv.collect_word_windows({"structure": [
        {"clip_id": "clip_001", "word_timestamps": [
            {"word": "money", "start": 1.0, "end": 1.5}]}]}) == []


# --------------------------------------------------------------------------
# From test_semantic_visual_authoring.py
#
# The planning step is actually ASKED for semantic visuals.
#
# The motion-graphics planner is ASKED for anchored semantic visuals: the
# machine-readable output schema names the anchor keys, no worked example
# teaches the `conflicting_timing` shape, and a planner-authored entry
# lands on its measured word. Incident: `docs/evidence/semantic_visual.md`.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_06_render_motion_graphics.bridge import (
    timeline_rows,
)
from library.steps.step_4_06_render_motion_graphics.generate_motion_props import (
    generate_motion_props,
)

STEP_DIR = os.path.join(
    PROJECT_ROOT, "library", "steps", "step_4_06_render_motion_graphics")


def _manifest():
    with open(os.path.join(STEP_DIR, "manifest.json"),
              encoding="utf-8") as f:
        return json.load(f)


def _handoff():
    with open(os.path.join(STEP_DIR, "handoff.md"), encoding="utf-8") as f:
        return f.read()


def test_the_machine_readable_schema_names_the_anchor_keys():
    """The `llm_outputs` description is the Required Output Format block.

    `run_pipeline.generate_output_schema_text` renders it verbatim into
    the prompt and the agent request file carries it as `expected_schema`.
    If `subject`/`anchor_phrase`/`hold_seconds` are named nowhere in it,
    the model is not asked - handoff prose notwithstanding.
    """
    manifest = _manifest()
    llm_outputs = manifest["interface"]["llm_outputs"]
    plan = next(o for o in llm_outputs if o["name"] == "motion_graphics_plan")
    for key in ("subject", "anchor_phrase", "hold_seconds"):
        assert key in plan["description"], (
            f"{key!r} is named nowhere in the motion_graphics_plan "
            f"llm_outputs description, so the rendered Required Output "
            f"Format block never asks for it")


def test_no_worked_example_mixes_anchor_with_declared_seconds():
    """No single example entry may carry both timings.

    An entry naming `anchor_phrase` beside `start_seconds` or
    `duration_seconds` is dropped as `conflicting_timing` - the engine
    picks neither. An example showing both teaches the failing shape.
    """
    blocks = re.findall(r"```json\n(.*?)```", _handoff(), re.DOTALL)
    assert blocks, "the handoff carries no JSON answer template at all"
    checked = 0
    for block in blocks:
        try:
            parsed = json.loads(block)
        except json.JSONDecodeError:
            continue
        entries = parsed if isinstance(parsed, list) else [parsed]
        for entry in entries:
            if not isinstance(entry, dict) or "element" not in entry:
                continue
            checked += 1
            if str(entry.get("anchor_phrase") or "").strip():
                assert entry.get("start_seconds") is None, (
                    "worked example names anchor_phrase beside "
                    "start_seconds - resolve_plan drops that shape as "
                    "conflicting_timing")
                assert entry.get("duration_seconds") is None, (
                    "worked example names anchor_phrase beside "
                    "duration_seconds - resolve_plan drops that shape "
                    "as conflicting_timing")
    assert checked, "no example entry with an element key was found"


def _spine():
    """One block of speech, with source-clocked word measurements.

    The words say the rent is due; the planner's subject below is one
    nobody enumerated - no table maps these words to anything.
    """
    words = ["the", "rent", "is", "due", "on", "friday"]
    return {"structure": [{
        "position": 1,
        "block_type": "body",
        "clip_id": "clip_001",
        "timeline_start": 40.0,
        "timeline_end": 46.0,
        "source_start": 10.0,
        "source_end": 16.0,
        "content": {"text": "the rent is due on friday no exceptions"},
        "word_timestamps": [
            {"word": w, "start": 10.0 + i * 0.4, "end": 10.3 + i * 0.4}
            for i, w in enumerate(words)
        ],
    }]}


def test_a_planner_authored_entry_quoted_from_bridge_context_lands():
    """Context the bridge built, entry the planner reasoned, landing real.

    The anchor phrase is quoted from the bridge's own
    `timeline_context_toon` table - the only speech the prompt shows -
    and the entry carries no `start_seconds`/`duration_seconds`, the
    shape the fixed handoff teaches. `generate_motion_props` is the
    runner's own resolution half, so a landing here is a landing on a
    run.
    """
    spine = _spine()
    rows = timeline_rows(spine)
    says = " ".join(r["says"] for r in rows)
    assert "rent is due" in says

    entry = {
        "element": "subject_emblem",
        "subject": "deadline pressure - the rent lands friday",
        "anchor_phrase": "rent is due",
        "hold_seconds": 2.0,
        "anchor": "middle_right",
        "copy": {"display": "FRI", "supporting": "rent due friday"},
        "color": "#F5C518",
        "why": "answers when the deadline the speech names actually is",
    }
    segments, resolved = generate_motion_props(
        [entry], spine, fps=30, width=1080, height=1920,
        project_folder="")
    assert resolved.basis == "elements_planned", (
        f"planner-authored entry did not survive: "
        f"{[(d.element, d.reason) for d in resolved.dropped]}")
    moment = resolved.moments[0]
    assert moment["timing_basis"] == "word_window:rent is due"
    assert moment["subject"] == "deadline pressure - the rent lands friday"
    assert moment["timeline_start"] == segments[0]["timeline_start"]


# --------------------------------------------------------------------------
# From test_superseded_props_visible.py
#
# A re-plan must never leave a silent duplicate: two props files, one span.
#
# POLICY (leave-and-make-visible): the step LEAVES the old file and REPORTS
# the pair (`ambiguous_span_pairs`, ledger-grouped, timeline-scoped), so the
# orphan is identifiable as the non-drawing mate without opening either
# render. Why not retire-with-archive: `docs/evidence/superseded_props.md`.

if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.steps.step_4_05_render_subtitles.step import (
    RENDERED,
    _ambiguous_pairs_for_dir,
    render_one_segment,
)
from library.tools.caption_asset_gc import (
    ambiguous_span_pairs,
    ledger_path_for,
)
from tests.unit.captions.test_overlay_carriage import (
    _props,
    _StubRenderer,
)


def _old_plan_props():
    """A plan entry predating `card_index`, in the old capitalisation."""
    props = _props()
    assert "_card_index" not in props
    props["subtitles"] = [dict(props["subtitles"][0],
                               text="And So My Very")]
    return props


def _replanned_props():
    """The same card re-planned: carded, lowercased, span shifted.

    The source span moves past the millisecond the filename carries,
    so the provenance stem - and the card - differs: the same-card
    retention rule names nothing superseded, and the old file would
    sit silent beside its replacement. The timeline span is
    untouched: one span, two files.
    """
    props = _props()
    props["_card_index"] = 7
    props["_source_start"] = 20.0
    props["_source_end"] = 22.0
    props["subtitles"] = [dict(props["subtitles"][0],
                               text="and so my very")]
    return props


def _render(props, out_dir):
    return render_one_segment(props, out_dir, "tl",
                              remotion_dir="/none",
                              renderer=_StubRenderer(),
                              overlay_geometry="full")


def test_replan_pair_is_reported_not_moved(tmp_path, capsys):
    """The brief's case: the orphan is named as the mate, on disk."""
    out_dir = str(tmp_path)
    first = _render(_old_plan_props(), out_dir)
    assert first["provenance"] == RENDERED
    second = _render(_replanned_props(), out_dir)
    assert second["provenance"] == RENDERED
    assert second["overlay_path"] != first["overlay_path"]
    # The same-card rule missed it: different card, nothing superseded.
    assert second["superseded"] == []
    # Nothing moved: both generations are still on disk.
    assert os.path.isfile(first["overlay_path"])
    assert os.path.isfile(second["overlay_path"])

    pairs = _ambiguous_pairs_for_dir(out_dir, {second["overlay_path"]})

    assert len(pairs) == 1
    pair = pairs[0]
    assert pair["timeline"] == "tl"
    assert pair["files"] == sorted(
        [first["overlay_path"], second["overlay_path"]])
    # Identifiable WITHOUT opening either render: the ledger alone
    # says which file drew this pass and which did not.
    assert pair["drawn_fresh"] == [second["overlay_path"]]
    # And said loudly, not just carried.
    assert "AMBIGUOUS" in capsys.readouterr().err


def test_explained_pair_is_not_ambiguous(tmp_path):
    """A re-render the ledger already attributes leaves no pair behind."""
    out_dir = str(tmp_path)
    first = _render(_props(), out_dir)
    changed = _props()
    changed["subtitles"] = [dict(changed["subtitles"][0],
                                 text="and so my very CHANGED")]
    second = _render(changed, out_dir)
    assert second["provenance"] == RENDERED
    assert second["superseded"] == [first["overlay_path"]]
    pairs = _ambiguous_pairs_for_dir(out_dir, {second["overlay_path"]})
    assert pairs == []


def test_sharing_and_cross_timeline_spans_are_not_pairs():
    """One file serving two placings is the sharing, not a duplicate."""
    entry = {"overlay_path": "/d/sub_a_b_1-2_aaaaaaaa.mov",
             "binding": {"timeline": "tl"},
             "timeline_start": 0.0, "timeline_end": 2.0,
             "superseded": []}
    twin = dict(entry, binding={"timeline": "tl", "block_position": 2})
    assert ambiguous_span_pairs([entry, twin], set()) == []
    # A master card and a reel card share absolute seconds legitimately:
    # the props carry no timeline, which is why this groups on the ledger
    # bindings instead of on a directory scan.
    master = {"overlay_path": "/d/sub_a_b_1-2_aaaaaaaa.mov",
              "binding": {"timeline": "master"},
              "timeline_start": 10.0, "timeline_end": 12.0,
              "superseded": []}
    reel = {"overlay_path": "/d/sub_c_d_3-4_bbbbbbbb.mov",
            "binding": {"timeline": "reel 01"},
            "timeline_start": 10.0, "timeline_end": 12.0,
            "superseded": []}
    assert ambiguous_span_pairs([master, reel], set()) == []


def test_stale_duplicate_with_no_fresh_claim_is_still_visible():
    """A duplicate from history no pass in this process drew is still
    reported - with nobody named as drawing - rather than silent."""
    old = {"overlay_path": "/d/sub_a_b_1-2_aaaaaaaa.mov",
           "binding": {"timeline": "tl"},
           "timeline_start": 0.0, "timeline_end": 2.0,
           "superseded": []}
    new = {"overlay_path": "/d/sub_c_d_3-4_bbbbbbbb.mov",
           "binding": {"timeline": "tl"},
           "timeline_start": 0.0, "timeline_end": 2.0,
           "superseded": []}
    pairs = ambiguous_span_pairs([old, new], set())
    assert len(pairs) == 1
    assert pairs[0]["drawn_fresh"] == []
    assert pairs[0]["files"] == sorted(
        [old["overlay_path"], new["overlay_path"]])


def test_ledger_is_the_only_thing_the_report_reads(tmp_path):
    """The pair is established from ledger paths alone: corrupt the
    ledger and the report degrades to a note, never a refusal."""
    out_dir = str(tmp_path)
    _render(_old_plan_props(), out_dir)
    with open(ledger_path_for(out_dir), "w", encoding="utf-8") as handle:
        handle.write("{not json")
    assert _ambiguous_pairs_for_dir(out_dir, set()) == []
