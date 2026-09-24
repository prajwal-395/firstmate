"""The version object: one version per BATCH of the captain's feedback.

The captain's answer of 2026-09-12 to the versioning question
(`data/vep-can-it-hold-up-in-a-real-editing-workflow` §7) is *per
round*, and a round is DISCOVERED from what already exists rather than
declared: the interleaving of the asks in the feedback ledger with the
promotions that answered them.

Each test here fails if its mechanism is removed:

- the boundary rule itself - remove it (one round for everything, or a
  round per ask) and the batching assertions fail;
- replies of ours never opening a round;
- the stamp carrying the rows, which is what makes `round_diff` free;
- backfill marking every entry RECONSTRUCTED and carrying no
  `built_with` - a stamp invented after the fact is not a measurement;
- a stamped entry outranking a reconstructed one.
"""
import json
import subprocess

import pytest

from library.tools import round_version as rv


def _ask(identity, reel, when):
    return {"identity": identity, "reel": reel, "first_asked": when}


# ── The boundary rule ────────────────────────────────────────────

def test_a_batch_typed_before_anything_was_rebuilt_is_one_round():
    """Six markers at once, one pass, one result to look at.

    Nothing was promoted between them, so they are one batch - which is
    the captain's own definition of a round and is what a per-ask
    version would get wrong.
    """
    rounds = rv.round_boundaries(
        [_ask(f"n{n}", f"Reel 0{n}", f"2026-09-11T03:08:4{n}Z")
         for n in range(1, 6)],
        promotions=["2026-09-10T22:00:00Z"])
    assert [entry["round"] for entry in rounds] == [1, 2]
    assert len(rounds[1]["opened_by"]) == 5
    assert rounds[1]["opened_at"] == "2026-09-11T03:08:41Z"


def test_a_new_ask_after_a_build_opens_the_next_round():
    """The other half: an ask that arrives once a build has answered the
    previous batch opens a round of its own. Remove the promotion from
    between them and the two asks collapse into one round, which is
    what makes this the real boundary rather than a clock."""
    asks = [_ask("a", "Reel 01", "2026-09-11T03:08:43Z"),
            _ask("b", "Reel 09", "2026-09-11T22:51:54Z")]
    with_build = rv.round_boundaries(
        asks, promotions=["2026-09-10T20:00:00Z",
                          "2026-09-11T17:05:52Z"])
    assert [entry["round"] for entry in with_build] == [1, 2, 3]
    assert [len(entry["opened_by"]) for entry in with_build] == [0, 1, 1]

    without_build = rv.round_boundaries(
        asks, promotions=["2026-09-10T20:00:00Z"])
    assert [entry["round"] for entry in without_build] == [1, 2]
    assert len(without_build[1]["opened_by"]) == 2




def test_a_reply_of_ours_opens_no_round(tmp_path, monkeypatch):
    """`asks_of` reads ASKS only. A green reply of ours typed onto the
    timeline between two rounds is not the captain asking for anything,
    and a version stamped on one would be a version of our own
    answer."""
    from library.tools import feedback_ledger

    class Entry:
        def __init__(self, identity, kind):
            self.identity = identity
            self.reel = "Reel 09"
            self.first_asked = "2026-09-11T04:47:30Z"
            self.kind = kind

    monkeypatch.setattr(
        feedback_ledger, "collect",
        lambda folder: {"a": Entry("a", feedback_ledger.KIND_ASK),
                        "b": Entry("b", feedback_ledger.KIND_REPLY)})
    asks = rv.asks_of(str(tmp_path))
    assert [ask["identity"] for ask in asks] == ["a"]


# ── Stamping ─────────────────────────────────────────────────────

def _rows(count, frames):
    return {"video:Akshita": {
        "media_type": "video", "index": 1, "name": "Akshita",
        "items": [{"name": f"clip {n}", "start": n * 10,
                   "end": n * 10 + 10, "duration": 10}
                  for n in range(count)],
        "count": count, "frames": frames}}


def test_a_promotion_stamps_the_round_with_the_rows_it_promoted(tmp_path):
    """The rows are the payload that makes a round diff free: stored
    here, `round_diff` answers off disk with no Resolve and long after
    the timeline itself has been retired and collected."""
    project = tmp_path / "project"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    stamped = rv.stamp_promotion(
        str(project), {"Reel 09 - x": _rows(3, 30)},
        provenance={"built_at_reels": {"Reel 09 - x": "2026-09-12T01:00Z"},
                    "built_with": {"Reel 09 - x": "abc123"}})
    assert stamped["round"] == 1
    entry = stamped["reels"]["Reel 09 - x"]
    assert entry["source"] == rv.SOURCE_STAMPED
    assert entry["built_with"] == "abc123"
    assert entry["rows"]["video:Akshita"]["count"] == 3

    document = rv.read_rounds(str(project))
    assert document["rounds"][-1]["reels"]["Reel 09 - x"]["rows"] \
        ["video:Akshita"]["frames"] == 30


def test_an_unreadable_rounds_file_refuses_rather_than_starting_empty(
        tmp_path):
    """The record of what was built when is the one thing that cannot be
    recomputed. Replacing an unreadable one with an empty document
    would lose every round silently."""
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    (review / rv.ROUNDS_FILENAME).write_text("{not json",
                                             encoding="utf-8")
    with pytest.raises(rv.RoundsUnreadable):
        rv.read_rounds(str(project))




# ── Backfill ─────────────────────────────────────────────────────

def _git(folder, *args):
    return subprocess.run(["git", "-C", str(folder), *args],
                          capture_output=True, encoding="utf-8",
                          check=True)


def _snapshot(count):
    return {"metadata": {"name": "Reel 09 - x"},
            "tracks": [{"type": "video", "index": 1, "name": "Akshita",
                        "clips": [{"name": f"clip {n}",
                                   "record_in": n * 10,
                                   "record_out": n * 10 + 10,
                                   "duration": 10}
                                  for n in range(count)]}]}


@pytest.fixture
def repo_project(tmp_path):
    """A project with two committed timeline snapshots - the shape
    `build_version_control.record_reel_promotion` writes on every
    promotion."""
    project = tmp_path / "project"
    review = project / "pipeline_output" / "review"
    review.mkdir(parents=True)
    _git(project.parent, "init", "-q", str(project))
    _git(project, "config", "user.email", "t@example.com")
    _git(project, "config", "user.name", "t")
    path = review / "Reel_09_-_x.timeline.json"
    for count in (2, 3):
        path.write_text(json.dumps(_snapshot(count)), encoding="utf-8")
        _git(project, "add", "-A")
        _git(project, "commit", "-q", "-m", f"reels build: {count}")
    return project


def test_backfill_reconstructs_from_the_committed_snapshots(repo_project):
    """The rounds that predate the stamp are recoverable, because every
    promotion since 2026-09-11 committed the timeline it promoted."""
    report = rv.backfill(str(repo_project))
    assert report["commits"] == 2
    assert report["reels"] >= 1
    rounds = rv.read_rounds(str(repo_project))["rounds"]
    promoted = [entry for entry in rounds if entry["reels"]]
    assert promoted, "backfill recorded no reel"
    latest = promoted[-1]["reels"]["Reel 09 - x"]
    assert latest["rows"]["video:Akshita"]["count"] == 3


def test_a_reconstructed_entry_never_claims_a_built_with(repo_project):
    """Which engine revision built a reel cannot be recovered once the
    build is over (AGENTS.md 10.1: merge time is not build time), so a
    reconstruction says RECONSTRUCTED and leaves the stamp empty rather
    than inventing one."""
    rv.backfill(str(repo_project))
    entries = [reel for entry in rv.read_rounds(str(repo_project))["rounds"]
               for reel in (entry["reels"] or {}).values()]
    assert entries
    for reel in entries:
        assert reel["source"] == rv.SOURCE_RECONSTRUCTED
        assert reel["built_with"] == ""
        assert reel["commit"]


def test_a_measurement_outranks_a_reconstruction(repo_project):
    """A stamp taken at promotion time is never overwritten by a later
    backfill: the reconstruction knows strictly less."""
    rv.backfill(str(repo_project))
    document = rv.read_rounds(str(repo_project))
    target = [entry for entry in document["rounds"] if entry["reels"]][-1]
    target["reels"]["Reel 09 - x"]["source"] = rv.SOURCE_STAMPED
    target["reels"]["Reel 09 - x"]["built_with"] = "deadbeef"
    rv.write_rounds(str(repo_project), document)

    rv.backfill(str(repo_project))
    kept = [entry for entry in rv.read_rounds(str(repo_project))["rounds"]
            if entry["reels"]][-1]["reels"]["Reel 09 - x"]
    assert kept["source"] == rv.SOURCE_STAMPED
    assert kept["built_with"] == "deadbeef"


