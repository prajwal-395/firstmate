"""Different reels never contend; the same decision twice SURFACES.

The two properties the scheme is worth building for, and the second one
demonstrated across real processes rather than argued.
"""

import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from library.tools import declaration_keys as dk
from library.tools.declaration_keys import (
    DeclarationConflict,
    DeclarationKeyError,
    edit_declaration,
    entry_key,
    merge,
    read_entries,
    write_entries,
)

REPO_ROOT = str(Path(__file__).resolve().parent.parent)


@pytest.fixture
def project(tmp_path):
    """A project of our own. Never a real one (AGENTS.md 8)."""
    (tmp_path / "external").mkdir()
    (tmp_path / "project.yaml").write_text("name: contention\n",
                                           encoding="utf-8")
    return tmp_path


def ending(reel, phrase):
    return {"reel": reel, "ends_on": {"anchor_phrase": phrase},
            "tail_element": "none", "reason": f"test: {reel}"}


# ── The key scheme names what each owner already reasons in ─────────

def test_every_store_key_is_derivable_or_already_keyed():
    for stem, store in dk.stores().items():
        assert store.container, stem
        if store.is_mapping:
            continue
        assert callable(store.key_of), stem


def test_a_reel_ending_is_keyed_by_its_reel():
    assert entry_key("reel_ending", ending("Reel 03", "x")) == "Reel 03"


def test_a_captain_edit_is_keyed_by_the_owners_own_identity():
    """Imported, not restated: two answers to one question would drift."""
    from library.tools.captain_edits import _edit_identity
    edit = {"kind": "redraw_closer", "anchor_phrase": "And So We Built",
            "from_phrase": "we built", "reason": "t"}
    assert json.loads(entry_key("captain_edits", edit)) == \
        list(_edit_identity(edit))
    # Case and spacing are the owner's normalisation, so a re-ruling
    # typed differently is still the SAME decision.
    louder = dict(edit, anchor_phrase="  and so  we built ")
    assert entry_key("captain_edits", louder) == entry_key(
        "captain_edits", edit)


def test_a_file_with_no_key_scheme_is_refused_rather_than_overwritten():
    with pytest.raises(DeclarationKeyError, match="not a keyed store"):
        entry_key("pipeline_data", {})


# ── Property one: different reels do not contend ────────────────────

def test_two_writers_on_different_reels_both_land(project):
    """Neither read the other's entry, and neither lost it."""
    base_a, _ = read_entries(project, "reel_ending")
    base_b, _ = read_entries(project, "reel_ending")
    assert base_a == base_b == {}

    write_entries(project, "reel_ending",
                  {"Reel 03": ending("Reel 03", "three")}, base_a)
    # B read BEFORE A wrote, and still does not clobber A.
    write_entries(project, "reel_ending",
                  {"Reel 07": ending("Reel 07", "seven")}, base_b)

    landed, _ = read_entries(project, "reel_ending")
    assert sorted(landed) == ["Reel 03", "Reel 07"]


def test_the_file_the_owners_reader_reads_is_what_lands(project):
    """A store created from nothing must satisfy its OWNER, not us."""
    from library.tools.reel_ending import load_endings
    with edit_declaration(project, "reel_ending") as endings:
        endings["Reel 03"] = ending("Reel 03", "so that is the play")
    assert [e["reel"] for e in load_endings(str(project))] == ["Reel 03"]


def test_a_write_leaves_no_half_file(project):
    """Temp plus replace. A reader never sees a partial store."""
    with edit_declaration(project, "reel_ending") as endings:
        endings["Reel 03"] = ending("Reel 03", "three")
    path = dk.store_path(project, "reel_ending")
    assert json.loads(path.read_text(encoding="utf-8"))["endings"]
    assert not [p for p in path.parent.iterdir() if p.name.endswith(".tmp")]


# ── Property two: a genuine conflict SURFACES ───────────────────────

def test_the_same_reel_changed_twice_raises_rather_than_losing_one(project):
    base, _ = read_entries(project, "reel_ending")
    write_entries(project, "reel_ending",
                  {"Reel 03": ending("Reel 03", "the other writer's words")},
                  base)

    with pytest.raises(DeclarationConflict) as raised:
        write_entries(project, "reel_ending",
                      {"Reel 03": ending("Reel 03", "my words")}, base)
    message = str(raised.value)
    assert "Reel 03" in message
    assert "the other writer's words" in message and "my words" in message
    assert "Nothing was written" in message

    # And nothing was: the other writer's decision is intact.
    landed, _ = read_entries(project, "reel_ending")
    assert landed["Reel 03"]["ends_on"]["anchor_phrase"] == \
        "the other writer's words"


def test_the_same_value_written_twice_is_not_a_conflict(project):
    """Agreement is not contention."""
    base, _ = read_entries(project, "reel_ending")
    same = {"Reel 03": ending("Reel 03", "three")}
    write_entries(project, "reel_ending", same, base)
    write_entries(project, "reel_ending", same, base)
    landed, _ = read_entries(project, "reel_ending")
    assert list(landed) == ["Reel 03"]


def test_a_deletion_is_a_change_like_any_other(project):
    with edit_declaration(project, "reel_ending") as endings:
        endings["Reel 03"] = ending("Reel 03", "three")
        endings["Reel 07"] = ending("Reel 07", "seven")
    with edit_declaration(project, "reel_ending") as endings:
        del endings["Reel 03"]
    landed, _ = read_entries(project, "reel_ending")
    assert list(landed) == ["Reel 07"]


def test_merge_keeps_what_neither_writer_touched():
    base = {"a": 1, "b": 2}
    assert merge("reel_ending", base, {"a": 1, "b": 2, "c": 3},
                 {"a": 1, "b": 9}) == {"a": 1, "b": 9, "c": 3}


# ── The captain's own store, wired ──────────────────────────────────

def test_record_edit_goes_through_the_key_scheme(project, monkeypatch):
    """The measured lost-update site, now merging per key."""
    import library.tools.captain_edits as ce
    monkeypatch.setattr(ce, "load_transcript", lambda _f: {})
    monkeypatch.setattr(ce, "check_anchor_spoken", lambda *_a: None)

    first = {"kind": "redraw_closer", "anchor_phrase": "one",
             "from_phrase": "a", "reason": "r"}
    second = {"kind": "redraw_closer", "anchor_phrase": "two",
              "from_phrase": "b", "reason": "r"}
    ce.record_edit(project, first, source="lane A")
    ce.record_edit(project, second, source="lane B")

    stored = json.loads(
        (project / "external" / "captain_edits.json").read_text("utf-8"))
    assert sorted(e["anchor_phrase"] for e in stored["value"]) == ["one", "two"]
    assert stored["key"] == ce.CAPTAIN_EDITS_KEY


def test_record_edit_still_supersedes_the_same_decision(project, monkeypatch):
    import library.tools.captain_edits as ce
    monkeypatch.setattr(ce, "load_transcript", lambda _f: {})
    monkeypatch.setattr(ce, "check_anchor_spoken", lambda *_a: None)

    edit = {"kind": "redraw_closer", "anchor_phrase": "one",
            "from_phrase": "a", "reason": "first ruling"}
    ce.record_edit(project, edit)
    _, action = ce.record_edit(project, dict(edit, reason="second ruling"))
    assert action == "superseded"
    stored = json.loads(
        (project / "external" / "captain_edits.json").read_text("utf-8"))
    assert [e["reason"] for e in stored["value"]] == ["second ruling"]


# ── Real contention, across processes ───────────────────────────────

_WRITER = textwrap.dedent("""
    import sys, json
    sys.path.insert(0, {repo!r})
    from library.tools.declaration_keys import (
        read_entries, write_entries, DeclarationConflict)
    project, reel, words = {project!r}, {reel!r}, {words!r}
    base, _ = read_entries(project, "reel_ending")
    entry = {{"reel": reel, "ends_on": {{"anchor_phrase": words}},
              "tail_element": "none", "reason": "subprocess"}}
    mine = dict(base); mine[reel] = entry
    try:
        write_entries(project, "reel_ending", mine, base)
        print("WROTE", flush=True)
    except DeclarationConflict as clash:
        print("CONFLICT", str(clash).replace(chr(10), " | "), flush=True)
""")


def _write_in_subprocess(project, reel, words):
    return subprocess.run(
        [sys.executable, "-c", _WRITER.format(
            repo=REPO_ROOT, project=str(project), reel=reel, words=words)],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        env=dict(os.environ), check=False)


def test_two_processes_on_different_reels_both_land(project):
    assert _write_in_subprocess(project, "Reel 03", "three").stdout.startswith(
        "WROTE")
    assert _write_in_subprocess(project, "Reel 07", "seven").stdout.startswith(
        "WROTE")
    landed, _ = read_entries(project, "reel_ending")
    assert sorted(landed) == ["Reel 03", "Reel 07"]


def test_a_process_writing_over_a_hand_edit_surfaces_the_conflict(project):
    """The uncooperative writer for files: a text editor, taking no lock."""
    assert _write_in_subprocess(
        project, "Reel 03", "what the agent decided").stdout.startswith("WROTE")

    # The captain edits the same reel by hand, in an editor. No lock.
    path = dk.store_path(project, "reel_ending")
    document = json.loads(path.read_text(encoding="utf-8"))
    document["endings"][0]["ends_on"]["anchor_phrase"] = "what I decided"
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")

    # A second agent that read the file BEFORE the hand edit.
    base = {}
    with pytest.raises(DeclarationConflict) as raised:
        write_entries(project, "reel_ending",
                      {"Reel 03": ending("Reel 03", "a third opinion")}, base)
    assert "what I decided" in str(raised.value)
