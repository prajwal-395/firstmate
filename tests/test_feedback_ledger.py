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


def test_only_the_engines_own_reel_suffixes_fold_into_one_reel():
    """Staged, backed up, promoted and archived (`reel_retirement`'s
    `(archived round NNN[.M])`) are names for one reel. Hand-made
    parentheses the project has carried keep their own identity - a
    regex over any parenthesis would collide two reels. And
    normalisation never merges different words or different reels: a
    ledger that answered note B with note A's resolution is worse than
    one that files the same words twice."""
    from library.tools.resolve_bin_layout import STAGING_TIMELINE_SUFFIX

    same = fl.durable_identity(REEL, ASK)
    for alias in (f"{REEL}{STAGING_TIMELINE_SUFFIX}",
                  f"{REEL} (pre-rebuild backup)",
                  f"{REEL} (archived round 001)",
                  f"{REEL} (archived round 001.2)"):
        assert fl.durable_identity(alias, ASK) == same, alias
    assert fl.base_reel_name(f"{REEL} (archived round 001)") == REEL

    for suffix in ("(batch-1050)", "(final)", "(MFA timings)",
                   "(all three fixes)", "(baseline scratch)"):
        name = f"{REEL} {suffix}"
        assert fl.base_reel_name(name) == name
        assert fl.durable_identity(name, ASK) != same

    assert fl.durable_identity(REEL, ASK + "!") != same
    assert fl.durable_identity("Reel 01 - a", ASK) \
        != fl.durable_identity("Reel 23 - b", ASK)


# ── The state, and the join back to a resolution record ──────────

def resolution(status, resolved_at, text=ASK, timeline=REEL):
    return {"note_id": f"{timeline}:timeline_marker:1902",
            "status": status, "resolved_at": resolved_at,
            "timeline": timeline, "name": "feedback", "note": text,
            "text": f"feedback\n\n{text}", "check": "a_roll_two_rows",
            "verifier": "marker_resolution.CHECKS[a_roll_two_rows]",
            "marker_removed": True}


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

    # Resolved AFTER its last pull: not a re-ask.
    document = fl.build(
        None, [pull(REEL, "2026-09-11T04:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_RESOLVED_VERIFIED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is False
    assert document["reasked"] == []

    # Only a VERIFIED resolution can be contradicted: a decline never
    # claimed the thing was fixed.
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z", note(ASK)),
         pull(REEL, "2026-09-11T18:00:00Z", note(ASK))],
        resolutions=[resolution(mr.STATUS_DECLINED,
                                "2026-09-11T10:00:00Z")])
    assert document["entries"][0]["reasked"] is False


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
    # ...and links back to the question it answers (a blue marker
    # became a green reply and the question was gone).
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

    # Only a FIRST line starting with `reply:` is the writer's stamp; a
    # captain note quoting the word deeper in is still theirs.
    document = fl.build(
        None,
        [pull(REEL, "2026-09-11T04:00:00Z",
              note("please reply to this note", frame=5))],
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


def test_render_shows_the_words_not_just_the_marker_name():
    """The first line of a marker's text is its NAME field.

    Printing it alone showed the word "feedback" for every note on
    `lucie/geo-podcast` and nothing the captain typed.
    """
    document = fl.build(None, [pull(REEL, "2026-09-11T04:00:00Z",
                                    note(ASK))], resolutions=[])
    printed = fl.render(document)
    assert "akshita finishes talking" in printed


# ── The identity grammar the `answers` single writer enforces ────────


def test_is_identity_rejects_prose_frames_and_fragments():
    """Every shape found on a live reel that is not an identity."""
    for bad in ("R04 blue feedback",
                "Reel 14 - why-ai-trusts-youtube@162",
                "Reel 29 - salvage#clip_marker@22",
                "", None, 0,
                "Reel_14", "Reel_14:xyz",
                "Reel_14:9f2c4a1b7e5d03a",
                "Reel_14:9f2c4a1b7e5d03aag"):
        assert fl.is_identity(bad) is False
