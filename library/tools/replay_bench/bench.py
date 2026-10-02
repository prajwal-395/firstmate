"""The bench: reconstruct, compare two revisions, and check against the archive.

Three operations, and the third is what licenses the other two.

`replay`     - one step, one revision, off a frozen snapshot.
`compare`    - the same step at two revisions; diffs the CONTEXT, and the
               ANSWERS when both are supplied.
`verify`     - every step the snapshot archived, reconstructed and compared
               to what the runner really wrote, byte for byte.  If it
               cannot reproduce the past it cannot be trusted to compare
               futures, so `verify` is a gate and not a report.


Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**A step's decision must be SOURCED from its own context.**
Judge routing by the assembled context, never by whether the run came out right. Check with `library/tools/replay_bench`. [why](docs/RULE_EVIDENCE.md#the-decision-that-was-remembered-not-sourced) `tests/unit/audio/test_pacing_and_sfx_are_not_remembered.py`.


Rules relocated from AGENTS.md 8
--------------------------------
These are the engine's rules for this module.  They lived in
AGENTS.md until it was split by subsystem; the wording is unchanged,
so each rule is findable by its own words, and AGENTS.md 8
keeps the headline and points here.

**A step's exact prompt and context can be rebuilt off frozen state, at a named revision, with no pipeline run, no Resolve and no project write.**
`library/tools/replay_bench/`, driven by `python3 -m library.tools.replay_bench`. Read [`docs/STEP_REPLAY_BENCH.md`](docs/STEP_REPLAY_BENCH.md) before changing what a step is routed: it answers "did that change what the model sees" in seconds.
- The reconstruction is the runner's OWN assembly - `gather_step_inputs`, the step's `bridge.py`, `project_fields`, `json_to_toon`, the handoff and `get_brand_constraints` - never a model of it. `reconstruct.py` imports nothing from `library` at module scope: it runs as a subprocess with the TARGET tree first on `sys.path`.
- **A snapshot is captured outside the repository; only its MANIFEST is committed** to `tests/fixtures/replay_snapshots/`.
- **`verify` is a gate, not a report.** It reconstructs every archived context and exits non-zero on any unaccounted difference: if it cannot reproduce the past it cannot be trusted to compare futures. A step that matches only after a named cause is subtracted reads `EXACT (explained)`, never as a clean pass.
- **Never use the pipeline's own token figures.** `present_llm_step` logs `len(s.split()) * 1.3`, which is 0.38x-0.54x the `o200k_base` count. The bench measures from the reconstructed string and names the tokenizer; with `tiktoken` absent the count is absent rather than estimated.
- The bench measures the pipeline and stays out of it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

from . import diffing, tokens, trees
from . import snapshot as snapshot_mod

WORKER = Path(__file__).resolve().parent / "reconstruct.py"


def _run_worker(tree: Path, snap: snapshot_mod.Snapshot, step: str,
                llm_authored=None, qa_feedback: str = "") -> dict:
    """Always a subprocess, even for this tree.

    A comparison imports `library.*` from two checkouts, and a module
    imported once stays imported.  One process per reconstruction is what
    makes "the same step at two revisions" mean two trees rather than the
    first one twice.

    And always in a throwaway clone of the snapshot project, never the
    snapshot directory, whose referenced areas are the live project's.
    """
    with tempfile.TemporaryDirectory(prefix="replay-bench-") as tmp, \
            snapshot_mod.isolated_project(snap) as project_dir:
        out = Path(tmp) / "result.json"
        cmd = [
            sys.executable, str(WORKER),
            "--tree", str(tree),
            "--state", str(project_dir / "pipeline_data.json"),
            "--step", step,
            "--project-dir", str(project_dir),
            "--declared-project-folder", snap.declared_project_folder,
            "--out", str(out),
        ]
        if llm_authored:
            cmd += ["--llm-authored", ",".join(llm_authored)]
        if qa_feedback:
            cmd += ["--qa-feedback", qa_feedback]
        env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", cwd=str(tree), env=env,
                              check=False)
        if proc.returncode != 0:
            raise RuntimeError(
                f"reconstructing {step} at {tree} failed:\n"
                f"{proc.stderr[-4000:]}")
        with open(out, encoding="utf-8") as fh:
            return json.load(fh)


def _staleness(snap) -> dict:
    """Is this snapshot still what it was, and is the world it points at?

    Run BEFORE a reconstruction it answers "am I about to compare against
    stale state".  Run again AFTER, the same two digests answer a second
    question for free: did anything the bench touched change.  The
    references are the whole of the live project the bench reads - `raw/`,
    `music/`, `assets/`, `pipeline_output/` - so an unmoved listing digest
    on both sides is a MEASUREMENT that no project file was written, not a
    promise that none was.
    """
    report = snap.verify()
    report["torn_at_capture"] = bool(snap.manifest.get("torn"))
    return report


def replay(snapshot_ref: str, step: str, rev: str = trees.WORKTREE,
           store: Path | None = None, cache: Path | None = None,
           llm_authored=None) -> dict:
    snap = snapshot_mod.load(snapshot_ref, store)
    cache = Path(cache or (snap.root.parent / "_trees"))
    tree = trees.tree_for(rev, cache)
    result = _run_worker(tree, snap, step, llm_authored=llm_authored)
    result["revision"] = trees.describe(rev)
    result["snapshot"] = snap.snapshot_id
    result["staleness"] = _staleness(snap)
    result["prompt_tokens"] = tokens.measure(result["prompt"])
    result["context_tokens"] = tokens.measure(result["context"])
    result["context_pipeline_heuristic"] = tokens.pipeline_heuristic(
        result["context"])
    return result


def compare(snapshot_ref: str, step: str, rev_a: str, rev_b: str,
            store: Path | None = None, cache: Path | None = None,
            answer_a: Path | None = None, answer_b: Path | None = None,
            llm_authored=None) -> dict:
    """The same step at two revisions.  Diff the context; diff the answers."""
    snap = snapshot_mod.load(snapshot_ref, store)
    cache = Path(cache or (snap.root.parent / "_trees"))
    left = _run_worker(trees.tree_for(rev_a, cache), snap, step,
                       llm_authored=llm_authored)
    right = _run_worker(trees.tree_for(rev_b, cache), snap, step,
                        llm_authored=llm_authored)

    out = {
        "snapshot": snap.snapshot_id,
        "step_id": step,
        "staleness": _staleness(snap),
        "left": {"revision": trees.describe(rev_a),
                 "context_tokens": tokens.measure(left["context"]),
                 "prompt_tokens": tokens.measure(left["prompt"]),
                 "top_level_keys": left["top_level_keys"]},
        "right": {"revision": trees.describe(rev_b),
                  "context_tokens": tokens.measure(right["context"]),
                  "prompt_tokens": tokens.measure(right["prompt"]),
                  "top_level_keys": right["top_level_keys"]},
        "context_identical": left["context"] == right["context"],
        "prompt_identical": left["prompt"] == right["prompt"],
        "context_section_deltas": diffing.section_deltas(
            left["context"], right["context"]),
        "sections_only_left": sorted(
            set(diffing.split_sections(left["context"]))
            - set(diffing.split_sections(right["context"]))),
        "sections_only_right": sorted(
            set(diffing.split_sections(right["context"]))
            - set(diffing.split_sections(left["context"]))),
        "prompt_diff": ("" if left["prompt"] == right["prompt"]
                        else diffing.unified(left["prompt"], right["prompt"],
                                             rev_a, rev_b)),
        "answers": None,
    }
    if answer_a and answer_b:
        with open(answer_a, encoding="utf-8") as fh:
            a = json.load(fh)
        with open(answer_b, encoding="utf-8") as fh:
            b = json.load(fh)
        rows = diffing.json_answer_diff(a, b)
        out["answers"] = {
            "left_file": str(answer_a), "right_file": str(answer_b),
            "identical": not rows, "differing_paths": rows,
        }
    return out


# A `deterministic_with_llm` step's recorded output has that step's OWN
# LLM answer merged into it, so replaying it off recorded state feeds the
# step its own answer.  Nothing in the state records which keys the model
# wrote - that is the per-attempt recording gap the context audit named -
# but the archived request does, in `expected_schema`.  Read it from there
# rather than guessing, and say so on every row.
def _llm_authored_from_archive(request: dict) -> list:
    # `could_not_determine`, `contradicts_direction` and `value_decisions`
    # are in the schema and are NOT among the step's outputs: all three are
    # split out of the answer before anything validates or records it, so
    # none is ever in the recorded state this is subtracting from.  Naming
    # them here would claim the model authored keys the state has never
    # held.  `value_decisions` is the one that is nonetheless LOAD-BEARING -
    # a post-bridge acts on it - but what the state records is the
    # DECISION, on the step's own output, not the answer.
    from library.tools.decided_value import FIELD as _DECIDED_VALUE_FIELD
    from library.tools.direction_contradiction import FIELD as _CONTRADICTION_FIELD
    from library.tools.undetermined import FIELD as _UNDETERMINED_FIELD
    _split_out = {_UNDETERMINED_FIELD, _CONTRADICTION_FIELD,
                  _DECIDED_VALUE_FIELD}
    try:
        return [o.get("name") for o in json.loads(request.get("expected_schema") or "[]")
                if o.get("name") and o.get("name") not in _split_out]
    except (json.JSONDecodeError, AttributeError, TypeError):
        return []


def verify_archive(snapshot_ref: str, rev: str = trees.WORKTREE,
                   store: Path | None = None, cache: Path | None = None,
                   steps=None, explain: bool = True) -> dict:
    """Reconstruct every archived context and compare it to what was written.

    Two passes over each step, and the difference between them is the whole
    discipline:

    `raw` is the reconstruction with nothing accounted for.
    `explained` subtracts the two causes the bench can name and reproduce -
    a QA-retry block the archive carries because a request file is
    last-write-wins per step, and a step's own LLM answer that the recorded
    state has merged back into its inputs.  Both are read out of the
    archive itself, both are reported per step, and neither is applied
    silently.

    A step that matches only after an explanation is not a pass.  It is a
    named, reproduced reason - which is what the plan asked for.
    """
    snap = snapshot_mod.load(snapshot_ref, store)
    cache = Path(cache or (snap.root.parent / "_trees"))
    tree = trees.tree_for(rev, cache)
    before = _staleness(snap)

    rows = []
    for step in (steps or snap.archived_steps()):
        req_path = snap.archive_dir / f"{step}.json"
        with open(req_path, encoding="utf-8") as fh:
            archived = json.load(fh)
        archived_context = archived.get("context", "")
        _, qa_block = diffing.split_qa_retry(archived_context)

        row = {"step_id": step,
               "archived_bytes": len(archived_context.encode("utf-8"))}
        try:
            raw = _run_worker(tree, snap, step)
        except Exception as exc:  # noqa: BLE001 - reported, not raised
            row.update({"verdict": "ERROR", "error": str(exc)[-1500:]})
            rows.append(row)
            continue

        row["step_type"] = raw["step_type"]
        row["reconstructed_bytes"] = len(raw["context"].encode("utf-8"))
        row["bridge_ran"] = raw["bridge_ran"]
        row["project_folder_substitutions"] = raw["project_folder_substitutions"]
        row["delta_bytes"] = row["reconstructed_bytes"] - row["archived_bytes"]
        row["exact"] = raw["context"] == archived_context
        row["explanations"] = []
        row["verdict"] = "EXACT" if row["exact"] else "DIFFERS"

        if not row["exact"] and explain:
            authored = (_llm_authored_from_archive(archived)
                        if raw["step_type"] == "deterministic_with_llm" else [])
            explained = raw
            if authored or qa_block:
                explained = _run_worker(tree, snap, step,
                                        llm_authored=authored,
                                        qa_feedback=qa_block)
            if qa_block:
                row["explanations"].append(
                    f"archive is a QA RETRY: `present_llm_step` appends "
                    f"{len(qa_block.encode('utf-8'))} B of QA feedback to the "
                    f"context before re-asking, and a request file is "
                    f"last-write-wins per step, so the surviving file is the "
                    f"retry rather than the first attempt")
            if authored:
                row["explanations"].append(
                    f"deterministic_with_llm self-reference: the recorded "
                    f"output of this step carries the keys its own LLM wrote "
                    f"({authored}); replaying off recorded state would feed "
                    f"the step its own answer, so they are withheld")
            row["explained_exact"] = explained["context"] == archived_context
            row["explained_bytes"] = len(explained["context"].encode("utf-8"))
            row["explained_delta_bytes"] = (row["explained_bytes"]
                                            - row["archived_bytes"])
            if row["explained_exact"]:
                row["verdict"] = "EXACT (explained)"
            else:
                row["verdict"] = "DIFFERS"
                row["residual_sections"] = diffing.section_deltas(
                    explained["context"], archived_context)[:6]
        rows.append(row)

    after = _staleness(snap)
    exact = sum(1 for r in rows if r["verdict"] == "EXACT")
    explained = sum(1 for r in rows if r["verdict"] == "EXACT (explained)")
    return {
        "snapshot": snap.snapshot_id,
        "revision": trees.describe(rev),
        "tree": str(tree),
        "staleness": before,
        "staleness_after": after,
        # Not "nothing has drifted since capture" - on a project other
        # workers are running, that is false and harmless.  This is the
        # narrow claim: between the digest taken before these
        # reconstructions and the one taken after, no file under any area
        # the bench reads changed.
        "wrote_nothing": (after["sealed"]
                          and after["reference_digests"]
                          == before["reference_digests"]),
        "tokenizers": tokens.available(),
        "total": len(rows),
        "exact": exact,
        "exact_explained": explained,
        "unaccounted": len(rows) - exact - explained,
        "rows": rows,
    }
