"""The selector can see a SUB-TURN boundary, and still cannot see the noise.

#583 took the raw `timeline_transcript` document out of step 3.04's
prompt: 817,316 characters, 8,509 per-word timing records, 940 absolute
source paths and 940 Resolve item ids, to say 47,182 characters of
English.  None of that should ever come back.

It also took away the only thing carrying the boundaries INSIDE a
speaker's turn, and nobody noticed because the pre-bridge is the
document's only reader in CODE - the model was reading it too.  Measured
on the field-test episode of 2026-09-06:

  * the run that still had the document timed a reel's closer to
    `Akshita 337.59-341.27`, a boundary that exists only in `segments`;
    her turn is 328.61-341.27;
  * the run after it was projected away said, unprompted, that *"every
    boundary I can name is a TURN boundary, because `turns` is the only
    speech table I was given"*, and opened its reel on "well this has
    been fun recently" - which the brief names as throat-clearing -
    because the line that should have opened it sits inside a turn;
  * 929 bound segments collapse into 136 turns, so 793 of the places a
    reel may start or stop disappeared in the collapse;
  * and the step's own post-bridge snaps every chosen boundary OUT to a
    bound-segment edge (`reel_proposal.snap_to_speech`), so the two
    halves of one step disagreed about what a boundary IS.

So this file holds both directions at once, because a gate that can only
fail one way is not a gate (AGENTS.md 10.4):

  * the sub-turn boundaries and their words REACH the prompt, and
  * the word timings, source paths and item ids still do NOT.

Every assertion here fails at `origin/main` (20dde19) or would fail on
the revert that would be the lazy way to buy the first half.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import project_step_context
from library.tools.context_projector import (
    MisplacedContextFields,
    declared_context_fields,
    project_fields,
)
from library.tools.context_views import CONTEXT_VIEWS, build_view
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

def test_the_view_is_declared_at_the_manifests_top_level():
    """#583's mechanism is what carries this, not a route around it."""
    fields = declared_context_fields(manifest(), "select_reels")
    assert fields is not None, (
        "select_reels declares no context_fields, so it is handed every "
        "byte it was routed - which is the state #583 removed")
    assert "view:spoken_lines" in fields
    assert "spoken_lines" in CONTEXT_VIEWS


def test_the_view_declares_the_input_it_reads():
    """A view is not routing. The step still has to be sent the input."""
    inputs = {i["name"] for i in manifest()["interface"]["inputs"]}
    assert "timeline_transcript" in inputs, (
        "select_reels asks for the spoken-lines view and no longer "
        "declares timeline_transcript, so there is nothing to read")


def test_every_bound_segment_is_a_row_with_its_speaker_and_its_words():
    view = build_view("spoken_lines", {"timeline_transcript": DOCUMENT})
    lines = view["spoken_lines"]["lines"]
    bound = [s for s in DOCUMENT["segments"] if s["resolve_item_id"]]
    assert len(lines) == len(bound)
    assert lines[0] == {"speaker": "Craig", "start": 312.75, "end": 320.1,
                        "text": "search didn't change, the question changed"}
    for row in lines:
        assert set(row) == {"speaker", "start", "end", "text"}, (
            "a row carries its start, its end, its speaker and its text - "
            "and nothing else")


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


def test_a_straddling_segment_is_not_a_row_and_is_still_reported():
    """A reel boundary is never placed on one - and hiding them is what
    let a borrowed closer land inside a 12.07s row nothing could see."""
    built = build_view("spoken_lines", {"timeline_transcript": DOCUMENT})
    view = built["spoken_lines"]
    assert all(row["start"] != 461.26 for row in view["lines"]), (
        "a segment that straddles a cut is offered as a boundary the "
        "post-bridge will not snap to")
    said = view["not_a_boundary"]
    assert "461.26-473.34" in said and "Akshita" in said
    assert "1 stretch(es)" in said


# ── Direction two: the noise still cannot get through ────────────────

def test_no_word_timing_source_path_or_item_id_reaches_the_prompt():
    output = projected()
    blob = json.dumps(output)
    for key in NOISE_KEYS:
        assert f'"{key}"' not in blob, (
            f"{key!r} is back in select_reels' prompt")
    assert ".MXF" not in blob and "/Volumes/" not in blob
    assert "clip-a" not in blob and "clip-b" not in blob
    carried = output["timeline_transcript"]
    assert set(carried) == {"measurement"}, (
        "the transcript document is back beside the view it was "
        "replaced by")


def test_a_field_the_manifest_does_not_declare_cannot_reach_the_prompt():
    """#583's guarantee, asked of a field invented after it landed."""
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
