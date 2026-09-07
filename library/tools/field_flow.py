"""Which FIELD of which document a line of this repository reads.

`output_contract.py` asks who reads what a step PRODUCES, and says in as
many words what it cannot see: *"it is an OUTPUT-level question.  A field
INSIDE an output that nothing reads - which is what the confidence and
the caption hash both were - is not visible here."*  This module is that
missing half, and `data_map.py` is the map built on top of it.

Why a name match cannot answer it
---------------------------------
The obvious instrument is to grep for the field name.  It does not work,
and the repository has already paid for finding that out twice:

* #601 - the producer/consumer survey credited SIX outputs to lines
  WRITING their own key.  `{"total_files": total}` in an unrelated module
  is a write, not a read of `scan.total_files`.
* `output_contract._read_literals` fixed that by only counting a READ
  POSITION - `d["k"]`, `d.get("k")`, `"k" in d`, a key inside a list
  handed to a call.  It closed the write/read confusion and left the
  other half open, which its own docstring records as
  `KNOWN_NAME_COLLISIONS`: *"even in read position a key-name match can
  land on an unrelated dict"*.  `source` was the example.

At OUTPUT level that residue is small - there are 70 output names and
most are distinctive.  At FIELD level it is the whole problem.  `start`,
`end`, `text`, `name`, `path`, `clip_id`, `duration`, `type`, `reason`
and `speaker` are read in hundreds of places off dozens of unrelated
dicts.  A field-level survey built on name matching would report every
field as read and could not fail.

So this module does not match names.  It follows the VALUE.

What it does
------------
An intraprocedural dataflow with interprocedural argument and return
propagation, run to a fixpoint over every Python file in `library/` plus
`manage_project.py`.  A local variable carries a TAG naming the document
and the path within it that the value came from; a subscript, a `.get`,
an `in`, a `pop` or an attribute access on a tagged value is recorded as
a READ of that exact field path, with the file, the line, and the SHAPE
of the access.

    data = json.loads(sys.stdin.read())      # data  : IN@step_3_01_assign_aroll
    catalog = data["clip_catalog"]           # catalog: IN@...clip_catalog
    for clip in catalog:                     # clip  : IN@...clip_catalog[]
        w = clip.get("width", 0)             # READ  IN@...clip_catalog[].width

`data_map` then resolves `IN@<step>.clip_catalog` back through the DAG's
`data_mapping` to the node that produced it, so the read is recorded
against `catalog.clip_catalog[].width` - the field, in the document, that
the value actually came from.

The SHAPE is the answer to "what breaks"
----------------------------------------
The four access shapes are not decoration.  They are the only mechanical
answer this repository has to the captain's fourth question, and they
differ from each other in exactly the way that matters:

    d["k"]              KeyError - the step raises and the run stops
    require_keys(d,...) a named refusal before anything is computed
    d.get("k")          None flows onward, and something downstream
                        decides on it without knowing
    d.get("k", 30.0)    a DEFAULT is substituted and the decision is
                        made on a number nobody measured
    "k" in d            a branch is not taken; the feature silently
                        does not happen
    obj.attr            an attribute that must exist - construction
                        fails, or AttributeError at the read

`d.get("k", <creative value>)` is the shape AGENTS.md 10.5 forbids, and
this is the first instrument in the tree that can enumerate them.

What it CANNOT see, stated rather than left implicit
----------------------------------------------------
* **A value that leaves Python.**  A field handed to a Remotion props
  file, a Fusion `.comp` or a Resolve call is written by this side and
  read by the other; the read is in TypeScript or in Blackmagic's C++.
  `data_map.EXTERNAL_READERS` is where those are named.
* **A dynamic key.**  `row[key]` where `key` is a loop variable records
  a read of `<tag>[]`, not of any particular field.  That is honest -
  it is a read of the whole container - but it means a field only ever
  reached that way looks unread.  `dynamic_container_reads` reports
  every such site so the blind spot has a size.
* **A value the analyzer loses.**  A tag does not survive a `dict(**x)`
  rebuild, a `json.dumps`/`json.loads` round trip through a subprocess,
  or a container this analyzer does not model.  Every tag it fails to
  carry makes a field look LESS read, never more - so a field this
  reports as read really is read, and a field it reports as unread has
  to be adjudicated rather than believed.  That asymmetry is the whole
  reason the module is usable: `data_map` treats an unread field as a
  QUESTION and every finding carries the evidence for its answer.
* **A step running as a subprocess.**  The runner writes a step's inputs
  to its stdin as JSON.  `json.loads(sys.stdin.read())` in a step body is
  a SEED here precisely because the analyzer cannot follow a pipe.

    python3 -m library.tools.field_flow --reads OUT@catalog.clip_catalog
    python3 -m library.tools.field_flow --stats

`tests/test_field_flow.py`.
"""

from __future__ import annotations

import ast
import fnmatch
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Iterable, List, Sequence, Set, Tuple

_LIBRARY_ROOT = Path(__file__).resolve().parents[1]
_REPO_ROOT = _LIBRARY_ROOT.parent


# ── Access shapes, and what each one means when the field is absent ──

SUBSCRIPT = "subscript"
GET = "get"
GET_DEFAULT = "get_default"
CONTAINS = "contains"
POP = "pop"
REQUIRE = "require"
ATTR = "attribute"

BREAKS = {
    SUBSCRIPT: "KeyError - the reader raises",
    REQUIRE: "refused by name before anything is computed",
    GET: "None flows onward",
    GET_DEFAULT: "a default is substituted silently",
    CONTAINS: "a branch is not taken",
    POP: "None flows onward",
    ATTR: "AttributeError, or the object cannot be constructed",
}

# Severity order, worst first.  `data_map` reports the strongest
# consequence across a field's readers, and lists the rest.
BREAK_ORDER = (SUBSCRIPT, REQUIRE, ATTR, POP, GET, CONTAINS, GET_DEFAULT)


# ── Where a tag can start ────────────────────────────────────────────
#
# A seed is the ONE place the analyzer is allowed to invent a tag.
# Everything else is propagation.  Keeping the list short and named is
# what makes the survey auditable: a document nobody seeded is a
# document this module reports nothing about, and `data_map` fails on
# exactly that rather than showing an empty row.

ROOT_STEP_INPUTS = "IN@"
"""`IN@<step_dir>` - the merged dict the runner hands a step.

Two seeds produce it and they are the same value: `json.loads(
sys.stdin.read())` in a step body (the runner writes the step's inputs
to its stdin), and a parameter named in `operations.MERGED_INPUT_
PARAMETERS` on a function in a step directory."""

ROOT_LLM = "LLM@"
"""`LLM@<step_dir>` - what the model answered, before the post-bridge."""

ROOT_STATE = "STATE@"
"""`STATE@<key>` - a top-level key of `pipeline_data.json`."""

ROOT_OUTPUT = "OUT@"
"""`OUT@<node>` - one step's declared output, read off state."""

ROOT_DOC = "DOC@"
"""`DOC@<document id>` - a file on disk, from `data_map.DOCUMENTS`."""

ROOT_ENV = "ENV@"
"""`ENV@<VAR>` - an environment variable."""

ROOT_VIEW = "VIEW@"
"""`VIEW@<name>` - the inputs a named context view reads.

`context_views.CONTEXT_VIEWS` is the table; a step that declares
`view:picture` gets whatever `_picture` builds out of that step's own
inputs.  So a read inside a view builder is a read BY A PROMPT, and it
belongs to every step that declared the view."""

ROOT_PATH = "PATH@"
"""`PATH@<document id>` - a value that is a PLACE, not a document.

The document seed cannot be a filename literal at the `json.load` call:
almost nothing in this tree opens a file by name there.  A filename is
spelled once as a module constant, joined to an `Area` by
`ProjectLayout`, handed through two or three functions and finally read.
`PATH@` is that value travelling, and a `json.load` of something
carrying one is what seeds `DOC@`.

It carries no fields and produces no reads.  Its only job is to survive
the journey, which is why it propagates through any call it is an
argument to: over-reaching in this domain costs nothing, and being
strict costs every document whose path is composed."""

ROOT_CLASS = "CLASS@"
"""`CLASS@<module>:<Class>` - an instance of a repository dataclass.

Its own attributes are fields too, and `constructor_sources` links them
back to whatever document field each was built from, so a read of
`moment.timeline_start` is recorded as a read of the proposal file's
`moments[].timeline_start`."""


# ── The functions whose FIRST list argument is a refusal ─────────────
#
# `require_keys(data, ["a", "b"])` refuses when a key is absent, and the
# refusal is the strongest thing that can happen to a missing field.  It
# is a different SHAPE from a subscript and has to be recognised by name
# because the keys are in a list rather than in read position.

REFUSAL_CALLS = frozenset({
    "require_keys", "require", "assert_keys", "_require_keys",
    "require_fields", "_require",
})


# ── Modules that are ABOUT fields rather than readers of them ────────
#
# The same carve-out `output_contract.CLASSIFIERS` makes, for the same
# reason: a survey that counted itself would be a gate that cannot fail
# (AGENTS.md 10.4).

CLASSIFIERS = frozenset({
    "library/tools/field_flow.py",
    "library/tools/data_map.py",
    "library/tools/output_contract.py",
    "library/tools/input_contract.py",
    "library/tools/direction_contradiction.py",
})


@dataclass(frozen=True)
class Access:
    """One read of one field, with everything needed to judge it."""

    root: str
    """`IN@step_3_01_assign_aroll`, `DOC@reel_proposals_v2`, ..."""

    path: str
    """`clip_catalog[].width`.  `[]` means "an element of"."""

    module: str
    line: int
    func: str
    shape: str
    default: str = ""
    """`repr` of the substituted value, for GET_DEFAULT only.  This is
    what makes a creative fallback (AGENTS.md 10.5) visible."""

    @property
    def tag(self) -> str:
        return (f"{self.root}{TAG_SEPARATOR}{self.path}" if self.path
                else self.root)

    @property
    def breaks(self) -> str:
        return BREAKS[self.shape]


@dataclass(frozen=True)
class ConstructorSource:
    """`Cls(field=<expr>)` where `<expr>` carried a tag.

    The mechanical half of "which JSON key became which attribute".
    `ReelMoment.from_dict` is why: the reel path reads its proposal
    document through a frozen dataclass, so every read downstream is
    `moment.timeline_start` rather than `data["timeline_start"]`, and
    without this link the document's fields would all look unread."""

    cls: str
    field: str
    source_tag: str
    module: str
    line: int


def _is_str(node) -> bool:
    return isinstance(node, ast.Constant) and isinstance(node.value, str)


TAG_SEPARATOR = "#"
"""Between the ROOT and the path inside it.

A dot would not do: a root is
`CLASS@library/tools/reel_proposal.py:ReelMoment`, and splitting that on
the first dot puts half a module path into the field name."""

CONTAINER = "*"
"""Suffix meaning "a container whose elements are this".

`[m for m in data["moments"]]` is not the moments list and it is not one
moment: it is a container of `...moments[]`.  One marker covers a list,
a tuple, a set and a lookup dict, because the only thing this analyzer
ever does with a container is take an element out of it - by iterating,
by indexing, or by `.get`.

It matters because `{c["clip_id"]: c for c in clip_catalog}` is the
single most common idiom in this tree, and without it every read through
a lookup table is invisible.  `step_3_01_assign_aroll` builds one and
then reads `width`, `height`, `rotation` and `frame_rate` off it."""


MAX_PATH_SEGMENTS = 12
"""How deep a tag may go before the analyzer stops following it.

Not a judgement about the data: the DEEPEST field path any real
artefact in `data_map_observed.json` carries is 9 segments
(`conformance_report.quality_bar.verdicts[].findings[].detail.
assumes_known[].quote`), so nothing past 12 can be a field of anything
that exists.  It is here because a flow-insensitive environment
compounds: `run_pipeline.gather_step_inputs` builds one `inputs` dict
out of every step's outputs, and without a bound the same name grows
`step_outputs[][][][]` chains every round.  `World.too_deep` counts what
this drops, so the bound has a size."""

MAX_TAGS_PER_NAME = 4096
"""Widening backstop.  A name carrying more than this many tags is a
generic container rather than one document.  It currently fires ZERO
times - the two bounds above do the work - and it is kept as the thing
that stops a future idiom from making the fixpoint quadratic.
`World.widened` is the count, and a non-zero one is worth reading."""

MAX_NESTED_ELEMENTS = 1
"""How many `[]` may sit next to each other in a path.

MEASURED, not chosen: no field path in any artefact in
`data_map_observed.json` has two consecutive `[]` - the deepest is one
(`conformance_report.provenance_findings[].class`).  So
`step_outputs[][][][]` is not a field of anything; it is the analyzer
losing a dict it built out of a loop, and every one of those chains was
noise this bound removes rather than data it hides.
`World.too_nested` counts it."""


def depth(tag: str) -> int:
    _, path = split(tag)
    return 0 if not path else path.count(".") + path.count("[]") + 1


def container(tag: str) -> str:
    """A container of `tag`.  Nesting collapses: one level is enough."""
    if tag.startswith(ROOT_PATH):
        return tag        # a PLACE has no fields and no elements
    return tag if tag.endswith(CONTAINER) else tag + CONTAINER


def is_container(tag: str) -> bool:
    return tag.endswith(CONTAINER)


def element(tag: str) -> str:
    """One element out of `tag`."""
    if tag.startswith(ROOT_PATH):
        # A glob yields more PLACES, and each is the same document.
        return tag
    if tag.endswith(CONTAINER):
        return tag[:-len(CONTAINER)]
    return f"{tag}[]" if TAG_SEPARATOR in tag else f"{tag}{TAG_SEPARATOR}[]"


def extend(tag: str, key: str) -> str:
    """`DOC@x` + `moments` -> `DOC@x#moments`, then + `slug` -> `#moments.slug`."""
    if tag.startswith(ROOT_PATH):
        return tag
    return (f"{tag}.{key}" if TAG_SEPARATOR in tag
            else f"{tag}{TAG_SEPARATOR}{key}")


def split(tag: str) -> Tuple[str, str]:
    root, _, path = tag.partition(TAG_SEPARATOR)
    return root, path


def canonical(tag: str) -> str:
    """A tag with its trailing container markers removed.

    `...#moments[].source_spans[]*` is "a container of the elements of
    `source_spans`", which is the SAME FIELD as `source_spans`.  Only
    the trailing markers go - the `[]` in the middle of the path says
    the field is inside a list element and is part of its identity."""
    return _TRAILING_MARKERS.sub("", tag)


_TRAILING_MARKERS = re.compile(r"(\[\]|\*)+$")


# ── The analyzer ────────────────────────────────────────────────────

class _FunctionScope:
    """One function body, analysed with the tags its callers gave it.

    `tags_of` is the whole analyzer.  It returns the tags a value may
    carry AND records, as a side effect, every field read it passed
    through - so every expression in a body must reach it exactly once.
    Nothing else records a read, and `walk` exists only to bind names
    and to hand every expression to it.
    """

    def __init__(self, owner: "_ModuleAnalysis", node,
                 params: Dict[str, Set[str]], owner_class: str = ""):
        self.owner = owner
        self.node = node

        self.owner_class = owner_class
        self.name = getattr(node, "name", "<module>")
        self.env: Dict[str, Set[str]] = {k: set(v) for k, v in params.items()}
        self.globals_declared: Set[str] = set()
        """Names this body declared `global`.  `compile_manifest` fills
        `_STATE_OUTPUTS` that way - `global _STATE_OUTPUTS;
        _STATE_OUTPUTS = _load_state_outputs(out_dir)` - and every
        `load()` in that step reads through it, so a module-global map
        built only from module-level assignments finds none of them."""
        self.strings: Dict[str, Set[str]] = {}
        """Which string constants a name may hold.

        `beat_grid._times(music_analysis, key, ...)` reads
        `tempo.get(key)`, and its two callers pass `"beats"` and
        `"downbeats"`.  Without this the beat grid - the whole reason
        `music_analysis.tempo` is measured - reads as an unread field.
        The same shape resolves `compile_manifest.load(out_dir,
        "music_selection.json")`, which indexes `step_outputs` by a stem
        computed from that argument."""
        self.returns: Set[str] = set()

    # -- string constants ------------------------------------------------

    def strings_of(self, node) -> Set[str]:
        """Which string constants an expression may evaluate to.

        Deliberately tiny.  Constants, names bound to them, the two
        branchy expressions, and `.replace(a, b)` - which is how
        `compile_manifest` turns `"catalog.json"` into `"catalog"`."""
        if node is None:
            return set()
        if _is_str(node):
            return {node.value}
        if isinstance(node, ast.Name):
            return set(self.strings.get(node.id, ()))
        if isinstance(node, ast.IfExp):
            return self.strings_of(node.body) | self.strings_of(node.orelse)
        if isinstance(node, ast.BoolOp):
            found = set()
            for value in node.values:
                found |= self.strings_of(value)
            return found
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            if node.func.attr == "replace" and len(node.args) == 2 \
                    and _is_str(node.args[0]) and _is_str(node.args[1]):
                return {text.replace(node.args[0].value, node.args[1].value)
                        for text in self.strings_of(node.func.value)}
            if node.func.attr in ("strip", "lower", "upper", "lstrip",
                                  "rstrip") and not node.args:
                method = node.func.attr
                return {getattr(text, method)()
                        for text in self.strings_of(node.func.value)}
        return set()

    def bind_strings(self, target, values: Set[str]) -> None:
        if isinstance(target, ast.Name) and values:
            self.strings.setdefault(target.id, set()).update(values)

    # -- tag arithmetic -------------------------------------------------

    def tags_of(self, node) -> Set[str]:
        """Every tag the value of `node` may carry.  Never raises."""
        out: Set[str] = set()
        if node is None or not isinstance(node, ast.expr):
            return out

        if isinstance(node, ast.Constant):
            if isinstance(node.value, str):
                doc = self.owner.world.seeds.document_named(node.value)
                if doc:
                    return {f"{ROOT_PATH}{doc}"}
            return out

        if isinstance(node, ast.JoinedStr):
            for value in node.values:
                if isinstance(value, ast.Constant) and isinstance(
                        value.value, str):
                    doc = self.owner.world.seeds.document_named(value.value)
                    if doc:
                        out.add(f"{ROOT_PATH}{doc}")
                else:
                    # `f"{JOURNAL_PREFIX}*.json"` - the part that names
                    # the document is an expression, not a literal.
                    out |= _paths(self.tags_of(value))
            return out

        if isinstance(node, ast.BinOp):
            # `directory / FILENAME` is how every path in this tree is
            # built.  Either side may be the one that names the document.
            left, right = self.tags_of(node.left), self.tags_of(node.right)
            return _paths(left) | _paths(right)

        if isinstance(node, ast.Name):
            return set(self.env.get(node.id, ()))

        if isinstance(node, (ast.Starred, ast.FormattedValue)):
            return self.tags_of(node.value)

        if isinstance(node, ast.IfExp):
            self.tags_of(node.test)
            return self.tags_of(node.body) | self.tags_of(node.orelse)

        if isinstance(node, ast.BoolOp):
            for value in node.values:
                out |= self.tags_of(value)
            return out

        if isinstance(node, ast.Compare):
            # `"k" in d` is a read; every other comparison is not.
            left = node.left
            for op, comparator in zip(node.ops, node.comparators):
                subject = self.tags_of(comparator)
                if isinstance(op, (ast.In, ast.NotIn)) and _is_str(left):
                    for tag in subject:
                        self.read(tag, left.value, node.lineno, CONTAINS)
            self.tags_of(left)
            return out

        if isinstance(node, ast.Subscript):
            if _is_environ(node.value) and _is_str(node.slice):
                return {f"{ROOT_ENV}{node.slice.value}"}
            base = self.tags_of(node.value)
            self.tags_of(node.slice)
            for tag in base:
                if is_container(tag):
                    # Indexing a container: the key is DATA, not a field
                    # name, so nothing is read and an element comes out.
                    out.add(element(tag))
                elif _is_str(node.slice):
                    self.read(tag, node.slice.value, node.lineno, SUBSCRIPT)
                    out.add(extend(tag, node.slice.value))
                else:
                    keys = self.strings_of(node.slice)
                    for key in keys:
                        self.read(tag, key, node.lineno, SUBSCRIPT)
                        out.add(extend(tag, key))
                    if not keys:
                        self.owner.dynamic.append((tag, self.owner.rel,
                                                   node.lineno))
                        out.add(element(tag))
            return out

        if isinstance(node, ast.Attribute):
            base = self.tags_of(node.value)
            for tag in base:
                if node.attr in _NOT_A_FIELD or is_container(tag):
                    continue
                root, _ = split(tag)
                if root.startswith(ROOT_CLASS):
                    # A PROPERTY is a function, not a stored field.
                    # `layout.pipeline_data_path` and `moment.duration`
                    # both are, and treating them as fields both invents
                    # a field nothing stores and loses the tag the
                    # property really returns.
                    module_rel, _, class_name = root[
                        len(ROOT_CLASS):].partition(":")
                    method = f"{class_name}.{node.attr}"
                    if self.owner.world.kind_of(module_rel, method):
                        self.owner.bind_call(module_rel, method, [], {})
                        out |= self.owner.return_tags_of(module_rel, method)
                        continue
                self.read(tag, node.attr, node.lineno, ATTR)
                out.add(extend(tag, node.attr))
            return out

        if isinstance(node, (ast.List, ast.Tuple, ast.Set)):
            for item in node.elts:
                out |= {container(t) for t in self.tags_of(item)}
            return out

        if isinstance(node, (ast.ListComp, ast.SetComp, ast.GeneratorExp)):
            self.bind_comprehension(node)
            return {container(t) for t in self.tags_of(node.elt)}

        if isinstance(node, ast.DictComp):
            self.bind_comprehension(node)
            self.tags_of(node.key)
            return {container(t) for t in self.tags_of(node.value)}

        if isinstance(node, ast.Dict):
            # A dict LITERAL is a write.  Its keys name nothing that is
            # being read, which is the distinction #601 turned on.
            for value in node.values:
                self.tags_of(value)
            for key in node.keys:
                self.tags_of(key)
            return out

        if isinstance(node, ast.Call):
            return self.call_tags(node)

        if isinstance(node, ast.Await):
            return self.tags_of(node.value)

        if isinstance(node, ast.Lambda):
            self.tags_of(node.body)
            return out

        # Anything else - BinOp, UnaryOp, JoinedStr, Slice, Yield.  The
        # value is not a document, but its parts must still be visited.
        for child in ast.iter_child_nodes(node):
            self.tags_of(child)
        return out

    def bind_comprehension(self, node) -> None:
        for generator in node.generators:
            source = self.tags_of(generator.iter)
            self.bind_target(generator.target, {element(t) for t in source})
            for condition in generator.ifs:
                self.tags_of(condition)

    def bind_target(self, target, tags: Set[str]) -> None:
        """Assign `tags` to a name.  NEVER records a read: this is the
        left-hand side, and crediting it is exactly the write-counted-as
        -a-read defect #601 found six of."""
        if isinstance(target, ast.Name):
            tags = self.bound(tags)
            if tags:
                slot = self.env.setdefault(target.id, set())
                slot.update(tags)
                if len(slot) > MAX_TAGS_PER_NAME:
                    self.owner.world.widened += 1
                    self.env[target.id] = set(sorted(slot, key=len)
                                              [:MAX_TAGS_PER_NAME])
                if target.id in self.globals_declared:
                    slot = self.owner.world.globals.setdefault(
                        self.owner.rel, {}).setdefault(target.id, set())
                    if not tags <= slot:
                        slot |= tags
                        self.owner.world.changed = True
            return
        if isinstance(target, (ast.Tuple, ast.List)):
            for item in target.elts:
                # Unpacking loses which position a tag came from; an
                # element of the container is the honest answer.
                self.bind_target(item, {element(t) for t in tags})
            return
        if isinstance(target, ast.Subscript):
            # `lookup[c["clip_id"]] = c` builds a container.  The base
            # keeps its own name; the value's tags become its elements.
            self.tags_of(target.value)
            self.tags_of(target.slice)
            if isinstance(target.value, ast.Name) and tags:
                self.env.setdefault(target.value.id, set()).update(
                    {container(t) for t in tags})
            return
        if isinstance(target, ast.Attribute):
            self.tags_of(target.value)
            return
        if isinstance(target, ast.Starred):
            self.bind_target(target.value, tags)

    # -- calls ----------------------------------------------------------

    def call_tags(self, node: ast.Call) -> Set[str]:
        func = node.func
        out: Set[str] = set()

        if isinstance(func, ast.Attribute):
            method = func.attr

            # `d.get("k")` / `d.pop("k")` / `d.setdefault("k", ...)`
            if method in ("get", "pop", "setdefault"):
                base = self.tags_of(func.value)
                for argument in node.args[1:]:
                    self.tags_of(argument)
                if node.args and _is_str(node.args[0]):
                    key = node.args[0].value
                    if method == "pop":
                        shape = POP
                    elif len(node.args) > 1 or node.keywords:
                        shape = GET_DEFAULT
                    else:
                        shape = GET
                    default = (ast.unparse(node.args[1])[:60]
                               if shape == GET_DEFAULT and len(node.args) > 1
                               else "")
                    for tag in base:
                        if is_container(tag):
                            out.add(element(tag))
                            continue
                        self.read(tag, key, node.lineno, shape, default)
                        out.add(extend(tag, key))
                    return out
                keys = self.strings_of(node.args[0]) if node.args else set()
                if node.args:
                    self.tags_of(node.args[0])
                shape = POP if method == "pop" else (
                    GET_DEFAULT if len(node.args) > 1 else GET)
                for tag in base:
                    if is_container(tag) or not keys:
                        out.add(element(tag))
                        continue
                    for key in keys:
                        self.read(tag, key, node.lineno, shape)
                        out.add(extend(tag, key))
                return out

            if method in ("values", "__iter__"):
                for tag in self.tags_of(func.value):
                    out.add(element(tag))
                return out

            if method in ("append", "add"):
                base = func.value
                for argument in node.args:
                    tags = self.tags_of(argument)
                    if isinstance(base, ast.Name) and tags:
                        self.env.setdefault(base.id, set()).update(
                            {container(t) for t in tags})
                    else:
                        self.tags_of(base)
                return out

            if method in ("extend", "update"):
                base = func.value
                for argument in node.args:
                    tags = self.tags_of(argument)
                    if isinstance(base, ast.Name) and tags:
                        self.env.setdefault(base.id, set()).update(tags)
                return out

            if method in ("keys", "items", "sort", "reverse", "clear"):
                self.tags_of(func.value)
                for argument in node.args:
                    self.tags_of(argument)
                return out

            if method in _PATH_METHODS:
                receiver = self.tags_of(func.value)
                for argument in node.args:
                    self.tags_of(argument)
                return _paths(receiver)

        # A refusal call: `require_keys(data, ["a", "b"])`
        called = self.owner.called_name(func)
        if called in REFUSAL_CALLS:
            subject: Set[str] = set()
            for argument in node.args:
                subject |= self.tags_of(argument)
            for argument in node.args + [kw.value for kw in node.keywords]:
                if isinstance(argument, (ast.List, ast.Tuple, ast.Set)):
                    for item in argument.elts:
                        if _is_str(item):
                            for tag in subject:
                                self.read(tag, item.value, node.lineno,
                                          REQUIRE)
            return out

        seeded = self.owner.seed_for(node, self)
        if seeded:
            for argument in node.args:
                self.tags_of(argument)
            return seeded

        if isinstance(func, ast.Attribute):
            self.tags_of(func.value)
        argument_tags = [self.tags_of(a) for a in node.args]
        keyword_tags = {kw.arg: self.tags_of(kw.value)
                        for kw in node.keywords if kw.arg}
        argument_strings = [self.strings_of(a) for a in node.args]
        keyword_strings = {kw.arg: self.strings_of(kw.value)
                           for kw in node.keywords if kw.arg}
        for keyword in node.keywords:
            if keyword.arg is None:
                self.tags_of(keyword.value)

        target = self.owner.resolve_call(func)
        if target is None and isinstance(func, ast.Name) \
                and func.id == "cls" and self.owner_class:
            target = ("class", self.owner.rel, self.owner_class)
        if target is not None:
            kind, module_rel, name = target
            if kind == "class":
                splat = [self.tags_of(kw.value) for kw in node.keywords
                         if kw.arg is None]
                if splat:
                    # `TrackedText(**d)` reads EVERY field the class
                    # declares, by name, out of `d`.  Ten of
                    # `ocr_result`'s sixteen fields are reached only
                    # this way, and without it the OCR document's whole
                    # content reads as unread.
                    declared = self.owner.world.class_fields(module_rel,
                                                             name)
                    for field_name in declared:
                        for tags in splat:
                            for tag in tags:
                                self.read(tag, field_name, node.lineno, ATTR)
                for field_name, tags in keyword_tags.items():
                    for tag in tags:
                        self.owner.constructor_sources.append(
                            ConstructorSource(
                                cls=f"{module_rel}:{name}", field=field_name,
                                source_tag=tag, module=self.owner.rel,
                                line=node.lineno))
                return {f"{ROOT_CLASS}{module_rel}:{name}"}
            self.owner.bind_call(module_rel, name, argument_tags, keyword_tags,
                                 argument_strings, keyword_strings)
            out |= self.owner.return_tags_of(module_rel, name)
            return out

        # Builtins that hand the container straight back.
        if isinstance(func, ast.Name) and func.id in _PASS_THROUGH:
            if argument_tags:
                out |= argument_tags[0]
        # A PLACE survives a call that COMPOSES one.  Enumerated rather
        # than blanket: a first attempt let every call pass a PATH
        # through, and `plan_provenance.record(proposal_path, ...)` then
        # seeded the provenance file as the proposal file - false reads,
        # which is the one direction this analyzer must not err in.
        if self.owner.called_name(func) in _PATH_BUILDERS:
            for tags in argument_tags:
                out |= _paths(tags)
            for tags in keyword_tags.values():
                out |= _paths(tags)
        return out

    # -- recording ------------------------------------------------------

    def bound(self, tags: Set[str]) -> Set[str]:
        kept = set()
        for tag in tags:
            if depth(tag) > MAX_PATH_SEGMENTS:
                self.owner.world.too_deep += 1
                continue
            if _NESTED.search(tag):
                self.owner.world.too_nested += 1
                continue
            kept.add(tag)
        return kept

    def read(self, tag: str, key: str, line: int, shape: str,
             default: str = "") -> None:
        if is_container(tag) or tag.startswith(ROOT_PATH):
            return
        if depth(tag) >= MAX_PATH_SEGMENTS:
            self.owner.world.too_deep += 1
            return
        root, path = split(tag)
        if "@" not in root:
            return
        self.owner.record(Access(
            root=root, path=f"{path}.{key}" if path else key,
            module=self.owner.rel, line=line, func=self.name, shape=shape,
            default=default))

    # -- statements -----------------------------------------------------

    def run(self) -> None:
        body = getattr(self.node, "body", [])
        if not isinstance(body, list):
            body = [body]
        for _ in range(2):
            # Twice.  A name assigned textually after its first use -
            # inside a loop, or a helper defined below its caller - is
            # still the same name, and one pass would miss every read
            # that appears before the assignment.
            self.walk_body(body)

    def walk_body(self, body: Iterable) -> None:
        for statement in body:
            self.walk(statement)

    def walk(self, node) -> None:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            return  # analysed on its own, with its own callers' tags
        if isinstance(node, ast.Assign):
            tags = self.tags_of(node.value)
            values = self.strings_of(node.value)
            for target in node.targets:
                self.bind_target(target, tags)
                self.bind_strings(target, values)
            return
        if isinstance(node, ast.AnnAssign):
            tags = self.tags_of(node.value) if node.value else set()
            self.bind_target(node.target, tags)
            return
        if isinstance(node, ast.AugAssign):
            tags = self.tags_of(node.value)
            self.bind_target(node.target, tags)
            return
        if isinstance(node, ast.NamedExpr):
            self.bind_target(node.target, self.tags_of(node.value))
            return
        if isinstance(node, (ast.For, ast.AsyncFor)):
            source = self.tags_of(node.iter)
            if _is_items_call(node.iter) and isinstance(
                    node.target, (ast.Tuple, ast.List)) \
                    and len(node.target.elts) == 2:
                # `for key, value in lookup.items()`: the KEY is data
                # and the VALUE is the element.  Tagging both would
                # record every later `key[...]` as a field read.
                values = {element(t) for t in
                          self.tags_of(node.iter.func.value)}
                self.bind_target(node.target.elts[1], values)
            else:
                self.bind_target(node.target, {element(t) for t in source})
                if isinstance(node.iter, (ast.List, ast.Tuple, ast.Set)):
                    self.bind_strings(node.target, {
                        item.value for item in node.iter.elts
                        if _is_str(item)})
            self.walk_body(node.body)
            self.walk_body(node.orelse)
            return
        if isinstance(node, ast.Return):
            if node.value is not None:
                self.returns |= self.tags_of(node.value)
            return
        if isinstance(node, (ast.With, ast.AsyncWith)):
            for item in node.items:
                tags = self.tags_of(item.context_expr)
                if item.optional_vars is not None:
                    self.bind_target(item.optional_vars, tags)
            self.walk_body(node.body)
            return
        if isinstance(node, (ast.If, ast.While)):
            self.tags_of(node.test)
            self.walk_body(node.body)
            self.walk_body(node.orelse)
            return
        if isinstance(node, ast.Try):
            self.walk_body(node.body)
            for handler in node.handlers:
                self.walk_body(handler.body)
            self.walk_body(node.orelse)
            self.walk_body(node.finalbody)
            return
        if isinstance(node, (ast.Global, ast.Nonlocal)):
            self.globals_declared.update(node.names)
            return
        if isinstance(node, ast.Delete):
            # `del d["k"]` removes a field.  It is not a read of one.
            for target in node.targets:
                if isinstance(target, ast.Subscript):
                    self.tags_of(target.value)
            return
        if isinstance(node, ast.Expr):
            self.tags_of(node.value)
            return
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.expr):
                self.tags_of(child)
            elif isinstance(child, ast.stmt):
                self.walk(child)


_NOT_A_FIELD = frozenset({
    # Methods, not fields.  Reading `d.get` is not reading a field named
    # `get`, and `moment.as_dict()` is not a field called `as_dict`.
    "get", "pop", "setdefault", "keys", "items", "values", "append",
    "extend", "copy", "update", "as_dict", "to_dict", "from_dict",
    "format", "join", "split", "strip", "lower", "upper", "replace",
    "startswith", "endswith", "add", "sort", "index", "count", "insert",
    "remove", "clear", "encode", "decode", "read_text", "write_text",
    "exists", "is_file", "is_dir", "mkdir", "resolve", "relative_to",
    "rstrip", "lstrip", "splitlines", "isdigit", "title", "capitalize",
})

_PATH_METHODS = frozenset({
    # Methods that turn a place into another place, or into its bytes.
    "read_text", "read_bytes", "open", "resolve", "expanduser", "absolute",
    "joinpath", "with_suffix", "with_name", "as_posix", "__truediv__",
})

_PATH_BUILDERS = frozenset({
    # Calls that turn a place into another place.  AGENTS.md 8 is why
    # this is short: *"A step never composes a project path.  It names
    # an `Area` and gets a path via `write_dir`/`write_path`/`read_dir`/
    # `read_path`"* - so `project_layout` owns nearly all of them.
    "read_path", "write_path", "read_dir", "write_dir", "step_dir",
    "resolve_project_relative", "Path", "join", "fspath", "expanduser",
    "abspath", "realpath", "normpath", "open",
    # A GLOB is how three documents are found: `clip_*.json` (the
    # per-clip temporal index), `clip_profile_*.json` (the vision
    # profiles) and the proposal history.  Its pattern IS the document's
    # name, and without this the two largest analysis documents in the
    # tree read as files nothing opens.
    "glob", "rglob", "iglob",
})

_PASS_THROUGH = frozenset({
    # The value is still the same value.  `int(data["number"])` IS
    # `data["number"]`, and without the coercions here every field a
    # `from_dict` casts on the way in loses its provenance - which was
    # five of `ReelMoment`'s six required fields.
    "list", "tuple", "sorted", "reversed", "iter", "set", "frozenset",
    "dict", "deepcopy", "copy", "int", "float", "str", "bool", "round",
    "abs",
})


_NESTED = re.compile(r"(?:\[\]){%d}" % (MAX_NESTED_ELEMENTS + 1))


def _paths(tags: Set[str]) -> Set[str]:
    return {tag for tag in tags if tag.startswith(ROOT_PATH)}


def _is_items_call(node) -> bool:
    return (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
            and node.func.attr == "items")


class _ModuleAnalysis:
    """One file, and everything the fixpoint needs to share about it."""

    def __init__(self, world: "World", rel: str, tree: ast.Module):
        self.world = world
        self.rel = rel
        self.tree = tree
        self.accesses: List[Access] = []
        self._seen: Set[Access] = set()
        self.constructor_sources: List[ConstructorSource] = []
        self.dynamic: List[Tuple[str, str, int]] = []
        self.functions: Dict[str, ast.AST] = {}
        self.classes: Dict[str, ast.ClassDef] = {}
        self.import_module: Dict[str, str] = {}
        """local name -> repo-relative module path"""
        self.import_symbol: Dict[str, Tuple[str, str]] = {}
        """local name -> (module path, original name)"""
        self._index()

    # -- indexing -------------------------------------------------------

    def _index(self) -> None:
        for node in ast.walk(self.tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions.setdefault(node.name, node)
            elif isinstance(node, ast.ClassDef):
                self.classes.setdefault(node.name, node)
                for child in node.body:
                    if isinstance(child, (ast.FunctionDef,
                                          ast.AsyncFunctionDef)):
                        self.functions.setdefault(
                            f"{node.name}.{child.name}", child)
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    path = _module_path(alias.name)
                    if path:
                        self.import_module[
                            alias.asname or alias.name.split(".")[-1]] = path
            elif isinstance(node, ast.ImportFrom):
                if not node.module:
                    continue
                for alias in node.names:
                    submodule = _module_path(f"{node.module}.{alias.name}")
                    if submodule:
                        self.import_module[alias.asname or alias.name] = submodule
                        continue
                    package = _module_path(node.module)
                    if package:
                        self.import_symbol[alias.asname or alias.name] = (
                            package, alias.name)

    def record(self, access: Access) -> None:
        """One line reading one field once.  A body is walked twice and
        an expression can be reached from two directions, so the same
        read arrives more than once; counting it twice would inflate
        every number this module reports."""
        if access in self._seen:
            return
        self._seen.add(access)
        self.accesses.append(access)

    def called_name(self, func) -> str:
        if isinstance(func, ast.Name):
            return func.id
        if isinstance(func, ast.Attribute):
            return func.attr
        return ""

    def resolve_call(self, func) -> Tuple[str, str, str] | None:
        """`("func"|"class", module path, name)` for a call this can follow."""
        if isinstance(func, ast.Name):
            name = func.id
            if name in self.classes:
                return ("class", self.rel, name)
            if name in self.functions:
                return ("func", self.rel, name)
            if name in self.import_symbol:
                module_rel, original = self.import_symbol[name]
                kind = self.world.kind_of(module_rel, original)
                if kind:
                    return (kind, module_rel, original)
            return None
        if isinstance(func, ast.Attribute):
            base = func.value
            if isinstance(base, ast.Name):
                if base.id in self.import_module:
                    module_rel = self.import_module[base.id]
                    kind = self.world.kind_of(module_rel, func.attr)
                    if kind:
                        return (kind, module_rel, func.attr)
                if base.id in self.classes:
                    inner = f"{base.id}.{func.attr}"
                    if inner in self.functions:
                        return ("func", self.rel, inner)
                if base.id in self.import_symbol:
                    module_rel, original = self.import_symbol[base.id]
                    inner = f"{original}.{func.attr}"
                    if self.world.kind_of(module_rel, inner):
                        return ("func", module_rel, inner)
        return None

    # -- seeds ----------------------------------------------------------

    def seed_for(self, node: ast.Call, scope: _FunctionScope) -> Set[str]:
        return self.world.seed_for(self, node, scope)

    # -- fixpoint plumbing ----------------------------------------------

    def bind_call(self, module_rel: str, name: str,
                  positional: List[Set[str]], keyword: Dict[str, Set[str]],
                  positional_strings: List[Set[str]] = None,
                  keyword_strings: Dict[str, Set[str]] = None) -> None:
        self.world.bind_call(module_rel, name, positional, keyword,
                             positional_strings, keyword_strings)

    def return_tags_of(self, module_rel: str, name: str) -> Set[str]:
        return self.world.returns.get((module_rel, name), set())


_IMPORT_ROOTS = ("", "library")
"""Where a dotted import may resolve from.

`library/` is on `sys.path` for every step - each `step.py` inserts the
repository root's `library` before importing - so `from
tools.frame_utils import ...` and `from library.tools.frame_utils import
...` are the SAME module, and both spellings appear in one file
(`step_5_04_compile_manifest/step.py` uses both, on adjacent lines).
Resolving only the second missed every `tools.`-spelled import, which is
most of `compile_manifest`'s - and `compile_manifest` is the step that
reads the most state in the tree."""


def _module_path(dotted: str) -> str:
    """`library.tools.reel_proposal` -> `library/tools/reel_proposal.py`."""
    if not dotted:
        return ""
    tail = dotted.replace(".", "/") + ".py"
    for root in _IMPORT_ROOTS:
        candidate = (_REPO_ROOT / root / tail) if root else (_REPO_ROOT / tail)
        if candidate.is_file():
            return candidate.relative_to(_REPO_ROOT).as_posix()
    return ""


class World:
    """Every module, analysed together until the tags stop changing."""

    MAX_ROUNDS = 6

    def __init__(self, seeds: "Seeds"):
        self.seeds = seeds
        self.modules: Dict[str, _ModuleAnalysis] = {}
        self.params: Dict[Tuple[str, str], Dict[str, Set[str]]] = {}
        self.param_strings: Dict[Tuple[str, str], Dict[str, Set[str]]] = {}
        self.globals: Dict[str, Dict[str, Set[str]]] = {}
        self.global_strings: Dict[str, Dict[str, Set[str]]] = {}
        self.returns: Dict[Tuple[str, str], Set[str]] = {}
        self.changed = False
        self.accesses: List[Access] = []
        self.constructor_sources: List[ConstructorSource] = []
        self.dynamic: List[Tuple[str, str, int]] = []
        self.too_deep = 0
        self.too_nested = 0
        self.widened = 0

    def load(self, files: Sequence[Path]) -> None:
        for path in files:
            try:
                rel = path.relative_to(_REPO_ROOT).as_posix()
            except ValueError:
                # A synthetic module a test built under `tmp_path`.  The
                # analyzer has to be runnable on one, or nothing can
                # prove what it does without asserting against the
                # repository's own moving contents.
                rel = path.as_posix()
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (OSError, SyntaxError, ValueError):
                continue
            self.modules[rel] = _ModuleAnalysis(self, rel, tree)

    def visible_globals(self, module: _ModuleAnalysis) -> Dict[str, Set[str]]:
        """A module constant is in scope in every function below it.

        `PROPOSAL_FILENAME = "reel_proposals_v2.json"` is spelled once at
        module level (`reel_proposal.py` says so in as many words), and a
        function scope that started empty could not see it - so the one
        document whose name is spelled properly was the one document
        with no readers.  Imported constants count for the same reason.
        """
        found: Dict[str, Set[str]] = {}
        for name, tags in self.globals.get(module.rel, {}).items():
            found[name] = set(tags)
        for local, (other, original) in module.import_symbol.items():
            tags = self.globals.get(other, {}).get(original)
            if tags:
                found.setdefault(local, set()).update(tags)
        return found

    def visible_string_globals(self, module: _ModuleAnalysis
                               ) -> Dict[str, Set[str]]:
        """The same rule as `visible_globals`, for string constants.

        `music_section.read_section` does `music_selection.get(
        SECTION_KEY)`, and `SECTION_KEY = "section"` is a module
        constant.  Without this the whole music-section decision - which
        part of the track plays (AGENTS.md 10.5) - reads as an unread
        field."""
        found: Dict[str, Set[str]] = {}
        for name, values in self.global_strings.get(module.rel, {}).items():
            found[name] = set(values)
        for local, (other, original) in module.import_symbol.items():
            values = self.global_strings.get(other, {}).get(original)
            if values:
                found.setdefault(local, set()).update(values)
        return found

    def class_fields(self, module_rel: str, class_name: str) -> List[str]:
        """The annotated fields a dataclass declares, in order."""
        module = self.modules.get(module_rel)
        if module is None:
            return []
        node = module.classes.get(class_name)
        if node is None:
            return []
        found = []
        for statement in node.body:
            if isinstance(statement, ast.AnnAssign) and isinstance(
                    statement.target, ast.Name):
                found.append(statement.target.id)
        return found

    def kind_of(self, module_rel: str, name: str) -> str:
        module = self.modules.get(module_rel)
        if module is None:
            return ""
        if name in module.classes:
            return "class"
        if name in module.functions:
            return "func"
        return ""

    def bind_call(self, module_rel: str, name: str,
                  positional: List[Set[str]], keyword: Dict[str, Set[str]],
                  positional_strings: List[Set[str]] = None,
                  keyword_strings: Dict[str, Set[str]] = None) -> None:
        module = self.modules.get(module_rel)
        if module is None:
            return
        node = module.functions.get(name)
        if node is None:
            return
        names = [a.arg for a in node.args.posonlyargs + node.args.args]
        if "." in name and names and names[0] in ("self", "cls"):
            names = names[1:]
        for store, positions, keywords in (
                (self.params, positional, keyword),
                (self.param_strings, positional_strings or [],
                 keyword_strings or {})):
            slot = store.setdefault((module_rel, name), {})
            for index, values in enumerate(positions):
                if index >= len(names) or not values:
                    continue
                current = slot.setdefault(names[index], set())
                if not values <= current:
                    current |= values
                    self.changed = True
            for key, values in keywords.items():
                if not values:
                    continue
                current = slot.setdefault(key, set())
                if not values <= current:
                    current |= values
                    self.changed = True

    def seed_for(self, module: _ModuleAnalysis, node: ast.Call,
                 scope: _FunctionScope) -> Set[str]:
        return self.seeds.match(module, node, scope)

    def run(self) -> None:
        for round_number in range(self.MAX_ROUNDS):
            self.changed = False
            self.accesses = []
            self.constructor_sources = []
            self.dynamic = []
            self.too_deep = 0
            self.too_nested = 0
            self.widened = 0
            for rel, module in self.modules.items():
                module.accesses = []
                module._seen = set()
                module.constructor_sources = []
                module.dynamic = []
                self._analyse(module)
                self.accesses.extend(module.accesses)
                self.constructor_sources.extend(module.constructor_sources)
                self.dynamic.extend(module.dynamic)
            if not self.changed:
                break

    def _analyse(self, module: _ModuleAnalysis) -> None:
        module_scope = _FunctionScope(module, module.tree, {})
        module_scope.name = "<module>"
        module_scope.run()
        # MERGE, never replace.  A `global X` assignment inside a
        # function writes here between rounds, and overwriting with the
        # module-level environment would throw it away every round -
        # which is exactly `compile_manifest._STATE_OUTPUTS`, declared
        # `= {}` at module level and filled by `build_manifest`.
        slot = self.globals.setdefault(module.rel, {})
        for name, tags in module_scope.env.items():
            slot.setdefault(name, set()).update(tags)
        strings = self.global_strings.setdefault(module.rel, {})
        for name, values in module_scope.strings.items():
            strings.setdefault(name, set()).update(values)
        for name, node in module.functions.items():
            params = dict(self.visible_globals(module))
            params.update(self.params.get((module.rel, name), {}))
            seeded = self.seeds.parameter_seeds(module.rel, name, node)
            for key, tags in seeded.items():
                params.setdefault(key, set()).update(tags)
            owner_class = name.split(".")[0] if "." in name else ""
            if owner_class:
                # A method reads its own object's fields, and a property
                # that computes from two of them is a real reader of
                # both.  `ReelMoment.duration` is why the reel path's
                # `timeline_start` has a reader at all.
                instance = {f"{ROOT_CLASS}{module.rel}:{owner_class}"}
                params.setdefault("self", set()).update(instance)
            scope = _FunctionScope(module, node, params, owner_class)
            scope.strings = self.visible_string_globals(module)
            scope.strings.update({k: set(v) for k, v in
                                  self.param_strings.get((module.rel, name),
                                                         {}).items()})
            scope.run()
            key = (module.rel, name)
            current = self.returns.setdefault(key, set())
            if not scope.returns <= current:
                current |= scope.returns
                self.changed = True


# ── The seed table ──────────────────────────────────────────────────

class Seeds:
    """Every way a tag may be invented, in one place.

    `data_map` supplies the document table; this class turns it into the
    recognisers.  Kept separate from `World` so a test can seed a single
    document and assert what that alone finds - which is how the
    instrument is validated against a read that is known to exist.
    """

    def __init__(self, documents: Dict[str, Sequence[str]] = None,
                 step_dirs: Sequence[str] = (),
                 view_builders: Dict[Tuple[str, str], str] = None):
        self.documents = dict(documents or {})
        """document id -> every string literal this repository spells its
        name with.  A pattern (`clip_*.json`) is spelled as it appears in
        the code, and matched by its non-wildcard tail."""
        self.by_literal: Dict[str, str] = {}
        self.by_suffix: Dict[str, str] = {}
        for doc_id, literals in self.documents.items():
            for literal in literals:
                if "*" in literal:
                    self.by_suffix[literal] = doc_id
                else:
                    self.by_literal[literal] = doc_id
        self.step_dirs = set(step_dirs)
        self.view_builders = dict(view_builders or {})
        """(module path, function) -> view name.

        A named context view is a PROMPT, and its builder reads the
        step's inputs through one `data` parameter shared by every step
        that declares the view.  Seeding it separately is what lets
        `data_map` say which fields `view:picture` puts in front of a
        model - six steps declare one, and without this the whole
        prompt-side of the picture, the prosody and the transcript would
        read as unread."""
        self.merged_parameters = ("data", "inputs")
        self.llm_parameters = ("llm_output",)

    # -- parameter seeds -------------------------------------------------

    def parameter_seeds(self, module_rel: str, func_name: str,
                        node) -> Dict[str, Set[str]]:
        """A step body's merged-input parameter is the runner's dict.

        `operations.MERGED_INPUT_PARAMETERS` is the same enumeration from
        the other side: the runner binds the WHOLE gathered dict to a
        parameter of one of these names, so a function in a step
        directory that takes one is reading step inputs.
        """
        view = self.view_builders.get((module_rel, func_name))
        if view:
            names = [a.arg for a in node.args.posonlyargs + node.args.args]
            if names:
                return {names[0]: {f"{ROOT_VIEW}{view}"}}
            return {}
        step = _step_dir_of(module_rel)
        if not step or step not in self.step_dirs:
            return {}
        found: Dict[str, Set[str]] = {}
        names = [a.arg for a in node.args.posonlyargs + node.args.args
                 + node.args.kwonlyargs]
        for name in names:
            if name in self.merged_parameters:
                found[name] = {f"{ROOT_STEP_INPUTS}{step}"}
            elif name in self.llm_parameters:
                found[name] = {f"{ROOT_LLM}{step}"}
        return found

    # -- call seeds ------------------------------------------------------

    def match(self, module: _ModuleAnalysis, node: ast.Call,
              scope: "_FunctionScope") -> Set[str]:
        """The only place a `DOC@`, `IN@`, `LLM@` or `ENV@` tag begins."""
        func = node.func
        name = module.called_name(func)

        if name in ("getenv",) and node.args and _is_str(node.args[0]):
            return {f"{ROOT_ENV}{node.args[0].value}"}

        if name in ("load", "loads") and isinstance(func, ast.Attribute) \
                and isinstance(func.value, ast.Name) \
                and func.value.id == "json":
            if not node.args:
                return set()
            argument = node.args[0]
            if _mentions_stdin(argument):
                step = _step_dir_of(module.rel)
                if step in self.step_dirs:
                    return {f"{ROOT_STEP_INPUTS}{step}"}
                return set()
            found = set()
            for tag in _paths(scope.tags_of(argument)):
                doc_id = tag[len(ROOT_PATH):]
                found.add(self.root_for(doc_id))
            return found
        return set()

    def root_for(self, doc_id: str) -> str:
        """`DOC@<id>`, plus the path inside it the loader really returns.

        `reel_proposals_v2` is a document with a `moments` list in it and
        `read_proposal` hands back the list, not the file.  Without the
        offset every read through it would be recorded one level too
        shallow and would not join to the document's own shape."""
        return f"{ROOT_DOC}{doc_id}"

    def document_named(self, literal: str) -> str:
        """Which document a string literal names, if any.

        Exact match, then a suffix match for the templated names -
        `f"{stem}_prosody.json"`, `clip_*.json`.  A pattern in
        `DOCUMENTS` is spelled with its literal wildcard so the table
        reads the way the code does.
        """
        if literal in self.by_literal:
            return self.by_literal[literal]
        if literal in self.by_suffix:
            return self.by_suffix[literal]
        for pattern, doc_id in self.by_suffix.items():
            if fnmatch.fnmatch(literal, pattern):
                return doc_id
        return ""


def _is_environ(node) -> bool:
    return (isinstance(node, ast.Attribute) and node.attr == "environ"
            and isinstance(node.value, ast.Name) and node.value.id == "os")


def _mentions_stdin(node) -> bool:
    return any(isinstance(child, ast.Attribute) and child.attr == "stdin"
               for child in ast.walk(node))


def _literals_in(node) -> List[str]:
    return [child.value for child in ast.walk(node)
            if isinstance(child, ast.Constant)
            and isinstance(child.value, str)]


def _names_in(node) -> List[str]:
    return [child.id for child in ast.walk(node) if isinstance(child, ast.Name)]


def _step_dir_of(module_rel: str) -> str:
    parts = module_rel.split("/")
    if len(parts) >= 3 and parts[0] == "library" and parts[1] == "steps":
        return parts[2]
    return ""


def python_files() -> List[Path]:
    """The engine's own modules.  Tests are not readers (`output_contract`)."""
    files = [_REPO_ROOT / "manage_project.py"]
    for path in sorted((_REPO_ROOT / "library").rglob("*.py")):
        text = path.as_posix()
        if "__pycache__" in text or "/tests/" in text:
            continue
        if path.name.startswith("test_"):
            continue
        if path.relative_to(_REPO_ROOT).as_posix() in CLASSIFIERS:
            continue
        files.append(path)
    return [f for f in files if f.is_file()]


def analyse(seeds: Seeds, files: Sequence[Path] = None) -> World:
    world = World(seeds)
    world.load(files if files is not None else python_files())
    world.run()
    return world


# ── CLI ─────────────────────────────────────────────────────────────

def main(argv=None) -> int:
    import argparse

    from library.tools import data_map

    parser = argparse.ArgumentParser(
        description="Which field of which document each line reads.")
    parser.add_argument("--reads", default="",
                        help="Print every read of this tag prefix.")
    parser.add_argument("--stats", action="store_true")
    args = parser.parse_args(argv)

    world = data_map.analysed_world()
    if args.reads:
        for access in sorted(world.accesses,
                             key=lambda a: (a.module, a.line)):
            if access.tag.startswith(args.reads):
                print(f"{access.tag:<62} {access.shape:<12} "
                      f"{access.module}:{access.line} ({access.func})")
        return 0
    roots: Dict[str, int] = {}
    for access in world.accesses:
        roots[access.root] = roots.get(access.root, 0) + 1
    for root, count in sorted(roots.items(), key=lambda kv: -kv[1]):
        print(f"{count:>7}  {root}")
    print(f"\n{len(world.accesses)} attributed field reads across "
          f"{len(world.modules)} modules.")
    print(f"{len(world.dynamic)} dynamic-key reads (a whole container, "
          f"no field name).")
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
