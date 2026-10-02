"""A variant must carry every RECORDED OBEDIENCE and DECLARATION the rebuild carries.

A variant is the approved reel with ONE change in it; a build that drops a
recorded decision (pinned closer, struck span, declared grade, declared
ending) shows two changes and calls the difference the seam - and conformance
passes it, because a dropped obedience is structurally perfect. The
Reel 09 incident (2026-09-11) is in docs/RULE_EVIDENCE.md and
docs/evidence/variants.md.
"""
import ast
import pathlib

SOURCE = (pathlib.Path(__file__).resolve().parents[1]
          / "library" / "tools" / "reel_build.py")

#: The calls that APPLY something the captain recorded or declared.
#: Named explicitly: a heuristic over every call in a 6,000-line module
#: would either miss one or drown the failure in noise.
RECORDED_OBEDIENCE = (
    "apply_closer_redraws",   # captain_edits: the pinned closer
    "keep_exclusions",        # transcript_corrections: struck spans
    "exclusion_cuts_for_span",
    "resolve_power_grade",    # the declared Color page grade
    "resolve_grade_cdl",      # the declared CDL half
    "resolve_look",           # the declared series look
    "resolve_document_mic_bleed",  # the measured ISO mic choice
    # The per-reel DECLARATION readers - the stores a variant may differ
    # in, so also the ones it must READ.
    "load_intent", "load_pins", "resolve_ending", "apply_ending",
    "apply_pins",
)


def _module_functions():
    """Every top-level function `reel_build.py` defines, by name."""
    return {node.name: node
            for node in ast.parse(SOURCE.read_text(encoding="utf-8")).body
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _called_names(func):
    names = set()
    for node in ast.walk(func):
        if isinstance(node, ast.Call):
            target = node.func
            if isinstance(target, ast.Attribute):
                names.add(target.attr)
            elif isinstance(target, ast.Name):
                names.add(target.id)
    return names


def _called_names_transitive(defined, name):
    """What `name` reaches, following same-module helpers to a fixpoint
    (#1214 moved the rebuild's reads behind shared helpers one frame out,
    and a flat check read that refactor as a dropped obedience)."""
    seen, stack = set(), [name]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        func = defined.get(current)
        if func is not None:
            stack.extend(_called_names(func) - seen)
    seen.discard(name)
    return seen


def test_variant_carries_every_obedience_the_rebuild_carries():
    defined = _module_functions()
    rebuild = _called_names_transitive(defined, "rebuild_reels_in_project")
    variant = _called_names_transitive(defined, "build_reel_variants")
    stale = [call for call in RECORDED_OBEDIENCE if call not in rebuild]
    assert not stale, (
        f"rebuild_reels_in_project no longer reaches {stale} - either the "
        f"rebuild stopped obeying a recorded decision, or RECORDED_OBEDIENCE "
        f"is stale and must be updated to the new spelling.")
    dropped = [call for call in RECORDED_OBEDIENCE if call not in variant]
    assert not dropped, (
        f"rebuild_reels_in_project applies {dropped} and build_reel_variants "
        f"does not: the variant differs from the approved reel somewhere "
        f"other than its seam, and conformance passes it.")


def test_a_variant_passes_its_declarations_on_and_files_its_record():
    """Reading a declaration and not passing it to `build_reel_timeline`
    is the same defect one step later. And a variant that files no
    semantic record cannot be promoted carrying a disabled graphic
    (Reel 09, 2026-10-02: `reel_disabled_clip_carry`)."""
    func = _module_functions()["build_reel_variants"]
    calls = [node for node in ast.walk(func) if isinstance(node, ast.Call)]
    builds = [node for node in calls
              if getattr(node.func, "id", None) == "build_reel_timeline"]
    assert builds, "build_reel_variants calls no build_reel_timeline()"
    for keyword in ("ending", "overlay_intent"):
        assert keyword in {kw.arg for kw in builds[0].keywords}, (
            f"build_reel_variants calls build_reel_timeline without "
            f"{keyword}=, so the declaration it read never reaches the picture")
    assert any(getattr(node.func, "id", None) == "_write_reel_record"
               and any(isinstance(arg, ast.Attribute)
                       and arg.attr == "write_records" for arg in node.args)
               for node in calls), (
        "build_reel_variants files no semantic record, so a promoted "
        "variant cannot carry a disabled graphic")
