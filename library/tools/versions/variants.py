"""VARIANTS: candidate reel versions alive beside a promoted one - declared on
a branch, built, compared and CHOSEN. Part of the version model
(`library/tools/versions/__init__.py`); AGENTS.md 3: run state.

Reached through `manage_project.py variant new|build|list|diff|approve|reject|choose|merge`.

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
   conflicts reach a human; the isolated conflict attempt is discarded
   so neither checkout is left mid-merge. Marker pulls never
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

A variant is a semantic creative branch rooted at a canonical revision.
Its DECLARATIONS live on a Git branch; its Resolve timeline is an optional
materialization recorded only after a build. The branch remains in the
canonical revision ledger when that timeline is discarded. Comparison reads
the stored structural and component manifests, then names the two timelines
for side-by-side review without reopening Resolve.

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

3. APPROVING, as a recorded decision
--------------------------------------
`approve` and `reject` record the decision, reason and exact build revision
digest. The record also carries Resolve's unique timeline id. Rebuilding a
variant replaces its revision and clears the prior decision. `choose` refuses
unless the current revision has an approval and its live timeline id still
matches.

4. CHOOSING, as an act with a consequence
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

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path

from library.tools import reel_retirement as retire
from library.tools.ren_refusal import RenRefusal
from library.tools.resolve_lock import under_lease
from library.tools.stable_json import write_stable
from library.tools.versions import edit_variants, rounds, store

VARIANT_BRANCH_PREFIX = "variant/"
"""Every variation branch reads `variant/<slug>`."""

VARIANTS_FORMAT = "reel_variants/1"

VARIANTS_FILENAME = "reel_variants.json"
"""Under `pipeline_output/review/` - versioned by the allow-list's
`review/**`, captain-reviewable beside the proposals, written only by
this module, never by a pipeline step."""

SPEC_KEYS = ("suffix", "j_cut", "cutaway", "cover", "declares", "watch",
             "base_revision", "semantic_changes")
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
    # Recovery copies belong to the target's approved reels. A branch
    # may carry an older archive, but merge keeps the target's copy and
    # a later promotion writes the next one after its comp text lands.
    "pipeline_output/review/recovery_archives",
    # What variants are ALIVE and what their pictures contain
    # (`record_build`). Rebuilt by every variant build,
    # and it describes RESOLVE's state, which is not per-branch - so
    # there is nothing on the source side a merge could preserve that
    # the rebuild will not overwrite. The record that must survive is
    # the round's, and `choose` moves the chosen rows
    # into the round BEFORE forgetting the build.
    "pipeline_output/review/reel_variant_builds.json",
)
# Generated projections stay out of the merged working tree to avoid
# irrelevant conflicts. The source commit remains the second merge parent,
# so gate decisions and provenance remain reachable as durable evidence.


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
    base_revision = spec.get("base_revision")
    if base_revision is not None and (not isinstance(base_revision, str)
                                      or not base_revision.strip()):
        errors.append("base_revision must be a non-empty revision id")
    semantic_changes = spec.get("semantic_changes")
    if semantic_changes is not None and (
            not isinstance(semantic_changes, (list, tuple))
            or not all(isinstance(change, dict) for change in semantic_changes)):
        errors.append("semantic_changes must be a list of semantic change objects")
    return errors


def produce_reel_variant_alternative(graph: dict, intent_model: dict,
                                     reel_number: int, spec: dict) -> dict:
    """Produce the edit variant alternative for a reel variant spec.

    This is the new path: a reel variant's alternative is produced
    through the general mechanism (`edit_variants.produce_reel_variant`),
    so the two systems meet. The reel variant is an edit variant of the
    region the reel was cut from.

    `spec` is a reel variant spec (the shape `validate_variant_spec`
    accepts) PLUS the edit variant keys: `region` (the node ids the
    reel's moment maps to), `dimension` (the edit dimension the reel
    difference maps to), and `value` (the variant's value). The
    reel-specific keys (`suffix`, `j_cut`, `cutaway`, `cover`,
    `declares`, `watch`) are carried on the spec but not used in the
    edit variant production - they are the reel build's own vocabulary,
    applied after the fork is chosen.

    Returns the alternative, the same shape `produce_alternatives`
    returns: the forked graph, the intent model carried alongside, and
    the spec as a flat dict.
    """
    reel_spec = edit_variants.ReelVariantSpec(
        reel_number=int(reel_number),
        base_timeline_name=str(spec.get("base_timeline_name", "")),
        suffix=str(spec.get("suffix", "")),
        region=tuple(spec.get("region", ())),
        dimension=str(spec.get("dimension", "")),
        value=tuple(spec.get("value", ())),
        trade_off=str(spec.get("trade_off", "")),
    )
    return edit_variants.produce_reel_variant(graph, intent_model, reel_spec)


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
    current = store.git(project_folder, "rev-parse", "HEAD")
    if current.returncode != 0:
        return {"created": False,
                "reason": f"could not resolve branch base: {current.stderr.strip()[-200:]}"}
    base_git_revision = current.stdout.strip()
    worktree_dir = tempfile.mkdtemp(prefix="ren-variant-create-")
    shutil.rmtree(worktree_dir)
    added = store.git(project_folder, "worktree", "add", "-b", branch,
                      worktree_dir, base_git_revision)
    if added.returncode != 0:
        return {"created": False,
                "reason": f"isolated variant worktree failed: "
                          f"{added.stderr.strip()[-200:]}"}
    try:
        from library.tools import creative_revision
        from library.tools.versions import reel_versions

        latest = next((revision for revision in reversed(
            creative_revision.list_revisions(worktree_dir))
            if str(base_timeline_name) in revision.get("affected_entities", ())), None)
        if spec.get("base_revision"):
            latest = creative_revision.get(worktree_dir,
                                           str(spec["base_revision"]))
            if latest is None:
                raise ValueError(f"base creative revision {spec['base_revision']!r} is not recorded")
        if latest is None:
            latest_version = next((entry for entry in reversed(
                reel_versions.versions_of(worktree_dir, base_timeline_name))
                if entry.get("revision_id")), None)
            latest = creative_revision.get(
                worktree_dir, latest_version["revision_id"]) if latest_version else None
        if latest is None:
            baseline_id = "declarations:" + hashlib.sha256(
                base_git_revision.encode("utf-8")).hexdigest()
            latest = creative_revision.create(
                worktree_dir, revision_id=baseline_id,
                author="ren", intent="recorded declaration base",
                git_revision=base_git_revision,
                affected_entities=[base_timeline_name],
                refs={"git_declaration": {"revision": base_git_revision}},
                status="recorded")
        spec = dict(spec)
        spec["base_revision"] = str(
            spec.get("base_revision") or latest["revision_id"])
        if spec["base_revision"] != latest["revision_id"]:
            named_base = creative_revision.get(worktree_dir,
                                               spec["base_revision"])
            if named_base is None:
                raise ValueError(f"base creative revision {spec['base_revision']!r} is not recorded")
            latest = named_base
        semantic_changes = spec.get("semantic_changes")
        if semantic_changes is None:
            semantic_changes = [{"kind": key, "value": spec[key]}
                                for key in ("j_cut", "cutaway", "cover", "declares")
                                if key in spec]
            spec["semantic_changes"] = semantic_changes
        record = read_variant_specs(worktree_dir)
        variants = record.setdefault("variants", {})
        reel_key = str(int(reel_number))
        reel_specs = [s for s in variants.get(reel_key, [])
                      if s.get("suffix") != spec["suffix"]]
        reel_specs.append(dict(spec))
        variants[reel_key] = reel_specs
        write_variant_specs(worktree_dir, record)
        from library.tools import undo_journal
        branch_record = undo_journal.create_creative_branch(
            worktree_dir, name=branch,
            base_revision=str(spec["base_revision"]),
            semantic_changes=semantic_changes,
            declarations={"spec_path": "pipeline_output/review/" + VARIANTS_FILENAME,
                          "reel": int(reel_number), "suffix": spec["suffix"],
                          "affected_entities": [base_timeline_name]},
            git_branch=branch, intent=f"variant {base_timeline_name}{spec['suffix']}")
    except ValueError as exc:
        store.git(project_folder, "worktree", "remove", "--force",
                  worktree_dir)
        store.git(project_folder, "branch", "-D", branch)
        return {"created": False, "reason": str(exc)}
    add = store.git(worktree_dir, "add",
               "pipeline_output/review/" + VARIANTS_FILENAME,
               "pipeline_output/review/branches",
               "pipeline_output/review/creative_revisions.json")
    if add.returncode != 0:
        store.git(project_folder, "worktree", "remove", "--force",
                  worktree_dir)
        store.git(project_folder, "branch", "-D", branch)
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
    commit = store.git(worktree_dir, "commit", "-m", msg)
    if commit.returncode != 0:
        store.git(project_folder, "worktree", "remove", "--force",
                  worktree_dir)
        store.git(project_folder, "branch", "-D", branch)
        return {"created": False,
                "reason": f"git commit failed: "
                          f"{commit.stderr.strip()[-400:]}"}
    rev = store.git(worktree_dir, "rev-parse", "--short", "HEAD")
    store.git(project_folder, "worktree", "remove", worktree_dir)
    return {"created": True, "branch": branch,
            "timeline_name": timeline_name,
            "commit": rev.stdout.strip(),
            "base_revision": str(spec["base_revision"]),
            "creative_branch_revision": branch_record["revision_id"],
            "materialized": False}


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


def compare_merge_readback(target_rows: dict, source_rows: dict,
                           merged_rows: dict, timeline_id: str,
                           readback_generation: int, readback_at: float,
                           merge_committed_at: float,
                           parent_acceptances: dict | None = None) -> dict:
    """Compare a fresh merged-timeline readback independently to both parents.

    The returned report is evidence for the post-merge Resolve review;
    a Git merge alone never sets this result or marks the merge verified.
    Lost rows and items stay explicit for human review because a captain
    may have accepted an intentional deletion during the merge.
    """
    from library.tools.versions import rounds

    if not isinstance(timeline_id, str) or not timeline_id.strip():
        raise ValueError("fresh Resolve timeline_id is required")
    if (isinstance(readback_generation, bool)
            or not isinstance(readback_generation, int)
            or readback_generation < 1):
        raise ValueError("fresh Resolve readback generation is required")
    if (isinstance(readback_at, bool)
            or not isinstance(readback_at, (int, float))
            or isinstance(merge_committed_at, bool)
            or not isinstance(merge_committed_at, (int, float))
            or readback_at < merge_committed_at):
        raise ValueError(
            "Resolve readback must be recorded after the merge commit")
    target = rounds.diff_reel(target_rows, merged_rows)
    source = rounds.diff_reel(source_rows, merged_rows)
    accepted = (isinstance(parent_acceptances, dict)
                and parent_acceptances.get("target") is True
                and parent_acceptances.get("source") is True)
    return {
        "timeline_id": timeline_id,
        "readback_generation": readback_generation,
        "readback_at": readback_at,
        "target_parent": target,
        "source_parent": source,
        "parent_acceptances": {
            "target": parent_acceptances.get("target") is True
            if isinstance(parent_acceptances, dict) else False,
            "source": parent_acceptances.get("source") is True
            if isinstance(parent_acceptances, dict) else False,
        },
        "verified": accepted,
        "review_required": not accepted,
    }


def _is_recovery_archive_path(rel_path: str) -> bool:
    path = Path(rel_path)
    recovery_root = (Path("pipeline_output") / "review"
                     / store.DRT_RECOVERY_DIRNAME)
    if path.suffix.lower() != ".drt":
        return False
    if path.parent == recovery_root:
        return True
    try:
        parts = path.relative_to(recovery_root).parts
    except ValueError:
        return False
    digest = path.stem
    return (len(parts) == 3 and parts[:2] == ("objects", "sha256")
            and len(digest) == 64
            and all(char in "0123456789abcdef" for char in digest))


def merge_variations(project_folder: str, source_branch: str,
                     target_branch: str | None = None,
                     semantic_only: bool = False) -> dict:
    """Merge a fixed source revision in an isolated checkout.

    The project checkout is the target's publication point, but the
    merge algorithm and conflict resolution run in a detached worktree.
    A successful result is fast-forwarded onto the same active branch;
    branch identity never changes. Conflicts are reported without leaving
    the project checkout in a half-merged state.
    """
    blocked = _repo_ok(project_folder)
    if blocked:
        return {"merged": False, "reason": blocked}
    active = current_branch(project_folder)
    if not active or active == "HEAD":
        return {"merged": False,
                "reason": "project checkout is detached; merge needs an active target branch"}
    if target_branch and target_branch != active:
        return {"merged": False, "target": active,
                "reason": f"target {target_branch!r} is not the active project "
                          f"branch {active!r}; merge will not switch the "
                          "project checkout"}
    source = store.git(project_folder, "rev-parse", "--verify",
                       f"refs/heads/{source_branch}^{{commit}}")
    if source.returncode != 0:
        return {"merged": False,
                "reason": f"source branch {source_branch!r} does not exist"}
    target = store.git(project_folder, "rev-parse", "HEAD")
    if target.returncode != 0:
        return {"merged": False, "reason": "cannot resolve active target revision"}
    target_revision = target.stdout.strip()
    source_revision = source.stdout.strip()
    worktree_dir = tempfile.mkdtemp(prefix="ren-variant-merge-")
    shutil.rmtree(worktree_dir)
    added = store.git(project_folder, "worktree", "add", "--detach",
                      worktree_dir, target_revision)
    if added.returncode != 0:
        return {"merged": False,
                "reason": f"isolated merge worktree failed: "
                          f"{added.stderr.strip()[-200:]}"}
    try:
        result = _merge_variations_in_checkout(
            worktree_dir, source_revision, semantic_only=semantic_only)
        result["target"] = active
        result["source"] = source_branch
        if result.get("merged") and result.get("commit"):
            current = store.git(project_folder, "rev-parse", "HEAD")
            if current.returncode != 0 or current.stdout.strip() != target_revision:
                return {"merged": False, "target": active,
                        "source": source_branch,
                        "reason": "active target moved during isolated merge; "
                                  "merge result was not published"}
            published = store.git(project_folder, "merge", "--ff-only",
                                  result["commit"])
            if published.returncode != 0:
                return {"merged": False, "target": active,
                        "source": source_branch,
                        "reason": "could not fast-forward isolated merge onto "
                                  f"active target: {published.stderr.strip()[-200:]}"}
            result["commit"] = store.git(
                project_folder, "rev-parse", "--short", "HEAD").stdout.strip()
        return result
    finally:
        store.git(project_folder, "worktree", "remove", "--force",
                  worktree_dir)
        shutil.rmtree(worktree_dir, ignore_errors=True)


def _merge_variations_in_checkout(project_folder: str, source_branch: str,
                                  semantic_only: bool = False) -> dict:
    """Git merge of one variation into the target, then stop for the rebuild.

    Merges the immutable source branch into the already active target,
    and auto-resolves
    conflicting GENERATED paths to the target side - that state is
    rebuilt right after the merge, and asking a human to hand-merge
    two ledgers is how a feature goes missing quietly. Declaration
    conflicts (specs, captain_edits, project.yaml, proposals, marker
    pulls cannot conflict) are reported for a human; the isolated merge
    attempt is discarded. Generated auto-resolutions are listed in the report so the
    rebuild knows what it must regenerate. `semantic_only=True` is the
    task-worktree path: every generated source change is restored to the
    target, and semantic conflicts abort so task finish can be retried.
    `.drt` recovery archives are kept out of every merge, including
    source-only adds that Git would otherwise merge without a conflict.
    A later promotion writes a fresh archive from its approved timeline.

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
    current = store.git(project_folder, "rev-parse", "--abbrev-ref", "HEAD")
    target = current.stdout.strip()
    source_ref = store.git(project_folder, "rev-parse", "--verify",
                           f"{source_branch}^{{commit}}")
    if source_ref.returncode != 0:
        return {"merged": False,
                "reason": f"source branch {source_branch!r} does not exist"}
    target_ref = store.git(project_folder, "rev-parse", "HEAD")
    target_revision = target_ref.stdout.strip()
    changed = store.git(project_folder, "diff", "--name-only",
                        f"{target}...{source_branch}")
    if changed.returncode != 0:
        return {"merged": False,
                "reason": "cannot compare source branch with target: "
                          f"{changed.stderr.strip()[-200:]}"}
    paths = sorted(p for p in changed.stdout.splitlines() if p.strip())
    recovery_archive_changes = [p for p in paths
                                if _is_recovery_archive_path(p)]
    generated_source_changes = []
    semantic_source_changes = []
    target_generated_files = {}
    if semantic_only:
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
    else:
        diffable_changes = [p for p in paths
                            if not _is_recovery_archive_path(p)]
        if paths and not diffable_changes:
            rev = store.git(project_folder, "rev-parse", "--short", "HEAD")
            return {"merged": True, "target": target,
                    "source": source_branch, "commit": rev.stdout.strip(),
                    "auto_resolved": [], "conflicts": [],
                    "recovery_archives_discarded": recovery_archive_changes,
                    "note": "no diffable changes; recovery archives were "
                            "kept out of the merge"}
        root = Path(project_folder)
        target_generated_files = {
            path: (store.git(project_folder, "cat-file", "-e",
                             f"HEAD:{path}").returncode == 0,
                   (root / path).exists())
            for path in recovery_archive_changes
        }
    merged = store.git(project_folder, "merge", "--no-commit", "--no-ff",
                  source_ref.stdout.strip())
    if semantic_only:
        restored = _restore_generated_changes(
            project_folder, generated_source_changes, target_generated_files)
        if restored:
            store.git(project_folder, "merge", "--abort")
            return {"merged": False,
                    "reason": "could not discard generated task state: "
                              + restored}
    elif recovery_archive_changes:
        restored = _restore_generated_changes(
            project_folder, recovery_archive_changes, target_generated_files)
        if restored:
            store.git(project_folder, "merge", "--abort")
            return {"merged": False,
                    "reason": "could not keep source recovery archives out "
                              "of the merge: " + restored,
                    "recovery_archives_discarded": recovery_archive_changes}
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
                  "resolve_verified": False,
                  "verification_required": {
                      "target_parent": target_revision,
                      "source_parent": source_ref.stdout.strip(),
                      "timeline_readback": "compare merged timeline against both parents"},
                  "note": "clean merge - rebuild into the final timeline "
                          "and read it back against both variations "
                          "before promoting (issue #925)"}
        if semantic_only:
            result.update(semantic_changes=semantic_source_changes,
                          generated_discarded=generated_source_changes,
                          source_evidence_revision=source_ref.stdout.strip(),
                          note="semantic state merged; generated task "
                               "state was kept out of the target")
        elif recovery_archive_changes:
            result["recovery_archives_discarded"] = \
                recovery_archive_changes
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
        result = {"merged": False, "target": target,
                "source": source_branch, "auto_resolved": sorted(auto),
                "conflicts": sorted(human),
                "reason": "declaration conflicts need a human; the isolated "
                          "merge attempt was discarded, and generated paths "
                          "were resolved to the target side in that attempt. "
                          "Resolve the declarations on the active target, "
                          "then retry the merge"}
        if recovery_archive_changes:
            result["recovery_archives_discarded"] = \
                recovery_archive_changes
        return result
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
              "resolve_verified": False,
              "verification_required": {
                  "target_parent": target_revision,
                  "source_parent": source_ref.stdout.strip(),
                  "timeline_readback": "compare merged timeline against both parents"},
              "note": "generated state resolved to the target side - the "
                      "rebuild must regenerate every path listed in "
                      "auto_resolved, then read the final timeline back "
                      "against both variations before promoting "
                      "(issue #925)"}
    if semantic_only:
        result.update(semantic_changes=semantic_source_changes,
                      generated_discarded=generated_source_changes,
                      source_evidence_revision=source_ref.stdout.strip(),
                      note="semantic state merged; generated task state "
                           "was kept out of the target")
    elif recovery_archive_changes:
        result["recovery_archives_discarded"] = recovery_archive_changes
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

    USER_MESSAGE = ("I couldn't make this choice as asked, so nothing "
                    "changed. Check the issue below and ask again with a "
                    "choice I can carry out.")


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
                 declares: Sequence[str] = (), built_at: str = "",
                 resolve_timeline_id: str = "",
                 render_cache: Mapping | None = None) -> dict:
    """Record one built variant: what it is and what it contains.

    Keyed by the variant's TIMELINE NAME, which is the only identity
    Resolve and the captain share. Re-recording the same name replaces
    the entry: a rebuilt variant is the same variant with a new
    picture, and two rows snapshots for one live timeline is exactly the
    disagreement AGENTS.md 10.1 keeps catching.
    """
    final = variant_timeline_name(str(base_final), str(suffix))
    document = read_builds(project_folder)
    built_at = str(built_at or datetime.now(UTC).isoformat())
    row_snapshot = dict(rows or {})
    revision_id = hashlib.sha256(json.dumps(
        {"reel": int(reel_number), "timeline": final,
         "built_at": built_at, "rows": row_snapshot},
        sort_keys=True, separators=(",", ":"), ensure_ascii=False,
    ).encode("utf-8")).hexdigest()
    spec = spec_by_suffix(project_folder, reel_number, suffix) or {}
    semantic_changes = list(spec.get("semantic_changes") or [
        {"kind": key, "value": spec[key]}
        for key in ("j_cut", "cutaway", "cover", "declares")
        if key in spec])
    from library.tools import creative_revision, reel_component_manifest
    from library.tools.versions import reel_versions

    component_manifest = reel_component_manifest.build_manifest(
        str(project_folder), final, row_snapshot)
    from library.tools import undo_journal
    branch_name = variant_branch_name(reel_number, suffix)
    branch_record = next((branch for branch in
                          undo_journal.list_branches(project_folder)
                          if branch.get("name") == branch_name), None)
    reel_versions.record(
        project_folder, final, kind=reel_versions.KIND_VARIANT,
        rows=row_snapshot, at=built_at, revision_id=revision_id,
        variant=str(suffix), timeline_id=str(resolve_timeline_id or ""),
        base_final=str(base_final), base_revision=str(spec.get("base_revision") or ""),
        creative_branch_revision=str(
            (branch_record or {}).get("revision_id") or ""),
        component_manifest=component_manifest, author="variant_build",
        intent=f"build {final}", operations=semantic_changes,
        git_revision=current_branch(str(project_folder)),
        verification={"rows_recorded": True,
                      "timeline_id": str(resolve_timeline_id or "")},
        revision_status="verified")
    creative_revision.create(
        project_folder, revision_id=revision_id,
        refs={"variant_build": {"timeline": final,
                                "base_revision": str(spec.get("base_revision") or ""),
                                "creative_branch_revision": str(
                                    (branch_record or {}).get("revision_id") or ""),
                                "manifest_digest": component_manifest.get("manifest_digest", "")}},
        status="verified")
    document["builds"][final] = {
        "reel": int(reel_number),
        "base_final": str(base_final),
        "suffix": str(suffix),
        "timeline": final,
        "watch": str(watch or ""),
        "declares": sorted(str(name) for name in (declares or ())),
        "built_at": built_at,
        "revision_id": revision_id,
        "base_revision": str(spec.get("base_revision") or ""),
        "creative_branch_revision": str(
            (branch_record or {}).get("revision_id") or ""),
        "semantic_changes": semantic_changes,
        "component_manifest": component_manifest,
        "render_cache": dict(render_cache or {}),
        "materialized": True,
        "resolve_timeline_id": str(resolve_timeline_id or ""),
        "rows_digest": rounds.digest_rows(row_snapshot),
        "row_count": len(row_snapshot),
        "item_count": sum((row.get("count") or 0)
                           for row in row_snapshot.values()),
    }
    document["format"] = BUILDS_FORMAT
    _write_builds(project_folder, document)
    if branch_record:
        undo_journal.record_branch_materialization(
            project_folder, branch_name, timeline=final,
            timeline_id=str(resolve_timeline_id or ""),
            revision_id=revision_id)
    return document["builds"][final]


def _rows_for_build(project_folder, entry: Mapping) -> dict:
    """Read legacy embedded rows or resolve the canonical revision rows."""
    if "rows" in entry:
        return dict(entry["rows"] or {})
    from library.tools.versions import reel_versions

    try:
        return reel_versions.rows_for_revision(
            project_folder, str(entry["revision_id"]))
    except Exception as unreadable:
        raise ChoiceRefused(
            f"variant build {entry.get('timeline', '')!r} has no readable revision rows",
            str(unreadable),
            "rebuild the variant before comparing or choosing it") from unreadable


def record_approval(project_folder, reel_number: int, suffix: str,
                    why: str, *, approved_by: str = "captain",
                    approved_at: str = "") -> dict:
    """Approve the exact currently recorded variant build revision."""
    reason = str(why or "").strip()
    if not reason:
        raise ChoiceRefused(
            "an approval with no reason",
            "the approval must explain which treatment was accepted",
            "re-run with --why describing the decision")
    entry = build_for(project_folder, reel_number, suffix)
    if entry is None:
        raise ChoiceRefused(
            f"variant {suffix!r} has no recorded build",
            "an unbuilt variant has no revision to approve",
            "build the variant, then compare and approve it")
    if not entry.get("revision_id"):
        raise ChoiceRefused(
            f"variant {suffix!r} predates revision identity recording",
            "its exact build revision cannot be established safely",
            "rebuild this variant, then compare and approve the new revision")
    document = read_builds(project_folder)
    approval = {
        "revision_id": str(entry["revision_id"]),
        "timeline": str(entry["timeline"]),
        "resolve_timeline_id": str(entry.get("resolve_timeline_id") or ""),
        "built_at": str(entry["built_at"]),
        "decision": "approved",
        "why": reason,
        "by": str(approved_by or "captain"),
        "at": str(approved_at or datetime.now(UTC).isoformat()),
    }
    document["builds"][str(entry["timeline"])] = {**entry,
                                                     "approval": approval}
    _write_builds(project_folder, document)
    from library.tools import creative_revision
    creative_revision.create(
        project_folder, revision_id=str(entry["revision_id"]),
        approval=approval, refs={"variant_review": approval}, status="approved")
    return approval


def record_rejection(project_folder, reel_number: int, suffix: str,
                     why: str, *, rejected_by: str = "captain",
                     rejected_at: str = "") -> dict:
    """Reject the exact currently recorded variant build revision."""
    reason = str(why or "").strip()
    if not reason:
        raise ChoiceRefused(
            "a rejection with no reason",
            "the rejection must explain why this treatment was declined",
            "re-run with --why describing the decision")
    entry = build_for(project_folder, reel_number, suffix)
    if entry is None:
        raise ChoiceRefused(
            f"variant {suffix!r} has no recorded build",
            "an unbuilt variant has no revision to reject",
            "build the variant first")
    if not entry.get("revision_id"):
        raise ChoiceRefused(
            f"variant {suffix!r} predates revision identity recording",
            "its exact build revision cannot be established safely",
            "rebuild this variant, then review the new revision")
    decision = {
        "revision_id": str(entry["revision_id"]),
        "timeline": str(entry["timeline"]),
        "resolve_timeline_id": str(entry.get("resolve_timeline_id") or ""),
        "built_at": str(entry["built_at"]),
        "decision": "rejected",
        "why": reason,
        "by": str(rejected_by or "captain"),
        "at": str(rejected_at or datetime.now(UTC).isoformat()),
    }
    document = read_builds(project_folder)
    document["builds"][str(entry["timeline"])] = {**entry,
                                                     "approval": decision}
    _write_builds(project_folder, document)
    from library.tools import creative_revision
    creative_revision.create(
        project_folder, revision_id=str(entry["revision_id"]),
        approval=decision, refs={"variant_review": decision}, status="rejected")
    return decision


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
    diff = rounds.diff_reel(_rows_for_build(project_folder,
                                            entries[earlier_suffix]),
                            _rows_for_build(project_folder,
                                            entries[later_suffix]))
    diff["reel"] = int(reel_number)
    diff["earlier"] = entries[earlier_suffix]["timeline"]
    diff["later"] = entries[later_suffix]["timeline"]
    diff["earlier_watch"] = entries[earlier_suffix].get("watch", "")
    diff["later_watch"] = entries[later_suffix].get("watch", "")
    diff["earlier_revision"] = entries[earlier_suffix].get("revision_id", "")
    diff["later_revision"] = entries[later_suffix].get("revision_id", "")
    from library.tools import reel_component_manifest
    diff["components"] = reel_component_manifest.diff_manifests(
        entries[earlier_suffix].get("component_manifest"),
        entries[later_suffix].get("component_manifest"))
    diff["render_cache"] = {
        "earlier": dict(entries[earlier_suffix].get("render_cache") or {}),
        "later": dict(entries[later_suffix].get("render_cache") or {}),
    }
    changed_regions = []
    earlier_rows = entries[earlier_suffix].get("rows") or {}
    for row in diff.get("changed", ()):
        spans = [{"start": item.get("start"), "end": item.get("end"),
                  "name": item.get("name"), "side": "later"}
                 for item in row.get("gained", ())]
        gone = set(row.get("gone", ()))
        old_row = earlier_rows.get(row["key"], {})
        spans.extend({"start": item.get("start"), "end": item.get("end"),
                      "name": item.get("name"), "side": "earlier"}
                     for item in old_row.get("items", ())
                     if item.get("name") in gone)
        spans = [span for span in spans
                 if span.get("start") is not None and span.get("end") is not None]
        for span in spans:
            span["preview_at_frame"] = round(
                (span["start"] + span["end"]) / 2)
        changed_regions.append({"row": row["key"], "state": row["state"],
                                "spans": spans})
    diff["changed_regions"] = changed_regions
    diff["reuse"] = {
        "unchanged_components": list(diff["components"].get("unchanged", ())),
        "requires_footage_intelligence": False,
        "render_cache": "shared content-addressed cache; hits depend on asset and renderer identity",
    }
    diff["side_by_side"] = {
        "earlier_timeline": entries[earlier_suffix]["timeline"],
        "later_timeline": entries[later_suffix]["timeline"],
    }
    diff["earlier_review"] = dict(entries[earlier_suffix].get("approval") or {})
    diff["later_review"] = dict(entries[later_suffix].get("approval") or {})
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
                if diff.get("later_watch") else ""),
             f"  A revision: {diff.get('earlier_revision') or 'legacy/unrecorded'}",
             f"  B revision: {diff.get('later_revision') or 'legacy/unrecorded'}"]
    component_diff = diff.get("components") or {}
    if component_diff:
        detail = component_diff.get("detail") or {}
        changed_components = [name for name, item in detail.items()
                             if item.get("changed") is True]
        unknown_components = [name for name, item in detail.items()
                              if item.get("changed") == "unknown"]
        lines.append(
            "  Components: changed "
            + (", ".join(changed_components) or "none")
            + "; unknown "
            + (", ".join(unknown_components) or "none")
            + "; confirmed reusable "
            + (", ".join(component_diff.get("unchanged") or ()) or "none"))
    render_cache = diff.get("render_cache") or {}
    if render_cache:
        for side, label in (("earlier", "A"), ("later", "B")):
            cache = render_cache.get(side) or {}
            reused = sum(int(item.get("reused", 0))
                         for item in cache.values())
            rendered = sum(int(item.get("rendered", 0))
                           for item in cache.values())
            unknown = sum(int(item.get("unknown", 0))
                          for item in cache.values())
            lines.append(f"  {label} overlay renders: {reused} reused, "
                         f"{rendered} rendered, {unknown} unmeasured")
    for label, review in (("A", diff.get("earlier_review") or {}),
                          ("B", diff.get("later_review") or {})):
        if review:
            lines.append(f"  {label} review: {review.get('decision', 'recorded')}"
                         + (f" - {review['why']}" if review.get("why") else ""))
    if diff.get("side_by_side"):
        lines.append("  Side by side: "
                     f"{diff['side_by_side']['earlier_timeline']} | "
                     f"{diff['side_by_side']['later_timeline']}")
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
    for region in diff.get("changed_regions") or ():
        windows = [f"{span['side']} {span['name']} "
                   f"{span['start']}..{span['end']} "
                   f"(preview at frame {span['preview_at_frame']})"
                   for span in region.get("spans") or ()]
        if windows:
            lines.append(f"  Changed range for preview {region['row']}: "
                         + "; ".join(windows))
    return "\n".join(lines)


# ── Compared, semantically ─────────────────────────────────────────
#
# The row diff (`compare` above) says which ROWS moved. It cannot say
# what the viewer experiences: two variants with identical rows but
# different captions compare as equal, and two with different rows but
# the same editorial effect compare as different. The semantic
# comparison layers on top of the row diff - it runs `reel_hearing.hear`
# on both variants and diffs the heard scripts, and diffs the watch
# answers. The row diff stays the mechanical substrate; this is the
# choice surface. Report, not a gate (both modules already report).

_DRIFT_COMPARED = ("matched_words", "median_seconds", "mean_seconds",
                   "mean_absolute_seconds", "max_absolute_seconds")
"""The measured drift values a hearing carries, and this diff compares.

Read off `reel_hearing.Hearing.drift` directly (AGENTS.md 10.1 - key-name
mismatches are the dominant bug class). The settings echoes
(`noise_floor_seconds`, `run_min_words`) are not measurements and are
not compared: two hearings of the same reel with the same settings
would otherwise compare as different for a reason that is not the
reel."""


def diff_hearings(earlier: Mapping, later: Mapping) -> dict:
    """What the viewer HEARS differs between two hearings.

    Pure: two hearing records (`reel_hearing.Hearing.as_dict()` forms) in,
    a diff out. The row diff says which rows moved; this says what the
    story, the pacing and the captions do differently. Two variants with
    identical rows but different captions compare as different here, and
    two with different rows but the same heard script compare as equal.

    Compares the keys a hearing record carries, read directly:
    `heard_script`, `divergences`, `drift`, `caption_coverage`,
    `caption_pairing`. Report, not a gate - `reel_hearing` already
    reports, and this adds no verdict of its own.
    """
    earlier_script = str(earlier.get("heard_script") or "")
    later_script = str(later.get("heard_script") or "")
    earlier_divergences = list(earlier.get("divergences") or [])
    later_divergences = list(later.get("divergences") or [])
    earlier_drift = dict(earlier.get("drift") or {})
    later_drift = dict(later.get("drift") or {})
    earlier_coverage = dict(earlier.get("caption_coverage") or {})
    later_coverage = dict(later.get("caption_coverage") or {})
    earlier_pairing = dict(earlier.get("caption_pairing") or {})
    later_pairing = dict(later.get("caption_pairing") or {})

    script_equal = earlier_script == later_script
    divergences_equal = earlier_divergences == later_divergences
    drift_equal = all(earlier_drift.get(key) == later_drift.get(key)
                      for key in _DRIFT_COMPARED)
    coverage_equal = (
        earlier_coverage.get("caption_cards")
        == later_coverage.get("caption_cards")
        and earlier_coverage.get("uncaptioned_words")
        == later_coverage.get("uncaptioned_words")
        and earlier_coverage.get("half_captioned_words")
        == later_coverage.get("half_captioned_words"))
    pairing_equal = (
        earlier_pairing.get("mispaired") == later_pairing.get("mispaired")
        and earlier_pairing.get("unestablished")
        == later_pairing.get("unestablished")
        and earlier_pairing.get("max_edge_offset_seconds")
        == later_pairing.get("max_edge_offset_seconds")
        and earlier_pairing.get("mean_edge_offset_seconds")
        == later_pairing.get("mean_edge_offset_seconds"))
    return {
        "non_empty": not (script_equal and divergences_equal and drift_equal
                          and coverage_equal and pairing_equal),
        "heard_script_equal": script_equal,
        "earlier_heard_script": earlier_script,
        "later_heard_script": later_script,
        "divergences": {
            "equal": divergences_equal,
            "earlier_count": len(earlier_divergences),
            "later_count": len(later_divergences),
        },
        "drift": {
            "equal": drift_equal,
            "earlier": {key: earlier_drift.get(key)
                        for key in _DRIFT_COMPARED},
            "later": {key: later_drift.get(key) for key in _DRIFT_COMPARED},
        },
        "caption_coverage": {
            "equal": coverage_equal,
            "earlier_caption_cards": earlier_coverage.get("caption_cards"),
            "later_caption_cards": later_coverage.get("caption_cards"),
        },
        "caption_pairing": {
            "equal": pairing_equal,
            "earlier_mispaired": len(earlier_pairing.get("mispaired") or []),
            "later_mispaired": len(later_pairing.get("mispaired") or []),
        },
    }


def diff_watch_answers(earlier_watch: str, later_watch: str) -> dict:
    """Diff two watch answers - the free-text viewing notes on each variant.

    Pure: two strings in, a diff out. The watch is a report (it gates
    nothing), so this is a report of a report - what the viewer SAW
    differs, in the captain's own words.
    """
    earlier = str(earlier_watch or "").strip()
    later = str(later_watch or "").strip()
    return {
        "non_empty": earlier != later,
        "earlier": earlier,
        "later": later,
    }


def compare_semantic(earlier: Mapping, later: Mapping,
                     earlier_watch: str = "", later_watch: str = "") -> dict:
    """The semantic diff between two variants - what the viewer experiences.

    Pure: two hearing records and two watch answers in, a diff out.
    Layers on top of the row diff (`compare`): the row diff says which
    rows moved, this says what the story, the pacing and the captions do
    differently. The row diff is the mechanical substrate; this is the
    choice surface.

    Report, not a gate - both `reel_hearing` and `render_watch` already
    report, and this adds no verdict of its own.
    """
    hearing = diff_hearings(earlier, later)
    watch = diff_watch_answers(earlier_watch, later_watch)
    return {
        "non_empty": hearing["non_empty"] or watch["non_empty"],
        "hearing": hearing,
        "watch": watch,
    }


def hear_comparison(earlier_timeline: Mapping, later_timeline: Mapping,
                    transcript: Mapping, earlier_video_path: str,
                    later_video_path: str, transcriber,
                    project_folder: str = "") -> dict:
    """Run the hearing pass on both variants and the semantic diff.

    `transcriber` maps a video path to a `heard_speech.HeardSpeech` - the
    model pass, injectable so a test doubles it. Two hear passes, one per
    variant; the diff is `compare_semantic`'s.

    Runs OUTSIDE the build critical section: no resolve lease is taken,
    because a comparison is a report and a report must not block the next
    build. The transcriber is the slow part and it runs unlocked.
    """
    from library.tools import reel_hearing

    earlier_spoken = transcriber(earlier_video_path)
    later_spoken = transcriber(later_video_path)
    earlier_hearing = reel_hearing.hear(
        earlier_timeline, transcript, earlier_spoken,
        project_folder=project_folder or None,
        video_path=earlier_video_path)
    later_hearing = reel_hearing.hear(
        later_timeline, transcript, later_spoken,
        project_folder=project_folder or None,
        video_path=later_video_path)
    return compare_semantic(earlier_hearing.as_dict(), later_hearing.as_dict())


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
    """Promote an approved variant revision; other variants are archived.

    The order is the promotion's order and for its reasons: the
    incumbent is retired FIRST (so the reel's name is free), the chosen
    variant takes the name SECOND, and the sign-off bookkeeping happens
    LAST - a choice that refuses partway must never have already retired
    an approval it did not replace.

    `why` is required. The exact built revision must already have an
    approval record, and the Resolve timeline id is checked against that
    record before any rename. `variant_names` is the reel's declared
    variant timeline names (`declared_variant_names`).
    """
    from library.tools import reel_signoff as signoff
    if not str(why or "").strip():
        raise ChoiceRefused(
            "a choice with no reason",
            "a choice with no reason is not a decision anybody can read "
            "later",
            "re-run with --why saying why this treatment won")
    chosen = variant_timeline_name(str(base_final), str(chosen_suffix))
    approved_build = build_for(project_folder, reel_number, chosen_suffix)
    approval = ((approved_build or {}).get("approval") or {})
    if (not approved_build
            or approval.get("decision") != "approved"
            or approval.get("revision_id")
            != approved_build.get("revision_id")):
        raise ChoiceRefused(
            f"variant {chosen!r} has no approval for its current revision",
            "promotion is allowed only for the exact variant revision the "
            "captain approved",
            "compare the built variants, then run `ren variant approve` "
            "before choosing one")
    live = _timelines_by_name(project)
    chosen_timeline = live.get(chosen)
    expected_timeline_id = str(approval.get("resolve_timeline_id") or "")
    actual_timeline_id = str(
        chosen_timeline.GetUniqueId() or "") if chosen_timeline else ""
    if (chosen_timeline is None
            or (expected_timeline_id
                and actual_timeline_id != expected_timeline_id)):
        raise ChoiceRefused(
            f"approved variant {chosen!r} no longer has its recorded "
            "Resolve timeline identity",
            "the approved revision is stale; no timeline was promoted",
            "rebuild and review the current variant before approving it")
    from library.tools import plan_provenance, reel_replace_guard
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
              "approval": dict(approval),
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
        operation="variant choice", ren_owned_ids=ren_owned_ids,
        operation_id=inventory_operation)
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
    chosen_rows = _rows_for_build(project_folder, chosen_entry or {})
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
                "revision_id": str(approved_build["revision_id"]),
                "approved_by": str(approval.get("by") or ""),
                "approved_at": str(approval.get("at") or ""),
                "approval_why": str(approval.get("why") or ""),
                "resolve_timeline_id": str(
                    approval.get("resolve_timeline_id") or ""),
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
