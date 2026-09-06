"""The reel path is addressable, and its contract can REFUSE.

The captain's ask, verbatim: *"don't try to run functionality on
something we don't have requirements for"*.  Before this file, reels were
the one thing the registry could not say that about - `grep -n reel
library/tools/operations.py` on `834a29b` returned nothing, so the
captain's own use case was reachable only by knowing which script to run.

Two halves, and the second is the load-bearing one.

**Registration.**  `reel.candidates` and `reel.select` are `select_reels`'
own two bodies, named.  Reel BUILDING is deliberately absent and
`docs/REEL_BUILD_HAS_NO_OWNING_NODE.md` says why; `test_no_reel_operation
_claims_a_node_that_does_not_own_it` is what stops it being registered
under a node whose contract is about something else.

**The contract.**  `select_reels` derived ZERO requirements until
`requirements.derive_runner_injected_keys` existed, so an operation owned
by it could not have refused for any reason at all - a gate that cannot
fail, which reads as coverage and is worse than no gate (AGENTS.md 10.4).
So this file asserts BOTH directions on one project fixture: the same
operation refuses without the transcript and passes with it.  Either
assertion alone would keep passing if the check collapsed to a constant.

`tests/test_operations_execute.py` owns the general execute/refuse
machinery; this file is about reels and about the enumeration that keeps
the derivation honest.
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from library.tools import operations, run_scope
from library.tools import requirements as R
from library.tools.timeline_transcript import transcript_path

REPO = Path(__file__).resolve().parents[1]

REEL_OPERATIONS = tuple(op for op in operations.all()
                        if op.name.startswith("reel."))


def _project(tmp_path, with_transcript: bool) -> str:
    """A project under tmp_path, never a real one (AGENTS.md 8)."""
    (tmp_path / "pipeline_output").mkdir(parents=True, exist_ok=True)
    (tmp_path / "pipeline_data.json").write_text(json.dumps({
        "project_folder": str(tmp_path), "step_outputs": {}}),
        encoding="utf-8")
    if with_transcript:
        path = transcript_path(tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            "segments": [], "segment_count": 0,
            "derived_from": {"duration_seconds": 0.0}}), encoding="utf-8")
    return str(tmp_path)


# ── The reel path is in the registry ────────────────────────────────


def test_the_registry_carries_reel_operations():
    """The gap this file closes. `grep -n reel operations.py` returned
    nothing on `834a29b`, so the only way to produce the captain's field
    test reels was to know which standalone script to run."""
    assert REEL_OPERATIONS, (
        "no operation names the reel path, so reels are reachable only "
        "by running a script directly - which is the defect the "
        "operation registry exists to remove")
    assert {op.name for op in REEL_OPERATIONS} == {"reel.candidates",
                                                   "reel.select"}


@pytest.mark.parametrize("op", REEL_OPERATIONS, ids=lambda o: o.name)
def test_a_reel_operation_is_owned_by_the_node_whose_decision_it_is(op):
    """`owning_node` is what derives `requires`, so a wrong owner is a
    wrong contract - not a cosmetic mislabel."""
    assert op.owning_node == "select_reels"
    assert op.owning_dir == "step_3_04_select_reels"
    assert op.body in ("bridge.py", "post_bridge.py")


def test_no_reel_operation_claims_a_node_that_does_not_own_it():
    """The specific mis-registration this task had to refuse.

    `render` is the obvious-looking home for "build a reel timeline in
    Resolve", and its ONE derived requirement is `assembly_manifest` from
    `compile_manifest` - a key `library/tools/reel_build.py` never reads.
    An operation owned by `render` would therefore refuse for a reason
    that is not true AND pass on a project that has an assembly manifest
    and not one approved reel in it.  See
    docs/REEL_BUILD_HAS_NO_OWNING_NODE.md.
    """
    render_asks = {r.name for r in R.all_requirements()
                   if "render" in r.consumers}
    assert render_asks == {"state.render.assembly_manifest"}, (
        f"render's contract changed; re-read the finding before moving "
        f"a reel operation onto it. It now asks: {sorted(render_asks)}")
    for op in REEL_OPERATIONS:
        assert op.owning_node != "render"


# ── The contract BITES, in both directions ──────────────────────────


@pytest.mark.parametrize("op", REEL_OPERATIONS, ids=lambda o: o.name)
def test_a_reel_operation_asks_for_something(op):
    """An operation with an empty `requires` cannot refuse for any
    reason, which is the shape a test of refusal would pass over."""
    assert op.requires, (
        f"{op.name} derives no requirement at all, so nothing it is run "
        f"against can be refused")


@pytest.mark.parametrize("op", REEL_OPERATIONS, ids=lambda o: o.name)
def test_a_reel_operation_refuses_without_the_transcript(op, tmp_path):
    """The captain's ask, made mechanical."""
    result = op.execute(_project(tmp_path, with_transcript=False))

    assert result.refused, (
        f"{op.name} agreed to run against a project with no timeline "
        f"transcript - the one input reel selection cannot work without")
    assert [r.name for r in result.unsatisfied] == [
        "timeline_transcript.on_file"]
    assert "timeline_transcript" in result.error
    # The remedy, because no step produces this and "run the producer"
    # is not an answer that exists.
    assert "library.tools.timeline_transcript" in result.error
    assert "NO STEP MAKES ONE" in result.error


@pytest.mark.parametrize("op", REEL_OPERATIONS, ids=lambda o: o.name)
def test_the_same_operation_passes_its_contract_with_the_transcript(
        op, tmp_path):
    """The other direction, on the same fixture.

    A gate that FAILS correct input is no more coverage than one that
    cannot fail (AGENTS.md 10.4), and a refusal-only test would keep
    passing if the check collapsed to `return UNSATISFIED(...)`.
    """
    assert op.unmet(_project(tmp_path, with_transcript=True)) == []


def test_the_refusal_names_no_producer_because_there_is_none():
    """`_teach` offers "run that step first" only for a requirement that
    names a producer.  Suggesting one here would be confidently wrong:
    the transcript is written by a CLI tool that needs Resolve open."""
    requirement = next(r for r in R.all_requirements()
                       if r.name == "timeline_transcript.on_file")
    assert requirement.produced_by == ()
    assert requirement.kind == R.KIND_STATE_KEY


# ── The enumeration that keeps the derivation honest ────────────────


def test_the_transcript_is_the_only_required_input_no_edge_carries():
    """`derive_runner_injected_keys` names ONE input, and this is the
    measurement that says naming one is enough.

    `derive_state_keys` reads edges; a required manifest input with no
    producing edge is invisible to it.  Measured 2026-09-06 there are
    exactly two, and only one of them can be absent.  If a third appears,
    this fails rather than the pipeline quietly gaining a hard input
    nothing refuses on.
    """
    dag = run_scope.load_dag()
    manifests = run_scope.load_manifests(dag)
    supplied = {}
    for edge in dag.get("edges", []):
        mapping = edge.get("data_mapping") or {}
        supplied.setdefault(edge["to"], set()).update(mapping.values())

    unrouted = set()
    for node_id, manifest in manifests.items():
        for inp in ((manifest or {}).get("interface") or {}).get("inputs") or []:
            if inp.get("required", False) and \
                    inp.get("name") not in supplied.get(node_id, set()):
                unrouted.add((node_id, inp["name"]))

    assert unrouted == {
        # A whitelisted global - `gather_step_inputs` puts it on every
        # step, so it is never absent and there is nothing to refuse.
        ("scan", "project_folder"),
        # The real one.
        ("select_reels", "timeline_transcript"),
    }, f"a required input with no producing edge appeared: {sorted(unrouted)}"


def test_the_input_name_agrees_with_the_runner():
    """Two spellings would give a requirement about a key nothing
    supplies - it would refuse a correct project forever."""
    source = (REPO / "library" / "processes" / "edit_video"
              / "run_pipeline.py").read_text(encoding="utf-8")
    assert (f'TIMELINE_TRANSCRIPT_INPUT = "{R.TIMELINE_TRANSCRIPT_INPUT}"'
            in source), (
        "requirements.TIMELINE_TRANSCRIPT_INPUT no longer matches the "
        "name the runner injects")


def test_the_transcript_path_is_spelled_once_outside_reel_build():
    """The writer, the runner and the requirement must agree on WHERE.

    It was composed by hand in five places before this, which is how a
    requirement about a file could disagree with the runner that reads
    it.  `timeline_transcript.transcript_path` is the one spelling now.

    `reel_build.py` still composes it TWICE.  The deferral was taken
    because PR #568 was in flight over `rebuild_reels_in_project`; it
    LANDED as `a26050e` and did not touch either line, so what is left is
    an ordinary debt in the build path this change deliberately does not
    enter.  The count is asserted rather than described, so when those
    two are fixed this fails and the exemption has to be DELETED rather
    than quietly outliving its reason.
    """
    hits = subprocess.run(
        ["grep", "-rnI", "--exclude-dir=__pycache__",
         "-e", 'timeline_transcript" / "transcript.json',
         "-e", "timeline_transcript/transcript.json", "library"],
        cwd=REPO, capture_output=True, encoding="utf-8",
        check=False).stdout.strip().splitlines()

    in_reel_build = [h for h in hits if h.startswith("library/tools/reel_build.py")]
    assert len(in_reel_build) == 2, (
        f"reel_build.py composed the path twice when this was written and "
        f"now composes it {len(in_reel_build)} time(s). If they are fixed, "
        f"delete this exemption:\n" + "\n".join(in_reel_build))

    elsewhere = [h for h in hits
                 if not h.startswith("library/tools/reel_build.py")
                 and not h.startswith("library/tools/timeline_transcript.py")]
    assert elsewhere == [], (
        "the transcript path is composed outside its own module; use "
        "`timeline_transcript.transcript_path`:\n" + "\n".join(elsewhere))


# ── What is NOT registered, and the shape it would have taken ───────


def test_the_finding_is_written_down():
    """A registration that cannot be made honestly is worth more as a
    finding than as a forced one, but only if the reasoning survives."""
    doc = REPO / "docs" / "REEL_BUILD_HAS_NO_OWNING_NODE.md"
    assert doc.is_file(), f"{doc} is missing"
    text = doc.read_text(encoding="utf-8")
    for expected in ("reel_build", "render", "select_reels",
                     "assembly_manifest"):
        assert expected in text, (
            f"the finding does not mention {expected!r}, so a reader "
            f"cannot check it")


def test_no_reel_operation_reimplements_reel_code():
    """Ruling 1, spelled for this file's two entries.

    The general gate is
    `tests/test_operations_add_no_second_implementation.py`; this asserts
    the specific thing that would be tempting here - an operation whose
    `run` is a wrapper defined in the registry.
    """
    import inspect
    for op in REEL_OPERATIONS:
        source = Path(inspect.getsourcefile(op.run)).resolve()
        assert source.parent == (REPO / "library" / "steps" / op.owning_dir), (
            f"{op.name} resolves to {source}, not to its owning step")
