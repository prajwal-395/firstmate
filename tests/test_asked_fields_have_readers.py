"""Reader discipline, one layer earlier than the manifest boundary.

`tests/test_manifest_readers.py` holds every top-level key of the
assembly manifest to a named reader.  That boundary is the LAST one, and
everything this test covers was dropped BEFORE it: a handoff asks a model
for a field, the model answers it, and nothing downstream ever names it
again.  The pipeline decision map (2026-08-25) counted eight such
families; the worst was `creative_direction`, where SEVEN code sites read
keys step 2.01 is not asked for at all, so each returned a default that
read as a decision.

Two halves, and only one of them fails a run:

* **ENFORCING** - `creative_direction`.  These are the fields repaired in
  this change, so they are held to the rule now: no code may read a key
  outside `DIRECTION_KEYS`, and every one of the eight must be either
  mechanically read or declared prompt-only AND actually reach a prompt.
* **REPORTING** - every other step's asked-for fields.  Printed with a
  verdict and never asserted.  Which of them should get a reader and
  which should be deleted is the captain's call (the map's closing
  section), and a test that failed the build on them would be forcing
  that call rather than surfacing it.

Run the reporting half on its own:

    python3 -m pytest tests/test_asked_fields_have_readers.py -s -q \
        -k unread_fields_across_the_pipeline
"""
import ast
import json
import pathlib

import pytest

from library.tools.creative_direction import (
    DIRECTION_KEYS,
    MECHANICALLY_READ_KEYS,
    PROMPT_ONLY_KEYS,
    WITHDRAWN_DIRECTION_KEYS,
)

REPO = pathlib.Path(__file__).resolve().parent.parent
STEPS = REPO / "library" / "steps"
LIBRARY = REPO / "library"

# Names a local variable holding the creative direction goes by.  A read
# through any of them is a read of the direction.
DIRECTION_NAMES = {"creative_direction", "cd", "direction"}


def _python_sources():
    for path in sorted(LIBRARY.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        yield path


def _keys_read_off(name_set, tree):
    """Every constant key read off a variable in `name_set`.

    Catches `x.get("k")`, `x["k"]` and `direction_value(x, "k")`.
    """
    found = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            if (isinstance(func, ast.Attribute) and func.attr == "get"
                    and isinstance(func.value, ast.Name)
                    and func.value.id in name_set
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                found.add(node.args[0].value)
            if (isinstance(func, ast.Name) and func.id == "direction_value"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                found.add(node.args[1].value)
        elif isinstance(node, ast.Subscript):
            if (isinstance(node.value, ast.Name)
                    and node.value.id in name_set
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                found.add(node.slice.value)
    return found


# ── ENFORCING: the creative direction ────────────────────────────────

@pytest.mark.heavy
def test_no_code_reads_a_creative_direction_key_that_cannot_exist():
    """The defect this change closes, made unrepeatable.

    Step 2.01's manifest is the whole of what the model is asked for, so
    a read of anything else could only ever return its own default.
    """
    offences = []
    for path in _python_sources():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for key in sorted(_keys_read_off(DIRECTION_NAMES, tree)):
            if key in DIRECTION_KEYS:
                continue
            reason = WITHDRAWN_DIRECTION_KEYS.get(key, "")
            offences.append(
                f"{path.relative_to(REPO)}: reads creative_direction"
                f"[{key!r}], which step 2.01 is never asked for. "
                + (reason or
                   "Name a key from DIRECTION_KEYS, or read the value "
                   "from whatever really declares it.")
            )
    assert not offences, (
        "A creative_direction read names a key the schema cannot "
        "produce, so it returns its default on every run:\n  "
        + "\n  ".join(offences)
    )


def test_every_asked_direction_field_is_read_or_declared_prompt_only():
    assert set(MECHANICALLY_READ_KEYS) | set(PROMPT_ONLY_KEYS) == set(
        DIRECTION_KEYS), (
        "library/tools/creative_direction.py must account for every field "
        "step 2.01 is asked for, as mechanically read or as prompt-only. "
        f"schema={sorted(DIRECTION_KEYS)} "
        f"accounted={sorted(set(MECHANICALLY_READ_KEYS) | set(PROMPT_ONLY_KEYS))}"
    )
    assert not (set(MECHANICALLY_READ_KEYS) & set(PROMPT_ONLY_KEYS)), (
        "a field is one or the other, not both")


def test_the_mechanically_read_fields_really_have_a_reader():
    read_anywhere = set()
    for path in _python_sources():
        if path.name == "creative_direction.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        read_anywhere |= _keys_read_off(DIRECTION_NAMES, tree)
    for key in MECHANICALLY_READ_KEYS:
        assert key in read_anywhere, (
            f"creative_direction.{key} is recorded as mechanically read "
            f"and no code under library/ names it. Move it to "
            f"PROMPT_ONLY_KEYS, or give it the reader."
        )


def _manifests():
    for path in sorted(STEPS.glob("*/manifest.json")):
        yield path, json.loads(path.read_text(encoding="utf-8"))


def test_the_prompt_only_fields_really_reach_a_prompt():
    """Prompt-only is a claim about the context, so check the context.

    A step's `context_fields` is the allow-list `present_llm_step`
    projects with: naming `creative_direction` sends the whole object,
    naming `creative_direction.<key>` sends that path.  A field neither
    read by code nor selected by any allow-list is asked for and thrown
    away, which is the defect - not a category to file it under.
    """
    whole = []
    per_key = set()
    for path, manifest in _manifests():
        fields = manifest.get("context_fields") or []
        for entry in fields:
            if entry == "creative_direction":
                whole.append(path.parent.name)
            elif entry.startswith("creative_direction."):
                per_key.add(entry.split(".", 1)[1])
    assert whole, (
        "no step's context_fields routes `creative_direction` whole, so "
        "the prompt-only claim below rests on nothing")
    for key in PROMPT_ONLY_KEYS:
        assert key in per_key or whole, (
            f"creative_direction.{key} is recorded as prompt-only and "
            f"reaches no prompt: no manifest selects it. It is asked for "
            f"and thrown away."
        )


# ── REPORTING: every other step ──────────────────────────────────────

def _asked_fields(manifest):
    """What a step's model is asked to produce, per its manifest.

    The union of `interface.outputs[].expected_schema` and
    `interface.llm_outputs[].expected_schema`.  `present_llm_step` builds
    the injected schema from `llm_outputs` where a step declares it
    (AGENTS.md section 10.1), but several steps declare `llm_outputs`
    with a prose `description` and no schema, and the shape then lives
    only on the `outputs` side - so taking one and not the other loses
    whole steps.
    """
    interface = manifest.get("interface", {})
    asked = {}
    for group in (interface.get("outputs") or [],
                  interface.get("llm_outputs") or []):
        for out in group:
            schema = out.get("expected_schema")
            # Some steps write `expected_schema` as a prose string
            # describing the shape. Only a mapping names fields.
            if isinstance(schema, dict) and schema:
                asked.setdefault(out.get("name", ""), set()).update(schema)
    return {name: sorted(fields) for name, fields in asked.items()}


def _every_key_read_under_library(sources):
    """Every constant string key any code reads off any mapping.

    Deliberately over-approximating: a key read off an unrelated dict
    counts.  A field this reports as unread is therefore named by NO
    read anywhere, which is a strong statement; a field it reports as
    read may still be inert, which is why this half only reports.
    """
    keys = {}
    for path, src in sources.items():
        try:
            tree = ast.parse(src)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            key = None
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "get" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                key = node.args[0].value
            elif (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Name)
                    and node.func.id == "direction_value"
                    and len(node.args) >= 2
                    and isinstance(node.args[1], ast.Constant)
                    and isinstance(node.args[1].value, str)):
                key = node.args[1].value
            elif (isinstance(node, ast.Subscript)
                    and isinstance(node.slice, ast.Constant)
                    and isinstance(node.slice.value, str)):
                key = node.slice.value
            if key is not None:
                keys.setdefault(key, set()).add(path)
    return keys


@pytest.mark.heavy
def test_unread_fields_across_the_pipeline(capsys):
    """Reports; never fails. The captain decides reader-or-delete."""
    sources = {p: p.read_text(encoding="utf-8") for p in _python_sources()}
    read_keys = _every_key_read_under_library(sources)

    rows = []
    for path, manifest in _manifests():
        step = path.parent.name
        for state_key, fields in _asked_fields(manifest).items():
            for field in fields:
                outside = {
                    q for q in read_keys.get(field, set())
                    if q.parent.name != path.parent.name
                }
                rows.append((step, state_key, field, sorted(
                    str(q.relative_to(REPO)) for q in outside)))

    unread = [r for r in rows if not r[3]]
    with capsys.disabled():
        print(f"\n  asked-for fields declared in a manifest schema: "
              f"{len(rows)}   read by no step but their own: {len(unread)}")
        for step, state_key, field, _ in unread:
            print(f"    UNREAD  {step:38s} {state_key}.{field}")
        print("  (reporting only - reader-or-delete is the captain's call.)")
        print("  Blind spot, stated: this can only see fields a manifest")
        print("  DECLARES in an `expected_schema`. A field asked for in a")
        print("  handoff, in an llm_outputs `description`, or inside a")
        print("  list-item shape - 4.02's `duration_feel`, and the")
        print("  `verdict`/`why` of 3.03's `cut_decisions` rows - is")
        print("  invisible here, because no declaration carries it.")
        print("  Closing that needs the schemas to describe list-item")
        print("  shapes; it does not need a new file format.")
    assert True
