"""The selector can see a SUB-TURN boundary, and still cannot see the noise.

Both directions at once, because a gate that can only fail one way is
not a gate (AGENTS.md 10.4): the sub-turn boundaries and their words
REACH step 3.04's prompt, and the word timings, source paths and item
ids still do NOT. Incident (#583 and the 337.59 closer):
docs/evidence/spoken_lines.md.
"""

import json
import sys
from pathlib import Path


REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import project_step_context
from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon

STEP_DIR = REPO / "library" / "steps" / "step_3_04_select_reels"


def manifest() -> dict:
    return json.loads((STEP_DIR / "manifest.json").read_text(encoding="utf-8"))


def segment(speaker, start, end, text, item="clip-a"):
    """One transcript row, carrying everything the real document carries.

    The noise is IN the fixture on purpose: a test that leaves it out
    proves nothing about a projection whose whole job is to remove it.
    """
    words = []
    step = (end - start) / max(len(text.split()), 1)
    for i, word in enumerate(text.split()):
        words.append({"word": word, "start": round(start + i * step, 3),
                      "end": round(start + (i + 1) * step, 3), "timed": True})
    return {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": "/Volumes/Media/podcast media/LCATL0013.MXF",
        "source_start": start + 100.0,
        "source_end": end + 100.0,
        "resolve_item_id": item,
        "words": words,
        "read_from_words": False,
    }


# The shape of the field-test episode, small enough to read: Akshita's
# 328.61-341.27 turn is FOUR segments, and 337.59 is the third of them -
# the boundary the noisy run used and the clean run could not name.
CLOSER = "And if you want to see how your brand appears, you should go check it out."
BIO = "The link's in our bio."

DOCUMENT = {
    "measurement": "Speech transcribed by WhisperX. Times are timeline time.",
    "derived_from": {"duration_seconds": 400.0, "fps": 23.976,
                     "picture_holes": []},
    "segments": [
        segment("Craig", 312.75, 320.10,
                "search didn't change, the question changed", "clip-a"),
        segment("Craig", 320.40, 328.23,
                "and that is what decides where you show up", "clip-a"),
        segment("Akshita", 328.61, 333.20,
                "yes and that is exactly what we found in the audit",
                "clip-b"),
        segment("Akshita", 333.30, 337.40,
                "one is a search engine the other is a decision engine",
                "clip-b"),
        segment("Akshita", 337.59, 340.47, CLOSER, "clip-b"),
        segment("Akshita", 340.59, 341.27, BIO, "clip-b"),
        # Straddles a cut: no `resolve_item_id`. A boundary is never
        # placed on one, and it is REPORTED rather than hidden.
        {"speaker": "Akshita", "text": "Yeah.",
         "timeline_start": 461.26, "timeline_end": 473.34,
         "source_file": None, "source_start": None, "source_end": None,
         "resolve_item_id": None, "words": [], "read_from_words": False},
    ],
}

NOISE_KEYS = ("words", "source_file", "source_start", "source_end",
              "resolve_item_id", "read_from_words")


def projected() -> dict:
    """What step 3.04's prompt really carries, through the real projection."""
    from library.steps.step_3_04_select_reels.bridge import build_context

    tables = build_context({"timeline_transcript": DOCUMENT})
    inputs = dict(tables)
    inputs["timeline_transcript"] = DOCUMENT
    inputs["project_folder"] = "/nowhere/project"
    return project_step_context(inputs, manifest(), set(tables))


# ── Direction one: the sub-turn boundaries reach the prompt ──────────

def test_every_bound_segment_is_a_row_and_a_straddling_one_is_reported():
    """A row carries its speaker, start, end and text - nothing else. A
    segment that straddles a cut is never offered as a boundary (the
    post-bridge will not snap to it), and hiding it is what let a
    borrowed closer land inside a 12.07s row nothing could see, so it is
    reported."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": DOCUMENT})["spoken_lines"]
    lines = view["lines"]
    bound = [s for s in DOCUMENT["segments"] if s["resolve_item_id"]]
    assert len(lines) == len(bound)
    assert lines[0] == {"speaker": "Craig", "start": 312.75, "end": 320.1,
                        "text": "search didn't change, the question changed"}
    for row in lines:
        assert set(row) == {"speaker", "start", "end", "text"}
    assert all(row["start"] != 461.26 for row in lines)
    said = view["not_a_boundary"]
    assert "461.26-473.34" in said and "Akshita" in said
    assert "1 stretch(es)" in said


def test_the_boundary_inside_a_turn_reaches_the_prompt():
    """`Akshita 337.59-341.27` is the measured case, and it is sub-turn.

    Her turn is 328.61-341.27. 337.59 exists only in `segments`, and the
    run that could not see it opened its reel on throat-clearing.
    """
    context = json_to_toon(projected())
    assert "328.61,341.27" in context, "the turn itself must still be there"
    assert "337.59" in context, (
        "the sub-turn boundary the earlier run cut on is invisible again")
    assert CLOSER in context, (
        "a boundary with no words at it is not a boundary anyone can use")


# ── Direction two: the noise still cannot get through ────────────────

def test_no_word_timing_source_path_item_id_or_undeclared_field_reaches():
    """#583's guarantee, including for a field invented after it landed."""
    output = projected()
    blob = json.dumps(output)
    for key in NOISE_KEYS:
        assert f'"{key}"' not in blob, (
            f"{key!r} is back in select_reels' prompt")
    assert ".MXF" not in blob and "/Volumes/" not in blob
    assert "clip-a" not in blob and "clip-b" not in blob
    assert set(output["timeline_transcript"]) == {"measurement"}, (
        "the transcript document is back beside the view it was "
        "replaced by")

    document = json.loads(json.dumps(DOCUMENT))
    document["a_field_nobody_declared"] = "x" * 64
    document["segments"][0]["another_undeclared_field"] = "y" * 64
    from library.steps.step_3_04_select_reels.bridge import build_context

    tables = build_context({"timeline_transcript": document})
    inputs = dict(tables)
    inputs["timeline_transcript"] = document
    blob = json.dumps(project_step_context(inputs, manifest(), set(tables)))
    assert "a_field_nobody_declared" not in blob
    assert "another_undeclared_field" not in blob
    assert "x" * 64 not in blob and "y" * 64 not in blob


def test_the_revert_is_exactly_what_the_noise_assertions_forbid():
    """The cheap way to buy sub-turn boundaries is to declare the raw
    document again, and it must stay refused.

    Without this the four assertions above would be describing a
    projection rather than gating one: they pass at `origin/main` too,
    because #583 is what makes them pass. This one shows what they are
    holding shut - swap the declaration for the revert and the words,
    the paths and the item ids all come straight back.
    """
    from library.steps.step_3_04_select_reels.bridge import build_context

    reverted = manifest()
    reverted["context_fields"] = ["timeline_transcript", "reel_candidates"]
    tables = build_context({"timeline_transcript": DOCUMENT})
    inputs = dict(tables)
    inputs["timeline_transcript"] = DOCUMENT
    blob = json.dumps(project_step_context(inputs, reverted, set(tables)))
    for key in NOISE_KEYS:
        assert f'"{key}"' in blob, (
            f"the revert no longer carries {key!r}, so forbidding it "
            f"proves nothing")
    assert ".MXF" in blob and "clip-a" in blob


def test_projecting_an_already_projected_tree_keeps_the_view():
    """A hybrid step can be projected twice; the second pass sees a tree
    the first one already took `segments` out of."""
    once = projected()
    twice = project_step_context(json.loads(json.dumps(once)), manifest(),
                                 {"turns", "reel_candidates"})
    assert twice["spoken_lines"] == once["spoken_lines"]


# ── The words are sent ONCE ──────────────────────────────────────────

def test_the_turn_table_no_longer_carries_the_words_the_view_carries():
    """A turn's text is its segments' texts joined by a space, to the
    byte, so publishing both is the summary and its own source."""
    output = projected()
    for row in output["turns"]:
        assert set(row) == {"speaker", "start", "end"}, (
            "`turns` carries the words a second time")
    context = json_to_toon(output)
    assert context.count(CLOSER) == 1, (
        "the speech reaches the prompt twice")
    assert context.count(BIO) == 1
