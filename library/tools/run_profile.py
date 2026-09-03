"""One enumeration for a NAMED run configuration: which steps fire, and
where the run stops for the captain to look at it.

The captain's ask, 2026-08-30
-----------------------------
"what if i want to setup specific breakpoints and such for a given run
and/or enable/disable specific steps because they are not needed (ex: a
podcast may not need anything but colorgrading and transitions after the
rough cut, and it's creative direction is also not needed because it just
needs to cut silences, and if this is how i want to have it, then that's
how it should be able to run and i should be able to configure it as
such)".

The capability that was missing is not selection - `run_scope` has done
that since #250.  It is that a selection had to be written in PYTHON:
`run_scope.TARGETS` is a dict in a source file, so "a podcast" was a code
change and a pull request.  A profile is the same statement written as
DATA, in a file the captain owns.

What a profile is NOT
---------------------
It is not a second selection mechanism.  A profile carries the same four
words `run_scope.Selection` carries and hands them straight to
`run_scope.resolve`, so every guarantee that module makes - hard and soft
edges read off the manifests, a prerequisite asked of STATE, and the
refusal arriving BEFORE the run starts - applies to a declared profile
exactly as it applies to `--target rough_cut_subtitles`.  A profile
cannot express anything `run_scope` would refuse, because a profile is
not consulted about what a selection means.  It only says which words to
pass.

Where a profile lives, and why in two places
--------------------------------------------
The captain's example is a KIND of run - "a podcast" - not one project's
setting, so a profile that could only live inside one project.yaml would
miss the ask.  Two directories, and a name resolves in this order:

* ``<project>/profiles/<name>.yaml`` - the PROJECT's own.  A run shape
  that is true of this series and nobody else.  `Kind.INPUT`, so the
  pipeline reads it and can never write it.
* ``library/profiles/<name>.yaml`` - the ENGINE's.  A run shape that
  survives being handed another series' footage, which is section 14's
  own test for what belongs in the engine.

The project wins on a name collision, and the resolution SAYS which file
answered, on every run.  That is the whole composition story: there is no
`extends:`, because three layers already compose without one -

    engine profile  ->  a project ADOPTS it  ->  one run OVERRIDES it

A fourth mechanism needs evidence that those three could not say it.

Adoption and override
---------------------
A project adopts one with ``pipeline.run_profile: podcast`` in its
project.yaml.  A single run says ``--profile <name>`` to use a different
one, or ``--profile none`` to decline the adopted one and run plainly.

Naming a step on the command line outranks the profile.  `--skip` and
`--with` ADD to what the profile said; `--target` and `--only` REPLACE
its goals, because they are a whole answer to the same question; and
`--only`/`--with` REMOVE a step from the profile's skip list, since
naming a step is a stronger statement than a default (the same rule
`run_scope` already applies to a step that is off by default).

Why YAML
--------
A brand template, a project.yaml and a run profile are all declarations
the captain writes, and they should not be three formats.  YAML also
takes comments, which matters here more than anywhere: a profile that
leaves a step out wants to say WHY on the line above it, and that is the
one thing JSON cannot carry.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

**A run shape is DECLARED as data, and it has no power `run_scope` does not already have.**
One enumeration, `library/tools/run_profile.py`.
- A profile carries `goals` (or a built-in `target`), `skip`, `with` and `breakpoints`, and hands the first four to `run_scope.resolve` as a `Selection`. **The dependency refusal, the hard/soft edge derivation and the pre-run failure all apply unchanged**; `tests/test_run_profile.py` asserts a declared profile and the equivalent flags produce the SAME refusal string.
- **Two directories, and a name resolves in the project first**: `<project>/profiles/<name>.yaml` (`Kind.INPUT`, the captain's) shadows `library/profiles/<name>.yaml` (the engine's), and the run header says which file answered. There is no `extends:` - the three layers that compose are engine profile -> a project ADOPTS it (`pipeline.run_profile`) -> one run OVERRIDES it.
- **Naming a step on the command line outranks the profile.** `--skip`/`--with` ADD to what it said; `--target`/`--only` REPLACE its goals; `--only`/`--with` take a step OUT of its skip list. `--skip X --only X` is still the contradiction `run_scope` refuses.
- **A profile is refused by name** for an unknown key, an unknown step, an unknown target, no `description`, a `name` disagreeing with its filename, or declaring both `target` and `goals`.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

try:
    import yaml
except ImportError:  # pragma: no cover - PyYAML is in requirements.txt
    yaml = None

from library.tools import run_scope


class ProfileError(ValueError):
    """A profile that cannot be used, refused by name before the run."""


# The word that DECLINES a profile for one run. A file may not be called
# this, or `--profile none` would be ambiguous between "no profile" and
# "the profile called none".
NO_PROFILE_WORD = "none"

# Every key a profile file may carry. An unknown key is refused by name:
# a misspelled `breakpoint:` that silently armed nothing would be exactly
# the trap this repo's step-directory check exists to stop.
PROFILE_KEYS = frozenset({
    "name",
    "description",
    "target",
    "goals",
    "skip",
    "with",
    "breakpoints",
})

_LIST_KEYS = ("goals", "skip", "with", "breakpoints")

PROFILE_SUFFIXES = (".yaml", ".yml")

ENGINE = "engine"
PROJECT = "project"

# The project.yaml key a project adopts a profile with.
ADOPTION_KEY = "run_profile"


@dataclass(frozen=True)
class RunProfile:
    """A named run configuration, as declared.

    Everything here is what the FILE said. Nothing is resolved against
    the DAG in this object - that is `run_scope`'s job, and it happens
    once, on the composed selection.
    """

    name: str = ""
    description: str = ""
    path: str = ""
    """The file this was read from. Empty for :data:`NO_PROFILE`."""

    source: str = ""
    """`project` or `engine`. Empty for :data:`NO_PROFILE`."""

    target: str = ""
    goals: Tuple[str, ...] = ()
    skip: Tuple[str, ...] = ()
    with_steps: Tuple[str, ...] = ()
    breakpoints: Tuple[str, ...] = ()

    adopted: bool = False
    """True when the project.yaml named it rather than the command line.
    Reported, because a run that stops somewhere the captain did not type
    on this command line has to say where that came from."""

    @property
    def is_declared(self) -> bool:
        return bool(self.name)


NO_PROFILE = RunProfile()
"""The absence of a declaration. Every field empty, so composing with it
leaves the command line's own words exactly as they arrived."""


# ── Where profiles live ──────────────────────────────────────────────

_REPO_ROOT = Path(__file__).resolve().parents[2]


def engine_profiles_dir() -> Path:
    """The engine's own profiles. Ships with the repository."""
    return _REPO_ROOT / "library" / "profiles"


def project_profiles_dir(project_folder: Optional[str]) -> Optional[Path]:
    """The project's own profiles, or None when there is no project.

    Read through the layout owner, so this cannot disagree with
    `project_layout` about where `profiles/` is.
    """
    if not project_folder:
        return None
    from library.tools.project_layout import Area, ProjectLayout

    return ProjectLayout(project_folder).read_dir(Area.RUN_PROFILES)


@dataclass(frozen=True)
class ProfileFile:
    name: str
    path: str
    source: str


def available(project_folder: Optional[str] = None) -> Dict[str, ProfileFile]:
    """`{name: ProfileFile}` - every profile this run could name.

    The project's own SHADOW the engine's, and both halves are listed by
    :func:`describe_available` so a shadow is never silent.
    """
    found: Dict[str, ProfileFile] = {}
    for directory, source in ((engine_profiles_dir(), ENGINE),
                              (project_profiles_dir(project_folder), PROJECT)):
        if directory is None or not directory.is_dir():
            continue
        for path in sorted(directory.iterdir()):
            if path.suffix.lower() not in PROFILE_SUFFIXES or not path.is_file():
                continue
            found[path.stem] = ProfileFile(path.stem, str(path), source)
    return found


def shadowed(project_folder: Optional[str] = None) -> Dict[str, str]:
    """`{name: engine path}` for engine profiles a project has replaced."""
    directory = project_profiles_dir(project_folder)
    if directory is None or not directory.is_dir():
        return {}
    project_names = {p.stem for p in directory.iterdir()
                     if p.suffix.lower() in PROFILE_SUFFIXES and p.is_file()}
    engine = engine_profiles_dir()
    if not engine.is_dir():
        return {}
    return {p.stem: str(p) for p in sorted(engine.iterdir())
            if p.stem in project_names
            and p.suffix.lower() in PROFILE_SUFFIXES and p.is_file()}


# ── Adoption ─────────────────────────────────────────────────────────

def adopted_name(project_folder: Optional[str]) -> str:
    """The profile a project's project.yaml adopts, or ``""``.

    Read through `brand_registry.project_pipeline_block`, which is the
    one parse of the `pipeline:` block, so a project cannot declare this
    in a place nothing looks.
    """
    if not project_folder:
        return ""
    from library.tools import brand_registry

    block = brand_registry.project_pipeline_block(project_folder)
    return str(block.get(ADOPTION_KEY) or "").strip()


# ── Loading one ──────────────────────────────────────────────────────

def load(name: str,
         project_folder: Optional[str] = None,
         known_steps: Optional[Iterable[str]] = None,
         *,
         adopted: bool = False) -> RunProfile:
    """Read a profile by name, or raise `ProfileError` naming the fault.

    `known_steps` is the DAG's node ids; every step a profile names is
    checked against them here so a typo is refused with the file that
    contains it, rather than surfacing later as a scope error with no
    idea which line it came from.
    """
    name = (name or "").strip()
    if not name:
        raise ProfileError("no profile named.")
    if name == NO_PROFILE_WORD:
        return NO_PROFILE

    catalogue = available(project_folder)
    entry = catalogue.get(name)
    if entry is None:
        known = ", ".join(sorted(catalogue)) or "(none)"
        where = [str(engine_profiles_dir())]
        project_dir = project_profiles_dir(project_folder)
        if project_dir is not None:
            where.insert(0, str(project_dir))
        raise ProfileError(
            f"unknown run profile {name!r}. Known profiles: {known}. "
            f"Looked in: {', '.join(where)}."
        )

    profile = _parse(entry, known_steps)
    return replace(profile, adopted=True) if adopted else profile


def resolve_for_run(project_folder: Optional[str],
                    requested: Optional[str] = None,
                    known_steps: Optional[Iterable[str]] = None) -> RunProfile:
    """The profile this run uses: the one it named, else the one the
    project adopts, else none.

    `--profile none` declines an adopted profile for a single run, which
    is the only way to say "ignore what my project.yaml says" without
    editing project.yaml.
    """
    requested = (requested or "").strip()
    if requested:
        if requested == NO_PROFILE_WORD:
            return NO_PROFILE
        return load(requested, project_folder, known_steps)
    adopted = adopted_name(project_folder)
    if not adopted:
        return NO_PROFILE
    if adopted == NO_PROFILE_WORD:
        raise ProfileError(
            f"project.yaml declares `pipeline.{ADOPTION_KEY}: "
            f"{NO_PROFILE_WORD}`. {NO_PROFILE_WORD!r} is the word that "
            f"DECLINES a profile on the command line and cannot be "
            f"adopted. Remove the line instead."
        )
    return load(adopted, project_folder, known_steps, adopted=True)


def _parse(entry: ProfileFile,
           known_steps: Optional[Iterable[str]]) -> RunProfile:
    if yaml is None:
        raise ProfileError("PyYAML is required to read a run profile.")
    path = Path(entry.path)
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ProfileError(f"{path}: cannot be read - {exc}") from exc
    if raw is None:
        raise ProfileError(f"{path}: is empty. A profile that declares "
                           f"nothing is not a profile.")
    if not isinstance(raw, dict):
        raise ProfileError(f"{path}: must be a mapping, not "
                           f"{type(raw).__name__}.")

    unknown = sorted(set(raw) - PROFILE_KEYS)
    if unknown:
        raise ProfileError(
            f"{path}: unknown key(s) {', '.join(repr(k) for k in unknown)}. "
            f"A profile may declare: {', '.join(sorted(PROFILE_KEYS))}."
        )

    declared_name = str(raw.get("name") or "").strip()
    if declared_name and declared_name != entry.name:
        raise ProfileError(
            f"{path}: declares `name: {declared_name}` but is filed as "
            f"{entry.name!r}. A profile is found by its filename, so the "
            f"two must agree."
        )
    if entry.name == NO_PROFILE_WORD:
        raise ProfileError(
            f"{path}: a profile may not be called {NO_PROFILE_WORD!r} - "
            f"that is the word that declines a profile for one run."
        )

    description = str(raw.get("description") or "").strip()
    if not description:
        raise ProfileError(
            f"{path}: needs a `description`. A run shape nobody can read "
            f"is a run shape nobody trusts."
        )

    lists: Dict[str, Tuple[str, ...]] = {}
    for key in _LIST_KEYS:
        lists[key] = _string_list(raw.get(key), key, path)

    target = str(raw.get("target") or "").strip()
    if target and lists["goals"]:
        raise ProfileError(
            f"{path}: declares both `target` and `goals`. They are two "
            f"answers to one question - name a built-in target, or list "
            f"the goal steps yourself."
        )
    if target and target not in run_scope.TARGETS:
        known = ", ".join(sorted(run_scope.TARGETS)) or "(none)"
        raise ProfileError(
            f"{path}: `target: {target}` is not a target of this "
            f"pipeline. Known targets: {known}."
        )

    steps = set(known_steps) if known_steps is not None else _dag_steps()
    for key in ("goals", "skip", "with"):
        for value in lists[key]:
            if value not in steps:
                raise ProfileError(
                    f"{path}: `{key}` names {value!r}, which is not a step "
                    f"in this pipeline. Known steps: "
                    f"{', '.join(sorted(steps))}."
                )
    from library.tools import breakpoints as breakpoints_module

    for value in lists["breakpoints"]:
        if value != breakpoints_module.EVERY_STEP and value not in steps:
            raise ProfileError(
                f"{path}: `breakpoints` names {value!r}, which is not a "
                f"step in this pipeline (and is not "
                f"{breakpoints_module.EVERY_STEP!r}, which means every "
                f"step). Known steps: {', '.join(sorted(steps))}."
            )

    contradicted = sorted(set(lists["goals"]) & set(lists["skip"]))
    if contradicted:
        raise ProfileError(
            f"{path}: {', '.join(contradicted)} is both a goal and "
            f"skipped. Say it once."
        )
    contradicted = sorted(set(lists["with"]) & set(lists["skip"]))
    if contradicted:
        raise ProfileError(
            f"{path}: {', '.join(contradicted)} is both selected with "
            f"`with` and skipped. Say it once."
        )

    return RunProfile(
        name=entry.name,
        description=description,
        path=str(path),
        source=entry.source,
        target=target,
        goals=lists["goals"],
        skip=lists["skip"],
        with_steps=lists["with"],
        breakpoints=lists["breakpoints"],
    )


def _string_list(value, key: str, path: Path) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise ProfileError(
            f"{path}: `{key}` must be a list, not a single string. Write "
            f"`{key}: [{value}]` or a `- ` list."
        )
    if not isinstance(value, (list, tuple)):
        raise ProfileError(f"{path}: `{key}` must be a list, not "
                           f"{type(value).__name__}.")
    out = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            raise ProfileError(
                f"{path}: `{key}` holds {item!r}, which is not a step name.")
        if item.strip() not in out:
            out.append(item.strip())
    return tuple(out)


def _dag_steps() -> set:
    return {n["id"] for n in run_scope.load_dag().get("nodes", [])}


# ── Composing with the command line ──────────────────────────────────

def compose(profile: RunProfile,
            target: Optional[str] = None,
            only: Sequence[str] = (),
            skip: Sequence[str] = (),
            with_steps: Sequence[str] = ()) -> run_scope.Selection:
    """The `run_scope.Selection` this run resolves.

    Naming a step on the command line outranks the profile:

    * `--target` or `--only` REPLACE the profile's goals. They are whole
      answers to the same question, so merging them would produce a
      selection nobody wrote.
    * `--skip` and `--with` ADD to the profile's.
    * A step named in `--only` or `--with` comes OUT of the profile's
      skip list. `run_scope` refuses a selection that both skips and
      selects a step, and a profile's skip is a default while a step on
      the command line is a statement.

    `--skip X` together with `--only X` is still a contradiction, and
    `run_scope._reject_unknown` still refuses it. Nothing here papers
    over a command line that says two things at once.
    """
    only = tuple(dict.fromkeys(only or ()))
    skip = tuple(dict.fromkeys(skip or ()))
    with_steps = tuple(dict.fromkeys(with_steps or ()))

    if target or only:
        resolved_target = target or None
        resolved_goals = () if target else only
    else:
        resolved_target = profile.target or None
        resolved_goals = profile.goals

    # A step the command line NAMES comes out of the profile's skip list.
    # A step the command line SKIPS stays skipped whatever else it says -
    # so `--skip X --only X` is still the contradiction `run_scope`
    # refuses, rather than something quietly resolved here.
    outranked = (set(only) | set(with_steps)) - set(skip)
    kept_from_profile = [step for step in profile.skip
                         if step not in outranked]
    resolved_skip = tuple(dict.fromkeys((*kept_from_profile, *skip)))

    return run_scope.Selection(
        target=resolved_target,
        only=tuple(resolved_goals),
        skip=resolved_skip,
        with_steps=tuple(dict.fromkeys((*profile.with_steps, *with_steps))),
    )


# ── Reporting ────────────────────────────────────────────────────────

def describe(profile: RunProfile) -> List[str]:
    """The lines a run prints about the profile it is running under.

    Printed on every run that has one, including a resumed one: a run
    that stops somewhere the captain did not type on this command line
    has to say where that came from.
    """
    if not profile.is_declared:
        return []
    how = ("adopted by project.yaml" if profile.adopted
           else "named on the command line")
    lines = [f"  Profile: {profile.name} ({profile.source}, {how})",
             f"           {profile.path}",
             f"           {profile.description}"]
    if profile.target:
        lines.append(f"           target: {profile.target}")
    if profile.goals:
        lines.append(f"           goals: {', '.join(profile.goals)}")
    if profile.skip:
        lines.append(f"           skip: {', '.join(profile.skip)}")
    if profile.with_steps:
        lines.append(f"           with: {', '.join(profile.with_steps)}")
    if profile.breakpoints:
        lines.append(f"           breakpoints: "
                     f"{', '.join(profile.breakpoints)}")
    return lines


def describe_available(project_folder: Optional[str] = None) -> List[str]:
    """Every profile a run of this project could name, and where from."""
    catalogue = available(project_folder)
    hidden = shadowed(project_folder)
    if not catalogue:
        return ["  (no run profiles declared)"]
    lines = []
    for name in sorted(catalogue):
        entry = catalogue[name]
        note = ""
        if name in hidden:
            note = f"  [shadows the engine's {hidden[name]}]"
        lines.append(f"  {name:<24} {entry.source:<8} {entry.path}{note}")
    return lines


# ── The CLI half ─────────────────────────────────────────────────────

def add_profile_arguments(parser) -> None:
    """`--profile`, registered from here by BOTH CLIs.

    Same reason `run_scope.add_scope_arguments` exists: `manage_project.py
    run` forwards to `run_pipeline.py`, so a flag defined twice is a flag
    that will eventually differ.
    """
    engine = ", ".join(sorted(p.stem for p in engine_profiles_dir().iterdir()
                              if p.suffix.lower() in PROFILE_SUFFIXES)) \
        if engine_profiles_dir().is_dir() else ""
    parser.add_argument(
        "--profile", metavar="NAME",
        help=f"Run under a declared run profile - which steps fire and "
             f"where the run stops. Engine profiles: {engine or '(none)'}; "
             f"a project may add its own under <project>/profiles/. "
             f"'{NO_PROFILE_WORD}' declines the profile the project "
             f"adopts. See library/tools/run_profile.py.")


def _main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python3 -m library.tools.run_profile",
        description="List the run profiles available to a project.")
    parser.add_argument("project", nargs="?", default="",
                        help="Project directory (optional)")
    parser.add_argument("--show", metavar="NAME",
                        help="Print one profile as it resolves")
    args = parser.parse_args(argv)

    project = os.path.abspath(args.project) if args.project else ""
    if args.show:
        try:
            profile = load(args.show, project)
        except ProfileError as exc:
            print(f"REFUSED: {exc}")
            return 2
        for line in describe(profile):
            print(line)
        return 0

    print("Run profiles" + (f" for {project}" if project else ""))
    for line in describe_available(project):
        print(line)
    if project:
        adopted = adopted_name(project)
        print(f"\nAdopted by project.yaml: {adopted or '(none)'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
