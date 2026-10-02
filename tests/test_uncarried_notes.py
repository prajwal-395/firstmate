"""A dropped captain's note is an obligation, not a log line.

History: docs/evidence/resolve_test_history.md#test_uncarried_notes.
"""
import pytest

from library.tools import reel_signoff as signoff
from library.tools import uncarried_notes as owed
from library.tools import feedback_ledger as ledger

REEL = "Reel 03 - the-blue-that-would-not-carry"
OTHER = "Reel 08 - the-take-he-asked-away"
WORDS = "the talking-head holds too long, tighten by a breath"


@pytest.fixture
def project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    return root


def _ask(frame=162, name="feedback", note=WORDS, color="Blue"):
    return {
        "frame": frame,
        "color": color,
        "name": name,
        "note": note,
        "duration": 1,
        "custom_data": "",
        "anchor": ("/footage/akshita-a.mp4", 4410),
        "why": "its anchor picture is in the replacement nowhere",
    }


def _markers(final=REEL, uncarried=(), replaced=(), declined=()):
    return {final: {
        "carried": [],
        "uncarried": list(uncarried),
        "replaced": list(replaced),
        "replace_declined": list(declined),
    }}


# ── The durable record ────────────────────────────────────────────

def test_dropped_note_leaves_a_record_naming_reel_and_quoting_words(project):
    """Content, not count: the reel by base name and his words verbatim."""
    ask = _ask()
    report = owed.record(str(project), _markers(uncarried=[ask]))
    assert len(report["filed"]) == 1

    document = owed.read_notes(str(project))
    entry = document["open"][report["filed"][0]]
    assert entry["reel"] == REEL
    assert WORDS in entry["text"]
    assert entry["name"] == "feedback"
    assert entry["note"] == WORDS
    assert entry["frame"] == 162
    assert entry["why"] == ask["why"]
    assert entry["discharged"] is None
    # The identity is the durable one - reel plus words, nothing a
    # rebuild moves - so it rejoins the ledger's own reading.
    assert entry["identity"] == ledger.durable_identity(REEL, entry["text"])


def test_the_record_carries_the_seam_outcome(tmp_path):
    """Where the Blue went back rides along, so the discharger can find
    it; a Blue the seam could not place leaves the record as the words."""
    for name, extra, check in (
        ("replaced", {"seam": 88, "ambiguous": False,
                      "explanation": "the cut removed it; the join is 88",
                      "reply_frame": 89},
         lambda seam: seam["replaced_at"] == 88
         and "the join is 88" in seam["explanation"]),
        ("declined", {"seam": None, "why": "the replacement plays no picture"},
         lambda seam: seam["declined"] is True and "no picture" in seam["why"]),
    ):
        root = tmp_path / name
        (root / "pipeline_output" / "review").mkdir(parents=True)
        ask = _ask(frame=40)
        owed.record(str(root), _markers(uncarried=[ask],
                                        **{name: [{**ask, **extra}]}))
        assert check(owed.open_for(str(root), REEL)[0]["seam"]), name


# ── The obligation ────────────────────────────────────────────────

def test_open_obligation_blocks_signoff_until_discharged(project):
    """The reel is not done while the captain's words are unaccounted for."""
    owed.record(str(project), _markers(uncarried=[_ask()]))
    with pytest.raises(signoff.UncarriedNotesOpen) as refused:
        signoff.sign_off(str(project), REEL, note="ships")
    # The refusal quotes his words and names the reel - the content a
    # later grep, human or machine, acts on.
    assert REEL in str(refused.value)
    assert WORDS in str(refused.value)
    assert "discharge-uncarried" in str(refused.value)
    assert signoff.signed_off(str(project)) == {}

    identity = owed.open_for(str(project), REEL)[0]["identity"]
    owed.discharge(str(project), REEL, identity, by="captain",
                   note="answered at the re-placed Blue at frame 88")
    entry = signoff.sign_off(str(project), REEL, note="ships")
    assert entry["reel"] == REEL


def test_discharge_is_recorded_never_erased(project):
    owed.record(str(project), _markers(uncarried=[_ask()]))
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    owed.discharge(str(project), REEL, identity,
                   note="pinned to the removed take, correctly dropped")
    assert owed.open_for(str(project), REEL) == []
    document = owed.read_notes(str(project))
    kept = [e for e in document["discharged"]
            if e["identity"] == identity]
    assert kept[0]["discharged"]["note"] == \
        "pinned to the removed take, correctly dropped"


def test_a_discharge_needs_a_reason_and_a_real_identity(project):
    """A discharge with no stated reason is the bypass wearing the
    uniform; discharging what is not owed names what was asked."""
    owed.record(str(project), _markers(uncarried=[_ask()]))
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    with pytest.raises(owed.DischargeRefused):
        owed.discharge(str(project), REEL, identity, note="  ")
    assert len(owed.open_for(str(project), REEL)) == 1
    with pytest.raises(owed.UncarriedNoteUnknown) as unknown:
        owed.discharge(str(project), REEL, "Reel_03:deadbeefdeadbeef",
                       note="mistyped identity")
    assert "deadbeef" in str(unknown.value)


def test_a_note_dropped_again_counts_once_and_reopens_after_discharge(
        project):
    """Re-reporting without a discharge never duplicates; a discharge
    answered that instance, so a new drop after it is a new fact."""
    ask = _ask()
    first = owed.record(str(project), _markers(uncarried=[ask]))
    second = owed.record(str(project), _markers(uncarried=[ask]))
    assert len(first["filed"]) == 1
    assert second["filed"] == []
    assert len(owed.open_for(str(project), REEL)) == 1
    assert owed.open_for(str(project), REEL)[0]["occurrences"] == 2
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    owed.discharge(str(project), REEL, identity, note="answered")
    report = owed.record(str(project), _markers(uncarried=[ask]))
    assert report["reopened"] == [identity]
    assert report["filed"] == []
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["occurrences"] == 3
    assert entry["discharged"] is None
    assert entry["history"][0]["prior_discharge"]["note"] == "answered"
    with pytest.raises(signoff.UncarriedNotesOpen):
        signoff.sign_off(str(project), REEL)


# ── The clean path ────────────────────────────────────────────────

def test_carrying_everything_files_nothing_and_signs_off(project):
    """A promotion that drops nothing owes nothing - and writes nothing."""
    carried = [{**_ask(), "to_frame": 160, "pairing": "note"}]
    report = owed.record(
        str(project), {REEL: {"carried": carried, "uncarried": []}})
    assert report["filed"] == [] and report["reopened"] == []
    import os
    assert not os.path.exists(owed.notes_path_for(str(project)))
    assert signoff.sign_off(str(project), REEL)["reel"] == REEL


def test_an_unreadable_obligation_file_refuses(project):
    """An unreadable obligation reads exactly like no obligation, so it
    must refuse rather than let the sign-off through."""
    (project / "pipeline_output" / "review"
     / owed.NOTES_FILENAME).write_text("{broken", encoding="utf-8")
    with pytest.raises(owed.UncarriedNotesUnreadable):
        owed.read_notes(str(project))
    with pytest.raises(owed.UncarriedNotesUnreadable):
        signoff.sign_off(str(project), REEL)


# ── Whose words ───────────────────────────────────────────────────

def test_our_replies_are_never_filed_as_his_words(project):
    """Stranded and independent replies stay reported, never re-filed as
    Blue notes of his - the same line the seam re-placement holds to."""
    ask = _ask(frame=162)
    stranded = {**_ask(frame=200, name="re: feedback",
                       note="your note did not carry"),
                "pairing": "stranded", "reply_of": 162,
                "why": "its note is itself NOT CARRIED"}
    independent = {**_ask(frame=210, name="re: feedback",
                          note="an old answer binding to nothing"),
                   "pairing": "independent-unpaired",
                   "pairing_flags": ["unpaired"],
                   "to_frame": 208,
                   "why": "carried by its own picture"}
    report = owed.record(
        str(project),
        _markers(uncarried=[ask, stranded, independent]))
    assert len(report["filed"]) == 1
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["frame"] == 162
    assert WORDS in entry["text"]


def test_obligations_are_per_reel(project):
    """One reel owing a note never holds back a sibling's sign-off."""
    owed.record(str(project), _markers(uncarried=[_ask()]))
    assert signoff.sign_off(str(project), OTHER)["reel"] == OTHER
    with pytest.raises(signoff.UncarriedNotesOpen):
        signoff.sign_off(str(project), REEL)


# ── The step consumes the third key ───────────────────────────────

def _verify_step_module():
    from library.tools.operations import load_step_module

    return load_step_module("step_7_02_verify_reels", "step.py")


def test_step_helper_files_promoted_markers_and_continues(project):
    """`step_7_02` reads the key it used to drop - and never fails on it."""
    module = _verify_step_module()
    report = module.record_uncarried_notes(
        str(project), _markers(uncarried=[_ask()]))
    assert len(report["filed"]) == 1
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["reel"] == REEL
    assert WORDS in entry["text"]


def test_partial_promotion_carries_its_marker_losses(project):
    """A batch that raises after its passing reels landed still hands
    their drops to the step - on the exception, where no return record
    exists to carry them. The message is unchanged."""
    from library.tools.reel_build import (
        ReelBuildError, _raise_partial_promotion)

    markers = _markers(uncarried=[_ask()])
    with pytest.raises(ReelBuildError) as partial:
        _raise_partial_promotion(["Reel 03"], ["Reel 03"],
                                 {"Reel 09": "REFUSING: rows differ"},
                                 markers=markers)
    assert "REFUSING to promote 1 reel(s)" in str(partial.value)
    assert partial.value.markers[REEL]["uncarried"][0]["note"] == WORDS
