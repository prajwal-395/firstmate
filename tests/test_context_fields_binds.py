"""A field a manifest does not declare cannot reach the prompt.

`context_fields` is the allow-list `project_step_context` applies before
a step's inputs are serialised into a request.  It is enforcement, not
documentation - but only where it is READ, and where the declaration
lives had two answers.

Step 3.04 declared its allow-list under `interface`, beside the inputs
and outputs it reads as though it belongs with.  `project_step_context`
looks at the manifest's top level, found nothing there, and took that
for "this step declares none" - which is a real and deliberate state
(`render` and `validate` are in it) and therefore indistinguishable from
the accident.  The projection never ran.  Every reel selection this
pipeline has ever made was made from a request in which 817,317
characters - 92% of it, 8,509 words carrying individual start/end
timings - were the raw transcript the step's declaration had asked to
drop.

The tests here are the gate, and they are written to fail on
`origin/main` as it stands: they read the declaration from BOTH
locations, so they exercise the mechanism rather than the fix.

- `test_a_declaration_is_where_the_projection_reads_it` fails on the
  MISPLACED declaration.
- `test_an_undeclared_field_cannot_reach_a_prompt` fails on the
  CONSEQUENCE, through the real `project_step_context`.
- `test_a_declared_field_still_reaches_the_prompt` is the other
  direction: a gate that passed by deleting everything would be no gate.
- `test_every_llm_step_declares_an_allow_list` derives the LLM steps
  from the DAG, because the hand-written list in
  `test_llm_context_routing.py` did not name `select_reels` and so had
  never once looked at it.

See library/tools/context_projector.declared_context_fields and
AGENTS.md 10.1.
"""

import json
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from library.processes.edit_video.run_pipeline import (  # noqa: E402
    get_step_implementation, project_step_context,
)

STEPS = REPO / "library" / "steps"
DAG = json.loads(
    (REPO / "library/processes/edit_video/dag.json").read_text(encoding="utf-8"))

# A sentinel no manifest declares and no restore-by-name knows about, so
# its survival means one thing only: the allow-list did not bind.
UNDECLARED = "a_field_no_manifest_declares"

# The two steps handed their whole input set on a standing captain
# decision (AGENTS.md 10.1). Both express it as a `-`-only declaration,
# which means "everything, minus these" - so an undeclared key reaching
# them is the declared behaviour, not a leak. Enumerated rather than
# detected so that a THIRD step written that way has to be argued for.
UNPROJECTED = {"render", "validate"}

# An LLM step that is never actually asked anything. `semantic_analysis`
# has a handoff.md but every key it declares is produced by its own
# step.py, so the schema handed to the model is `[]` and the call is
# skipped - see test_llm_context_routing.
NO_CALL = {"semantic_analysis": "asks the model for nothing - the call "
                                "is skipped, so there is no prompt"}


def step_dir(node_id: str) -> Path:
    for node in DAG["nodes"]:
        if node["id"] == node_id:
            return REPO / "library" / node["step_ref"]
    raise KeyError(node_id)


def manifest(node_id: str) -> dict:
    return json.loads(
        (step_dir(node_id) / "manifest.json").read_text(encoding="utf-8"))


def declared_anywhere(m: dict):
    """The allow-list, from EITHER location a manifest has used.

    Deliberately not `declared_context_fields`: these tests have to be
    able to run against a tree where the declaration is still in the
    wrong place, or they cannot prove they would have caught it.
    """
    top = m.get("context_fields")
    if top is not None:
        return top
    return (m.get("interface") or {}).get("context_fields")


def llm_nodes() -> list:
    """Every DAG node whose implementation reaches a model.

    DERIVED, from the same `get_step_implementation` the runner uses.
    A hand-written list is what let `select_reels` go unexamined.
    """
    out = []
    for node in DAG["nodes"]:
        impl = get_step_implementation(REPO / "library" / node["step_ref"])
        if impl["type"] in ("llm_only", "hybrid", "deterministic_with_llm"):
            out.append(node["id"])
    return sorted(out)


def projecting_nodes() -> list:
    """LLM nodes whose declaration should narrow the prompt."""
    return [n for n in llm_nodes()
            if n not in UNPROJECTED and n not in NO_CALL
            and declared_anywhere(manifest(n))]


ALL_STEP_MANIFESTS = sorted(
    p.parent.name for p in STEPS.glob("*/manifest.json"))


# ---------------------------------------------------------------------------
# The declaration
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("step", ALL_STEP_MANIFESTS)
def test_a_declaration_is_where_the_projection_reads_it(step):
    """One location, and a second one is refused rather than ignored.

    Reading both would be two spellings of the same rule, free to
    disagree; ignoring the second is what shipped. The manifest's top
    level is the location, enforced by `declared_context_fields` - the
    dead `library/schema/manifest.schema.json` once described it too,
    but nothing ever loaded that file, so it was deleted.
    """
    m = json.loads(
        (STEPS / step / "manifest.json").read_text(encoding="utf-8"))
    interface = m.get("interface") or {}
    assert "context_fields" not in interface, (
        f"{step} declares context_fields under `interface`, where nothing "
        f"reads it: the projection would never run and the model would be "
        f"handed every byte the step was routed. Move it to the "
        f"manifest's top level."
    )


def test_the_reader_refuses_a_misplaced_declaration():
    """The accessor RAISES; it does not fall back to reading it.

    A fallback would make the wrong location work, and the wrong
    location would then spread.
    """
    from library.tools.context_projector import (
        MisplacedContextFields, declared_context_fields,
    )

    assert declared_context_fields(
        {"context_fields": ["a"]}) == ["a"]
    assert declared_context_fields({"interface": {"inputs": []}}) is None

    with pytest.raises(MisplacedContextFields):
        declared_context_fields(
            {"interface": {"context_fields": ["a"]}}, "step_x")


# ---------------------------------------------------------------------------
# The consequence
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("node_id", projecting_nodes())
def test_an_undeclared_field_cannot_reach_a_prompt(node_id):
    """Driven through the real `project_step_context`.

    The manifest is read whole and passed as the runner passes it, so
    this asks the question the runner asks: given what this step really
    declares, does a key it does not name survive?
    """
    projected = project_step_context(
        {UNDECLARED: "x" * 1000, "project_folder": "/tmp/p"},
        manifest(node_id),
    )
    assert UNDECLARED not in projected, (
        f"'{node_id}' hands the model a field its manifest does not "
        f"declare. Either the declaration is somewhere the projection "
        f"does not read, or the step is not projected at all."
    )


KEPT = "a_declared_leaf"


def _shaped_for(path: str):
    """A tree whose only content sits exactly where `path` points.

    `*` becomes a one-item list, so `clip_catalog.*.clip_id` builds
    `{"clip_catalog": [{"clip_id": KEPT, <UNDECLARED>: ...}]}`. The
    sibling is what proves the projection narrowed rather than passed
    the tree through.
    """
    parts = path.split(".")

    def build(rest):
        if not rest:
            return KEPT
        head, tail = rest[0], rest[1:]
        if head == "*":
            return [build(tail)]
        inner = build(tail)
        if isinstance(inner, dict) or inner is KEPT:
            return {head: inner, UNDECLARED: "x"}
        return {head: inner}

    return {parts[0]: build(parts[1:])} if parts[1:] else {parts[0]: KEPT}


def _leaf_at(tree, path: str):
    for part in path.split("."):
        if part == "*":
            if not isinstance(tree, list) or not tree:
                return None
            tree = tree[0]
            continue
        if not isinstance(tree, dict) or part not in tree:
            return None
        tree = tree[part]
    return tree


@pytest.mark.parametrize("node_id", projecting_nodes())
def test_a_declared_field_still_reaches_the_prompt(node_id):
    """The other direction: the gate must not pass by deleting everything.

    A projection that dropped its own declared paths would satisfy every
    assertion above. The first path each step declares is built as a
    tree with an undeclared sibling; the declared leaf must arrive and
    the sibling must not.
    """
    fields = declared_anywhere(manifest(node_id))
    keeps = [f for f in fields if not f.startswith(("-", "view:"))]
    assert keeps, (
        f"'{node_id}' declares no positive path, so this direction "
        f"cannot be checked here - narrow the test, do not drop it"
    )
    path = keeps[0]
    inputs = _shaped_for(path)
    inputs["project_folder"] = "/tmp/p"
    projected = project_step_context(inputs, manifest(node_id))

    assert _leaf_at(projected, path) == KEPT, (
        f"'{node_id}' lost {path!r}, which its own manifest declares"
    )
    assert UNDECLARED not in json.dumps(projected), (
        f"'{node_id}' kept an undeclared sibling of {path!r}"
    )


def test_the_unprojected_steps_are_the_two_that_were_decided():
    """A third one has to be argued for, not discovered.

    `render` and `validate` are handed their whole input set by a
    standing decision, expressed as a `-`-only declaration. Anything
    else declaring nothing is a step nobody projected.
    """
    silent = [n for n in llm_nodes()
              if n not in NO_CALL and not declared_anywhere(manifest(n))]
    assert silent == [], (
        f"{silent} reach a model and declare no context_fields, so each "
        f"is handed every byte it was routed. Project them, or record "
        f"the decision here."
    )
    for node_id in sorted(UNPROJECTED):
        fields = declared_anywhere(manifest(node_id))
        assert fields and all(f.startswith("-") for f in fields), (
            f"'{node_id}' no longer expresses its exemption as a "
            f"`-`-only declaration; UNPROJECTED is now describing "
            f"something else"
        )


# ---------------------------------------------------------------------------
# The enumeration that missed it
# ---------------------------------------------------------------------------

def test_every_llm_step_declares_an_allow_list():
    """Derived from the DAG, so a new LLM step cannot escape it."""
    missing = [n for n in llm_nodes()
               if n not in NO_CALL and not declared_anywhere(manifest(n))]
    assert missing == [], missing


def test_the_hand_written_llm_step_list_names_every_one():
    """`test_llm_context_routing.LLM_STEPS` is a list, and it was short.

    It omitted `select_reels` and `render_motion_graphics`, so the
    assertion that every LLM step projects its context had never been
    asked about either of them. AGENTS.md 10.4: a gate that cannot fail
    on the case it exists for is not coverage.
    """
    from tests.test_llm_context_routing import LLM_STEPS

    missing = sorted(set(llm_nodes()) - set(LLM_STEPS))
    assert missing == [], (
        f"{missing} reach a model and are not in LLM_STEPS, so nothing "
        f"in test_llm_context_routing.py has ever looked at them"
    )


# ---------------------------------------------------------------------------
# What 3.04 declares now
# ---------------------------------------------------------------------------

def test_select_reels_does_not_carry_the_transcript_document():
    """`timeline_transcript.turns` did not exist, and `segments` is huge.

    The document carries `segments`, not `turns`; the `turns` the
    handoff tells the model to read is the PRE-BRIDGE's own table, built
    from those segments and restored by name after the projection. The
    old declaration named a path that resolves to nothing - which the
    projector would have WARNED about on every run, had the projection
    ever run.

    What is declared now is the document's one-sentence `measurement`,
    which says what the times mean and how the speech was obtained. The
    940 segments and the 8,509 per-word timings under them are not.
    """
    document = {
        "measurement": "Speech transcribed by WhisperX. Times are "
                       "timeline time.",
        "derived_from": {"fps": 23.976, "picture_holes": [[1.0, 2.0]]},
        "segments": [{
            "speaker": "Craig", "text": "hello", "timeline_start": 0.0,
            "timeline_end": 1.0, "source_file": "/m/a.MXF",
            "resolve_item_id": "u",
            "words": [{"word": "hello", "start": 0.0, "end": 1.0,
                       "timed": True}],
        }],
    }
    projected = project_step_context(
        {"timeline_transcript": document, "project_folder": "/tmp/p"},
        manifest("select_reels"),
    )
    carried = projected.get("timeline_transcript") or {}
    assert carried.get("measurement"), (
        "the model no longer knows what the times it is choosing MEAN"
    )
    assert "segments" not in carried, (
        "the transcript document is back in select_reels' prompt: "
        "817,317 characters, 8,509 per-word timings"
    )
    # The per-word array is forbidden by its KEY, not by the letters
    # `words` appearing anywhere in the blob. The view's own prose says
    # what the transcriber recorded about the WORDS on a line, and a
    # transcript line may contain the English word too - matching those
    # is the test firing on its own explanation.
    assert '"words"' not in json.dumps(projected)

    bridge = (step_dir("select_reels") / "bridge.py").read_text(
        encoding="utf-8")
    assert '"turns"' in bridge, (
        "the pre-bridge no longer builds `turns`, so dropping the "
        "transcript document from the prompt now blinds the step"
    )
