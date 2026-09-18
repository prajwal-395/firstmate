"""A piece of captain feedback keeps its identity across a rebuild.

The defect: `marker_routing._note_id` keys a note on
``timeline:source:frame``, and `marker_resolution` states the
consequence itself - *"NOT [stable] across a rebuild that moves the
frame"*.  Every fix in this pipeline rebuilds the timeline, so the act
of answering a note destroys the only handle on it, and the next round
it gets typed again.

These tests pin the durable half: the same words on the same reel are
one note whatever frame they are read at, and a marker we wrote back
says so mechanically rather than by its colour.
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import feedback_ledger as fl  # noqa: E402
from library.tools import marker_feedback as mf  # noqa: E402
from library.tools import marker_resolution as mr  # noqa: E402


REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
ASK = ("the ending tv close animation needs to happen right after "
       "akshita finishes talking")


def pull(timeline, pulled_at, *notes):
    return (Path(f"{pulled_at}.markers.json"),
            {"format": "marker_feedback/1", "timeline": timeline,
             "pulled_at": pulled_at, "notes": list(notes)})


def note(text, frame=100, **extra):
    return {"name": "feedback", "note": text, "text": f"feedback\n\n{text}",
            "source": "timeline_marker", "frame": frame, **extra}


# ── The identity survives what a rebuild changes ─────────────────

def test_the_same_words_at_a_different_frame_are_ONE_note():
    """The whole point. Remove the identity and this is two notes.

    A rebuild moves every frame, which is the exact thing the existing
    `timeline:source:frame` id is made of.
    """
    before = fl.durable_identity(REEL, ASK)
    after = fl.durable_identity(REEL, ASK)
    assert before == after
    entries = fl.collect(None, [
        pull(REEL, "2026-09-11T04:00:00Z", note(ASK, frame=1902)),
        pull(REEL, "2026-09-11T18:00:00Z", note(ASK, frame=1907))])
    assert len(entries) == 1
    entry = next(iter(entries.values()))
    assert entry.pulls == 2
    assert sorted(entry.frames) == [1902, 1907]
    assert entry.first_asked == "2026-09-11T04:00:00Z"
    assert entry.last_seen == "2026-09-11T18:00:00Z"


def test_a_staging_container_is_the_same_reel():
    """Staged, backed up and promoted are three names for one reel."""
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX

    assert fl.durable_identity(f"{REEL}{STAGING_TIMELINE_SUFFIX}", ASK) \
        == fl.durable_identity(REEL, ASK)
    assert fl.durable_identity(f"{REEL} (pre-rebuild backup)", ASK) \
        == fl.durable_identity(REEL, ASK)


def test_an_archived_generation_is_the_same_reel():
    """A rebuild retires every reel it touches into
    `... (archived round NNN[.M])`; those are names for one reel, so
    the identity folds them - the shape `reel_retirement` builds, read
    back through its own `parse_archived`."""
    assert fl.durable_identity(f"{REEL} (archived round 001)", ASK) \
        == fl.durable_identity(REEL, ASK)
    assert fl.durable_identity(f"{REEL} (archived round 001.2)", ASK) \
        == fl.durable_identity(REEL, ASK)
    assert fl.base_reel_name(f"{REEL} (archived round 001)") == REEL


def test_a_hand_made_parens_name_is_its_own_reel():
    """Copies the project has actually carried - `(batch-1050)`,
    `(final)`, `(MFA timings)`, `(all three fixes)`,
    `(baseline scratch)` - carry captain or firstmate intent no
    pattern can recover. Only the engine's own machine-shaped
    suffixes strip; a regex over any parenthesis would collide a reel
    legitimately named with one against a different reel."""
    for suffix in ("(batch-1050)", "(final)", "(MFA timings)",
                   "(all three fixes)", "(baseline scratch)"):
        name = f"{REEL} {suffix}"
        assert fl.base_reel_name(name) == name
        assert fl.durable_identity(name, ASK) \
            != fl.durable_identity(REEL, ASK)


def test_whitespace_and_case_do_not_split_a_note():
    assert fl.durable_identity(REEL, "  The  Ending\nCuts  Early ") \
        == fl.durable_identity(REEL, "the ending cuts early")


def test_different_words_are_different_notes():
    """Normalisation must not merge two questions into one.

    A ledger that answered note B with note A's resolution is worse
    than one that files the same words twice.
    """
    assert fl.durable_identity(REEL, ASK) != fl.durable_identity(REEL, ASK + "!")


def test_the_same_words_on_two_reels_are_two_notes():
    assert fl.durable_identity("Reel 01 - a", ASK) \
        != fl.durable_identity("Reel 23 - b", ASK)


def test_a_marker_with_no_words_asks_nothing():
    entries = fl.collect(None, [
        pull(REEL, "2026-09-11T04:00:00Z",
             {"name": "", "note": "", "text": "", "frame": 1})])
    assert entries == {}


# ── The state, and the join back to a resolution record ──────────

def resolution(status, resolved_at, text=ASK, timeline=REEL):
    return {"note_id": f"{timeline}:timeline_marker:1902",
            "status": status, "resolved_at": resolved_at,
            "timeline": timeline, "name": "feedback", "note": text,
            "text": f"feedback\n\n{text}", "check": "a_roll_two_rows",
            "verifier": "marker_resolution.CHECKS[a_roll_two_rows]",
            "marker_removed": True}


def test_an_unanswered_note_is_open():
    document = fl.build(None, [pull(REEL, "2026-09-11T04:00:00Z",
                                    note(ASK))], resolutions=[])
    assert document["entries"][0]["state"] == fl.STATE_OPEN
    assert len(document["open"]) == 1


def test_a_resolution_reaches_its_note_ACROSS_a_rebuild():
    """The record keys on a frame; the join is on the WORDS.

    Remove the durable identity and this resolution reaches nothing,
    which is the state the project is in today.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK, frame=1902))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    entry = document["entries"][0]
    assert entry["state"] == mr.STATUS_RESOLVED_VERIFIED
    assert entry["resolution"]["check"] == "a_roll_two_rows"
    assert document["open"] == []


def test_a_note_seen_AFTER_being_recorded_resolved_is_RE_ASKED():
    """We said done, and the captain's marker is still there.

    The expensive failure the round report named, with a name and a
    count. Remove the re-ask reading and a fix that did not hold looks
    exactly like one that did.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK, frame=1902)),
         pull(REEL, "2026-09-11T18:00:00Z", note(ASK, frame=1907))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is True
    assert len(document["reasked"]) == 1


def test_a_note_resolved_AFTER_its_last_pull_is_not_a_re_ask():
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is False
    assert document["reasked"] == []


def test_a_declined_note_seen_again_is_not_a_re_ask():
    """Only a VERIFIED resolution can be contradicted by a re-ask.

    A decline never claimed the thing was fixed, so the marker still
    being there is the documented outcome, not a failure.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK)),
         pull(REEL, "2026-09-11T18:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_DECLINED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is False


def test_the_latest_resolution_wins():
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z"),
                     resolution(mr.STATUS_ADDRESSED_UNVERIFIED,
                                "2026-09-11T12:00:00Z")])
    assert document["entries"][0]["state"] == mr.STATUS_ADDRESSED_UNVERIFIED


def test_a_resolution_for_words_nobody_pulled_creates_no_entry():
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z",
                                text="something never typed")])
    assert len(document["entries"]) == 1
    assert document["entries"][0]["state"] == fl.STATE_OPEN


def test_the_states_are_marker_resolutions_own():
    """A second vocabulary is how two records come to disagree."""
    assert set(fl.STATES) == {fl.STATE_OPEN, mr.STATUS_RESOLVED_VERIFIED,
                              mr.STATUS_ADDRESSED_UNVERIFIED,
                              mr.STATUS_DECLINED, mr.STATUS_UNVERIFIABLE}


def test_a_status_outside_the_vocabulary_does_not_set_a_state():
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z", note(ASK))],
        resolutions=[resolution("looks_fine_to_me",
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["state"] == fl.STATE_OPEN


# ── Our reply is OURS, by record and not by colour ───────────────

def reply_note(answers, frame=101):
    return {"name": "reply: done", "note": "we did it",
            "text": "reply: done\n\nwe did it",
            "source": "timeline_marker", "frame": frame,
            "custom_data_raw": mf.reply_custom_data(
                answers=answers, answers_text=ASK)}


def test_a_reply_of_ours_is_identified_by_its_own_record():
    """Remove the record and this counts as an open captain question."""
    identity = fl.identity_of(note(ASK), REEL)
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              note(ASK, frame=1902), reply_note(identity))],
        resolutions=[])
    kinds = {e["identity"]: e["kind"] for e in document["entries"]}
    assert kinds[identity] == fl.KIND_ASK
    assert fl.KIND_REPLY in kinds.values()
    assert document["open"] == [identity]


def test_a_reply_links_back_to_the_question_it_answers():
    """A blue marker became a green reply and the question was gone."""
    identity = fl.identity_of(note(ASK), REEL)
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              note(ASK, frame=1902), reply_note(identity))],
        resolutions=[])
    asked = next(e for e in document["entries"]
                 if e["identity"] == identity)
    assert len(asked["answered_by"]) == 1
    reply = next(e for e in document["entries"]
                 if e["kind"] == fl.KIND_REPLY)
    assert reply["answers"] == identity


def test_an_unmarked_note_defaults_to_the_CAPTAINS():
    """Getting this backwards LOSES a question. Unmarked is theirs.

    `marker_feedback` has no vocabulary of marker colours by design, so
    a green marker with no record and no `reply:` shape is still read
    as an ask.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              {"name": "feedback", "note": "x",
               "text": "feedback\n\nx", "frame": 5, "color": "Green"})],
        resolutions=[])
    assert document["entries"][0]["kind"] == fl.KIND_ASK


def test_a_green_reply_shape_with_no_record_is_ours():
    """The live-project case: two green `reply:` markers, empty customData.

    Remove the shape half of `_authorship` and these count as the
    captain's open questions - the ledger then reports resolved
    feedback as outstanding.
    """
    shaped = {"name": "reply: tail breath for the TV switch-off",
              "note": "You asked (marker @1902): the ending plays early.",
              "text": ("reply: tail breath for the TV switch-off\n\n"
                       "You asked (marker @1902): the ending plays early."),
              "source": "timeline_marker", "frame": 1902,
              "color": "Green", "custom_data": {},
              "custom_data_raw": ""}
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z",
                    note(ASK, frame=1902), shaped)],
        resolutions=[])
    kinds = {e["identity"]: e["kind"] for e in document["entries"]}
    assert list(kinds.values()).count(fl.KIND_REPLY) == 1
    assert kinds[fl.identity_of(note(ASK), REEL)] == fl.KIND_ASK
    assert "1 reply of ours" in fl.render(document)


def test_a_captain_sentence_mentioning_reply_stays_an_ask():
    """Only a FIRST line starting with `reply:` is the writer's stamp.

    A captain note quoting the word back deeper in the body is still
    theirs - the prefix rule must not reach past the first line.
    """
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              note("please reply to this note", frame=5))],
        resolutions=[])
    assert document["entries"][0]["kind"] == fl.KIND_ASK


def test_a_replys_words_do_not_count_as_the_captain_echoing_themselves():
    identity = fl.durable_identity("Reel 01 - a", ASK)
    document = fl.build(
        None,
        [pull("Reel 01 - a", "2026-09-11T04:00:00Z", reply_note(identity)),
         pull("Reel 23 - b", "2026-09-11T04:00:00Z", reply_note(identity))],
        resolutions=[])
    assert document["echoes"] == {}


def test_one_instruction_on_four_reels_is_reported_as_ONE(tmp_path):
    """"apply this to all of the reels" - one mechanism, not N edits."""
    words = "this animation here is something i want applied to all reels"
    document = fl.build(
        None,
        [pull(f"Reel {n} - x", "2026-09-11T04:00:00Z", note(words))
         for n in ("01", "23", "28", "31")],
        resolutions=[])
    echoed = document["echoes"]
    assert len(echoed) == 1
    assert len(next(iter(echoed.values()))) == 4


# ── The file, and what it is not ─────────────────────────────────

def test_the_ledger_is_rewritten_whole_from_the_durable_records(tmp_path):
    """It is DERIVED. Nothing accumulates state of its own here.

    A ledger that kept state the pulls and resolutions do not have
    would become the second source of truth beside the captain's
    markers, which is the one thing this must not be.
    """
    (tmp_path / "marker_feedback").mkdir()
    fl.write_ledger(tmp_path, {"format": fl.LEDGER_FORMAT,
                               "entries": [{"identity": "stale"}]})
    document = fl.build(None, [pull(REEL, "2026-09-11T04:00:00Z",
                                    note(ASK))], resolutions=[])
    fl.write_ledger(tmp_path, document)
    written = fl.read_ledger(tmp_path)
    assert [e["identity"] for e in written["entries"]] \
        != ["stale"]
    assert json.loads(fl.ledger_path(tmp_path).read_text(
        encoding="utf-8"))["format"] == fl.LEDGER_FORMAT


def test_the_ledger_lands_in_the_captured_area(tmp_path):
    """Beside the pulls, where a re-render cannot reach it."""
    from library.tools.project_layout import Area, ProjectLayout

    path = fl.ledger_path(tmp_path)
    assert path.parent == ProjectLayout(str(tmp_path)).read_dir(
        Area.MARKER_FEEDBACK)


def test_render_shows_the_words_not_just_the_marker_name():
    """The first line of a marker's text is its NAME field.

    Printing it alone showed the word "feedback" for every note on
    `lucie/geo-podcast` and nothing the captain typed.
    """
    document = fl.build(None, [pull(REEL, "2026-09-11T04:00:00Z",
                                    note(ASK))], resolutions=[])
    printed = fl.render(document)
    assert "akshita finishes talking" in printed


def test_render_names_a_re_ask_out_loud():
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK, frame=1902)),
         pull(REEL, "2026-09-11T18:00:00Z", note(ASK, frame=1907))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    assert "RE-ASKED" in fl.render(document)


def test_render_survives_an_empty_project():
    assert "no note" in fl.render(fl.build(None, [], resolutions=[]))
