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
every invocation.  (A ``library/schema/manifest.schema.json`` once listed
the field too, but nothing ever loaded it, so it was deleted rather than
mistaken for the gate.)  ``tests/unit/context/test_ledgers.py`` walks every step
directory instead.

The two ledgers are separate keys in the state file, so
``reset_stage(state, EDIT, ...)`` is STRUCTURALLY INCAPABLE of touching
the preflight ledger: it never names that key.  That is the whole point,
and ``tests/unit/context/test_ledgers.py`` asserts it directly.

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


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

One enumeration: `library/tools/step_ledger.py`.
- Every step manifest declares `classification.stage`. An undeclared or unknown stage raises rather than defaulting.
- **preflight** is enrichment of THIS PROJECT'S SOURCE FOOTAGE - scan, catalog, vision, transcription, prosody, segmentation, OCR - recorded in `preflight_completed`.
- **edit** is everything downstream of a creative decision, recorded in `edit_completed`.
- `validate_sfx_library` (0.01) is edit: it validates a SHARED library, not this project's footage.
- `music_analysis` (2.06) is edit: it enriches a CHOSEN asset, and the choice is what an edit reset discards.
- Per-clip granularity works because each step declares where its per-clip artifacts live, in `classification.per_clip_artifacts`.  **Add a per-clip artifact and you must declare it**, or nothing can invalidate it.
- Preflight is skipped once done, and that is safe because identity is checked. `library/tools/footage_identity.py` fingerprints each clip by size plus a digest of its first and last mebibyte - not a whole-file hash and NOT mtime - against `source_fingerprints` in the state file.
- **The identity check watches the FOOTAGE and the CODE.** `library/tools/code_identity.py` hashes each preflight step's source files (.py, .json) against `preflight_code_hashes` in the state file, PLUS the shared implementation files the step executes, declared per step in `code_identity.STEP_IMPLEMENTATION_DEPS`. A fix to a preflight step - or to the shared measurement code behind it - invalidates its cached output on the next run.
- **A project's own declarations do NOT travel in a preflight cache.** `project_config` carries only `brand_registry.PROJECT_CONFIG_KEYS`, and `pipeline.framing_intent`, `pipeline.subtitle_typography` and the rest of the `pipeline:` block are read straight off `project.yaml` at the point of use, every run.  The mechanism is one declared field, one split ledger, one re-run flag, one identity check.
- The per-clip index lives with the PROJECT: it resolves from `project_folder`, not the runner's CWD, and reuses any per-clip file already there instead of re-transcribing it.
"""

import os
import re
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

# The project-level record of the code that produced each preflight cache.
# {node_id: hash_hex} - see library/tools/code_identity.py.
CODE_FINGERPRINTS_KEY = "preflight_code_hashes"


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


# A pattern names the AREA it lives in, not the directory.
#
# It used to spell the path out - `pipeline_output/temporal_index/
# {clip_id}.json`. When the layout moved those directories, two of these
# declarations were left behind pointing at `raw/analysis/`, so
# `--rerun semantic_analysis:clip_007` deleted nothing and silently
# re-ran nothing. A declaration that can go stale is the failure mode the
# layout owner exists to remove, so the prefix comes from the owner and
# only the filename is the step's to state.
_AREA_TOKEN = re.compile(r"^\{area:([a-z_]+)\}/")


def artifact_paths(project_folder: str, patterns: Iterable[str],
                   clip_id: str, stem: str = "") -> List[str]:
    """Concrete paths for one clip's declared artifacts."""
    from library.tools.project_layout import Area, ProjectLayout

    out = []
    layout = None
    for pattern in patterns:
        match = _AREA_TOKEN.match(pattern)
        if match:
            name = match.group(1)
            try:
                area = Area(name)
            except ValueError:
                raise LedgerError(
                    f"per_clip_artifacts names area {name!r}, which is not a "
                    f"row in project_layout.AREAS."
                ) from None
            layout = layout or ProjectLayout(project_folder)
            rendered = pattern[match.end():].format(clip_id=clip_id, stem=stem)
            out.append(str(layout.read_dir(area) / rendered))
            continue
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


def completed_capabilities(state: dict) -> set:
    """The capability ids the ledgers record as finished.

    The ledgers stay keyed by node, because the runner runs nodes; this
    is the capability-keyed reading of them.  An entry that names its
    `operation` was written by a path that ran ONE capability
    (`footage_intelligence`) and completes exactly that one.  An entry
    without one is a node the runner ran end to end, which completes
    every capability of the node that produces something at project
    scope - never a region splice, a touch-up or a segment unit, which a
    node run does not execute.
    """
    from library.tools import dag_adapter
    from library.tools.scope import PROJECT

    done = set()
    for node_id, entry in all_completed(state).items():
        named = entry.get("operation") if isinstance(entry, dict) else None
        if named:
            done.add(named)
            continue
        done.update(op.name for op in dag_adapter.capabilities_at(node_id)
                    if op.produces and PROJECT in op.scopes
                    and not op.caller_supplied)
    return done


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
        from library.tools import capability_outputs
        capability_outputs.forget(state, node_id)
        state.get("step_errors", {}).pop(node_id, None)
    failed = state.get("failed_steps")
    if failed:
        state["failed_steps"] = [s for s in failed if s not in set(cleared)]
    return cleared


# ── The re-run flag ──────────────────────────────────────────────────

def parse_rerun_target(target: str, known_steps: Iterable[str]) -> Tuple[str, str]:
    """Parse one ``--rerun`` value.

    FIVE forms, and an unknown one raises rather than being ignored - a
    typo that silently re-runs nothing is how an operator concludes the
    flag does not work:

        preflight                 -> ("stage",  "preflight")
        edit                      -> ("stage",  "edit")
        temporal_index            -> ("step",   "temporal_index")
        temporal_index:clip_007   -> ("clip",   "temporal_index:clip_007")
        plan_subtitles@45.0-72.0  -> ("region", "plan_subtitles@45.0-72.0")

    The region form is increment 5's, and `@` rather than `:` because
    the two axes are different: `:clip_007` names a piece of the
    FOOTAGE, `@45.0-72.0` names an interval of the TIMELINE, and one
    clip supplies several non-adjacent stretches of it - on project 001,
    clip_011 supplies six blocks spread over 44s of a 56.6s timeline.
    Reusing `:` would have made a footage address and a timeline address
    indistinguishable at a glance.

    The span is validated HERE by handing it to `region.parse`, so a
    malformed interval is refused when the flag is read rather than
    forty minutes into a run. This module deliberately does not become a
    second reader of an interval - `library/tools/region.py` is the one
    owner, and the timeline is part of that type.
    """
    target = (target or "").strip()
    if not target:
        raise LedgerError("--rerun needs a target: a stage, a step, or step:clip_id.")
    if target in STAGES:
        return ("stage", target)

    known = set(known_steps)
    if "@" in target:
        step_id, _, span = target.partition("@")
        if step_id not in known:
            raise LedgerError(
                f"--rerun {target!r}: no step named {step_id!r} in this "
                f"pipeline."
            )
        from library.tools.region import parse as parse_region
        try:
            parse_region(span)
        except ValueError as exc:
            raise LedgerError(
                f"--rerun {target!r}: {exc}"
            ) from None
        return ("region", f"{step_id}@{span}")

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
