"""Attaching a creative brief is a choice, and every reading of it is stated.

The captain, 2026-09-02: *"this should be like an optional attachment we
can add as context if we want, not something that automatically goes in"*
and *"it should be optionally configurable by the user whether we are
even adding a creative brief or not"*.

The two failure modes this sits between, and both are silences:

* **Attaching automatically.** A declared path went into eight prompts
  on every run, with no way to say "not this time".
* **Reading an absent key as a decline.** Every project that already
  declares a brief would lose it on its next run, silently - the same
  defect arriving from the other direction.

So the key is a THREE-state declaration and the absent state is "the
PATH is the declaration", said out loud in the run header.
"""
import os
import sys

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from library.tools import brief_attachment as ba


def read(**yaml_body):
    return ba.read_declaration_from(yaml_body, where="project.yaml")


# ── The three readings ───────────────────────────────────────────────

def test_a_declared_path_and_no_key_is_read_as_attaching_it():
    """The compatibility case, and it is a READING, not a default.

    Project 001 declares `creative_brief` at the top level and no
    attach key. Reading that as a decline would take the captain's own
    brief off the run that most depends on it.
    """
    got = read(creative_brief="brief.md")
    assert got.reading == ba.ATTACHED
    assert got.attached
    assert not got.interview
    assert "read as the choice to attach" in got.basis


def test_declining_is_one_line_and_wins_over_a_declared_path():
    got = read(creative_brief="brief.md", attach_creative_brief=False)
    assert got.reading == ba.DECLINED
    assert not got.attached
    assert got.interview
    assert got.path == "brief.md", (
        "the path is still recorded - the project has a brief and chose "
        "not to send it, which is a different fact from having none")


# ── The one refusal ──────────────────────────────────────────────────

def test_an_unsatisfiable_or_unreadable_declaration_is_refused():
    """Attaching a brief that does not exist is refused by name; an
    unreadable value is refused rather than guessed - guessing is the
    difference between the brief reaching every planning step and none."""
    with pytest.raises(ba.BriefAttachmentError) as excinfo:
        read(attach_creative_brief=True)
    assert "creative_brief" in str(excinfo.value)
    with pytest.raises(ba.BriefAttachmentError):
        read(attach_creative_brief="maybe")


# ── It says so, whichever way it read ────────────────────────────────

def test_every_reading_prints_a_line_naming_itself():
    """The shape `describe_brand_absence` established: an absence stated
    once per run is a decision a reader can see."""
    for attachment in (read(creative_brief="b.md"),
                       read(creative_brief="b.md", attach_creative_brief=False),
                       read()):
        line = ba.describe(attachment)
        assert line
        assert "Creative brief:" in line
    assert "ATTACHED" in ba.describe(read(creative_brief="b.md"))
    assert "DECLINED" in ba.describe(
        read(creative_brief="b.md", attach_creative_brief=False))
    assert "NONE DECLARED" in ba.describe(read())
