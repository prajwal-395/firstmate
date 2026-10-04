"""VARIANTS: candidate reel versions alive beside a promoted one - declared on
a branch, built, compared and CHOSEN. Part of the version model
(`library/tools/versions/__init__.py`); AGENTS.md 3: run state.

Reached through `manage_project.py variant new|build|list|diff|choose|merge`.

Declaring: timeline variations as git branches
----------------------------------------------
A timeline is DERIVED - step 6.01 rebuilds it from declarations rather
than editing it in place - so a variation is not merged as a timeline.
Its DECLARATIONS are: merge the declarations, rebuild, and the combined
timeline falls out.  Never merge the projection (OTIO, `.drp`, serializer
JSON); always merge the source.

1. `create_variation` - branches from the current committed state
   (requiring a clean tree and an unheld branch name), records the
   variant's spec in `pipeline_output/review/reel_variants.json`
   (`VARIANTS_FILENAME`), and commits.  The rebuild runs through the
   normal build paths (`build_reel_variants` for seam comparisons,
   `build-reels` for plan reels); the branch half never touches Resolve.
2. A branch name and a timeline name derivable from each other, both
   ways (`variant/r09-reaction-cutaway` <-> `Reel 09 - ...
   (reaction-cutaway)`): `variant_branch_name`, `variant_timeline_name`,
   `branch_for_timeline`, `timeline_for_branch`.
3. `merge_variations` - git merge, with conflicting GENERATED run state
   (`GENERATED_PATHS`: `pipeline_data.json`, `pipeline_run.json`, step
   outputs, the manifest, LLM request records) resolved to the target
   side, because it is REBUILT, never hand-merged.  Only declaration
   conflicts reach a human, left uncommitted.  Marker pulls never
   conflict (each is its own timestamped file).  Model ANSWERS
   (`llm_responses`) merge for real: one side answering wins, both
   answering differently conflicts for a human.

A clean merge is not a green build: the merged tree must be rebuilt (in
Resolve, by the caller) and the rebuilt timeline read back against both
variations' timelines before anything is promoted - see the docstring on
`merge_variations`.

What a variant may differ in, and the boundary that is held
-----------------------------------------------------------
A spec carries only `SPEC_KEYS`: `suffix` (required) and at least one of
a SEAM (`j_cut`, `cutaway`, with `cover`) or `declares` - a per-project
DECLARATION from `external_inputs.DECLARATIONS` (`declarable`).  A
variant may differ in a declaration the ordinary rebuild already reads
and in the seam offsets `build_reel_timeline` already takes - nothing
else, because anything wider would need a SECOND BUILDER.
`OUT_OF_VOCABULARY` names what is out and who owns each near miss;
`validate_variant_spec` refuses the rest.

The declaration itself is never in the spec.  It is the CONTENT of
`external/declarations/<store>.json` on the variant's own branch, the only place the
rebuild reads it from - so `branch_requirement` refuses, by name, a
declaring variant built from any other branch.  A seam variant builds
from anywhere.

A variant is ONE object with two projections: its DECLARATIONS live on a
branch (merged one at a time) and its PICTURE on a Resolve timeline
(compared side by side).

Built, compared and chosen
--------------------------
Two versions of a reel alive at once, compared, and one CHOSEN, in the
order a captain uses them.

1. What is ALIVE
----------------
`record_build` stores, per built variant, its timeline name, its suffix,
what it declares, its `watch` note and the ROW SNAPSHOT
(`reel_read.rows_of`) of what was placed.  Written by
`reel_build.build_reel_variants` when each variant passes conformance,
into `pipeline_output/review/reel_variant_builds.json`
(`BUILDS_FILENAME`), on the version-control allow-list.  The rows outlive
the timeline they describe, so a comparison stays answerable after the
loser has been archived and collected.

2. COMPARED, not just watched
-----------------------------
`compare` runs two stored snapshots through `rounds.diff_reel` and says
what differs off disk, with Resolve closed.  A side that was never
built and recorded RAISES rather than diffing against nothing.

3. CHOOSING, as an act with a consequence
-----------------------------------------
`choose`:

- the CHOSEN variant is renamed to the reel's own name - the same
  timeline, not a copy;
- the version that HELD that name is retired to `05 - Reels/Archive`
  exactly as a promotion retires it (`reel_retirement.retire_timelines`),
  FIRST, so a choice that refuses partway has replaced nothing;
- every UNCHOSEN variant is renamed into the same archive with the same
  `(archived round NNN)` suffix;
- the ROUND records which suffix was chosen, what it was chosen over,
  and WHY.  `--why` is required;
- the choice runs under the Resolve lease, and it never promotes or
  overwrites an editor's work: a chosen or incumbent timeline the editor
  created or renamed REFUSES (`ChoiceRefused`), and an edit made by hand
  on either is protected by `reel_replace_guard.protect_editor_changes`
  unless `accept_editor_changes` accepts it.

The clutter bound
-----------------
    at most `RETAINED_UNCHOSEN` (1) unchosen variant survives PER REEL.

Per REEL, not per variant identity: every new suffix is a new identity,
so a per-identity bound would grow with the number of comparisons.  The
archive holds, per reel, the previous cut and the most recent runner-up.
The reel's OWN retired generations are bounded by
`reel_retirement.plan_collection`, which `choose` runs every time.  A
generation carrying a captain SIGN-OFF is never collected, and every
retained one is NAMED in the report on every choice.

A variant and the sign-off
--------------------------
A variant is a BUILT reel, so it can be signed off;
`feedback_ledger.base_reel_name` already makes `Reel 09 - ...
(reaction-cutaway)` its own sign-off identity.  What that means here:

- choosing a variant over a SIGNED-OFF incumbent refuses
  (`ChoiceRefused`) unless the choice declares it, in `reel_signoff`'s
  declare-then-proceed shape (`supersede_declared`);
- choosing a variant that itself carries a sign-off CARRIES the sign-off
  onto the reel's name;
- a signed-off variant that LOSES keeps its sign-off and is never
  collected.

`tests/unit/reels/test_version_variants.py`, `tests/unit/reels/test_version_variants.py`.

The measurements and rulings behind these rules (the version-control
report's merge ruling, issue #925, the captain's 2026-09-12 answer, the
clutter measured against a real Resolve): docs/evidence/variants.md.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from datetime import datetime, timezone
from pathlib import Path

from library.tools import reel_retirement as retire
from library.tools.ren_refusal import RenRefusal
from library.tools.resolve_lock import under_lease
from library.tools.stable_json import write_stable
from library.tools.versions import rounds, store

VARIANT_BRANCH_PREFIX = "variant/"
"""Every variation branch reads `variant/<slug>`."""

VARIANTS_FORMAT = "reel_variants/1"

VARIANTS_FILENAME = "reel_variants.json"
"""Under `pipeline_output/review/` - versioned by the allow-list's
`review/**`, captain-reviewable beside the proposals, written only by
this module, never by a pipeline step."""

SPEC_KEYS = ("suffix", "j_cut", "cutaway", "cover", "declares", "watch")
"""The only keys a variant spec carries. `suffix` is required; at
least one of `j_cut` / `cutaway` / `declares` must be present (a
comparison with no difference is not a comparison); `cover` rides with
a cutaway; `watch` is the free-text viewing note."""


def declarable() -> tuple:
    """The declaration stores a variant may differ in. DERIVED.

    `external_inputs.DECLARATIONS` is the engine's own enumeration of
    what a per-project DECLARATION is - a standing decision read by the
    module that owns it, carried in `external/declarations/<stem>.json`, checked by
    its owner's own reader. Deriving from it rather than listing the
    stems again is the point: a declaration added there becomes
    variant-expressible with no second edit, and one removed stops
    being expressible at the same moment.

    `tests/unit/reels/test_version_variants.py::
    test_the_declarable_set_is_derived_not_listed_again` pins the
    derivation.
    """
    from library.tools.external_inputs import DECLARATIONS

    return tuple(sorted(DECLARATIONS))


#: What a variant spec may NOT express, and the module that owns each
#: near miss. The boundary is the whole point (the shape
#: `motion_graphics_vocabulary.OUT_OF_VOCABULARY` takes): a spec that
#: can express ANY difference is a second builder, and then the thing
#: beside the approved reel is no longer the approved reel with one
#: change in it.
#:
#: The rule that draws the line: a variant may differ in a PER-REEL
#: DECLARATION the rebuild already reads, and in the SEAM offsets
#: `build_reel_timeline` already takes. Both are things the ordinary
#: rebuild does on the ordinary path, so a variant is the rebuild with
#: one input changed - which is what makes the git half work at all
#: (merge the declaration, rebuild, and the combined timeline falls
#: out) and what makes the comparison honest.
OUT_OF_VOCABULARY = {
    "a different moment, span or approval": (
        "library/tools/reel_proposal.py - a variant compares two "
        "treatments of ONE approved moment; two moments are two reels, "
        "and the plan is where a reel is chosen."),
    "a different transcript or wording": (
        "library/tools/transcript_corrections.py - the transcript is "
        "the root every span, caption and anchor is measured against, "
        "so two variants cut from two transcripts share no clock."),
    "a different look, grade or delivery format": (
        "library/tools/reel_look.py, library/tools/delivery_format.py "
        "- these resolve from project.yaml and the brand template, so "
        "they are properties of the SERIES. A variant that differs in "
        "one compares two series, not two treatments."),
    "a different engine revision": (
        "library/tools/code_identity.py - `built_with` is stamped at "
        "build time and is a fact about the build, never an input a "
        "spec may ask for."),
    "a different step output or plan": (
        "library/processes/reels/dag.json - a step output is derived, "
        "and a variant that overrode one would be asking the builder "
        "to take a path the rebuild does not take."),
}

# Conflicted paths at or under one of these are generated run state:
# rebuilt after the merge, never hand-merged. Everything NOT listed
# here that conflicts reaches a human - an unlisted file is one nobody
# classified, and silently resolving it is how a feature goes missing.
#
# Deliberately ABSENT: `pipeline_output/llm_responses`. A model answer
# is written once per decision and never rewritten by a rebuild, so
# one side answering where the other did not merges cleanly with the
# answer winning - exactly the refresh direction (main re-answers reel
# 9's motion, the variant just takes it). Both sides answering the
# same reel differently is a genuine fork in decisions and SHOULD
# conflict. Requests stay listed: they are rewritten (with a fresh
# timestamp) on every build.
GENERATED_PATHS = (
    "external/state",
    "pipeline_data.json",
    "pipeline_run.json",
    "pipeline_output/steps",
    "pipeline_output/llm_requests",
    "pipeline_output/gates",
    "pipeline_output/provenance",
    "pipeline_output/RUN-TRACEBACK.md",
    "pipeline_output/ARTIFACTS.md",
    # Plan-derived review records a rebuild regenerates (seen changing
    # under rebuilds in the wild: explainer/semantic plans and plan
    # provenance move with the plan, not with a variation decision).
    "pipeline_output/review/explainer_plans.json",
    "pipeline_output/review/semantic_visual_plans.json",
    "pipeline_output/review/plan_provenance.json",
    # What variants are ALIVE and what their pictures contain
    # (`record_build`). Rebuilt by every variant build,
    # and it describes RESOLVE's state, which is not per-branch - so
    # there is nothing on the source side a merge could preserve that
    # the rebuild will not overwrite. The record that must survive is
    # the round's, and `choose` moves the chosen rows
    # into the round BEFORE forgetting the build.
    "pipeline_output/review/reel_variant_builds.json",
)


# ── Branch <-> timeline names ────────────────────────────────────────

def slug_for_suffix(suffix: str) -> str:
    """` (reaction-cutaway)` -> `reaction-cutaway`.

    The suffix shape is enforced, not assumed: variant timeline names
    carry the suffix in trailing parens (`built_name(moment, suffix)`),
    so a suffix that does not start with `" ("` and end with `")"`
    cannot round-trip and is refused at spec-validation time.
    """
    inner = suffix.strip()[1:-1].strip()
    slug = "".join(c.lower() if c.isalnum() else "-" for c in inner)
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-")


def variant_branch_name(reel_number: int, suffix: str) -> str:
    """The branch a variation of reel N with this suffix lives on."""
    return (f"{VARIANT_BRANCH_PREFIX}r{int(reel_number):02d}-"
            f"{slug_for_suffix(suffix)}")


def variant_timeline_name(base_timeline: str, suffix: str) -> str:
    """The Resolve timeline a variation builds into.

    Same spelling the builder uses (`built_name`: plan name plus
    suffix), so the name on the branch record and the name in Resolve
    cannot drift apart.
    """
    return f"{base_timeline}{suffix}"


def branch_for_timeline(timeline_name: str,
                        specs: tuple = ()) -> str | None:
    """The variation branch for a timeline name.

    Exact through the spec record, a guess without it: a trailing
    parens group is only a variant suffix when the reel's specs name
    it - `(final)` is the approved timeline's own name, not a
    variation, and no syntactic rule tells the two apart. Callers
    that file or route by this answer pass the reel's spec list from
    `read_variant_specs`; callers without it get the parse and a
    docstring warning. Returns None for approved (suffix-less or
    unmatched) timelines - they live on the default branch.
    """
    name = timeline_name.strip()
    if not name.endswith(")"):
        return None
    depth = 0
    start = -1
    for i in range(len(name) - 1, -1, -1):
        if name[i] == ")":
            depth += 1
        elif name[i] == "(":
            depth -= 1
            if depth == 0:
                start = i
                break
    if start <= 0 or name[start - 1] != " ":
        return None
    suffix = " " + name[start:]
    if not slug_for_suffix(suffix):
        return None
    import re
    head = re.match(r"Reel\s+(\d+)", name)
    if not head:
        return None
    if specs:
        known = {str((s or {}).get("suffix", "")) for s in specs}
        if suffix not in known:
            return None
    return variant_branch_name(int(head.group(1)), suffix)


def timeline_for_branch(branch: str, base_timeline: str,
                        specs: tuple = ()) -> str | None:
    """The variant timeline name for a branch, given the reel's
    approved (base) timeline name. None when the branch is not a
    variant branch.

    Exact through the spec record, approximate without it: the branch
    slug lowercases and dashes the suffix, so `specs` (the reel's spec
    list from `read_variant_specs`) is matched by slug to recover the
    true suffix. Without specs the slug is re-parenthesised as a
    canonical guess - right for every suffix that was already
    lowercase, wrong in case for the rest."""
    if not branch.startswith(VARIANT_BRANCH_PREFIX):
        return None
    slug = branch[len(VARIANT_BRANCH_PREFIX):]
    import re
    match = re.match(r"r(\d+)-(.+)", slug)
    if not match:
        return None
    for spec in specs:
        suffix = (spec or {}).get("suffix", "")
        if suffix and slug_for_suffix(suffix) == match.group(2):
            return f"{base_timeline}{suffix}"
    return f"{base_timeline} ({match.group(2)})"


# ── The variant spec record ──────────────────────────────────────────

def variants_path(project_folder: str) -> Path:
    from library.tools.project_layout import Area, ProjectLayout
    return Path(str(ProjectLayout(str(project_folder)).read_path(
        Area.REVIEW, VARIANTS_FILENAME)))


def read_variant_specs(project_folder: str) -> dict:
    """`{"format": ..., "variants": {"9": [spec, ...]}}`, or the empty
    record when no variation has been declared yet."""
    path = variants_path(project_folder)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"format": VARIANTS_FORMAT, "variants": {}}
    if not isinstance(data, dict):
        return {"format": VARIANTS_FORMAT, "variants": {}}
    data.setdefault("format", VARIANTS_FORMAT)
    data.setdefault("variants", {})
    return data


def validate_variant_spec(spec: dict) -> list[str]:
    """Structural errors. No Resolve, no media reads - a malformed
    declaration must fail on the branch, before any build is attempted."""
    errors = []
    if not isinstance(spec, dict):
        return ["variant spec must be an object"]
    unknown = set(spec) - set(SPEC_KEYS)
    if unknown:
        errors.append(f"unknown keys: {sorted(unknown)}")
    suffix = spec.get("suffix", "")
    if (not isinstance(suffix, str) or not suffix.startswith(" (")
            or not suffix.endswith(")") or not slug_for_suffix(suffix)):
        errors.append(
            "suffix must look like ' (name)' - trailing parens the "
            "timeline name carries and the branch name derives from")
    declares = spec.get("declares")
    if declares is not None:
        if (not isinstance(declares, (list, tuple))
                or not all(isinstance(name, str) for name in declares)):
            errors.append("declares must be a list of declaration names")
        elif not declares:
            errors.append(
                "declares is empty - a variant that differs in no "
                "declaration must not claim one; leave the key out")
        else:
            allowed = declarable()
            unknown = sorted(set(declares) - set(allowed))
            if unknown:
                errors.append(
                    f"declares names {unknown}, which no declaration "
                    f"store owns. A variant may differ in "
                    f"{list(allowed)} - the stores "
                    f"`external_inputs.DECLARATIONS` enumerates. "
                    f"Anything else is out of vocabulary: "
                    f"{sorted(OUT_OF_VOCABULARY)}")
            if len(set(declares)) != len(declares):
                errors.append("declares names one store twice")
    if (spec.get("j_cut") is None and spec.get("cutaway") is None
            and not declares):
        errors.append("one of j_cut / cutaway / declares is required - a "
                      "variation with no difference is not a comparison")
    j_cut = spec.get("j_cut")
    if j_cut is not None:
        if not isinstance(j_cut, dict):
            errors.append("j_cut must be an object")
        else:
            for key in ("join_seconds", "lead_seconds"):
                if key not in j_cut:
                    errors.append(f"j_cut misses {key!r}")
    cutaway = spec.get("cutaway")
    if cutaway is not None:
        if not isinstance(cutaway, dict):
            errors.append("cutaway must be an object")
        else:
            if not cutaway.get("hide_angle"):
                errors.append("cutaway misses 'hide_angle'")
            window = cutaway.get("window_seconds")
            if (not isinstance(window, (list, tuple)) or len(window) != 2):
                errors.append("cutaway.window_seconds must be [start, end]")
    cover = spec.get("cover")
    if cover is not None:
        if not isinstance(cover, dict):
            errors.append("cover must be an object")
        else:
            for key in ("source_file", "source_in", "source_out"):
                if key not in cover:
                    errors.append(f"cover misses {key!r}")
    watch = spec.get("watch")
    if watch is not None and not isinstance(watch, str):
        errors.append("watch must be text")
    return errors


def write_variant_specs(project_folder: str, record: dict) -> str:
    """Write the record canonically: reels sorted numerically, each
    reel's specs sorted by suffix, unknown top-level keys refused.

    Sorting is the merge-friendliness: two branches adding different
    reels (or different suffixes) touch different lines, so the merge
    is a union, never a conflict."""
    variants = record.get("variants", {})
    canonical = {"format": VARIANTS_FORMAT, "variants": {}}
    for reel in sorted(variants, key=lambda r: (int(r), r)):
        specs = variants[reel] or []
        seen = set()
        ordered = []
        for spec in sorted(specs, key=lambda s: str(s.get("suffix", ""))):
            if spec.get("suffix") in seen:
                raise ValueError(
                    f"reel {reel}: two specs carry suffix "
                    f"{spec.get('suffix')!r}")
            seen.add(spec.get("suffix"))
            entry = {k: spec[k] for k in SPEC_KEYS if k in spec}
            if isinstance(entry.get("declares"), (list, tuple)):
                # Sorted for the same reason the reels and suffixes
                # are: two branches naming the same stores in a
                # different order must not read as a conflict.
                entry["declares"] = sorted(entry["declares"])
            ordered.append(entry)
        canonical["variants"][str(reel)] = ordered
    return write_stable(variants_path(project_folder), canonical)


# ── Reading the record back ─────────────────────────────────────────

def specs_for_reel(project_folder: str, reel_number: int) -> list:
    """Every variant spec declared for one reel, suffix-ordered."""
    record = read_variant_specs(project_folder)
    specs = (record.get("variants") or {}).get(str(int(reel_number))) or []
    return sorted((s for s in specs if isinstance(s, dict)),
                  key=lambda s: str(s.get("suffix", "")))


def spec_by_suffix(project_folder: str, reel_number: int,
                   suffix: str) -> dict | None:
    """One reel's spec carrying this exact suffix, or None."""
    for spec in specs_for_reel(project_folder, reel_number):
        if spec.get("suffix") == suffix:
            return spec
    return None


def declared_variant_names(project_folder: str,
                           base_by_reel) -> set:
    """Every variant TIMELINE name this project has declared.

    `base_by_reel` is `{reel number: approved timeline name}` - the
    plan's own names, because a variant name is its reel's name plus a
    suffix and nothing else may guess it. The answer is exact through
    the spec record, which is the only thing that tells `(final)` (a
    reel's own name) from `(reaction-cutaway)` (a variation of it).

    Read by `reel_conformance_verifier.grades_as_a_reel`, so the sweep
    can leave a live variant out the way it leaves a retired
    generation out, and by `choose` to find what is alive.
    """
    names = set()
    record = read_variant_specs(project_folder)
    for reel, specs in (record.get("variants") or {}).items():
        try:
            base = (base_by_reel or {}).get(int(reel))
        except (TypeError, ValueError):
            continue
        if not base:
            continue
        for spec in specs or ():
            suffix = (spec or {}).get("suffix")
            if suffix:
                names.add(variant_timeline_name(base, suffix))
    return names


def declared_variant_timelines(project_folder: str) -> set:
    """Every declared variant timeline name, resolved through the plan.

    The reel proposal is where an approved reel's timeline name is
    decided (`reel_proposal.read_proposal` - the same read
    `build_reel_variants` makes to find the moment), so the two cannot
    disagree about what a variant of reel 9 is called.

    A project with no proposal, no spec record, or an unreadable one
    gets the EMPTY set, and the callers say what that means for them.
    `reel_conformance_verifier` grades every live variant when this is
    empty, which is the loud direction: a sweep that went quiet because
    a declaration file was malformed would hide the reels over
    something that is not about them.
    """
    try:
        from library.tools.reel_proposal import proposal_path, read_proposal

        moments = read_proposal(str(proposal_path(project_folder)))
        base_by_reel = {int(m.number): m.timeline_name for m in moments
                        if getattr(m, "timeline_name", "")}
    except Exception:                                       # noqa: BLE001
        return set()
    return declared_variant_names(project_folder, base_by_reel)


# ── A declaring variant is built from its own branch ────────────────

def current_branch(project_folder: str) -> str:
    """The branch the project tree is on, or `''` when it has no repo."""
    head = store.git(project_folder, "rev-parse", "--abbrev-ref", "HEAD")
    return head.stdout.strip() if head.returncode == 0 else ""


def branch_requirement(project_folder: str, reel_number: int,
                       spec: dict) -> str:
    """`''` when this variant may be built from the current branch,
    else the reason it may not.

    A SEAM variant (`j_cut` / `cutaway` / `cover`) is an offset the
    builder applies from the spec itself, so it builds from anywhere -
    which is what lets `build_reel_variants` put two seam treatments up
    side by side in ONE atomic call.

    A variant that `declares` is different in kind. Its difference is
    not in the spec at all: it is the CONTENT of
    `external/declarations/<store>.json`
    on its own branch, because that is the only place the rebuild reads
    a declaration from. Built from the wrong branch it would carry the
    other version's declaration and differ from the approved reel
    nowhere - a comparison of one thing with itself, reported as a
    comparison of two. So it refuses, by name, with the checkout that
    fixes it.

    This is the whole answer to "timelines or branches": a variant is
    ONE object with two projections. Git holds one branch at a time and
    Resolve holds every timeline at once, so the declarations are
    compared one at a time and the PICTURES are compared side by side -
    which is the half the captain actually watches.
    """
    if not (spec or {}).get("declares"):
        return ""
    want = variant_branch_name(reel_number, spec["suffix"])
    have = current_branch(project_folder)
    if not have:
        return (f"{spec['suffix'].strip()} differs in "
                f"{sorted(spec['declares'])}, which lives in "
                f"external/declarations/<store>.json on branch {want} - and this "
                f"project has no git repo to hold it. Run "
                f"init_project_repo (library/tools/"
                f"versions/store.py) first.")
    if have != want:
        return (f"{spec['suffix'].strip()} differs in "
                f"{sorted(spec['declares'])}, and a declaration lives "
                f"on the variant's own branch, never in the spec. The "
                f"tree is on {have!r}; build it from {want!r}:\n"
                f"    git -C <project> checkout {want}")
    return ""


def _repo_ok(project_folder: str) -> str:
    """'' when the folder is a versioned project with a clean TRACKED
    tree, else the reason it is not.

    Untracked files (`??`) are tolerated, not refused: they ride no
    branch, so branching or merging around them loses nothing - and a
    live project directory always has some (another lane's records,
    a render a commit must never swallow). Tracked modifications,
    deletions and staged entries refuse, because those ARE the state
    the variation forks from."""
    root = Path(str(project_folder))
    if not (root / ".git").exists():
        return ("no git repo - run init_project_repo "
                "(library/tools/versions/store.py) first; a "
                "variation branches from recorded state, never from "
                "an unversioned tree")
    status = store.git(project_folder, "status", "--porcelain")
    if status.returncode != 0:
        return f"git status failed: {status.stderr.strip()[-200:]}"
    tracked = [line for line in status.stdout.splitlines()
               if line.strip() and not line.startswith("??")]
    if tracked:
        return ("working tree is dirty - commit or stash first; a "
                "variation branches from a committed state so the "
                "branch diff reads as the variation")
    return ""


def create_variation(project_folder: str, reel_number: int,
                     base_timeline_name: str, spec: dict,
                     message: str | None = None) -> dict:
    """Branch, record the seam spec, commit. One command.

    Requires a clean tree on the current branch (the variation forks
    from committed state), a valid spec, and a branch name nothing
    holds yet. Other declaration edits (captain_edits, project.yaml)
    land as later commits on the branch through normal git - this
    command records the seam, which is the part that used to live
    nowhere. Returns branch, timeline name and commit.
    """
    errors = validate_variant_spec(spec)
    if errors:
        return {"created": False, "reason": "; ".join(errors)}
    if not base_timeline_name or not base_timeline_name.strip():
        return {"created": False,
                "reason": "base_timeline_name is required - the approved "
                          "timeline name the variation builds beside"}
    blocked = _repo_ok(project_folder)
    if blocked:
        return {"created": False, "reason": blocked}
    branch = variant_branch_name(reel_number, spec["suffix"])
    exists = store.git(project_folder, "rev-parse", "--verify", branch)
    if exists.returncode == 0:
        return {"created": False,
                "reason": f"branch {branch} already exists"}
    checkout = store.git(project_folder, "checkout", "-b", branch)
    if checkout.returncode != 0:
        return {"created": False,
                "reason": f"git checkout -b failed: "
                          f"{checkout.stderr.strip()[-200:]}"}
    try:
        record = read_variant_specs(project_folder)
        variants = record.setdefault("variants", {})
        reel_key = str(int(reel_number))
        reel_specs = [s for s in variants.get(reel_key, [])
                      if s.get("suffix") != spec["suffix"]]
        reel_specs.append(dict(spec))
        variants[reel_key] = reel_specs
        write_variant_specs(project_folder, record)
    except ValueError as exc:
        store.git(project_folder, "checkout", "-")
        store.git(project_folder, "branch", "-D", branch)
        return {"created": False, "reason": str(exc)}
    add = store.git(project_folder, "add",
               "pipeline_output/review/" + VARIANTS_FILENAME)
    if add.returncode != 0:
        return {"created": False,
                "reason": f"git add failed: {add.stderr.strip()[-200:]}"}
    timeline_name = variant_timeline_name(base_timeline_name.strip(),
                                          spec["suffix"])
    msg = (message or
           f"variation {timeline_name}\n\n"
           f"Branch {branch} from the current state; the seam spec "
           f"({', '.join(k for k in SPEC_KEYS if k in spec)}) is "
           f"recorded in {VARIANTS_FILENAME} so a rebuild needs "
           f"nothing off-branch. Build into {timeline_name!r} beside "
           f"{base_timeline_name.strip()!r}.")
    commit = store.git(project_folder, "commit", "-m", msg)
    if commit.returncode != 0:
        return {"created": False,
                "reason": f"git commit failed: "
                          f"{commit.stderr.strip()[-400:]}"}
    rev = store.git(project_folder, "rev-parse", "--short", "HEAD")
    return {"created": True, "branch": branch,
            "timeline_name": timeline_name,
            "commit": rev.stdout.strip()}


def _is_generated(rel_path: str) -> bool:
    if any(rel_path == g or rel_path.startswith(g + "/")
           for g in GENERATED_PATHS):
        return True
    # Old projects can still have flat external files before `organize`
    # moves them. Treat registered state as generated there too, while
    # declaration files remain ordinary merge conflicts.
    if rel_path.startswith("external/") and rel_path.count("/") == 1:
        from library.tools.external_inputs import CHECKS, declaration_stems

        stem = Path(rel_path).stem
        return stem in CHECKS and stem not in declaration_stems()
    return False


def merge_variations(project_folder: str, source_branch: str,
                     target_branch: str | None = None,
                     semantic_only: bool = False) -> dict:
    """Git merge of one variation into the target, then stop for the rebuild.

    Checks out the target, merges the source, and auto-resolves
    conflicting GENERATED paths to the target side - that state is
    rebuilt right after the merge, and asking a human to hand-merge
    two ledgers is how a feature goes missing quietly. Declaration
    conflicts (specs, captain_edits, project.yaml, proposals, marker
    pulls cannot conflict) are left in the tree UNCOMMITTED for a
    human; generated auto-resolutions are listed in the report so the
    rebuild knows what it must regenerate. `semantic_only=True` is the
    task-worktree path: every generated source change is restored to the
    target, and semantic conflicts abort so task finish can be retried.

    SAFETY (issue #925): do NOT treat a clean merge as a green build.
    Merging declarations is precisely where a dropped feature hides -
    the plan gate cannot fail on a feature the plan does not name -
    so the merged tree must be rebuilt and the rebuilt timeline read
    back against BOTH variations' timelines before anything is
    promoted. Until the promote diff lands, this workflow ships WITH
    that manual read-back, never without it.
    """
    blocked = _repo_ok(project_folder)
    if blocked:
        return {"merged": False, "reason": blocked}
    if target_branch:
        checkout = store.git(project_folder, "checkout", target_branch)
        if checkout.returncode != 0:
            return {"merged": False,
                    "reason": f"cannot check out {target_branch}: "
                              f"{checkout.stderr.strip()[-200:]}"}
    current = store.git(project_folder, "rev-parse", "--abbrev-ref", "HEAD")
    target = current.stdout.strip()
    generated_source_changes = []
    semantic_source_changes = []
    target_generated_files = {}
    if semantic_only:
        changed = store.git(project_folder, "diff", "--name-only",
                            f"{target}...{source_branch}")
        if changed.returncode != 0:
            return {"merged": False,
                    "reason": "cannot compare task branch with target: "
                              f"{changed.stderr.strip()[-200:]}"}
        paths = sorted(p for p in changed.stdout.splitlines() if p.strip())
        generated_source_changes = [p for p in paths if _is_generated(p)]
        semantic_source_changes = [p for p in paths if not _is_generated(p)]
        if not semantic_source_changes:
            rev = store.git(project_folder, "rev-parse", "--short", "HEAD")
            return {"merged": True, "target": target,
                    "source": source_branch, "commit": rev.stdout.strip(),
                    "auto_resolved": [], "conflicts": [],
                    "semantic_changes": [],
                    "generated_discarded": generated_source_changes,
                    "note": "no semantic changes; generated task state "
                            "was not merged"}
        root = Path(project_folder)
        target_generated_files = {
            path: (store.git(project_folder, "cat-file", "-e",
                             f"HEAD:{path}").returncode == 0,
                   (root / path).exists())
            for path in generated_source_changes
        }
    merged = store.git(project_folder, "merge", "--no-commit", "--no-ff",
                  source_branch)
    if semantic_only:
        restored = _restore_generated_changes(
            project_folder, generated_source_changes, target_generated_files)
        if restored:
            store.git(project_folder, "merge", "--abort")
            return {"merged": False,
                    "reason": "could not discard generated task state: "
                              + restored}
    if merged.returncode == 0:
        if "Already up to date" in (merged.stdout or ""):
            rev = store.git(project_folder, "rev-parse", "--short", "HEAD")
            result = {"merged": True, "target": target,
                      "source": source_branch,
                      "commit": rev.stdout.strip(),
                      "auto_resolved": [], "conflicts": [],
                      "note": "already up to date - nothing to merge, "
                              "nothing to rebuild"}
            if semantic_only:
                result.update(semantic_changes=[],
                              generated_discarded=generated_source_changes)
            return result
        # A clean merge still needs its merge commit recorded.
        commit = store.git(project_folder, "commit", "--no-edit")
        if commit.returncode != 0:
            store.git(project_folder, "merge", "--abort")
            return {"merged": False,
                    "reason": f"merge commit failed: "
                              f"{commit.stderr.strip()[-200:]}"}
        rev = store.git(project_folder, "rev-parse", "--short", "HEAD")
        result = {"merged": True, "target": target,
                  "source": source_branch, "commit": rev.stdout.strip(),
                  "auto_resolved": [], "conflicts": [],
                  "note": "clean merge - rebuild into the final timeline "
                          "and read it back against both variations "
                          "before promoting (issue #925)"}
        if semantic_only:
            result.update(semantic_changes=semantic_source_changes,
                          generated_discarded=generated_source_changes,
                          note="semantic state merged; generated task "
                               "state was kept out of the target")
        return result
    unmerged = store.git(project_folder, "diff", "--name-only",
                    "--diff-filter=U")
    if unmerged.returncode != 0:
        store.git(project_folder, "merge", "--abort")
        return {"merged": False, "reason": "git merge failed and the "
                                           "conflicted files cannot be "
                                           "listed; merge aborted"}
    conflicted = [p for p in unmerged.stdout.splitlines() if p.strip()]
    if semantic_only and conflicted:
        store.git(project_folder, "merge", "--abort")
        return {"merged": False, "target": target,
                "source": source_branch, "auto_resolved": [],
                "conflicts": sorted(conflicted),
                "generated_discarded": generated_source_changes,
                "reason": "semantic task changes conflict; merge was "
                          "aborted so the task can be revised and finished "
                          "again"}
    auto = [p for p in conflicted if _is_generated(p)]
    human = [p for p in conflicted if not _is_generated(p)]
    for path in auto:
        resolved = store.git(project_folder, "checkout", "--ours", "--", path)
        if resolved.returncode != 0:
            store.git(project_folder, "merge", "--abort")
            return {"merged": False,
                    "reason": f"cannot auto-resolve {path}; merge aborted"}
        store.git(project_folder, "add", "--", path)
    if human:
        return {"merged": False, "target": target,
                "source": source_branch, "auto_resolved": sorted(auto),
                "conflicts": sorted(human),
                "reason": "declaration conflicts need a human - the "
                          "merge is staged in the tree, generated "
                          "paths already resolved to the target side; "
                          "resolve, rebuild, read back both variations, "
                          "then commit"}
    commit = store.git(project_folder, "commit", "--no-edit")
    if commit.returncode != 0:
        store.git(project_folder, "merge", "--abort")
        return {"merged": False,
                "reason": f"merge commit failed: "
                          f"{commit.stderr.strip()[-200:]}"}
    rev = store.git(project_folder, "rev-parse", "--short", "HEAD")
    result = {"merged": True, "target": target, "source": source_branch,
              "commit": rev.stdout.strip(), "auto_resolved": sorted(auto),
              "conflicts": [],
              "note": "generated state resolved to the target side - the "
                      "rebuild must regenerate every path listed in "
                      "auto_resolved, then read the final timeline back "
                      "against both variations before promoting "
                      "(issue #925)"}
    if semantic_only:
        result.update(semantic_changes=semantic_source_changes,
                      generated_discarded=generated_source_changes,
                      note="semantic state merged; generated task state "
                           "was kept out of the target")
    return result


def _restore_generated_changes(project_folder: str, paths: list[str],
                               target_files: dict) -> str:
    """Drop task-generated paths from an in-progress merge.

    A task worktree is the place for semantic work. Its generated run
    outputs may be useful while the task runs, but must not become the
    target project's state at finish; a later build runs from the merged
    declarations. Files already present in the target checkout but
    untracked by git are preserved.
    """
    root = Path(project_folder)
    for path in paths:
        target_tracked, existed_before = target_files[path]
        if target_tracked:
            proc = store.git(project_folder, "restore", "--source=HEAD",
                             "--staged", "--worktree", "--", path)
        else:
            proc = store.git(project_folder, "rm", "--cached", "--force",
                             "--ignore-unmatch", "--", path)
            if proc.returncode == 0 and not existed_before:
                generated = root / path
                try:
                    generated.unlink(missing_ok=True)
                except OSError as exc:
                    return f"cannot remove {path}: {exc}"
        if proc.returncode != 0:
            return f"git could not restore {path}: {proc.stderr.strip()[-200:]}"
    return ""


# ══ What is built, compared and chosen ═════════════════════════════

BUILDS_FILENAME = "reel_variant_builds.json"
BUILDS_FORMAT = "reel_variant_builds/1"

RETAINED_UNCHOSEN = 1
"""How many unchosen variant generations of one REEL survive a choice.

One: the runner-up of the most recent comparison, which is the version
somebody asks to see again. This is a RETENTION bound, not a timeout -
nothing expires with the clock - and it is the reason a routine variant
workflow cannot become the clutter the captain has complained about.
"""


class ChoiceRefused(RenRefusal):
    """The choice declined to proceed, and says exactly why."""


# ── What is alive ────────────────────────────────────────────────

def builds_path_for(project_folder) -> str:
    """`pipeline_output/review/reel_variant_builds.json`."""
    return os.path.join(str(project_folder), "pipeline_output", "review",
                        BUILDS_FILENAME)


def read_builds(project_folder) -> dict:
    """The built-variant record, or an empty one.

    Unreadable is EMPTY here rather than a raise, because this record
    is written inside a build: a malformed one must not take a
    conformance-clean variant down with it. Every reader below states
    what it found, so an empty answer cannot be mistaken for "no
    variants were built".
    """
    path = builds_path_for(project_folder)
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return {"format": BUILDS_FORMAT, "builds": {}}
    if not isinstance(document, dict):
        return {"format": BUILDS_FORMAT, "builds": {}}
    document.setdefault("format", BUILDS_FORMAT)
    document.setdefault("builds", {})
    return document


def _write_builds(project_folder, document: Mapping) -> str:
    # A half-written record of what is alive would be read as a variant
    # that is not there - `store.write_record` lands whole or not at all.
    return store.write_record(builds_path_for(project_folder), document,
                              prefix=".variant-builds-")


def record_build(project_folder, reel_number: int, base_final: str,
                 suffix: str, rows: Mapping, watch: str = "",
                 declares: Sequence[str] = (), built_at: str = "") -> dict:
    """Record one built variant: what it is and what it contains.

    Keyed by the variant's TIMELINE NAME, which is the only identity
    Resolve and the captain share. Re-recording the same name replaces
    the entry: a rebuilt variant is the same variant with a new
    picture, and two rows snapshots for one live timeline is exactly the
    disagreement AGENTS.md 10.1 keeps catching.
    """
    final = variant_timeline_name(str(base_final), str(suffix))
    document = read_builds(project_folder)
    document["builds"][final] = {
        "reel": int(reel_number),
        "base_final": str(base_final),
        "suffix": str(suffix),
        "timeline": final,
        "watch": str(watch or ""),
        "declares": sorted(str(name) for name in (declares or ())),
        "built_at": str(built_at
                        or datetime.now(timezone.utc).isoformat()),
        "rows": dict(rows or {}),
    }
    document["format"] = BUILDS_FORMAT
    _write_builds(project_folder, document)
    return document["builds"][final]


def forget_builds(project_folder, timelines: Sequence[str]) -> list:
    """Drop build records for timelines that are no longer alive.

    Called by `choose` once the renames have landed: a record that
    still names `Reel 09 - ... (reaction-cutaway)` after that timeline
    became the reel (or went to the archive) claims something alive
    that is not.
    """
    document = read_builds(project_folder)
    gone = [name for name in (timelines or ())
            if document["builds"].pop(name, None) is not None]
    if gone:
        _write_builds(project_folder, document)
    return gone


def builds_for_reel(project_folder, reel_number: int) -> list:
    """Every recorded variant build of one reel, suffix-ordered."""
    document = read_builds(project_folder)
    entries = [entry for entry in (document.get("builds") or {}).values()
               if isinstance(entry, dict)
               and entry.get("reel") == int(reel_number)]
    return sorted(entries, key=lambda entry: str(entry.get("suffix", "")))


def build_for(project_folder, reel_number: int, suffix: str):
    """One reel's recorded build carrying this suffix, or None."""
    for entry in builds_for_reel(project_folder, reel_number):
        if entry.get("suffix") == suffix:
            return entry
    return None


# ── Compared, off disk ───────────────────────────────────────────

def compare(project_folder, reel_number: int, earlier_suffix: str,
            later_suffix: str) -> dict:
    """What differs between two built variants of one reel.

    Both must have been built and recorded; a missing side RAISES
    rather than diffing against nothing, for `rounds.diff_rounds`'s
    reason - an empty side reads exactly like a version that contained
    nothing, and the two must never be confused.
    """
    entries = {}
    for suffix in (earlier_suffix, later_suffix):
        entry = build_for(project_folder, reel_number, suffix)
        if entry is None:
            known = [str(e.get("suffix", ""))
                     for e in builds_for_reel(project_folder, reel_number)]
            raise ChoiceRefused(
                f"reel {reel_number} has no recorded build of variant "
                f"{suffix!r}",
                f"built and recorded: {known or 'none'} - a comparison "
                f"against nothing reads like a comparison against an "
                f"empty cut",
                f"build the variant first (`ren variant <project> build "
                f"{reel_number}`), then compare")
        entries[suffix] = entry
    diff = rounds.diff_reel(entries[earlier_suffix].get("rows") or {},
                                entries[later_suffix].get("rows") or {})
    diff["reel"] = int(reel_number)
    diff["earlier"] = entries[earlier_suffix]["timeline"]
    diff["later"] = entries[later_suffix]["timeline"]
    diff["earlier_watch"] = entries[earlier_suffix].get("watch", "")
    diff["later_watch"] = entries[later_suffix].get("watch", "")
    return diff


def render_comparison(diff: Mapping, show_items: int = 3) -> str:
    """The comparison, in the sentences a captain reads before watching.

    Per-row, through `rounds.row_line` - the same spelling
    a round diff uses, so two versions of a reel and two rounds of a
    reel read the same way rather than two vocabularies for one
    measurement.
    """
    lines = [f"── Reel {diff.get('reel')}: two versions ──",
             f"  A: {diff.get('earlier')}"
             + (f"  - watch: {diff['earlier_watch']}"
                if diff.get("earlier_watch") else ""),
             f"  B: {diff.get('later')}"
             + (f"  - watch: {diff['later_watch']}"
                if diff.get("later_watch") else "")]
    rerendered = diff.get("rerendered") or []
    changed = diff.get("changed") or []
    if not changed:
        lines.append("  Nothing differs between them"
                     + (f" ({len(rerendered)} row(s) re-rendered at "
                        f"identical spans)" if rerendered else "")
                     + " - two builds of one treatment, not a "
                       "comparison.")
        return "\n".join(lines)
    lines.append(f"  {len(changed)} row(s) differ:")
    for row in rerendered:
        lines.append(f"    {row['key']}: {row['later_count']} item(s) "
                     f"re-rendered at identical spans - nothing moved")
    for row in changed:
        lines.append("  " + rounds.row_line(row).strip())
        for item in (row.get("gone") or [])[:show_items]:
            lines.append(f"      only in A: {item['name']!r} "
                         f"@{item['start']}..{item['end']} "
                         f"({item['duration']}f)")
        if len(row.get("gone") or []) > show_items:
            lines.append(f"      ... and {len(row['gone']) - show_items} "
                         f"more only in A")
        for item in (row.get("gained") or [])[:show_items]:
            lines.append(f"      only in B: {item['name']!r} "
                         f"@{item['start']}..{item['end']} "
                         f"({item['duration']}f)")
        if len(row.get("gained") or []) > show_items:
            lines.append(f"      ... and "
                         f"{len(row['gained']) - show_items} more only "
                         f"in B")
    return "\n".join(lines)


# ── The choice, planned purely ───────────────────────────────────

def variant_generations(existing_names, variant_names) -> list:
    """Archived generations belonging to a reel's VARIANTS, newest first.

    Grouped across every variant identity of the reel rather than per
    suffix, which is what makes the retention bound a bound on the reel
    (see the module docstring). `variant_names` is the reel's declared
    variant timeline names - exact through the spec record, because no
    syntactic rule tells `(final)` from `(reaction-cutaway)`.
    """
    wanted = set(variant_names or ())
    found = []
    for name in existing_names or ():
        reel, number = retire.parse_archived(name)
        if reel in wanted:
            found.append((number, name))
    return [name for _number, name in sorted(found, reverse=True)]


def plan_choice(existing_names, base_final: str, variant_names,
                chosen: str, losers, round_number: int,
                signed_off_reels=()) -> dict:
    """Every rename and every deletion this choice will perform.

    Pure: handed the names that exist, it answers off them, so the whole
    policy is testable with no Resolve and the Resolve half below holds
    none of it. `chosen` and `losers` are variant TIMELINE names.

    Returns `{"promote", "retire", "archive", "collect", "kept"}`:
    `promote` is `(chosen, base_final)`; `retire` is the incumbent
    holding the reel's name, if one is there; `archive` maps each loser
    to its archived name; `collect` is what the retention bound
    releases; `kept` says why each survivor survived.
    """
    existing = list(existing_names or ())
    live = set(existing)
    signed = {str(reel) for reel in (signed_off_reels or ())}
    if chosen not in live:
        raise ChoiceRefused(
            f"{chosen!r} is not a timeline in this project, so it "
            f"cannot become {base_final!r}",
            "a choice never creates the thing it chooses",
            "build the variant first, then choose it")
    taken = set(existing)
    plan = {"promote": (chosen, base_final), "retire": None,
            "archive": {}, "collect": [], "kept": []}
    if base_final in live:
        name = retire.archived_name(base_final, round_number, taken)
        taken.add(name)
        plan["retire"] = (base_final, name)
    for loser in sorted(set(losers or ())):
        if loser not in live or loser == chosen:
            continue
        name = retire.archived_name(loser, round_number, taken)
        taken.add(name)
        plan["archive"][loser] = name

    # The retention bound, asked of the names that will exist AFTER the
    # renames: the runner-up just archived is a generation like any
    # other, and a bound that exempted it would keep one more every
    # time.
    after = (live - {chosen, base_final} - set(plan["archive"]))
    after |= set(plan["archive"].values())
    after.add(base_final)
    if plan["retire"]:
        after.add(plan["retire"][1])
    for position, name in enumerate(
            variant_generations(after, variant_names)):
        if position < RETAINED_UNCHOSEN:
            plan["kept"].append({
                "name": name,
                "why": f"the most recent unchosen variant of "
                       f"{base_final!r} - the runner-up of this "
                       f"comparison"})
            continue
        parsed, _number = retire.parse_archived(name)
        if parsed in signed:
            plan["kept"].append({
                "name": name,
                "why": f"{parsed!r} carries a captain sign-off, and "
                       f"the cut they approved is never collected"})
            continue
        plan["collect"].append(name)

    # And the REEL's own retired generations, through the bound that
    # already owns them (`reel_retirement.plan_collection`) rather than
    # a second rule here. A choice retires the version that held the
    # name exactly as a promotion does, so without this a project that
    # chooses repeatedly grows one archived generation of the reel per
    # choice - the accumulation this whole module is bounded against,
    # arriving by the other door. Measured 2026-09-12 against a real
    # Resolve: two choices left `(archived round 001)` AND
    # `(archived round 001.2)` standing.
    incumbent = retire.plan_collection(sorted(after), [base_final], signed)
    plan["collect"].extend(incumbent["collect"])
    plan["kept"].extend(incumbent["kept"])
    return plan


# ── The Resolve half ─────────────────────────────────────────────

def _timelines_by_name(project) -> dict:
    found = {}
    for index in range(1, project.GetTimelineCount() + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline:
            found[timeline.GetName()] = timeline
    return found


@under_lease("choose reel variant")
def choose(project, pool, project_folder, reel_number: int,
           base_final: str, chosen_suffix: str, why: str,
           variant_names=(), supersede_declared=(),
           promoted_at: str | None = None,
           accept_editor_changes=None) -> dict:
    """Make one variant the reel. The other goes to the archive.

    The order is the promotion's order and for its reasons: the
    incumbent is retired FIRST (so the reel's name is free), the chosen
    variant takes the name SECOND, and the sign-off bookkeeping happens
    LAST - a choice that refuses partway must never have already retired
    an approval it did not replace.

    `why` is required. `variant_names` is the reel's declared variant
    timeline names (`declared_variant_names`).
    """
    from library.tools import reel_signoff as signoff
    if not str(why or "").strip():
        raise ChoiceRefused(
            "a choice with no reason",
            "a choice with no reason is not a decision anybody can read "
            "later",
            "re-run with --why saying why this treatment won")
    chosen = variant_timeline_name(str(base_final), str(chosen_suffix))
    live = _timelines_by_name(project)
    from library.tools import plan_provenance
    from library.tools import reel_replace_guard
    review_dir = os.path.join(str(project_folder), "pipeline_output", "review")
    inventory_before = reel_replace_guard.timeline_inventory(project)
    inventory_operation = plan_provenance.begin_timeline_inventory(
        review_dir, "variant choice", inventory_before,
        ren_created_names={base_final, chosen} | set(variant_names or ()))
    protected = plan_provenance.protected_timeline_names(
        review_dir, inventory_before)
    try:
        if base_final in live:
            plan_provenance.assert_not_editor_timeline(
                review_dir, live[base_final])
        if chosen in live:
            plan_provenance.assert_not_editor_timeline(
                review_dir, live[chosen])
        if chosen in protected:
            raise reel_replace_guard.EditorChangeRefused(
                f"REFUSING to choose {chosen!r}: it is a timeline created "
                f"or renamed by the editor and remains untouched.")
    except reel_replace_guard.EditorChangeRefused as changed:
        plan_provenance.finish_timeline_inventory(
            review_dir, inventory_operation,
            reel_replace_guard.timeline_inventory(project))
        raise ChoiceRefused(
            str(changed), "an editor-owned timeline cannot be promoted",
            "build a Ren variant under its declared name, then choose it") \
            from changed

    # The sign-off on the INCUMBENT: the chosen variant is taking that
    # reel's name, which is exactly the replacement a sign-off exists
    # to catch. Declare-then-proceed, in `reel_signoff`'s own words.
    incumbent_signoff = None
    if base_final in live:
        incumbent_signoff = signoff.assert_declared(
            project_folder, base_final, supersede_declared,
            command=f"variant <project> choose {int(reel_number)} "
                    f"'{chosen_suffix}' --why '...'")

    recorded = rounds.discover(project_folder)
    current_round = recorded[-1]["round"] if recorded else 1
    losers = [entry["timeline"]
              for entry in builds_for_reel(project_folder, reel_number)
              if entry.get("timeline") != chosen]
    plan_live = {name: timeline for name, timeline in live.items()
                 if name not in protected}
    plan = plan_choice(sorted(plan_live), base_final, variant_names, chosen,
                       losers, retire.retiring_round(
                           recorded, base_final, current_round),
                       set(signoff.signed_off(project_folder)))
    editor_report = None
    chosen_editor_report = None
    incumbent_snapshot = None
    chosen_snapshot = reel_replace_guard.full_timeline_snapshot(
        live[chosen], project, project_folder)
    chosen_editor_report = reel_replace_guard.protect_editor_changes(
        str(project_folder), chosen, chosen_snapshot, chosen_snapshot,
        chosen_snapshot, accept=reel_replace_guard.accepts_editor_changes(
            base_final, accept_editor_changes))
    plan_provenance.record_timeline_snapshot(
        review_dir, chosen, chosen_snapshot,
        action="before_variant_choice", last_known=False)
    if base_final in live:
        incumbent_snapshot = reel_replace_guard.full_timeline_snapshot(
            live[base_final], project, project_folder)
        plan_provenance.record_timeline_snapshot(
            review_dir, base_final, incumbent_snapshot,
            action="before_variant_choice", last_known=False)
        try:
            # The incumbent's carried edits land on the chosen variant
            # before it takes the name (`editor_edit_carry`).
            from library.tools import editor_edit_carry
            accept_editor = reel_replace_guard.accepts_editor_changes(
                base_final, accept_editor_changes)
            detection = reel_replace_guard.detect_editor_changes(
                str(project_folder), base_final, incumbent_snapshot,
                chosen_snapshot)
            carry_report = editor_edit_carry.carry_editor_edits(
                str(project_folder), base_final, project, live[chosen],
                lambda: reel_replace_guard.full_timeline_snapshot(
                    live[chosen], project, project_folder),
                detection, accept=accept_editor)
            if carry_report["written"]:
                chosen_snapshot = reel_replace_guard.full_timeline_snapshot(
                    live[chosen], project, project_folder)
            editor_edit_carry.verify_carried_edits(
                carry_report, chosen_snapshot, base_final)
            editor_report = reel_replace_guard.protect_editor_changes(
                str(project_folder), base_final, incumbent_snapshot,
                chosen_snapshot, chosen_snapshot, accept=accept_editor,
                detection=detection, carried_edits=carry_report)
        except reel_replace_guard.EditorChangeRefused:
            plan_provenance.finish_timeline_inventory(
                review_dir, inventory_operation,
                reel_replace_guard.timeline_inventory(project))
            raise

    report = {"reel": int(reel_number), "chosen": chosen,
              "base_final": base_final, "why": str(why),
              "retired": None, "archived": {}, "unfiled": [],
              "collected": [], "kept": plan["kept"],
              "round": current_round}
    report["protected_timelines"] = sorted(protected)

    try:
        reel_replace_guard.assert_target_inventory_unchanged(
            inventory_before, reel_replace_guard.timeline_inventory(project),
            [base_final])
        if chosen in live:
            plan_provenance.assert_not_editor_timeline(
                review_dir, live[chosen])
    except reel_replace_guard.EditorChangeRefused as changed:
        plan_provenance.finish_timeline_inventory(
            review_dir, inventory_operation,
            reel_replace_guard.timeline_inventory(project))
        raise ChoiceRefused(
            str(changed), "the timeline identity changed during the choice",
            "re-read the timeline inventory, then re-run the choice") \
            from changed

    if plan["retire"]:
        outcome = retire.retire_timelines(
            project, pool, {base_final: live[base_final]},
            {base_final: retire.parse_archived(plan["retire"][1])[1]})
        report["retired"] = outcome["archived"].get(base_final)
        report["unfiled"].extend(outcome["unfiled"])

    if not live[chosen].SetName(base_final):
        raise ChoiceRefused(
            f"Resolve would not rename {chosen!r} to {base_final!r}",
            f"Nothing was deleted: the version that held the reel's "
            f"name is safe under {report['retired']!r} and the chosen "
            f"variant is still under its own",
            "rename it in Resolve and re-run")

    chosen_after = reel_replace_guard.full_timeline_snapshot(
        live[chosen], project, project_folder)
    if live[chosen].GetName() != base_final:
        raise ChoiceRefused(
            f"Resolve did not verify the chosen timeline name "
            f"{base_final!r} after renaming.",
            f"the chosen timeline is still named {live[chosen].GetName()!r}; "
            f"the prior version remains recoverable as {report['retired']!r}",
            "settle the timeline names in Resolve, then re-run the choice")
    plan_provenance.record_timeline_snapshot(
        review_dir, base_final, chosen_after, action="variant_choice",
        action_journal=f"variant:{chosen}->{base_final}:{why}")
    from library.tools import editor_edit_carry
    editor_edit_carry.record_after_promotion(
        str(project_folder), base_final,
        (editor_report or {}).get("carried_edits"), chosen_after,
        act=f"variant choice {chosen!r}")
    ren_owned_ids = set()
    for timeline in (live[chosen], live.get(base_final)):
        if timeline is None:
            continue
        try:
            unique_id = timeline.GetUniqueId()
        except Exception:  # noqa: BLE001
            unique_id = None
        if unique_id:
            ren_owned_ids.add(str(unique_id))
    plan_provenance.record_unattributed_timeline_changes(
        review_dir, inventory_before,
        reel_replace_guard.timeline_inventory(project),
        operation="variant choice", ren_owned_ids=ren_owned_ids)
    if editor_report:
        for record_id in editor_report.get("carried", ()):
            plan_provenance.resolve_editor_change(
                review_dir, base_final, record_id, status="carried")
        for record_id in editor_report.get("superseded", ()):
            plan_provenance.resolve_editor_change(
                review_dir, base_final, record_id, status="superseded",
                superseded_by=f"--accept-editor-changes {base_final}")
    for record_id in chosen_editor_report.get("carried", ()):
        plan_provenance.resolve_editor_change(
            review_dir, chosen, record_id, status="carried")
    for record_id in chosen_editor_report.get("superseded", ()):
        plan_provenance.resolve_editor_change(
            review_dir, chosen, record_id, status="superseded",
            superseded_by=f"--accept-editor-changes {base_final}")

    losing = {name: live[name] for name in plan["archive"] if name in live}
    if losing:
        outcome = retire.retire_timelines(
            project, pool, losing,
            {name: retire.parse_archived(plan["archive"][name])[1]
             for name in losing})
        report["archived"] = outcome["archived"]
        report["unfiled"].extend(outcome["unfiled"])

    after = set(_timelines_by_name(project))
    releasable = [name for name in plan["collect"] if name in after]
    if releasable:
        from library.tools.reel_build import assert_deletion_scope, timelines_to_replace
        targets = timelines_to_replace(project, set(releasable))
        assert_deletion_scope(targets, set(releasable))
        if targets:
            # Named BEFORE the delete: a deleted timeline object answers
            # `GetName()` with None (`reel_retirement.collect_superseded`
            # carries the same note and the same fix).
            collected = sorted(name for name in
                               (t.GetName() for t in targets) if name)
            # Judged by what Resolve RETURNS, never by `hasattr`
            # (AGENTS.md 5): a falsy delete RAISES rather than reporting
            # the names collected - the same defect PR 1149 fixed in
            # `reel_retirement.collect_superseded`, measured 2026-09-14
            # against a real Resolve
            # (`docs/LIVE_DEMO_1107_COMPARISON_RETIREMENT.md`). Refusal
            # for that PR's reason, established of THIS caller rather
            # than assumed from it: the sole production caller
            # (`manage_project.py variant choose`) already wraps the
            # choice in a handled refusal path (`REFUSED: ...`, exit 1).
            # The choice's renames already landed; only the retention
            # cleanup did not, and the next choice plans the
            # still-present generation again.
            if not pool.DeleteTimelines(targets):
                raise ChoiceRefused(
                    f"Resolve declined to delete {len(collected)} "
                    f"superseded generation(s) ({', '.join(collected)})",
                    "they are still in the project under their archived "
                    "names - nothing was reported collected. The "
                    "choice's renames already landed; only the "
                    "retention cleanup did not",
                    "delete the archived generations in Resolve by hand, "
                    "or re-run the choice - the next one plans the "
                    "still-present generations again")
            report["collected"] = collected

    # The record follows the picture. A sign-off the CHOSEN variant
    # carried moves onto the reel's name - the cut did not change, its
    # container did - and the incumbent's declared one is superseded,
    # never deleted.
    carried = signoff.signoff_for(project_folder, chosen)
    if incumbent_signoff is not None:
        signoff.supersede(project_folder, base_final,
                          round_number=current_round)
        report["superseded_signoff"] = incumbent_signoff
    if carried is not None:
        signoff.withdraw(project_folder, chosen,
                         why=f"chosen as {base_final!r}; the sign-off "
                             f"follows the cut onto the reel's name")
        signoff.sign_off(project_folder, base_final,
                         note=carried.get("note", ""),
                         by=carried.get("by", "captain"),
                         round_number=current_round)
        report["carried_signoff"] = carried

    # The rows of the cut that won, read off the build record BEFORE it
    # is forgotten - they were measured when the variant was built and
    # are the same snapshot a promotion stores, so the round diff can
    # compare the next round against this one with no Resolve.
    chosen_entry = build_for(project_folder, reel_number, chosen_suffix)
    chosen_rows = dict((chosen_entry or {}).get("rows") or {})
    forget_builds(project_folder, [chosen] + list(plan["archive"]))

    # The round records the CHOICE, not only the promotion: which
    # treatment won, what it won over, and why. Never fatal, for
    # `stamp_promotion`'s reason - a version record that fails a choice
    # already landed in Resolve is worse than no version record.
    try:
        from library.tools.plan_provenance import read_provenance
        try:
            provenance = read_provenance(
                os.path.join(str(project_folder), "pipeline_output",
                             "review"))
        except Exception:                                   # noqa: BLE001
            provenance = None
        stamped = rounds.stamp_promotion(
            project_folder, {base_final: chosen_rows}, provenance,
            promoted_at=promoted_at,
            choices={base_final: {
                "chosen": str(chosen_suffix),
                "chosen_timeline": chosen,
                "over": sorted(plan["archive"]),
                "why": str(why),
            }})
        report["round"] = stamped["round"]
    except Exception as stamp_failed:                       # noqa: BLE001
        report["round_not_stamped"] = f"{stamp_failed}"
    plan_provenance.finish_timeline_inventory(
        review_dir, inventory_operation,
        reel_replace_guard.timeline_inventory(project))
    report["editor_changes"] = editor_report or {
        "first_contact": base_final not in live,
        "carried": [], "superseded": [], "uncarried": []}
    report["chosen_editor_changes"] = chosen_editor_report
    return report


def render_choice(report: Mapping) -> str:
    """What the choice did, in the sentences an operator has to read."""
    lines = [(f"Reel {report.get('reel')}: chose "
              f"{report.get('chosen')!r} as "
              f"{report.get('base_final')!r}"),
             f"  why: {report.get('why')}"]
    if report.get("retired"):
        lines.append(f"  Retired the version that held the name -> "
                     f"{report['retired']}")
    for loser in sorted((report or {}).get("archived") or {}):
        lines.append(f"  Not chosen: {loser} -> "
                     f"{report['archived'][loser]}")
    for name in (report or {}).get("unfiled") or ():
        lines.append(f"  {name!r} was renamed but not moved into "
                     f"{'/'.join(retire.ARCHIVE_BIN)}; the organiser "
                     f"files it on the next build.")
    if report.get("collected"):
        lines.append(
            f"  Collected {len(report['collected'])} superseded "
            f"generation(s) (retention: {RETAINED_UNCHOSEN} unchosen "
            f"variant per reel): {', '.join(report['collected'])}")
    for kept in (report or {}).get("kept") or ():
        lines.append(f"  Kept {kept['name']!r} - {kept['why']}")
    if report.get("carried_signoff"):
        lines.append("  The sign-off on the chosen variant follows it "
                     "onto the reel's name.")
    if report.get("superseded_signoff"):
        lines.append("  The sign-off on the version it replaced is "
                     "recorded as superseded, not deleted.")
    if report.get("round_not_stamped"):
        lines.append(f"  Round NOT stamped ({report['round_not_stamped']}) "
                     f"- the choice landed and is unaffected, but it is "
                     f"not in the version record.")
    else:
        lines.append(f"  Round {report.get('round')} records the choice.")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    """`python3 -m library.tools.versions.variants <create|merge|names>`."""
    import argparse
    import sys
    parser = argparse.ArgumentParser(
        description="Timeline variations as git branches.")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="branch and record a seam spec")
    create.add_argument("project_folder")
    create.add_argument("reel", type=int)
    create.add_argument("base_timeline")
    create.add_argument("spec_json", help="the variant spec as JSON")
    create.add_argument("--message", default=None)
    merge = sub.add_parser("merge", help="merge a variation branch")
    merge.add_argument("project_folder")
    merge.add_argument("source_branch")
    merge.add_argument("--target", default=None)
    names = sub.add_parser("names", help="derive branch/timeline names")
    names.add_argument("reel", type=int)
    names.add_argument("base_timeline")
    names.add_argument("suffix")
    args = parser.parse_args(argv)
    if args.command == "create":
        try:
            spec = json.loads(args.spec_json)
        except ValueError as exc:
            print(f"spec is not JSON: {exc}", file=sys.stderr)
            return 2
        result = create_variation(args.project_folder, args.reel,
                                  args.base_timeline, spec,
                                  message=args.message)
        print(json.dumps(result, indent=2))
        return 0 if result.get("created") else 1
    if args.command == "merge":
        result = merge_variations(args.project_folder,
                                  args.source_branch,
                                  target_branch=args.target)
        print(json.dumps(result, indent=2))
        return 0 if result.get("merged") else 1
    branch = variant_branch_name(args.reel, args.suffix)
    print(json.dumps({
        "branch": branch,
        "timeline_name": variant_timeline_name(args.base_timeline,
                                               args.suffix),
        "round_trip": branch_for_timeline(
            variant_timeline_name(args.base_timeline, args.suffix)),
    }, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
