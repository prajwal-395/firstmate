"""Two stages, two ledgers, two lifetimes.

The problem this exists to remove
---------------------------------
``pipeline_data.json`` carried ONE flat ledger, ``steps_completed``,
covering all 28 steps with one lifetime.  Forty minutes of WhisperX
transcription and a two-second creative re-plan shared it, there was no
supported way to re-run a finished step (``--from`` only trims the plan;
the skip-if-finished check still fires), and so the only way to redo
creative work was to move the whole state file aside.
``docs/RUN_001_END_TO_END.md`` records exactly that being done - and that
single move is what paid for the transcription twice.

The fix, declared rather than hardcoded
---------------------------------------
Every step manifest declares its stage in ``classification.stage``:

* ``preflight`` - enrichment of THIS PROJECT'S SOURCE FOOTAGE.  Scan,
  catalog, vision, transcription, prosody, segmentation, OCR.  Expensive,
  and valid until the footage itself changes.
* ``edit`` - everything downstream of a creative decision, from
  ``creative_direction`` to the render.  Cheap to redo and redone often.

``stage_of`` below is where that declaration is ENFORCED - it raises on a
missing or unknown stage, and the runner calls it for every DAG node on
every invocation.  ``library/schema/manifest.schema.json`` lists the field
too, but nothing in the tree loads that file, so do not mistake it for the
gate; ``tests/test_step_ledger.py`` walks every step directory instead.

The two ledgers are separate keys in the state file, so
``reset_stage(state, EDIT, ...)`` is STRUCTURALLY INCAPABLE of touching
the preflight ledger: it never names that key.  That is the whole point,
and ``tests/test_step_ledger.py`` asserts it directly.

Two steps sit where the reader will expect an argument, so both are
recorded here:

* ``validate_sfx_library`` (0.01) is **edit**.  It validates a SHARED
  library resolved from ``PIPELINE_SFX_LIBRARY``, not this project's
  footage - there is no source of this project's to identity-check it
  against, and it is a directory listing plus a FAISS probe.  Putting it
  in the preflight ledger would buy nothing and would let a library that
  has since moved go unnoticed for the life of the project.
* ``music_analysis`` (2.06) is **edit**.  It does enrich an asset, but a
  CHOSEN one: it is downstream of ``music_selection``, which is a
  creative decision.  Preserving it across an edit reset would mean
  keeping the beat grid of the track the reset just discarded.  It is a
  librosa pass over one short track, seconds not minutes.

Naming
------
The pipeline's step ids still read ``step_1_04_...``, because an id is not
prose.  In prose this stage is the PREFLIGHT stage - never "phase 1",
which ``docs/PIPELINE_PLAN.md`` already uses for the quality-work
programme.

Scope
-----
This is not a caching framework and must not become one.  One declared
field, one split ledger, one re-run flag, one identity check.  The
artifacts are already per clip on disk; the only thing that was missing
is the bookkeeping.
"""

import os
from typing import Dict, Iterable, List, Optional, Tuple

PREFLIGHT = "preflight"
EDIT = "edit"
STAGES = (PREFLIGHT, EDIT)

# Where each stage's ledger lives in pipeline_data.json.
LEDGER_KEY: Dict[str, str] = {
    PREFLIGHT: "preflight_completed",
    EDIT: "edit_completed",
}

# The single flat ledger this replaced.  Read for migration only; never
# written again.
LEGACY_LEDGER_KEY = "steps_completed"

# The project-level record of the footage the preflight stage last saw.
SOURCE_FINGERPRINTS_KEY = "source_fingerprints"


class LedgerError(ValueError):
    """A step whose stage cannot be determined, or a bad re-run target."""


# ── The declared field ───────────────────────────────────────────────

def stage_of(manifest: Optional[dict], node_id: str = "") -> str:
    """The stage a step manifest declares.

    Raises rather than defaulting.  A step with no declared stage is a
    step nobody decided about, and guessing puts it on the wrong side of
    a split whose entire value is that it cannot be crossed by accident.
    """
    classification = (manifest or {}).get("classification") or {}
    stage = classification.get("stage")
    if not stage:
        raise LedgerError(
            f"Step '{node_id or (manifest or {}).get('id', '?')}' declares no "
            f"classification.stage. Every step must declare one of "
            f"{list(STAGES)} - see library/tools/step_ledger.py."
        )
    if stage not in STAGES:
        raise LedgerError(
            f"Step '{node_id or (manifest or {}).get('id', '?')}' declares "
            f"unknown stage {stage!r}. Known stages: {list(STAGES)}."
        )
    return stage


def per_clip_artifacts(manifest: Optional[dict]) -> List[str]:
    """Project-relative artifact patterns a step writes once per clip.

    Patterns may use ``{clip_id}`` and ``{stem}``.  A step that declares
    them can be re-run for one clip: the runner deletes exactly those
    files and the step's own "already on disk?" check recomputes exactly
    that clip.  A step that declares none is re-run whole.
    """
    classification = (manifest or {}).get("classification") or {}
    patterns = classification.get("per_clip_artifacts") or []
    if not isinstance(patterns, list) or not all(isinstance(p, str) for p in patterns):
        raise LedgerError(
            f"Step '{(manifest or {}).get('id', '?')}' declares "
            f"per_clip_artifacts that is not a list of strings."
        )
    return list(patterns)


def artifact_paths(project_folder: str, patterns: Iterable[str],
                   clip_id: str, stem: str = "") -> List[str]:
    """Concrete paths for one clip's declared artifacts."""
    out = []
    for pattern in patterns:
        rendered = pattern.format(clip_id=clip_id, stem=stem)
        out.append(os.path.join(project_folder, rendered))
    return out


# ── The split ledger ─────────────────────────────────────────────────

def ledger(state: dict, stage: str) -> dict:
    if stage not in STAGES:
        raise LedgerError(f"Unknown stage {stage!r}. Known: {list(STAGES)}.")
    return state.setdefault(LEDGER_KEY[stage], {})


def all_completed(state: dict) -> dict:
    """Merged read-only view of both ledgers, for reporting.

    Tolerates a state file written before the split - a fixture, or a
    project that has not been through ``migrate_legacy`` yet - so that
    every dashboard and status reader keeps working.
    """
    merged = dict(state.get(LEGACY_LEDGER_KEY) or {})
    for stage in STAGES:
        merged.update(state.get(LEDGER_KEY[stage]) or {})
    return merged


def is_completed(state: dict, node_id: str) -> bool:
    return node_id in all_completed(state)


def record(state: dict, stage: str, node_id: str, entry: dict) -> None:
    """Write a step's completion into its own stage's ledger."""
    ledger(state, stage)[node_id] = entry
    # A step recorded in one stage must not linger in the other, which is
    # how a re-classified step would end up immortal.
    for other in STAGES:
        if other != stage:
            state.get(LEDGER_KEY[other], {}).pop(node_id, None)
    state.get(LEGACY_LEDGER_KEY, {}).pop(node_id, None)


def forget(state: dict, node_id: str) -> None:
    """Drop a step from whichever ledger holds it.

    Used when a step fails.  Note what this does NOT do: it does not
    touch the step's per-clip artifacts.  A transcription that failed on
    clip 12 of 17 keeps the eleven indices it already wrote, so the
    re-run costs the remainder and not the lot.
    """
    for stage in STAGES:
        state.get(LEDGER_KEY[stage], {}).pop(node_id, None)
    state.get(LEGACY_LEDGER_KEY, {}).pop(node_id, None)


def migrate_legacy(state: dict, stage_by_node: Dict[str, str]) -> List[str]:
    """Fold a pre-split ``steps_completed`` into the two ledgers.

    Runs once per project, at load.  An entry for a step the current DAG
    does not contain is LEFT where it is rather than guessed at or
    deleted: it is somebody's record of work that happened, ``all_completed``
    still reports it, and a pipeline that regains that step picks it up on
    the next load.  Returns the migrated node ids.
    """
    legacy = state.get(LEGACY_LEDGER_KEY)
    if not legacy:
        state.pop(LEGACY_LEDGER_KEY, None)
        return []

    migrated = []
    for node_id, entry in list(legacy.items()):
        stage = stage_by_node.get(node_id)
        if not stage:
            continue
        ledger(state, stage)[node_id] = entry
        legacy.pop(node_id, None)
        migrated.append(node_id)
    if not legacy:
        state.pop(LEGACY_LEDGER_KEY, None)
    return sorted(migrated)


def reset_stage(state: dict, stage: str,
                stage_by_node: Dict[str, str]) -> List[str]:
    """Forget everything one stage has done. The other stage is untouched.

    This function names exactly one ledger key - ``LEDGER_KEY[stage]`` -
    and pops exactly the step outputs whose own declared stage matches.
    Resetting the edit run therefore cannot discard enrichment: there is
    no code path here that reaches the preflight ledger, the preflight
    outputs or the per-clip artifacts on disk.
    """
    if stage not in STAGES:
        raise LedgerError(f"Unknown stage {stage!r}. Known: {list(STAGES)}.")
    migrate_legacy(state, stage_by_node)

    cleared = sorted(node_id for node_id, s in stage_by_node.items()
                     if s == stage)

    stage_ledger = state.setdefault(LEDGER_KEY[stage], {})
    for node_id in cleared:
        stage_ledger.pop(node_id, None)
        state.get("step_outputs", {}).pop(node_id, None)
        state.get("step_errors", {}).pop(node_id, None)
    failed = state.get("failed_steps")
    if failed:
        state["failed_steps"] = [s for s in failed if s not in set(cleared)]
    return cleared


# ── The re-run flag ──────────────────────────────────────────────────

def parse_rerun_target(target: str, known_steps: Iterable[str]) -> Tuple[str, str]:
    """Parse one ``--rerun`` value.

    Four forms, and an unknown one raises rather than being ignored - a
    typo that silently re-runs nothing is how an operator concludes the
    flag does not work:

        preflight             -> ("stage", "preflight")
        edit                  -> ("stage", "edit")
        temporal_index        -> ("step",  "temporal_index")
        temporal_index:clip_007 -> ("clip", "temporal_index:clip_007")
    """
    target = (target or "").strip()
    if not target:
        raise LedgerError("--rerun needs a target: a stage, a step, or step:clip_id.")
    if target in STAGES:
        return ("stage", target)

    known = set(known_steps)
    if ":" in target:
        step_id, _, clip_id = target.partition(":")
        if step_id not in known:
            raise LedgerError(
                f"--rerun {target!r}: no step named {step_id!r} in this pipeline."
            )
        if not clip_id:
            raise LedgerError(
                f"--rerun {target!r}: expected step:clip_id, e.g. "
                f"{step_id}:clip_007."
            )
        return ("clip", f"{step_id}:{clip_id}")

    if target not in known:
        raise LedgerError(
            f"--rerun {target!r}: not a stage {list(STAGES)} and not a step in "
            f"this pipeline."
        )
    return ("step", target)
