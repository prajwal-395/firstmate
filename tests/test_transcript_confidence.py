"""The selector can see how sure the transcriber was, and still no threshold.

A clean selector run over the whole 45-minute episode named the one
thing it needed and did not have, and priced it in its own
`could_not_determine`: it could not tell a garbled READING from garbled
AUDIO, so it ended reel 22 at 284.07 rather than 299.41 - giving up
Craig's 290.73-299.41 pickup and about 15 seconds of length to keep the
damaged line outside the span.

What it asked for was "a per-line transcription confidence beside the
text - the ASR already produces one - or a flag saying a line's
characters fall outside the language the rest of the transcript is in".

Both now exist, and this file holds all three directions at once,
because a gate that can only fail one way is not a gate
(AGENTS.md 10.4):

  * the transcriber's own confidence REACHES the prompt, through the
    declaration at the manifest's top level that #583 built;
  * the word timings, source paths and item ids #583 removed still do
    NOT come back with it; and
  * NO threshold fires on any of it. The number is published and the
    model judges - the captain's standing ruling (AGENTS.md 10.5).

Measured on the field-test transcript, 2026-09-06
-------------------------------------------------
It carries no confidence at all: 940 segments whose keys are exactly
`speaker, text, timeline_start, timeline_end, source_file, source_start,
source_end, resolve_item_id, words, read_from_words`, and 8,509 words
whose keys are exactly `word, start, end, timed`.  `faster_whisper`
emits `avg_logprob` per segment and `whisperx.align` carries it; both
write sites in `timeline_transcript` rebuilt the dict without it.

Its letters are 38,008 LATIN and 8 HANGUL, and all 8 sit on one line -
284.13-288.06, exactly the line the model named.  One flagged row in
940.
"""

import ast
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import project_step_context
from library.tools.context_projector import declared_context_fields
from library.tools.context_views import build_view
from library.tools.toon_serializer import json_to_toon
from library.tools import transcript_confidence as tc

STEP_DIR = REPO / "library" / "steps" / "step_3_04_select_reels"


def manifest() -> dict:
    return json.loads((STEP_DIR / "manifest.json").read_text(encoding="utf-8"))


def segment(speaker, start, end, text, item="clip-a", avg_logprob=None):
    """One transcript row, carrying everything the real document carries.

    The noise is IN the fixture on purpose: a test that leaves it out
    proves nothing about a projection whose whole job is to remove it.
    """
    words = []
    step = (end - start) / max(len(text.split()), 1)
    for i, word in enumerate(text.split()):
        words.append({"word": word, "start": round(start + i * step, 3),
                      "end": round(start + (i + 1) * step, 3), "timed": True})
    row = {
        "speaker": speaker,
        "text": text,
        "timeline_start": start,
        "timeline_end": end,
        "source_file": "/Volumes/Media/podcast media/LC4932.MXF",
        "source_start": start + 100.0,
        "source_end": end + 100.0,
        "resolve_item_id": item,
        "words": words,
        "read_from_words": False,
    }
    if avg_logprob is not None:
        row["avg_logprob"] = avg_logprob
    return row


# The measured shape of the disputed stretch, small enough to read.
# 281.07 is the clean reading of the payoff line; 284.13 is the same
# sentence again with eight Hangul characters in front of it, and it is
# where the reel was cut short.
CLEAN = ("So one which is Google is a search engine, and the other chat GPT "
         "is a decision engine.")
GARBLED = "같이 라고 on Google search, and chat GPT is a decision engine."
PICKUP = "decision engine i like that name i mean the stats show too"


def document(with_confidence: bool) -> dict:
    """The stretch, with and without what the ASR recorded about it."""
    conf = (-0.21, -0.83, -0.19) if with_confidence else (None, None, None)
    return {
        "measurement": "Speech transcribed by WhisperX. Times are timeline time.",
        "derived_from": {"duration_seconds": 400.0, "fps": 23.976,
                         "picture_holes": []},
        "segments": [
            segment("Akshita", 281.07, 284.07, CLEAN, "clip-b", conf[0]),
            segment("Akshita", 284.13, 288.06, GARBLED, "clip-b", conf[1]),
            segment("Craig", 290.73, 299.41, PICKUP, "clip-c", conf[2]),
        ],
    }


NOISE_KEYS = ("words", "source_file", "source_start", "source_end",
              "resolve_item_id", "read_from_words")


def projected(doc: dict) -> dict:
    """What step 3.04's prompt really carries, through the real projection."""
    from library.steps.step_3_04_select_reels.bridge import build_context

    tables = build_context({"timeline_transcript": doc})
    inputs = dict(tables)
    inputs["timeline_transcript"] = doc
    inputs["project_folder"] = "/nowhere/project"
    return project_step_context(inputs, manifest(), set(tables))


# ── Direction one: the confidence reaches the prompt ─────────────────

def test_it_arrives_through_the_declaration_at_the_manifests_top_level():
    """#583's mechanism is what carries this, not a route around it.

    The confidence rides inside `view:spoken_lines`, which is where the
    words it sits beside already ride. Nothing widens the allow-list.
    """
    fields = declared_context_fields(manifest(), "select_reels")
    assert fields is not None
    assert "view:spoken_lines" in fields
    assert not any(f.startswith("timeline_transcript.segments")
                   for f in fields), (
        "the confidence was smuggled in by widening the projection "
        "rather than declared through the view that carries the words")


def test_the_transcribers_own_number_is_on_the_row_with_the_words():
    context = json_to_toon(projected(document(with_confidence=True)))
    assert "avg_logprob" in context
    for value in ("-0.21", "-0.83", "-0.19"):
        assert value in context, (
            f"{value} is what the transcriber recorded and the model "
            f"cannot see it")


def test_the_number_is_verbatim_and_nothing_is_derived_from_it():
    view = build_view("spoken_lines",
                      {"timeline_transcript": document(True)})["spoken_lines"]
    assert [row["avg_logprob"] for row in view["lines"]] == [-0.21, -0.83,
                                                             -0.19]
    for row in view["lines"]:
        assert set(row) == {"speaker", "start", "end", "text", "avg_logprob"}, (
            "a row carries the ASR's own number and nothing computed "
            "from it")


def test_the_prompt_says_what_the_number_is_and_that_nothing_fires_on_it():
    context = json_to_toon(projected(document(with_confidence=True)))
    assert tc.CONFIDENCE_LEGEND in context, (
        "a number with no legend is a number the model has to guess the "
        "scale of")
    assert "no threshold is applied to it" in tc.CONFIDENCE_LEGEND.lower()


def test_an_absent_confidence_is_STATED_rather_than_invented():
    """Every transcript written before this landed carries none, and
    saying so is the whole difference between a missing measurement and
    a measurement of zero."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": document(False)})["spoken_lines"]
    assert view["transcription_confidence"] == tc.CONFIDENCE_ABSENT
    for row in view["lines"]:
        assert "avg_logprob" not in row, (
            "a row carries a confidence key on a transcript that records "
            "none, so absent and measured read the same")
    context = json_to_toon(projected(document(False)))
    assert tc.CONFIDENCE_ABSENT in context


# ── The other half: the script the line is written in ────────────────

def test_the_line_the_model_asked_about_is_the_line_that_is_flagged():
    flagged = tc.script_mismatches(document(False))
    assert len(flagged) == 1
    assert flagged[0]["start"] == 284.13 and flagged[0]["end"] == 288.06
    assert flagged[0]["speaker"] == "Akshita"
    assert flagged[0]["foreign"] == {"HANGUL": 4}


def test_the_flag_reaches_the_prompt_with_the_span_and_the_words():
    context = json_to_toon(projected(document(False)))
    assert "284.13-288.06" in context
    assert "HANGUL" in context
    assert tc.SCRIPT_LEGEND in context


def test_a_transcript_written_in_one_script_flags_nothing():
    """The ordinary case costs nothing to say, and a report that fires
    on clean material is worse than none."""
    clean = document(False)
    clean["segments"][1]["text"] = CLEAN
    assert tc.script_mismatches(clean) == []
    view = build_view("spoken_lines",
                      {"timeline_transcript": clean})["spoken_lines"]
    assert "script_mismatch" not in view


def test_the_dominant_script_comes_from_the_transcript_itself():
    """Not from anything declared about the project: a Korean episode
    with one English line must flag the ENGLISH one."""
    korean = document(False)
    for row in korean["segments"]:
        row["text"] = "이것은 한국어 문장입니다"
    korean["segments"][2]["text"] = "and this one is not"
    assert tc.dominant_script(korean["segments"]) == "HANGUL"
    flagged = tc.script_mismatches(korean)
    assert len(flagged) == 1 and flagged[0]["start"] == 290.73
    assert set(flagged[0]["foreign"]) == {"LATIN"}


def test_digits_and_punctuation_are_not_evidence_of_a_script():
    assert tc.script_census("2023 -- $40,000!") == {}
    assert tc.script_of("7") is None and tc.script_of(" ") is None
    assert tc.script_of("a") == "LATIN"


def test_a_transcript_with_no_letters_flags_nothing():
    empty = document(False)
    for row in empty["segments"]:
        row["text"] = "1234"
    assert tc.dominant_script(empty["segments"]) is None
    assert tc.script_mismatches(empty) == []


def test_the_flag_says_what_it_cannot_catch():
    """The same transcript carries `kalabrahat Correct.` at 299.999 -
    the same failure in LATIN characters. A signal that reads as
    complete is worse than one that names its own blind spot."""
    latin_garble = document(False)
    latin_garble["segments"][1]["text"] = "kalabrahat Correct."
    assert tc.script_mismatches(latin_garble) == [], (
        "the fixture no longer demonstrates the blind spot")
    assert "not a line proven clean" in tc.SCRIPT_LEGEND


# ── Direction two: the noise still cannot get through ────────────────

def test_no_word_timing_source_path_or_item_id_comes_back_with_it():
    output = projected(document(with_confidence=True))
    blob = json.dumps(output)
    for key in NOISE_KEYS:
        assert f'"{key}"' not in blob, (
            f"{key!r} is back in select_reels' prompt")
    assert ".MXF" not in blob and "/Volumes/" not in blob
    assert "clip-b" not in blob and "clip-c" not in blob
    assert set(output["timeline_transcript"]) == {"measurement"}


def test_the_revert_is_exactly_what_that_forbids():
    """Declaring the raw document again is the cheap way to buy a
    confidence column, and it must stay refused. Without this the
    assertions above would be describing a projection rather than
    gating one."""
    from library.steps.step_3_04_select_reels.bridge import build_context

    doc = document(with_confidence=True)
    reverted = manifest()
    reverted["context_fields"] = ["timeline_transcript", "reel_candidates"]
    tables = build_context({"timeline_transcript": doc})
    inputs = dict(tables)
    inputs["timeline_transcript"] = doc
    blob = json.dumps(project_step_context(inputs, reverted, set(tables)))
    for key in NOISE_KEYS:
        assert f'"{key}"' in blob, (
            f"the revert no longer carries {key!r}, so forbidding it "
            f"proves nothing")
    assert ".MXF" in blob and "clip-b" in blob


# ── Direction three: nothing fires on any of it ──────────────────────

def _numeric_comparisons(source: str) -> list:
    """Every `x < 0.5`-shaped expression in a module, by line.

    The unary minus is unwrapped deliberately: `avg_logprob` is
    NEGATIVE, so any threshold anyone were tempted to write on it would
    read `< -1.0` and arrive as a `UnaryOp` around the constant rather
    than as the constant itself.
    """
    def literal(node):
        while isinstance(node, ast.UnaryOp) and isinstance(node.op,
                                                           (ast.USub, ast.UAdd)):
            node = node.operand
        return (isinstance(node, ast.Constant)
                and isinstance(node.value, (int, float))
                and not isinstance(node.value, bool))

    found = []
    for node in ast.walk(ast.parse(source)):
        if not isinstance(node, ast.Compare):
            continue
        for operand in [node.left, *node.comparators]:
            if literal(operand):
                found.append((node.lineno, ast.unparse(node)))
    return found


def test_the_threshold_check_can_fail():
    """A gate that cannot fail is worse than no gate (AGENTS.md 10.4)."""
    assert _numeric_comparisons("if confidence < 0.4:\n    pass\n")
    assert _numeric_comparisons("bad = [x for x in rows if x['s'] <= -1.0]")
    assert not _numeric_comparisons("if value is None:\n    pass\n")


def test_no_confidence_threshold_is_invented_anywhere():
    """`if confidence < 0.x` is the line the captain ruled out, and this
    is the module that would be where it went."""
    source = (REPO / "library" / "tools"
              / "transcript_confidence.py").read_text(encoding="utf-8")
    assert _numeric_comparisons(source) == [], (
        "a numeric comparison appeared in the module that publishes the "
        "transcriber's confidence")


def test_no_line_is_dropped_reordered_or_marked_by_its_confidence():
    """The model gets the number and judges. Nothing here judges for it."""
    doc = document(with_confidence=True)
    doc["segments"][1]["avg_logprob"] = -9.5      # as bad as it gets
    view = build_view("spoken_lines",
                      {"timeline_transcript": doc})["spoken_lines"]
    assert [row["start"] for row in view["lines"]] == [281.07, 284.13, 290.73]
    assert view["lines"][1]["avg_logprob"] == -9.5
    assert view["lines"][1]["text"] == GARBLED


def test_a_flagged_line_is_still_a_row_and_still_a_boundary():
    """The script flag REPORTS. It does not remove the line from the
    conversation or from the seconds a reel may be cut at - deciding
    that is the model's, and it is the decision that cost 15 seconds."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": document(False)})["spoken_lines"]
    assert [row["start"] for row in view["lines"]] == [281.07, 284.13, 290.73]
    assert view["lines"][1]["text"] == GARBLED


# ── What the transcriber writes down, so a re-run has it ─────────────

def test_the_transcriber_stops_throwing_the_number_away():
    """Both write sites rebuilt the dict without it. `avg_logprob` is
    the one the aligner carries, so it survives with nothing
    re-attaching it."""
    source = (REPO / "library" / "tools"
              / "timeline_transcript.py").read_text(encoding="utf-8")
    assert "AVG_LOGPROB: s.avg_logprob" in source, (
        "the segment handed to the aligner is rebuilt without the "
        "transcriber's own confidence again")


def test_a_row_carries_it_onto_disk_and_back():
    from library.tools.timeline_transcript import SpokenSegment

    row = SpokenSegment(speaker="Akshita", text=CLEAN, timeline_start=281.07,
                        timeline_end=284.07, source_file="/x.MXF",
                        source_start=1.0, source_end=4.0,
                        resolve_item_id="clip-b", avg_logprob=-0.21)
    assert row.as_dict()["avg_logprob"] == -0.21
    assert SpokenSegment(**row.as_dict()).avg_logprob == -0.21


def test_a_transcript_that_predates_this_still_loads():
    """`rebind_document` reconstructs every row, and every transcript on
    disk today was written without the field."""
    from library.tools.timeline_transcript import SpokenSegment

    old = segment("Craig", 1.0, 2.0, "hello", "clip-a")
    old["words"] = tuple(old["words"])
    assert SpokenSegment(**old).avg_logprob is None


def test_a_row_re_read_at_word_level_keeps_the_decodes_confidence():
    """`read_from_words` splits a row it did not re-hear, so every piece
    carries the confidence of the decode that produced those words."""
    from library.tools.timeline_ingest import TimelineClip
    from library.tools.timeline_transcript import read_from_words

    clip = TimelineClip(
        resolve_item_id="clip-a", track_type="video", track_index=1,
        track_name="Craig", speaker="Craig", source_file="/x.MXF",
        source_in=100.0, source_out=110.0, source_in_frame=2400,
        source_out_frame=2640, source_frames=None,
        timeline_start=0.0, timeline_end=10.0, name="clip")
    words = [{"word": w, "start": 1.0 + i, "end": 1.5 + i, "timed": True}
             for i, w in enumerate(["one", "two", "three"])]
    pieces = read_from_words("Craig", "one two three", words, [clip],
                             avg_logprob=-0.42)
    assert pieces and all(p.avg_logprob == -0.42 for p in pieces)


def test_the_document_says_how_many_rows_carry_one():
    """Zero and "nobody looked" read the same from outside."""
    from library.tools.timeline_transcript import (SpokenSegment,
                                                   transcript_document)

    class Snapshot:
        project_name = "p"
        timeline_name = "t"
        fps = 23.976
        duration = 400.0
        clips = ()

    rows = [SpokenSegment("Akshita", CLEAN, 281.07, 284.07, "/x.MXF", 1.0,
                          4.0, "clip-b", (), False, -0.21),
            SpokenSegment("Akshita", GARBLED, 284.13, 288.06, "/x.MXF", 4.0,
                          8.0, "clip-b", (), False, None)]
    built = transcript_document(Snapshot(), rows)
    assert built["segments_with_asr_confidence"] == 1
    assert built["segments"][0]["avg_logprob"] == -0.21
    assert built["segments"][1]["avg_logprob"] is None


# ── The prompt stays honest about its own size ───────────────────────

def test_the_report_is_one_line_rather_than_a_column_of_empty_cells():
    """Measured on the field-test episode: 1 flagged row in 929. A
    sparse per-row column costs 945 characters of empty cells to carry
    that one value; this costs about 660 and names the span, the
    speaker, the scripts and the words."""
    view = build_view("spoken_lines",
                      {"timeline_transcript": document(False)})["spoken_lines"]
    for row in view["lines"]:
        assert "script" not in row and "foreign" not in row
    assert "script_mismatch" in view


def test_the_handoff_names_both_things_the_projection_now_delivers():
    """A prompt naming a table nothing sends is the contract defect, and
    a table nothing names is the same defect facing the other way."""
    handoff = (STEP_DIR / "handoff.md").read_text(encoding="utf-8")
    assert "`transcription_confidence`" in handoff
    assert "`script_mismatch`" in handoff
    view = projected(document(True))["spoken_lines"]
    assert "transcription_confidence" in view and "script_mismatch" in view


def test_the_confidence_column_costs_about_seven_characters_a_row():
    """#585 justified +25,859 characters for 793 cut boundaries at 32.6
    each. The same arithmetic, asked of this.

    Measured on the field-test episode: 929 rows, +6,589 characters,
    7.1 a row. Asked here at the same scale, because the column header
    and the legend are fixed costs and a three-row fixture would price
    them per row and report 66.
    """
    rows = 929
    without = {"measurement": "x", "derived_from": {}, "segments": [
        segment("Akshita", float(i), i + 0.9,
                "one line of the conversation, about this long", "clip-b")
        for i in range(rows)]}
    with_it = json.loads(json.dumps(without))
    for i, row in enumerate(with_it["segments"]):
        row["avg_logprob"] = round(-0.05 - (i % 90) / 100.0, 3)

    grew = (len(json_to_toon(build_view("spoken_lines",
                                        {"timeline_transcript": with_it})))
            - len(json_to_toon(build_view("spoken_lines",
                                          {"timeline_transcript": without}))))
    per_row = grew / rows
    assert 0 < per_row < 12, (
        f"the confidence column costs {per_row:.1f} characters a row, "
        f"{grew} over {rows} rows")
