"""ONE ownership and retention policy for the artifacts a project holds.

`project_layout.Kind` says what KIND a directory is. This module says
what happens to the files IN it: who owns them, whether a re-run brings
them back, and whether a cleanup lane may name them.

The rule
--------
**A cleanup may name a file only when its AREA says the file is
disposable.** The area is the one thing every lane agrees on, because
`library/tools/project_layout.py` is the single owner of the layout.

The alternative - each lane deciding from the file's name, its age, or
the `retention_class` a record happens to carry - is how a delivered
render in `exports/` ends up nominated for deletion because it was
registered with the wrong class. The area is the fact; the record's
class is one writer's opinion about it. Where they disagree the MORE
protective of the two wins, and a file whose area cannot be resolved is
protected.

The vocabulary
--------------
One retention class per file. `RETENTION_CLASSES` is the complete set,
and it is the set every lane reads:

===================  ==========================================  ==========
class               meaning                                     cleanable
===================  ==========================================  ==========
``retained``        the captain's own material, kept by policy    no
``protected``       referenced, recoverable, or a record of what   no
                    happened; deleting it loses a fact
``captured``        the captain's material the pipeline itself     no
                    recorded, and cannot re-read
``deliverable``     what the run is FOR                           no
``staging``         in flight; a run may still be writing it       no
``shared_reusable`` source observations reused across requests     no
``intermediate``    computed work a re-run reproduces             yes
``cache``           recomputable work reused across requests      yes
``run_private``     owned by exactly one run                      yes
``temporary``       scratch with no reader after its writer        yes
===================  ==========================================  ==========

`NEVER_CLEANABLE` is the set a cleanup lane may not name, in any
class and under any plan. `REGENERABLE_CLASSES` is the set a
rebuild brings back; `AREA_POLICY` records HOW, per area, so a
"recoverable" file is recoverable by a command an operator can read.

What already existed
---------------------
- `library/tools/project_layout.py` owns the layout, the `Kind` of each
  area, and the guard that refuses a write into an input area.
- `library/tools/artifact_lifecycle.py` owns the ownership record, the
  lifecycle states, and the guarded delete. It stored whatever
  `retention_class` a writer handed it, and never checked it against
  the area the file actually sits in.
- `library/tools/storage_lifecycle.py` nominates purge candidates from
  the registered records, and excluded three classes by name
  (`retained`, `protected`, `staging`).
- `library/tools/retention.py` owns the project-side purge and its
  reachability rules.
- `library/tools/cache_lifecycle.py` owns the machine-wide cache
  budget.

This module is the one place those four agree on what a class MEANS,
and the one place the area is consulted. It replaces the three-class
name list in `storage_lifecycle` and the free-text `retention_class`
strings the writers used, without adding a cleanup lane of its own.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from library.tools.project_layout import AREAS, Area, Kind

# ── The vocabulary ──────────────────────────────────────────────────────

RETAINED = "retained"
PROTECTED = "protected"
CAPTURED = "captured"
DELIVERABLE = "deliverable"
STAGING = "staging"
SHARED_REUSABLE = "shared_reusable"
INTERMEDIATE = "intermediate"
CACHE = "cache"
RUN_PRIVATE = "run_private"
TEMPORARY = "temporary"

RETENTION_CLASSES = frozenset({
    RETAINED, PROTECTED, CAPTURED, DELIVERABLE, STAGING,
    SHARED_REUSABLE, INTERMEDIATE, CACHE, RUN_PRIVATE, TEMPORARY,
})
"""COMPLETE. A class that is not here is not a class, and a record
carrying one is treated as PROTECTED rather than as a lesser class."""

NEVER_CLEANABLE = frozenset({
    RETAINED, PROTECTED, CAPTURED, DELIVERABLE, STAGING, SHARED_REUSABLE,
})
"""A cleanup lane may not name a file of one of these classes, in any
class, under any plan, and whatever the record says about it."""

REGENERABLE_CLASSES = frozenset({INTERMEDIATE, CACHE, RUN_PRIVATE, TEMPORARY})
"""A re-run brings these back. `TEMPORARY` is regenerable in the sense
that nothing reads it: a re-run does not need to restore it."""

_PROTECTIVENESS = {
    TEMPORARY: 0,
    CACHE: 1,
    RUN_PRIVATE: 1,
    INTERMEDIATE: 2,
    SHARED_REUSABLE: 3,
    STAGING: 4,
    RETAINED: 5,
    PROTECTED: 5,
    DELIVERABLE: 6,
    CAPTURED: 6,
}
"""How protective a class is. The more protective of two classes wins,
so a writer cannot lower a class the area already fixed."""


def is_never_cleanable(retention_class: str) -> bool:
    """Whether a cleanup lane may never name a file of this class."""
    return retention_class in NEVER_CLEANABLE


def more_protective(first: str, second: str) -> str:
    """Return the more protective of two retention classes."""
    unknown = [name for name in (first, second) if name not in RETENTION_CLASSES]
    if unknown:
        raise ValueError(f"unknown retention class: {unknown[0]}")
    if _PROTECTIVENESS[first] >= _PROTECTIVENESS[second]:
        return first
    return second


# ── The area table ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class Policy:
    """What the layout says about the files in one area."""

    retention_class: str
    regenerable: bool
    regeneration_method: str
    why: str

    @property
    def protected(self) -> bool:
        return is_never_cleanable(self.retention_class)


def _rerun(node_id: str) -> str:
    return f"manage_project.py run <project> --only {node_id} --rerun {node_id}"


_REGENERATION_OVERRIDES = {
    Area.REEL_SAFE_ZONE_GUIDES: (
        "manage_project.py safe-zones <project> --overlay combined"),
    Area.PREVIEWS: "ren preview <project>",
}
"""The areas a step re-run does not bring back, and what does."""


AREA_POLICY: dict[Area, Policy] = {
    # The captain's own material. Read-only, never recomputed, never a
    # candidate: deleting one loses unrecoverable work.
    Area.PROJECT_ROOT: Policy(
        RETAINED, False, "",
        "the project itself: project.yaml and the run's own state"),
    Area.RAW: Policy(
        RETAINED, False, "", "source footage as the captain shot it"),
    Area.MUSIC: Policy(
        RETAINED, False, "", "music the captain put here by hand"),
    Area.ASSETS: Policy(
        RETAINED, False, "", "project-owned artwork and presets"),
    Area.BRAND_ASSETS: Policy(
        RETAINED, False, "", "fonts and logos this series owns"),
    Area.COMPOSITIONS: Policy(
        RETAINED, False, "", "project-owned compositions"),
    Area.EXTERNAL_STATE: Policy(
        RETAINED, False, "", "state the pipeline cannot re-read"),
    Area.EXTERNAL_DECLARATIONS: Policy(
        RETAINED, False, "", "the project's own declarations"),
    Area.RUN_PROFILES: Policy(
        RETAINED, False, "", "declared run configurations"),
    Area.CONTEXT: Policy(
        RETAINED, False, "", "the captain's context for this project"),
    Area.SUBTITLE_PLANS: Policy(
        RETAINED, False, "", "the project's own subtitle plans"),
    Area.SUBTITLE_OVERLAYS: Policy(
        RETAINED, False, "", "the project's own overlay artwork"),
    Area.LEARNED_CONTEXT: Policy(
        CAPTURED, False, "",
        "learned context the captain recorded; irreproducible"),

    # What the run is FOR.
    Area.EXPORTS: Policy(
        DELIVERABLE, False, "",
        "the delivered render and its receipts; the run exists to "
        "produce it"),

    # The captain's material the pipeline itself recorded.
    Area.MARKER_FEEDBACK: Policy(
        CAPTURED, False, "",
        "a note typed onto a Resolve timeline: irreproducible, and a "
        "re-run must never delete it"),
    Area.VOX_TEST_RENDERS: Policy(
        CAPTURED, False, "",
        "a captured test render the pipeline cannot re-read"),

    # Facts about what happened. A re-run cannot reconstruct them.
    Area.REVIEW: Policy(
        PROTECTED, False, "",
        "proposals, sign-offs, judgements and phase logs: the record of "
        "the captain's decisions"),
    Area.EDIT_OUTCOMES: Policy(
        PROTECTED, False, "",
        "what the captain did with a generated edit - took it, changed it, "
        "and whether he called the change a fault or a matter of taste; a "
        "re-run cannot reconstruct a judgement the captain made"),
    Area.GATES: Policy(
        PROTECTED, False, "",
        "review gates: a decision that was taken, not a computation"),
    Area.PROVENANCE: Policy(
        PROTECTED, False, "",
        "which run wrote which file, and from what"),
    Area.MIGRATIONS: Policy(
        PROTECTED, False, "",
        "the record of a layout migration; the layout depends on it"),
    Area.RUN_STATE: Policy(
        PROTECTED, False, "",
        "the runner's account of itself"),
    Area.UNSORTED: Policy(
        PROTECTED, False, "",
        "purpose undetermined: an admitted unknown, never a guess and "
        "never a deletion"),
    Area.QUARANTINE: Policy(
        TEMPORARY, False, "",
        "moved aside by a sweep; the retention lane's grace period "
        "decides when it goes"),
    Area.LOGS: Policy(
        PROTECTED, False, "",
        "the event log IS the ownership record, and the performance "
        "ledger is the measurement of the run"),

    # Source intelligence, kept across requests on purpose.
    Area.FOOTAGE_MEMORY: Policy(
        SHARED_REUSABLE, False, "",
        "source observations reused across editing requests; recomputing "
        "them is the cost this lane exists to avoid"),

    # In flight.
    Area.SCRATCH: Policy(
        TEMPORARY, True, "",
        "working files with no reader after the step that wrote them"),

    # Computed work a re-run reproduces.
    Area.OUTPUT_ROOT: Policy(
        INTERMEDIATE, True, "", "the pipeline's own output root"),
    Area.STEPS_ROOT: Policy(
        INTERMEDIATE, True, "", "one directory per step that leaves "
        "files behind"),
    Area.VISION_ANALYSIS: Policy(
        INTERMEDIATE, True, "",
        "the v3 vision observations"),
    Area.TEMPORAL_INDEX: Policy(
        INTERMEDIATE, True, "",
        "the cache: deleting a file here is what makes that clip get "
        "re-transcribed"),
    Area.AUDIO_CACHE: Policy(
        INTERMEDIATE, True, "",
        "extracted audio at 16 kHz mono"),
    Area.PROSODY: Policy(
        INTERMEDIATE, True, "",
        "the prosody measurement"),
    Area.SEGMENTATION: Policy(
        INTERMEDIATE, True, "",
        "the subject masks"),
    Area.OCR: Policy(
        INTERMEDIATE, True, "",
        "the on-screen text"),
    Area.ACQUIRED_MEDIA: Policy(
        INTERMEDIATE, True, "",
        "the downloaded tracks a chosen track came from"),
    Area.MUSIC_ANALYSIS: Policy(
        INTERMEDIATE, True, "",
        "the music measurement"),
    Area.FOOTAGE_ANALYSIS: Policy(
        INTERMEDIATE, True, "",
        "the footage analysis"),
    Area.WINDOW_FRAMES: Policy(
        INTERMEDIATE, True, "",
        "the frames the b-roll step looked at"),
    Area.ROUGHCUT_FRAMES: Policy(
        INTERMEDIATE, True, "",
        "the frames the rough-cut review looked at"),
    Area.REEL_CANDIDATE_DIAGNOSTICS: Policy(
        INTERMEDIATE, True, "",
        "the reel candidate diagnostics"),
    Area.SFX_CATALOGUE: Policy(
        INTERMEDIATE, True, "",
        "the sfx catalogue"),
    Area.SUBTITLE_SEGMENTS: Policy(
        INTERMEDIATE, True, "",
        "the rendered caption segments"),
    Area.MOTION_GRAPHICS_SEGMENTS: Policy(
        INTERMEDIATE, True, "",
        "the rendered motion graphics"),
    Area.TIMED_TEXT_SEGMENTS: Policy(
        INTERMEDIATE, True, "",
        "the rendered timed text"),
    Area.ASSEMBLY_MANIFEST: Policy(
        INTERMEDIATE, True, "",
        "the manifest that drives the final render"),
    Area.FUSION_COMPS: Policy(
        INTERMEDIATE, True, "",
        "the Fusion compositions"),
    Area.CARRIERS: Policy(
        INTERMEDIATE, True, "",
        "the ProRes carriers"),
    Area.TIMELINE_INTERCHANGE: Policy(
        INTERMEDIATE, True, "",
        "the timeline interchange export"),
    Area.QA_FRAMES: Policy(
        INTERMEDIATE, True, "",
        "the frames the QA step looked at"),
    Area.GATE_STILLS: Policy(
        INTERMEDIATE, True, "",
        "the stills a review gate showed"),
    Area.SHOT_STILLS: Policy(
        INTERMEDIATE, True, "",
        "the stills the grade was judged on"),
    Area.VFX_STILLS: Policy(
        INTERMEDIATE, True, "",
        "the stills the vfx plan was judged on"),
    Area.PREVIEWS: Policy(
        INTERMEDIATE, True, "",
        "the review previews; a re-render brings them back"),
    Area.LLM_REQUESTS: Policy(
        INTERMEDIATE, True, "",
        "what the model was asked"),
    Area.LLM_RESPONSES: Policy(
        INTERMEDIATE, True, "",
        "what the model answered"),
    Area.LLM_RESPONSES_BAK: Policy(
        TEMPORARY, True, "",
        "the previous answers, kept only while a newer one exists"),
    Area.REASONING: Policy(
        INTERMEDIATE, True, "",
        "the model's reasoning notes"),
    Area.RUN_ARCHIVES: Policy(
        INTERMEDIATE, True, "",
        "an archived run's own outputs"),
    Area.BACKUPS: Policy(
        INTERMEDIATE, True, "",
        "bounded automatic copies of the run state"),

    # The reel build's placed files. A timeline places them, so the
    # reachability rules protect the ones in flight; they are still
    # regenerable, and `retention.REEL_REGENERATE` names the command.
    Area.REEL_FRAME_OVERLAYS: Policy(
        INTERMEDIATE, True, "",
        "the frame overlays a reel timeline places"),
    Area.REEL_POST_HEADERS: Policy(
        INTERMEDIATE, True, "",
        "the post headers a reel timeline places"),
    Area.REEL_SAFE_ZONE_GUIDES: Policy(
        INTERMEDIATE, True, "",
        "the safe-zone guides a reel timeline places"),
    Area.REEL_CARDS: Policy(
        INTERMEDIATE, True, "",
        "the cards a reel timeline places"),
}
"""COMPLETE: one row per area in `project_layout.AREAS`. A test asserts
the two tables have the same members, so a new area cannot arrive
without a policy.

The regeneration method is DERIVED, not written here: an area whose
step produced it comes back from that step's re-run, and the two areas
whose files no step owns name their own command in
`_REGENERATION_OVERRIDES`. Writing the method out by hand would let it
drift away from the step that owns the files."""


def _derivation_method(area: Area) -> str:
    override = _REGENERATION_OVERRIDES.get(area)
    if override:
        return override
    step = AREAS[area].step
    return _rerun(step) if step else ""


AREA_POLICY = {
    area: Policy(policy.retention_class, policy.regenerable,
                 _derivation_method(area), policy.why)
    for area, policy in AREA_POLICY.items()
}


def _assert_table_is_complete() -> None:
    missing = [area.value for area in AREAS if area not in AREA_POLICY]
    extra = [area.value for area in AREA_POLICY if area not in AREAS]
    if missing or extra:
        raise ValueError(
            "the area retention policy is not complete: "
            f"missing={missing} unknown={extra}")


_assert_table_is_complete()


def policy_for_area(area: Area) -> Policy:
    """The policy for one area. Every area has one."""
    try:
        return AREA_POLICY[Area(area)]
    except KeyError:  # pragma: no cover - the table is asserted complete
        raise ValueError(f"no retention policy for area: {area}") from None


def area_for_path(project_folder: str | os.PathLike, path: str | os.PathLike) -> Area | None:
    """The area a path sits in, or None when it sits in no area.

    The longest matching area wins, which is how `pipeline_output/steps`
    beats `pipeline_output` and a step's own directory beats the step
    root. A path outside the project resolves to no area, and a path at
    the project root resolves to the root area.
    """
    root = Path(os.path.abspath(str(project_folder)))
    candidate = Path(os.path.abspath(str(path)))
    try:
        relative = candidate.relative_to(root)
    except ValueError:
        return None
    relative_posix = relative.as_posix()
    best: tuple[int, Area] | None = None
    for area, spec in AREAS.items():
        base = spec.relpath
        if base == ".":
            matches = relative_posix in ("", ".")
        else:
            matches = (relative_posix == base
                       or relative_posix.startswith(base + "/"))
        if not matches:
            continue
        depth = base.count("/") + 1
        if best is None or depth > best[0]:
            best = (depth, area)
    return best[1] if best else None


def policy_for_path(project_folder: str | os.PathLike,
                    path: str | os.PathLike) -> Policy:
    """The policy for a path, refusing to guess.

    A path in no named area is PROTECTED: the layout is the fact, and an
    unnamed place is an unknown, never a lesser class.
    """
    area = area_for_path(project_folder, path)
    if area is None:
        return Policy(
            PROTECTED, False, "",
            "the path is in no named area of the project layout, so its "
            "ownership cannot be established")
    return policy_for_area(area)


def is_inside_project(project_folder: str | os.PathLike,
                      path: str | os.PathLike) -> bool:
    """Whether a path sits inside the project folder at all."""
    root = Path(os.path.abspath(str(project_folder)))
    candidate = Path(os.path.abspath(str(path)))
    try:
        candidate.relative_to(root)
    except ValueError:
        return False
    return True


def effective_class(project_folder: str | os.PathLike,
                    path: str | os.PathLike,
                    recorded_class: str = "") -> str:
    """The class a cleanup must judge a file by.

    Inside the project the AREA and the RECORD are two readings of the
    same fact, and the more protective of the two wins: a writer cannot
    lower a class the area already fixed, and a record carrying an
    unknown class is treated as PROTECTED, because a class nobody can
    name is not evidence of a lesser one.

    A file inside the project that sits in NO named area is PROTECTED.
    The project root holds `project.yaml`, `pipeline_data.json` and
    `pipeline_run.json`; `project_layout.assert_writable` already
    refuses a write there, so a file that reached the root is either the
    captain's or a mistake, and neither is a cleanup candidate.

    OUTSIDE the project there is no area to read. The record is then the
    only reading there is, and the lanes that clean outside a project -
    the qualification broker workspace, the machine data home - carry
    their own root and git guards. An unrecorded class is still
    PROTECTED.
    """
    area = area_for_path(project_folder, path)
    if area is None:
        if is_inside_project(project_folder, path):
            return PROTECTED
        if not recorded_class or recorded_class not in RETENTION_CLASSES:
            return PROTECTED
        return recorded_class
    area_class = policy_for_area(area).retention_class
    if not recorded_class:
        return area_class
    if recorded_class not in RETENTION_CLASSES:
        return more_protective(area_class, PROTECTED)
    return more_protective(area_class, recorded_class)


def is_protected_path(project_folder: str | os.PathLike,
                      path: str | os.PathLike,
                      recorded_class: str = "") -> bool:
    """Whether no cleanup lane may name this path."""
    return is_never_cleanable(
        effective_class(project_folder, path, recorded_class))


def regenerable_path(project_folder: str | os.PathLike,
                     path: str | os.PathLike,
                     recorded_class: str = "") -> tuple[bool, str]:
    """Whether a re-run brings this path back, and by what command."""
    area = area_for_path(project_folder, path)
    if area is None:
        return False, ""
    policy = policy_for_area(area)
    recorded = recorded_class in REGENERABLE_CLASSES
    if policy.regenerable:
        return True, policy.regeneration_method
    return recorded, ""


def kind_is_never_writable(kind: Kind) -> bool:
    """Whether a kind may not be written to at all.

    `project_layout.WRITABLE_KINDS` owns that answer; this is the same
    fact under the name the retention lanes use.
    """
    from library.tools.project_layout import WRITABLE_KINDS

    return kind not in WRITABLE_KINDS


__all__ = [
    "AREA_POLICY",
    "CACHE",
    "CAPTURED",
    "DELIVERABLE",
    "INTERMEDIATE",
    "NEVER_CLEANABLE",
    "Policy",
    "PROTECTED",
    "REGENERABLE_CLASSES",
    "RETAINED",
    "RETENTION_CLASSES",
    "RUN_PRIVATE",
    "SHARED_REUSABLE",
    "STAGING",
    "TEMPORARY",
    "area_for_path",
    "effective_class",
    "is_inside_project",
    "is_never_cleanable",
    "is_protected_path",
    "kind_is_never_writable",
    "more_protective",
    "policy_for_area",
    "policy_for_path",
    "regenerable_path",
]
