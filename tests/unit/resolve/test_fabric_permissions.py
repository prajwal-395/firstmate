"""Ren Fabric permissions: the roster, the lease gate, the merge gate.

Every test names a defect it catches - a case that fails on
origin/main, where no permission model exists: a client's Ren taking a
lease it must never hold, a merge landing from an actor who may
execute but never merge, a refusal nobody can read back, a roster the
graph cannot see.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

import pytest  # noqa: E402

from library.tools import event_log  # noqa: E402
from library.tools import fabric_permissions as permissions  # noqa: E402
from library.tools import project_graph  # noqa: E402
from library.tools import undo_journal as uj  # noqa: E402
from library.tools.resource_leases import (  # noqa: E402
    LeaseDeclaration,
    ResourceRef,
    resource_lease,
)


@pytest.fixture
def lock_dir(tmp_path, monkeypatch):
    """Leases of our own, never the machine's.

    The `PIPELINE_RESOLVE_LOCK_DIR` redirect `resolve_lock` itself
    uses - the same redirect the resource lease tests use.
    """
    directory = tmp_path / "locks"
    directory.mkdir()
    monkeypatch.setenv("PIPELINE_RESOLVE_LOCK_DIR", str(directory))
    return directory


@pytest.fixture
def project(tmp_path):
    folder = tmp_path / "project"
    folder.mkdir(parents=True, exist_ok=True)
    return str(folder)


def _write_declaration():
    return LeaseDeclaration(
        write=frozenset({ResourceRef("timeline", "Reel 01")}),
        exclusive=frozenset({ResourceRef("timeline", "Reel 01")}),
    )


def _read_declaration():
    return LeaseDeclaration(
        read=frozenset({ResourceRef("timeline", "Reel 01")}),
    )


# ── The lease gate ────────────────────────────────────────────────

def test_a_client_ren_cannot_take_a_write_lease(lock_dir, project):
    """Observe+suggest never takes a lease.

    Defect caught: a lease acquisition that checks only conflicts lets
    the client's Ren hold the master timeline's exclusive lease - the
    spec's explicit never ("a client's Ren gets observe+suggest, never
    delete-media or modify-master-timeline"). On origin/main
    `resource_lease` takes no actor at all, so the acquisition
    succeeds.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    with pytest.raises(permissions.PermissionRefused) as refused:
        with resource_lease(_write_declaration(), "touch up Reel 01",
                            project=project, actor="client",
                            heartbeat_thread=False):
            pass
    assert "client" in str(refused.value)
    assert "execute" in str(refused.value)


def test_an_observer_cannot_take_even_a_read_lease(lock_dir, project):
    """Lease acquisition needs Execute, even for a read-only declaration.

    Defect caught: a gate that only checks write declarations lets an
    observer hold a lease over the timeline - and a lease is an
    intent to operate, which Observe ("inspect project") never
    includes. Read-only parallelism runs without leases at all.
    """
    permissions.grant(project, "viewer", permissions.OBSERVE)
    with pytest.raises(permissions.PermissionRefused):
        with resource_lease(_read_declaration(), "scan Reel 01",
                            project=project, actor="viewer",
                            heartbeat_thread=False):
            pass


def test_an_editor_with_execute_takes_the_lease(lock_dir, project):
    """The gate refuses the unauthorised, never the authorised.

    Defect caught: a gate that refuses every named actor - or that
    breaks the acquisition path itself - stops the lead editor's Ren
    from working at all. A gate that fails correct output is no
    coverage.
    """
    permissions.grant(project, "editor", permissions.EXECUTE)
    with resource_lease(_write_declaration(), "touch up Reel 01",
                        project=project, actor="editor",
                        heartbeat_thread=False):
        pass


def test_a_merger_outranks_execute_on_the_ladder(lock_dir, project):
    """Merge includes Execute: the spec's table order is the authority.

    Defect caught: a gate that demands exactly Execute refuses a
    participant trusted to merge - who outranks execute - from taking
    the lease their own merge needs first.
    """
    permissions.grant(project, "lead", permissions.MERGE)
    with resource_lease(_write_declaration(), "merge prep",
                        project=project, actor="lead",
                        heartbeat_thread=False):
        pass


def test_an_unnamed_act_is_never_checked(lock_dir, project):
    """The local operator names no actor and is never gated.

    Defect caught: a gate that defaults every acquisition to "unknown
    participant" refuses the human's own direct runs on a federated
    project - the captain would have to name himself to every command
    to keep working.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    with resource_lease(_write_declaration(), "touch up Reel 01",
                        project=project, heartbeat_thread=False):
        pass


def test_a_named_actor_with_no_roster_is_refused(lock_dir, project):
    """A federated identity nobody granted is not an operator.

    Defect caught: a gate that allows any named actor where no roster
    stands lets a stranger's Ren act on a project that never
    federated - fail-open for the federation itself. The fix names the
    grant that would allow them.
    """
    with pytest.raises(permissions.PermissionRefused) as refused:
        with resource_lease(_write_declaration(), "touch up Reel 01",
                            project=project, actor="stranger",
                            heartbeat_thread=False):
            pass
    assert "no permission roster" in str(refused.value)


def test_a_refusal_is_an_event_in_the_log(lock_dir, project):
    """The refusal lands in the event log, by name.

    Defect caught: a gate that raises but never records leaves "who
    was refused what" unanswerable - the spec's event-sourced account
    has a hole exactly where the federation's authority was tested.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    with pytest.raises(permissions.PermissionRefused):
        with resource_lease(_write_declaration(), "touch up Reel 01",
                            project=project, actor="client",
                            heartbeat_thread=False):
            pass
    refusals = [event for event in event_log.read_events(project)
                if event.get("type") == event_log.PERMISSION_REFUSED]
    assert len(refusals) == 1
    fields = refusals[0]["fields"]
    assert fields["actor"] == "client"
    assert fields["required"] == "execute"


# ── The merge gate ────────────────────────────────────────────────

def _commit_tree(project):
    """Root plus two non-conflicting children, for merge tests."""
    root = uj.record_commit(project, parent=None, intent="first",
                            changes=[], agent="reel_touchup",
                            timeline="Reel 01")
    left = uj.record_commit(project, parent=root["id"], intent="left",
                            changes=[], agent="editor", timeline="Reel 01")
    right = uj.record_commit(project, parent=root["id"], intent="right",
                             changes=[], agent="colourist",
                             timeline="Reel 01")
    return root, left, right


def _commit_chain(project):
    _root, left, right = _commit_tree(project)
    return left, right


def test_a_merge_from_an_actor_without_merge_is_refused(project):
    """Execute never merges into the shared chain.

    Defect caught: a merge path that checks nothing lets an editor -
    who may execute - merge into the shared timeline chain, collapsing
    the spec's Execute/Merge distinction. On origin/main
    `merge_commits` takes no actor, so the merge lands.
    """
    permissions.grant(project, "editor", permissions.EXECUTE)
    left, right = _commit_chain(project)
    with pytest.raises(permissions.PermissionRefused) as refused:
        uj.merge_commits(project, timeline="Reel 01",
                         left=left["id"], right=right["id"],
                         actor="editor")
    assert "editor" in str(refused.value)
    assert "merge" in str(refused.value)


def test_a_merge_from_an_approver_lands(project):
    """Approve outranks Merge: the human's acceptance merges.

    Defect caught: a gate that demands exactly Merge refuses the
    approver - the participant the proposal flow (second slice) acts
    through - so nothing approved could ever land.
    """
    permissions.grant(project, "director", permissions.APPROVE)
    left, right = _commit_chain(project)
    merged = uj.merge_commits(project, timeline="Reel 01",
                              left=left["id"], right=right["id"],
                              actor="director")
    assert merged["second_parent"] == right["id"]


def test_a_refused_merge_is_an_event_in_the_log(project):
    """The merge refusal reads back from the log like the lease one."""
    permissions.grant(project, "editor", permissions.EXECUTE)
    left, right = _commit_chain(project)
    with pytest.raises(permissions.PermissionRefused):
        uj.merge_commits(project, timeline="Reel 01",
                         left=left["id"], right=right["id"],
                         actor="editor")
    refusals = [event for event in event_log.read_events(project)
                if event.get("type") == event_log.PERMISSION_REFUSED]
    assert len(refusals) == 1
    assert refusals[0]["fields"]["required"] == "merge"


# ── Proposals: federated negotiation ────────────────────────────────

def _branched_operation(project, root, candidate, branch="alt"):
    """A merge operation naming a recorded branch, ready to queue.

    The branch is recorded actor-less: test scaffolding acts as the
    local operator, and only the federated acts under test name an
    actor. The master the candidate is evaluated against is the root;
    the candidate is the branch's tip.
    """
    uj.record_branch(project, name=branch, base=root["id"],
                     timeline="Reel 01", note="candidate line")
    return {"kind": "merge", "timeline": "Reel 01", "branch": branch,
            "left": root["id"], "right": candidate["id"],
            "note": "hold the reaction longer"}


def _tracks_with(clip_name, record_in=0, record_out=100):
    """A track read with one clip at a given span."""
    return [{"type": "video", "index": 1, "name": "V1", "clips": [
        {"name": clip_name, "record_in": record_in,
         "record_out": record_out, "duration": record_out - record_in,
         "source_in_frame": 0,
         "source_out_frame": record_out - record_in,
         "left_offset": 0, "right_offset": 0},
    ]}]


def _move_change(clip_name, new_start):
    """A real `timeline_diff` move change for one clip."""
    from library.tools import timeline_diff
    return timeline_diff.diff_timelines(
        _tracks_with(clip_name, 0, 100),
        _tracks_with(clip_name, new_start, new_start + 100))


def test_an_observer_cannot_record_a_proposal(project):
    """Proposing needs Suggest: observe alone never opens negotiation.

    Defect caught: a record path that checks nothing lets any listed
    name - including a read-only observer - put operations in front of
    an approver. On origin/main there is no proposal path at all.
    """
    permissions.grant(project, "viewer", permissions.OBSERVE)
    root, _left, right = _commit_tree(project)
    with pytest.raises(permissions.PermissionRefused):
        permissions.record_proposal(
            project, "viewer",
            _branched_operation(project, root, right),
            "hold the reaction longer")


def test_a_client_records_and_an_editor_answers_conflict(project):
    """The spec's negotiation: propose with a reason, answer by name.

    Defect caught: a proposal that stores the operation but drops the
    rationale leaves answerers negotiating against an edit with no
    reason - the spec's example ("hold reaction 1.4s longer" /
    "would break music sync") is reason against reason.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "editor", permissions.EXECUTE)
    root, _left, right = _commit_tree(project)
    recorded = permissions.record_proposal(
        project, "client",
        _branched_operation(project, root, right),
        "hold the reaction shot longer")
    proposal_id = recorded["fields"]["proposal_id"]
    permissions.answer_proposal(project, proposal_id, "editor",
                                "conflict", note="would break music sync")
    state = permissions.proposal_status(project, proposal_id)
    assert state["status"] == "recorded"
    assert (state["recorded"]["fields"]["rationale"]
            == "hold the reaction shot longer")
    assert len(state["answers"]) == 1
    assert state["answers"][0]["fields"]["kind"] == "conflict"


def test_an_accept_answer_from_a_non_approver_applies_nothing(project):
    """An accept ANSWER is support, never approval.

    Defect caught: a flow that treats any accept-shaped answer as
    approval lets the editor's Ren merge by agreeing - and the
    client's Ren could merge by agreeing with itself. Nothing applies
    until a participant with Approve accepts.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "editor", permissions.EXECUTE)
    root, _left, right = _commit_tree(project)
    recorded = permissions.record_proposal(
        project, "client",
        _branched_operation(project, root, right),
        "hold the reaction shot longer")
    proposal_id = recorded["fields"]["proposal_id"]
    permissions.answer_proposal(project, proposal_id, "editor", "accept")
    assert permissions.proposal_status(
        project, proposal_id)["status"] == "recorded"
    assert all(commit.get("second_parent") is None
               for commit in uj.list_commits(project))


def test_the_approver_accepts_onto_the_shared_queue(project):
    """Approval submits into the queue both lanes use as one.

    Defect caught: an accept that merges directly - or that emits its
    own private queue event - bypasses the shared queue: variant
    exploration evaluates one batch while approved proposals land
    beside it unmeasured. The accept submits through the queue's own
    `queue_merge` under the author's name and lands the accept with
    the queue entry's id; nothing merges here.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "director", permissions.APPROVE)
    root, _left, right = _commit_tree(project)
    recorded = permissions.record_proposal(
        project, "client",
        _branched_operation(project, root, right),
        "hold the reaction shot longer")
    proposal_id = recorded["fields"]["proposal_id"]
    accepted = permissions.accept_proposal(project, proposal_id, "director")
    assert accepted["fields"]["approver"] == "director"
    entry = uj.read_queue_entry(
        project, accepted["fields"]["queue_entry"])
    assert entry["branch"] == "alt"
    assert entry["candidate"] == right["id"]
    assert entry["master"] == root["id"]
    assert entry["submitted_by"] == "client"
    assert entry["status"] == uj.ENTRY_QUEUED
    assert permissions.proposal_status(
        project, proposal_id)["status"] == "accepted"
    assert all(commit.get("second_parent") is None
               for commit in uj.list_commits(project))


def test_a_second_accept_is_refused(project):
    """An accept applies once: deciding a decided proposal is refused.

    Defect caught: an accept path that never checks decided state
    merges the same change a second time - or records a second
    "accepted" event for work that applied once, and the log claims
    two applications.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "director", permissions.APPROVE)
    root, _left, right = _commit_tree(project)
    recorded = permissions.record_proposal(
        project, "client",
        _branched_operation(project, root, right),
        "hold the reaction shot longer")
    proposal_id = recorded["fields"]["proposal_id"]
    permissions.accept_proposal(project, proposal_id, "director")
    with pytest.raises(permissions.ProposalRefused) as refused:
        permissions.accept_proposal(project, proposal_id, "director")
    assert "already accepted" in str(refused.value)


def test_a_conflicting_proposal_fails_evaluation_with_the_reason(project):
    """The shared evaluation - not the accept - meets the conflict.

    Defect caught: an accept that merges directly raises the conflict
    at approval time, in the approver's face, with no verdict the
    batch can split on. Here the branch and the master both move
    clip_001: the accept queues cleanly, and the shared
    `evaluate_batch` fails the entry with the clips named.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "director", permissions.APPROVE)
    root = uj.record_commit(project, parent=None, intent="first",
                            changes=[], agent="reel_touchup",
                            timeline="Reel 01")
    master = uj.record_commit(project, parent=root["id"], intent="master",
                              changes=_move_change("clip_001", 50),
                              agent="editor", timeline="Reel 01")
    candidate = uj.record_commit(project, parent=root["id"],
                                 intent="candidate",
                                 changes=_move_change("clip_001", 60),
                                 agent="colourist", timeline="Reel 01")
    uj.record_branch(project, name="alt", base=root["id"],
                     timeline="Reel 01", note="candidate line")
    recorded = permissions.record_proposal(
        project, "client",
        {"kind": "merge", "timeline": "Reel 01", "branch": "alt",
         "left": master["id"], "right": candidate["id"],
         "note": "hold the reaction shot longer"},
        "hold the reaction shot longer")
    proposal_id = recorded["fields"]["proposal_id"]
    accepted = permissions.accept_proposal(project, proposal_id, "director")
    decided = uj.evaluate_batch(project, [accepted["fields"]["queue_entry"]])
    assert len(decided) == 1
    assert decided[0]["status"] == uj.ENTRY_EVALUATED_FAIL
    assert "clip_001" in (decided[0]["evaluation"] or {}).get("reason", "")


def test_branch_creation_needs_branch_level(project):
    """Branching is submitting: an observer never opens one.

    Defect caught: a branch path that checks nothing lets an observer
    open a line of development - the submit half of the federation
    with no authority behind it. Execute outranks Branch, so the
    editor's own touch-up branches keep working.
    """
    permissions.grant(project, "viewer", permissions.OBSERVE)
    permissions.grant(project, "editor", permissions.EXECUTE)
    root = uj.record_commit(project, parent=None, intent="first",
                            changes=[], agent="reel_touchup",
                            timeline="Reel 01")
    with pytest.raises(permissions.PermissionRefused):
        uj.record_branch(project, name="viewer-try", base=root["id"],
                         timeline="Reel 01", actor="viewer")
    branch = uj.record_branch(project, name="editor-try", base=root["id"],
                              timeline="Reel 01", actor="editor")
    assert branch["base"] == root["id"]


def test_open_proposals_register_as_graph_nodes(project):
    """The graph answers what awaits approval; decided ones leave it.

    Defect caught: a proposal store nothing reads is not shipped -
    the graph is the shared state other tracks feed, so an undecided
    proposal must be a node in it, and a decided one leaves only its
    queue entry behind.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "director", permissions.APPROVE)
    root, _left, right = _commit_tree(project)
    first = permissions.record_proposal(
        project, "client",
        _branched_operation(project, root, right, branch="alt-one"),
        "hold the reaction shot longer")
    second = permissions.record_proposal(
        project, "client",
        _branched_operation(project, root, right, branch="alt-two"),
        "try the alt ending")
    permissions.accept_proposal(
        project, first["fields"]["proposal_id"], "director")
    graph = project_graph.build_graph(project)
    awaiting = project_graph.open_proposals(graph)
    assert [entry["proposal_id"] for entry in awaiting] == [
        second["fields"]["proposal_id"]]
    assert awaiting[0]["rationale"] == "try the alt ending"


# ── The graph registration: participants ──────────────────────────

def test_the_graph_registers_roster_participants(project):
    """The project graph answers who holds what.

    Defect caught: a roster nothing reads is not shipped - the graph
    is the shared state other tracks feed, so the federation's
    participants must be nodes in it. On origin/main the roster file
    is invisible to `build_graph`.
    """
    permissions.grant(project, "director", permissions.APPROVE,
                      granted_by="captain")
    permissions.grant(project, "client", permissions.SUGGEST,
                      granted_by="captain")
    graph = project_graph.build_graph(project)
    participants = {node["payload"]["participant"]: node["payload"]["level"]
                    for node in graph["nodes"]
                    if node["node_type"] == project_graph.PARTICIPANT}
    assert participants == {"director": "approve", "client": "suggest"}


def test_participants_with_returns_only_those_granting_approve(project):
    """The approver query never returns the client's Ren.

    Defect caught: a query that returns every rostered participant as
    "may approve" hands the client's observe+suggest Ren the human's
    approval - the federation's authority boundary drawn in the wrong
    place.
    """
    permissions.grant(project, "director", permissions.APPROVE)
    permissions.grant(project, "admin_ren", permissions.ADMIN)
    permissions.grant(project, "editor", permissions.MERGE)
    permissions.grant(project, "client", permissions.SUGGEST)
    graph = project_graph.build_graph(project)
    approvers = {entry["participant"]
                 for entry in project_graph.participants_with(
                     graph, permissions.APPROVE)}
    assert approvers == {"director", "admin_ren"}


# ── The shared queue: the permission lane's seam ────────────────

def _propose_and_accept(project, author="client", approver="director",
                        branch="alt"):
    """Record a clean branched proposal and accept it. Returns the
    shared queue's entry id. Grants are the caller's setup; this is
    the federated acts alone. The master moves on once after the
    branch, so the candidate is not its own head at merge time - the
    branch tip and the master head are different commits, as in any
    real submission."""
    root, left, right = _commit_tree(project)
    recorded = permissions.record_proposal(
        project, author,
        _branched_operation(project, root, right, branch=branch),
        "hold the reaction shot longer")
    uj.record_commit(project, parent=left["id"], intent="master moves on",
                     changes=[], agent="editor", timeline="Reel 01")
    accepted = permissions.accept_proposal(
        project, recorded["fields"]["proposal_id"], approver)
    return accepted["fields"]["queue_entry"]


def test_a_passing_entry_merges_under_an_authorized_actor(project):
    """The full federated path: propose, approve, evaluate, merge.

    Defect caught: a seam where the accept submits but the executor
    cannot land it - the approval evaporates between the lanes. A
    clean candidate passes the shared evaluation and merges under a
    Merge-holding actor, and the entry carries its merge commit.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "director", permissions.APPROVE)
    entry_id = _propose_and_accept(project)
    decided = uj.evaluate_batch(project, [entry_id])
    assert decided[0]["status"] == uj.ENTRY_EVALUATED_PASS
    merged = uj.merge_passing(project, [entry_id], actor="director")
    assert merged[0]["status"] == uj.ENTRY_MERGED
    assert uj.read_commit(
        project, merged[0]["merge_commit"])["second_parent"] is not None


def test_queue_execution_needs_merge_level(project):
    """The shared executor is still a merge: runners prove authority.

    Defect caught: an execution path that skips the actor hook lets
    any runner - including a participant who may only execute - merge
    into the shared chain under cover of "the queue did it". The
    refusal lands before anything merges, and the passed entry waits
    for an authorized run.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "director", permissions.APPROVE)
    permissions.grant(project, "editor", permissions.EXECUTE)
    entry_id = _propose_and_accept(project)
    uj.evaluate_batch(project, [entry_id])
    with pytest.raises(permissions.PermissionRefused):
        uj.merge_passing(project, [entry_id], actor="editor")
    assert uj.read_queue_entry(
        project, entry_id)["status"] == uj.ENTRY_EVALUATED_PASS
    assert all(commit.get("second_parent") is None
               for commit in uj.list_commits(project))


def test_an_accept_naming_an_unknown_branch_is_refused(project):
    """The queue's refusal leaves the proposal open, not half-submitted.

    Defect caught: an accept that swallows the queue's refusal - or
    that lands "accepted" for an entry that never queued - claims in
    the log what the queue declined. Here the operation names a branch
    no record answers: the submit raises `BranchRefused` by name, and
    the proposal stays recorded for a corrected one.
    """
    permissions.grant(project, "client", permissions.SUGGEST)
    permissions.grant(project, "director", permissions.APPROVE)
    root, _left, right = _commit_tree(project)
    operation = {"kind": "merge", "timeline": "Reel 01", "branch": "ghost",
                 "left": root["id"], "right": right["id"],
                 "note": "hold the reaction shot longer"}
    recorded = permissions.record_proposal(
        project, "client", operation, "hold the reaction shot longer")
    proposal_id = recorded["fields"]["proposal_id"]
    with pytest.raises(uj.BranchRefused):
        permissions.accept_proposal(project, proposal_id, "director")
    assert permissions.proposal_status(
        project, proposal_id)["status"] == "recorded"
