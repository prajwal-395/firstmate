"""The reel a write edits is the one Ren itself last promoted.

THE DEFECT (measured 2026-10-21): two touch-ups promoted two new
generations under one reel's name, and the revise path could not name
the reel at all - "3 timelines in project 'Ren Scratch - vep-bench-demo2'
are named 'Reel 13 - ...'; name one by its id" - for a project that
holds ONE timeline with that name.

The cause: the SHADOW'S HISTORY was read as the project's inventory. A
replaced timeline keeps its head (recoverable history - VC-002
archives, VC-008 dependencies), so the shadow holds several heads under
one name, and the engine read every one of them as a live timeline.

The fix: the JOURNAL is the record of what Ren made current. A
promotion names the candidate that became the reel's timeline, so the
reel's current id is the last promotion's candidate id. With no journal
entry, the shadow answers - and it REFUSES when two of its candidates
share the name, because that is two LIVE timelines and the engine will
not pick one (AGENTS.md 5: exact names, never a substitute).

These are the defects this could reintroduce:

1. a touched reel resolves to the generation Ren last promoted;
2. with no journal entry, the shadow's refusal stands - and it NAMES
   the two candidates.
"""

from __future__ import annotations

import json
import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

REEL = "Reel 01 - topic"  # made unique per test below
PROJECT = "P-reel-current"  # made unique per test below


def _journal_entry(folder: Path, entry_id: str, reel: str, promoted: str) -> None:
    """One promotion in the undo journal, as Ren wrote it."""
    from library.tools import undo_journal

    root = folder / "pipeline_output/review/undo" / entry_id
    root.mkdir(parents=True, exist_ok=True)
    (root / undo_journal.ENTRY_FILENAME).write_text(json.dumps({
        "format": undo_journal.JOURNAL_FORMAT,
        "id": entry_id,
        "final": reel,
        "receipt": {"candidate_lifecycle": {"candidate": {"id": promoted},
                                            "source_id": "tl-old"}},
    }), encoding="utf-8")


def _record(project: str, timeline_id: str, name: str) -> None:
    """One recorded generation of a timeline, under its own id."""
    from library.tools import timeline_shadow

    timeline_shadow.ShadowStore().record(
        project=project, timeline_id=timeline_id, timeline_name=name,
        snapshot={"rows": [], "fps": 23.976, "start_frame": 0,
                  "markers": {"timeline": []}},
        source=timeline_shadow.OBSERVED, expected_head=0)


def test_a_touched_reel_resolves_to_the_generation_ren_last_promoted(tmp_path):
    """The reel is the one Ren promoted, not the newest head in the shadow.

    The defect: three recorded heads under one name, and the engine
    asked which one the note was about - for a project that holds one.
    """
    from library.tools import edit_spec

    project = f"{PROJECT}-{uuid.uuid4().hex[:8]}"
    reel = f"{REEL}-{uuid.uuid4().hex[:8]}"
    _record(project, "tl-old", reel)
    _record(project, "tl-first", reel)
    _record(project, "tl-second", reel)
    _journal_entry(tmp_path, "20261021T120000Z-reel-1", reel, "tl-first")
    _journal_entry(tmp_path, "20261021T130000Z-reel-1", reel, "tl-second")

    assert edit_spec._current_reel_timeline_id(str(tmp_path), project,
                                               reel) == "tl-second"


def test_without_a_journal_entry_the_shadow_refuses_two_candidates(tmp_path):
    """No journal entry: the shadow answers, and refuses what it cannot.

    The safety this keeps: two recorded timelines sharing a name is two
    LIVE timelines, and the engine will not pick one (AGENTS.md 5).
    """
    from library.tools import edit_spec, timeline_shadow

    project = f"{PROJECT}-{uuid.uuid4().hex[:8]}"
    reel = f"{REEL}-{uuid.uuid4().hex[:8]}"
    _record(project, "tl-a", reel)
    _record(project, "tl-b", reel)

    with pytest.raises(timeline_shadow.ShadowError) as refused:
        edit_spec._current_reel_timeline_id(str(tmp_path), project, reel)

    message = str(refused.value)
    assert "2 timelines" in message
    assert "tl-a" in message and "tl-b" in message


def test_without_a_journal_entry_one_candidate_answers(tmp_path):
    """One recorded timeline under the name: it answers, no journal needed."""
    from library.tools import edit_spec

    project = f"{PROJECT}-{uuid.uuid4().hex[:8]}"
    reel = f"{REEL}-{uuid.uuid4().hex[:8]}"
    _record(project, "tl-only", reel)

    assert edit_spec._current_reel_timeline_id(str(tmp_path), project,
                                               reel) == "tl-only"
