"""A dropped captain's note is an obligation, not a log line.

The defect this pins
--------------------
The promotion path reported a note it was about to lose, twice, and
both copies were discarded before anything could act:
`marker_carry.report` named it on stderr, and `step_7_02` read two
sibling keys off `promoted` while dropping `promoted["markers"]` - the
same losses as structured data. Measured 2026-09-20: a reel's blue was
correctly identified as unresolvable and named with the captain's
words, no durable record survived, and the note was gone for roughly
forty minutes.

Each test fails if its mechanism is removed:

- the durable record - delete the `record` call from the step helper
  and a dropped note leaves nothing on disk;
- the CONTENT - the record names the reel and quotes the captain's
  words verbatim (asserted on content, never on a count);
- the obligation - remove the gate from `sign_off` and the reel signs
  off with its note still unaccounted for;
- the discharge - without it the reel stays unsigned; a reasonless
  discharge is refused rather than waving the obligation through;
- the clean path - a promotion that carries everything files nothing
  and signs off exactly as before;
- our own words are never filed as his - stranded and independent
  replies stay reported, never re-filed as Blue notes of his.
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


def test_record_carries_where_the_blue_went_back(project):
    """The seam outcome rides along, so the discharger can find it."""
    ask = _ask(frame=40)
    replaced = [{**ask, "seam": 88, "ambiguous": False,
                 "explanation": "the cut removed it; the join is 88",
                 "reply_frame": 89}]
    owed.record(str(project), _markers(uncarried=[ask], replaced=replaced))
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["seam"]["replaced_at"] == 88
    assert "the join is 88" in entry["seam"]["explanation"]


def test_declined_replacement_says_the_words_survive_only_here(project):
    """A Blue the seam could not place leaves the record as the words."""
    ask = _ask(frame=40)
    declined = [{**ask, "seam": None,
                 "why": "the replacement plays no picture"}]
    owed.record(str(project), _markers(uncarried=[ask], declined=declined))
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["seam"]["declined"] is True
    assert "no picture" in entry["seam"]["why"]


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


def test_a_reasonless_discharge_is_refused(project):
    """A discharge with no stated reason is the bypass wearing the uniform."""
    owed.record(str(project), _markers(uncarried=[_ask()]))
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    with pytest.raises(owed.DischargeRefused):
        owed.discharge(str(project), REEL, identity, note="  ")
    assert len(owed.open_for(str(project), REEL)) == 1


def test_discharging_what_is_not_owed_names_what_is(project):
    owed.record(str(project), _markers(uncarried=[_ask()]))
    with pytest.raises(owed.UncarriedNoteUnknown) as unknown:
        owed.discharge(str(project), REEL, "Reel_03:deadbeefdeadbeef",
                       note="mistyped identity")
    assert "deadbeef" in str(unknown.value)


def test_a_note_dropped_again_after_discharge_reopens(project):
    """A discharge answered that instance; a new drop is a new fact."""
    ask = _ask()
    owed.record(str(project), _markers(uncarried=[ask]))
    identity = owed.open_for(str(project), REEL)[0]["identity"]
    owed.discharge(str(project), REEL, identity, note="answered")
    report = owed.record(str(project), _markers(uncarried=[ask]))
    assert report["reopened"] == [identity]
    assert report["filed"] == []
    entry = owed.open_for(str(project), REEL)[0]
    assert entry["occurrences"] == 2
    assert entry["discharged"] is None
    assert entry["history"][0]["prior_discharge"]["note"] == "answered"
    with pytest.raises(signoff.UncarriedNotesOpen):
        signoff.sign_off(str(project), REEL)


def test_rereport_without_discharge_never_duplicates(project):
    ask = _ask()
    first = owed.record(str(project), _markers(uncarried=[ask]))
    second = owed.record(str(project), _markers(uncarried=[ask]))
    assert len(first["filed"]) == 1
    assert second["filed"] == []
    assert len(owed.open_for(str(project), REEL)) == 1
    assert owed.open_for(str(project), REEL)[0]["occurrences"] == 2


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
