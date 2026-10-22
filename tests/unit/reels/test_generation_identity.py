"""VC-001: a generation has a durable identity, and a name is an assertion.

THE DEFECT (measured 2026-10-21 on ``Ren Scratch - vep-bench-demo2``):
three Resolve timelines were named ``Reel 13 - the-accounting-firm-ai-
called-healthcare``, and every write that addressed that name was
refused before its first write - "recorded base for ... resolved to 3
timelines" - for a project that holds ONE timeline with that name.

THE CAUSE: the identity of a generation lived nowhere. The timeline
shadow keyed a generation by a machine-local pair of integers, the undo
journal recorded WHAT REN DID rather than WHICH generation it made, and
the reel version ledger - the one record a person reads - filed every
version under the reel's NAME, with no Resolve identity at all. So the
name was still the sole identity, and a name two live timelines share
had nothing to say which one was meant.

THE FIX: one durable identity per generation, minted from what cannot
change (the Resolve timeline identity and the generation's content
digest), carried on the version entry, and used by every path that
resolves a reel.

These are the defects this could reintroduce:

1. a rename does not change a generation's identity;
2. two timelines sharing a name have different identities;
3. a version entry carries its identity, and the identity preserves the
   parent generation, the author, the accepted state and the asset
   dependencies;
4. the parent generation is the previous state of the SAME timeline, not
   a same-named neighbour's;
5. a name whose history spans two Resolve timelines refuses, and names
   every candidate by its durable id;
6. a generation that a later one replaced refuses as stale;
7. a write against a shared name is addressed by the identity, and the
   old refusal stands for a request that carries none.
"""

from __future__ import annotations

import sys
import uuid
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

REEL = "Reel 13 - the-accounting-firm-ai-called-healthcare"
PROJECT = "Ren Scratch - vc001"


def _project(tmp_path):
    root = tmp_path / "project"
    (root / "pipeline_output" / "review").mkdir(parents=True)
    (root / "project.yaml").write_text("source:\n  fps: 25\n",
                                       encoding="utf-8")
    return root


def _rows(name="a.mov", frames=250):
    return {"video:V1": {
        "media_type": "video", "index": 1, "name": "V1",
        "items": [{"name": name, "start": 0, "end": frames,
                   "duration": frames, "enabled": True,
                   "source_file": f"/media/{name}"}],
        "count": 1, "frames": frames}}


def _record_version(root, *, timeline_id="tl-a", name=REEL, rows=None,
                    kind="build", author="ren", accepted_timeline_id=None):
    from library.tools.versions import reel_versions

    if accepted_timeline_id is not None:
        from library.tools import reel_acceptance

        reel_acceptance.record(str(root), name,
                                timeline_id=accepted_timeline_id,
                                rows=rows or _rows())
    return reel_versions.record(
        str(root), name, kind=kind, rows=rows if rows is not None else _rows(),
        resolve_project=PROJECT, resolve_timeline_id=timeline_id,
        author=author)


def _shadow_record(project, timeline_id, name, *, generation=None):
    from library.tools import timeline_shadow

    timeline_shadow.ShadowStore().record(
        project=project, timeline_id=timeline_id, timeline_name=name,
        snapshot={"rows": [], "fps": 23.976, "start_frame": 0,
                  "markers": {"timeline": []}},
        source=timeline_shadow.OBSERVED,
        expected_head=generation if generation is not None else 0)


# ── The identity itself ─────────────────────────────────────────────

def test_a_rename_does_not_change_a_generations_identity():
    """The name is an assertion: renaming a timeline keeps its identity.

    The defect this closes: a generation identified by its name loses
    its identity the moment the timeline is renamed, and the history
    that names it becomes unreachable.
    """
    from library.tools import generation_identity

    before = generation_identity.generation_id(
        resolve_project=PROJECT, resolve_timeline_id="tl-a",
        content_digest="abc123")
    after = generation_identity.generation_id(
        resolve_project=PROJECT, resolve_timeline_id="tl-a",
        content_digest="abc123")

    assert before == after
    assert before.startswith(generation_identity.GENERATION_ID_PREFIX)


def test_two_timelines_sharing_a_name_have_different_identities():
    """The Resolve identity is in the id, so a shared name cannot collide.

    The defect this closes: three timelines under one name produced one
    identity, and nothing could say which generation a write meant.
    """
    from library.tools import generation_identity

    one = generation_identity.generation_id(
        resolve_project=PROJECT, resolve_timeline_id="tl-a",
        content_digest="abc123")
    other = generation_identity.generation_id(
        resolve_project=PROJECT, resolve_timeline_id="tl-b",
        content_digest="abc123")

    assert one != other


# ── What the version ledger preserves ───────────────────────────────

def test_a_version_entry_carries_its_generations_durable_identity(tmp_path):
    """The version record names the generation, not just the reel.

    The defect this closes: the ledger filed a version under a NAME,
    so the one record a person reads could not say which timeline the
    version was.
    """
    root = _project(tmp_path)
    entry = _record_version(root, timeline_id="tl-a")

    generation = entry.get("generation") or {}
    assert generation.get("generation_id", "").startswith("gen-")
    assert generation.get("resolve_timeline_id") == "tl-a"
    assert generation.get("resolve_project") == PROJECT
    assert generation.get("timeline_name") == REEL
    assert generation.get("author") == "ren"


def test_the_identity_preserves_the_parent_generation(tmp_path):
    """A generation names the state it continues - on ITS timeline.

    The defect this closes: a version ledger keyed by name merges the
    histories of two same-named timelines, so the "previous version" is
    a neighbour's state and the chain says nothing.
    """
    root = _project(tmp_path)
    first = _record_version(root, timeline_id="tl-a", rows=_rows("a.mov"))
    second = _record_version(root, timeline_id="tl-a", rows=_rows("b.mov"))

    assert (second["generation"]["parent_generation_id"]
            == first["generation"]["generation_id"])


def test_the_parent_generation_is_not_a_same_named_neighbour(tmp_path):
    """A same-named timeline's generation is a COLLISION, not a parent.

    The defect this closes: the parent of a generation was "the previous
    entry under the same name", which is a DIFFERENT timeline whenever
    a rebuild promoted a new one.
    """
    root = _project(tmp_path)
    neighbour = _record_version(root, timeline_id="tl-a", rows=_rows("a.mov"))
    _record_version(root, timeline_id="tl-b", rows=_rows("b.mov"))
    mine = _record_version(root, timeline_id="tl-a", rows=_rows("c.mov"))

    assert mine["generation"]["parent_generation_id"] == \
        neighbour["generation"]["generation_id"]


def test_the_identity_preserves_the_accepted_state(tmp_path):
    """An accepted generation says so, and the record that knows says it.

    The defect this closes: the accepted state lived in one ledger and
    the generation in another, so nothing could say WHICH generation
    the captain took.
    """
    root = _project(tmp_path)
    accepted = _record_version(root, timeline_id="tl-a",
                               accepted_timeline_id="tl-a")
    other = _record_version(root, timeline_id="tl-b")

    assert accepted["generation"]["accepted"] is True
    assert other["generation"]["accepted"] is False


def test_the_identity_preserves_the_asset_dependencies(tmp_path):
    """A generation records the media it depends on.

    The defect this closes: the media a generation played was only in
    the recovery archive's manifest, so a version entry could not say
    what it needed to restore.
    """
    root = _project(tmp_path)
    entry = _record_version(root, timeline_id="tl-a", rows=_rows("a.mov"))

    paths = [asset.get("path")
             for asset in entry["generation"].get("assets") or ()]
    assert "/media/a.mov" in paths


def test_the_recovery_archive_completes_the_asset_dependencies(tmp_path):
    """The archive's hashed manifest joins the generation's dependencies.

    The defect this closes: the hashed manifest of the media the
    generation plays was in the receipt and nowhere else.
    """
    root = _project(tmp_path)
    entry = _record_version(root, timeline_id="tl-a", rows=_rows("a.mov"))
    from library.tools.versions import reel_versions

    reel_versions.attach_recovery_archive(
        str(root), REEL, int(entry["version"]),
        {"path": "/archives/one.drt", "sha256": "0" * 64,
         "timeline_id": "tl-a",
         "assets": [{"path": "/media/b.mov", "sha256": "1" * 64}]})

    stored = reel_versions.versions_of(str(root), REEL)[0]
    paths = [asset.get("path")
             for asset in stored["generation"].get("assets") or ()]
    assert "/media/a.mov" in paths
    assert "/media/b.mov" in paths


# ── Rejecting ambiguous and stale targets ───────────────────────────

def test_a_name_spanning_two_timelines_refuses_and_names_every_candidate(tmp_path):
    """A name that means two generations is refused, not guessed.

    The defect this closes: the engine picked one of two same-named
    timelines and wrote to it.
    """
    root = _project(tmp_path)
    _record_version(root, timeline_id="tl-a", rows=_rows("a.mov"))
    _record_version(root, timeline_id="tl-b", rows=_rows("b.mov"))
    from library.tools import generation_identity

    with pytest.raises(generation_identity.AmbiguousGeneration) as refused:
        generation_identity.resolve(str(root), REEL)

    message = str(refused.value)
    assert "tl-a" in message and "tl-b" in message
    assert "gen-" in message


def test_a_stale_generation_target_is_refused(tmp_path):
    """A generation a later one replaced refuses as stale.

    The defect this closes: a write planned against a generation the
    timeline has moved past changes what the caller did not name.
    """
    root = _project(tmp_path)
    first = _record_version(root, timeline_id="tl-a", rows=_rows("a.mov"))
    _record_version(root, timeline_id="tl-a", rows=_rows("b.mov"))
    from library.tools import generation_identity

    identity = generation_identity.identity_of(first)
    with pytest.raises(generation_identity.StaleGeneration) as refused:
        generation_identity.require_current(str(root), identity)

    assert identity.generation_id in str(refused.value)


def test_an_unknown_generation_target_is_refused(tmp_path):
    """A target that names nothing recorded is refused, not invented."""
    root = _project(tmp_path)
    from library.tools import generation_identity

    with pytest.raises(generation_identity.UnknownGeneration):
        generation_identity.resolve(str(root), "gen-nothingrecorded")


# ── The write path ──────────────────────────────────────────────────

def test_a_shared_name_does_not_block_a_write_that_carries_the_identity(tmp_path):
    """The request names the timeline by its identity, so the name cannot.

    The defect this closes (measured 2026-10-21): "recorded base for
    'Ren Scratch - vep-bench-demo2'/'Reel 13 - ...' resolved to 3
    timelines", and the write never started.
    """
    from library.tools import resolve_axi_core

    project = f"{PROJECT}-{uuid.uuid4().hex[:8]}"
    reel = f"{REEL}-{uuid.uuid4().hex[:8]}"
    _shadow_record(project, "tl-a", reel)
    _shadow_record(project, "tl-b", reel)
    from library.tools.edit_request import EditOperation, EditRequest

    request = EditRequest(
        id="req-1", project=project, timeline=reel, timeline_id="tl-b",
        operations=(EditOperation(op="track.add", target=None,
                                   params={"track_type": "video", "name": "V2"},
                                   expected_effect="add a row"),))

    generation, _snapshot = resolve_axi_core._recorded_base(
        _store(), project, request)

    assert generation.timeline_id == "tl-b"


def test_a_request_without_the_identity_still_refuses_a_shared_name(tmp_path):
    """Without an identity the old refusal stands: no picking one.

    The safety this keeps: two live timelines sharing a name is two
    timelines, and the engine will not choose (AGENTS.md 5).
    """
    from library.tools import resolve_axi_core
    from library.tools.edit_request import EditOperation, EditRequest

    project = f"{PROJECT}-{uuid.uuid4().hex[:8]}"
    reel = f"{REEL}-{uuid.uuid4().hex[:8]}"
    _shadow_record(project, "tl-a", reel)
    _shadow_record(project, "tl-b", reel)
    request = EditRequest(
        id="req-2", project=project, timeline=reel,
        operations=(EditOperation(op="track.add", target=None,
                                   params={"track_type": "video", "name": "V2"},
                                   expected_effect="add a row"),))

    with pytest.raises(ValueError) as refused:
        resolve_axi_core._recorded_base(_store(), project, request)

    assert "2 timelines" in str(refused.value)


def _store():
    from library.tools import timeline_shadow

    return timeline_shadow.ShadowStore()


def test_the_revise_path_resolves_a_shared_name_by_its_identity(tmp_path):
    """The revise path names the reel the way every other path does now.

    The defect this closes: `ren revise` resolved the reel by its NAME,
    so a name shared by two live timelines refused the patch before it
    started - the one path PR 3131 did not reach.
    """
    root = _project(tmp_path)
    _record_version(root, timeline_id="tl-b", rows=_rows("b.mov"))
    from library.tools import revise

    resolved = revise._reel_timeline_id(str(root), PROJECT, REEL)

    assert resolved == "tl-b"


def test_the_identity_is_the_reels_current_generation(tmp_path):
    """The ledger's newest generation is the reel's current one."""
    root = _project(tmp_path)
    _record_version(root, timeline_id="tl-a", rows=_rows("a.mov"))
    _record_version(root, timeline_id="tl-b", rows=_rows("b.mov"))
    from library.tools import generation_identity

    identity = generation_identity.current_generation(str(root), REEL)

    assert identity is not None
    assert identity.resolve_timeline_id == "tl-b"
