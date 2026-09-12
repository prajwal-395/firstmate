"""Timeline variations as git branches (AGENTS.md 3: run state).

The captain's version-control ask: make the LLM create multiple
variations of timelines, keep them connected through clones and forks
of the project git repo, and combine edits from one variation with
comments from another into a final - as native git operations.

Why this works is architectural, and it is worth stating precisely
because it reads at first glance like it contradicts the scout's
ruling. `data/vep-version-control-for-edits/report.md` section 7 rule
4 says: do NOT attempt timeline-level merge, restore-from-export, or
`.drp` surgery - merging EXPORTED timelines (OTIO, `.drp`, serializer
JSON) is closed because every export is a lossy projection that does
not round-trip. This module does the opposite approach, the one that
same section sanctions: a timeline is DERIVED - step 6.01 deletes the
build timeline and rebuilds it from declarations rather than editing
in place - so a variation is not a thing to be merged. Its
DECLARATIONS are. Merge the declarations, rebuild, and the combined
timeline falls out. That is ordinary git on ordinary text, and the two
rulings agree: never merge the projection, always merge the source.

Three pieces:

1. `create_variation` - one command that branches from the current
   committed state, records the variation's seam spec as a declaration
   (the cutaway spec that issue #925 found living NOWHERE in the
   project is exactly what this file exists to hold), and commits, so
   the branch carries everything a rebuild needs. The rebuild itself
   still runs through the normal build paths (`build_reel_variants`
   for seam comparisons, `build-reels` for plan reels) into the
   derived timeline name - this module never touches Resolve, so it
   runs anywhere.
2. A branch name and a timeline name derivable from each other, both
   ways (`variant/r09-reaction-cutaway` <-> `Reel 09 - ...
   (reaction-cutaway)`), so a human looking at either knows the other.
3. `merge_variations` - git merge plus the one judgement the merge
   needs: generated run state (`pipeline_data.json`,
   `pipeline_run.json`, step outputs, the manifest, LLM request
   records) is REBUILT, never hand-merged, so conflicting generated
   paths resolve to the target side automatically and only
   declaration conflicts reach a human. Marker pulls never conflict
   at all - each pull is its own timestamped file, so merging them is
   a union. Model ANSWERS (`llm_responses`) are the deliberate
   exception: written once per decision, never rewritten by a
   rebuild, so they merge for real - one side answering wins cleanly,
   both sides answering differently conflicts for a human. The merged
   tree is then rebuilt (in Resolve, by the caller) and committed.

What this module does NOT do: issue #925's per-row promote diff. A
merge workflow makes that defect MORE dangerous - merging
declarations is precisely where a feature goes missing quietly - so
until that gate lands, every merged variation must end with a human
read-back of the rebuilt timeline (the 29f9722-style verification),
not with a green build. The two ship together; see the docstring on
`merge_variations`.

Past seam-only, and the boundary that is held
---------------------------------------------
The spec landed able to say one thing: a different SEAM. That is why
it never served a real question - "the same reel with the other CTA"
is what a creative A/B actually is, and it could not be written down.

It now reaches the per-project DECLARATIONS too (`declares`, derived
from `external_inputs.DECLARATIONS`), and stops exactly there.
`OUT_OF_VOCABULARY` names what is out and who owns each near miss. The
rule that draws the line: a variant may differ in a declaration the
ordinary rebuild already reads, and in the seam offsets
`build_reel_timeline` already takes - nothing else. Both are things the
rebuild does on its ordinary path, so a variant is the rebuild with one
input changed. Anything wider would need the builder to take a path the
rebuild does not take, which is a SECOND BUILDER - and then the thing
beside the approved reel is no longer the approved reel with one change
in it, and the difference the captain is looking at is not the one they
asked for.

The declaration itself is never in the spec. It is the CONTENT of
`external/<store>.json` on the variant's own branch, which is the only
place the rebuild reads a declaration from - so `branch_requirement`
refuses a declaring variant built from anywhere else.

Timelines or branches: BOTH, and they are one object
----------------------------------------------------
A variant's DECLARATIONS live on a branch and its PICTURE lives on a
Resolve timeline; the two names derive from each other, both ways.
Git holds one branch at a time and Resolve holds every timeline at
once, and that asymmetry is not a problem - it is the reason both
halves exist. Declarations are merged ONE AT A TIME (this module);
pictures are compared SIDE BY SIDE, which is the half the captain
actually watches (`library/tools/variant_choice.py`).
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from library.tools.stable_json import dumps_stable, write_stable

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
    module that owns it, carried in `external/<stem>.json`, checked by
    its owner's own reader. Deriving from it rather than listing the
    stems again is the point: a declaration added there becomes
    variant-expressible with no second edit, and one removed stops
    being expressible at the same moment.

    `tests/test_variant_spec_vocabulary.py` pins the derivation.
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
    # (`variant_choice.record_build`). Rebuilt by every variant build,
    # and it describes RESOLVE's state, which is not per-branch - so
    # there is nothing on the source side a merge could preserve that
    # the rebuild will not overwrite. The record that must survive is
    # the round's, and `variant_choice.choose` moves the chosen rows
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
    import json
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
    generation out, and by `variant_choice` to find what is alive.
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
    head = _git(project_folder, "rev-parse", "--abbrev-ref", "HEAD")
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
    not in the spec at all: it is the CONTENT of `external/<store>.json`
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
                f"external/<store>.json on branch {want} - and this "
                f"project has no git repo to hold it. Run "
                f"init_project_repo (library/tools/"
                f"build_version_control.py) first.")
    if have != want:
        return (f"{spec['suffix'].strip()} differs in "
                f"{sorted(spec['declares'])}, and a declaration lives "
                f"on the variant's own branch, never in the spec. The "
                f"tree is on {have!r}; build it from {want!r}:\n"
                f"    git -C <project> checkout {want}")
    return ""


# ── git ──────────────────────────────────────────────────────────────

def _git(project_folder: str, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", *args],
        cwd=str(project_folder),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=300,
        check=False,
    )


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
                "(library/tools/build_version_control.py) first; a "
                "variation branches from recorded state, never from "
                "an unversioned tree")
    status = _git(project_folder, "status", "--porcelain")
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
    exists = _git(project_folder, "rev-parse", "--verify", branch)
    if exists.returncode == 0:
        return {"created": False,
                "reason": f"branch {branch} already exists"}
    checkout = _git(project_folder, "checkout", "-b", branch)
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
        _git(project_folder, "checkout", "-")
        _git(project_folder, "branch", "-D", branch)
        return {"created": False, "reason": str(exc)}
    add = _git(project_folder, "add",
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
    commit = _git(project_folder, "commit", "-m", msg)
    if commit.returncode != 0:
        return {"created": False,
                "reason": f"git commit failed: "
                          f"{commit.stderr.strip()[-400:]}"}
    rev = _git(project_folder, "rev-parse", "--short", "HEAD")
    return {"created": True, "branch": branch,
            "timeline_name": timeline_name,
            "commit": rev.stdout.strip()}


def _is_generated(rel_path: str) -> bool:
    return any(rel_path == g or rel_path.startswith(g + "/")
               for g in GENERATED_PATHS)


def merge_variations(project_folder: str, source_branch: str,
                     target_branch: str | None = None) -> dict:
    """Git merge of one variation into the target, then stop for the rebuild.

    Checks out the target, merges the source, and auto-resolves
    conflicting GENERATED paths to the target side - that state is
    rebuilt right after the merge, and asking a human to hand-merge
    two ledgers is how a feature goes missing quietly. Declaration
    conflicts (specs, captain_edits, project.yaml, proposals, marker
    pulls cannot conflict) are left in the tree UNCOMMITTED for a
    human; generated auto-resolutions are listed in the report so the
    rebuild knows what it must regenerate.

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
        checkout = _git(project_folder, "checkout", target_branch)
        if checkout.returncode != 0:
            return {"merged": False,
                    "reason": f"cannot check out {target_branch}: "
                              f"{checkout.stderr.strip()[-200:]}"}
    current = _git(project_folder, "rev-parse", "--abbrev-ref", "HEAD")
    target = current.stdout.strip()
    merged = _git(project_folder, "merge", "--no-commit", "--no-ff",
                  source_branch)
    if merged.returncode == 0:
        if "Already up to date" in (merged.stdout or ""):
            rev = _git(project_folder, "rev-parse", "--short", "HEAD")
            return {"merged": True, "target": target,
                    "source": source_branch,
                    "commit": rev.stdout.strip(),
                    "auto_resolved": [], "conflicts": [],
                    "note": "already up to date - nothing to merge, "
                            "nothing to rebuild"}
        # A clean merge still needs its merge commit recorded.
        commit = _git(project_folder, "commit", "--no-edit")
        if commit.returncode != 0:
            _git(project_folder, "merge", "--abort")
            return {"merged": False,
                    "reason": f"merge commit failed: "
                              f"{commit.stderr.strip()[-200:]}"}
        rev = _git(project_folder, "rev-parse", "--short", "HEAD")
        return {"merged": True, "target": target,
                "source": source_branch, "commit": rev.stdout.strip(),
                "auto_resolved": [], "conflicts": [],
                "note": "clean merge - rebuild into the final timeline "
                        "and read it back against both variations "
                        "before promoting (issue #925)"}
    unmerged = _git(project_folder, "diff", "--name-only",
                    "--diff-filter=U")
    if unmerged.returncode != 0:
        _git(project_folder, "merge", "--abort")
        return {"merged": False, "reason": "git merge failed and the "
                                           "conflicted files cannot be "
                                           "listed; merge aborted"}
    conflicted = [p for p in unmerged.stdout.splitlines() if p.strip()]
    auto = [p for p in conflicted if _is_generated(p)]
    human = [p for p in conflicted if not _is_generated(p)]
    for path in auto:
        resolved = _git(project_folder, "checkout", "--ours", "--", path)
        if resolved.returncode != 0:
            _git(project_folder, "merge", "--abort")
            return {"merged": False,
                    "reason": f"cannot auto-resolve {path}; merge aborted"}
        _git(project_folder, "add", "--", path)
    if human:
        return {"merged": False, "target": target,
                "source": source_branch, "auto_resolved": sorted(auto),
                "conflicts": sorted(human),
                "reason": "declaration conflicts need a human - the "
                          "merge is staged in the tree, generated "
                          "paths already resolved to the target side; "
                          "resolve, rebuild, read back both variations, "
                          "then commit"}
    commit = _git(project_folder, "commit", "--no-edit")
    if commit.returncode != 0:
        _git(project_folder, "merge", "--abort")
        return {"merged": False,
                "reason": f"merge commit failed: "
                          f"{commit.stderr.strip()[-200:]}"}
    rev = _git(project_folder, "rev-parse", "--short", "HEAD")
    return {"merged": True, "target": target, "source": source_branch,
            "commit": rev.stdout.strip(), "auto_resolved": sorted(auto),
            "conflicts": [],
            "note": "generated state resolved to the target side - the "
                    "rebuild must regenerate every path listed in "
                    "auto_resolved, then read the final timeline back "
                    "against both variations before promoting "
                    "(issue #925)"}


def main(argv: list[str] | None = None) -> int:
    """`python3 -m library.tools.timeline_variants <create|merge|names>`."""
    import argparse
    import json
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
