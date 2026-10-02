"""Per-layer measurement cache for the semantic profile (step 1.03).

The problem this removes
------------------------
Step 1.03's code identity is eleven files, and any change to any of
them deleted every cached profile and re-measured every clip. On
project 001 that is 17 clips and 3,167 s of model time, paid 29 times
in September 2026 alone - for changes as far from a measurement as
the heavy-work lock, a refusal message or the schema adapter that runs
at READ time anyway.

The shape
---------
A profile is composed from LAYERS. The three that cost something are
cached here, each under its own key:

========== ============================================ =================
layer      what is cached                                cost
========== ============================================ =================
windows    the folded per-window answers (actions,       gemma, one call
           scene, camera, assessment votes)              per 10 s window
objects    coarse + detail object passes                 still vision
picture    soft-picture ranges                           ffmpeg decode
========== ============================================ =================

Everything else - scene normalization, camera merge, the assessment
vote merge, the deterministic assessment and usable ranges, the schema
adapter - is pure arithmetic over those and is recomputed on every
compose, so a change to it costs no inference at all.

Actions, scene and camera are ONE layer, not three: since the folded
call (#1384) they are answered by the same model call, so no change can
re-measure one without the others.

The key
-------
``sha256(layer, source digest, method digest, parameters)``:

* **source digest** - ``footage_identity.fingerprint``, the content
  identity source memory already keys on. The store lives in that
  source's memory directory, ``<source memory root>/<digest>/
  measurements/``, so the same camera file used by ten projects is
  measured once.
* **method digest** - the AST of every top-level definition the
  layer's entry points reach in its module, transitively, plus every
  repo module those definitions import (whole file, transitively).
  Docstrings and comments are not part of an AST dump, so prose edits
  invalidate nothing; any code edit on the reached path invalidates
  that layer and only that layer. An unresolvable name is not part of
  the module (a builtin, a third-party import) and contributes nothing:
  model WEIGHTS and library versions are outside the identity, as they
  are in ``library/tools/code_identity.py``; the model id is not, and
  is reached as a constant.
* **parameters** - every non-source input the layer reads, by VALUE:
  duration, fps, the clip's temporal index and transcript, which host
  looks at stills. A changed temporal index re-measures the windows.

``force`` reads nothing and overwrites, which is what ``--force``
always meant.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Optional

try:
    from library.tools import footage_identity
except ImportError:  # imported as `tools.*` from inside library/
    from tools import footage_identity

REPO_ROOT = Path(__file__).resolve().parents[3]

MEASUREMENTS_DIRNAME = "measurements"


def store_root() -> Path:
    """The source-memory root, by `source_memory.memory_root`'s rule.

    Restated rather than imported (with `shared_environment.vep_home`'s
    rule under it): both modules import far past what this one needs,
    and this module is part of step 1.03's code identity
    (`code_identity.STEP_IMPLEMENTATION_DEPS`), so importing them would
    make every transcription or environment fix recompose the vision
    profiles. `tests/unit/picture/test_source_memory.py` pins the two rules
    together. The entries sit beside that source's other slots, under
    the same content digest.
    """
    explicit = os.environ.get("PIPELINE_SOURCE_MEMORY_ROOT")
    if explicit:
        return Path(explicit).expanduser()
    home = os.environ.get("PIPELINE_VEP_HOME")
    if home:
        return Path(home).expanduser() / "source_memory"
    xdg = os.environ.get("XDG_DATA_HOME")
    base = Path(xdg).expanduser() if xdg else Path.home() / ".local" / "share"
    return base / "vep" / "source_memory"

# Bumped only when the STORED SHAPE of an entry changes in a way older
# entries cannot be read as. A method change never needs this.
STORE_FORMAT = 1


# ── Method digest ──────────────────────────────────────────────────


def _strip_docstrings(tree: ast.AST) -> ast.AST:
    for node in ast.walk(tree):
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef,
                             ast.AsyncFunctionDef)):
            body = node.body
            if (body and isinstance(body[0], ast.Expr)
                    and isinstance(body[0].value, ast.Constant)
                    and isinstance(body[0].value.value, str)):
                node.body = body[1:] or [ast.Pass()]
    return tree


def _parse(path: Path) -> ast.Module:
    return _strip_docstrings(ast.parse(path.read_text(encoding="utf-8")))


def _repo_module_file(module: str, repo_root: Path) -> Optional[Path]:
    """The repo file a dotted import names, or None outside the repo."""
    parts = module.split(".")
    if parts[0] == "tools":  # the `tools.*` spelling used inside library/
        parts = ["library"] + parts
    if parts[0] != "library":
        return None
    base = repo_root.joinpath(*parts)
    for candidate in (base.with_suffix(".py"), base / "__init__.py"):
        if candidate.is_file():
            return candidate
    return None


def _imported_files(nodes: Iterable[ast.AST], repo_root: Path,
                    only_names: Optional[set] = None) -> dict:
    """{bound name: repo file} for the imports inside ``nodes``.

    ``from library.tools.analysis import picture_quality`` binds a
    MODULE; ``from library.tools.x import f`` binds a name inside
    ``x.py``. Either way the file is what is hashed.
    """
    out = {}
    for node in nodes:
        for sub in ast.walk(node):
            if isinstance(sub, ast.ImportFrom) and sub.module and not sub.level:
                for alias in sub.names:
                    bound = alias.asname or alias.name
                    if only_names is not None and bound not in only_names:
                        continue
                    path = (_repo_module_file(f"{sub.module}.{alias.name}",
                                              repo_root)
                            or _repo_module_file(sub.module, repo_root))
                    if path:
                        out[bound] = path
            elif isinstance(sub, ast.Import):
                for alias in sub.names:
                    bound = alias.asname or alias.name.split(".")[0]
                    if only_names is not None and bound not in only_names:
                        continue
                    path = _repo_module_file(alias.name, repo_root)
                    if path:
                        out[bound] = path
    return out


def _module_file_closure(path: Path, repo_root: Path, seen: set) -> None:
    """``path`` and every repo module it imports, transitively."""
    if path in seen:
        return
    seen.add(path)
    for dep in _imported_files([_parse(path)], repo_root).values():
        _module_file_closure(dep, repo_root, seen)


def _top_level_definitions(tree: ast.Module) -> dict:
    """{name: node} for each top-level def, class and assignment."""
    defs = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef,
                             ast.ClassDef)):
            defs[node.name] = node
        elif isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = (node.targets if isinstance(node, ast.Assign)
                       else [node.target])
            for target in targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        defs[sub.id] = node
    return defs


def reach(module_file, entry_points: Iterable[str], repo_root=None):
    """(reached top-level nodes in source order, reached repo files).

    Walks the names each reached top-level definition references, so a
    helper three calls down is part of the identity without anyone
    listing it. Raises ``KeyError`` for an entry point the module does
    not define: a layer keyed on nothing would match anything.
    """
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    module_file = Path(module_file)
    tree = _parse(module_file)
    defs = _top_level_definitions(tree)
    module_imports = _imported_files(tree.body, root)

    reached, files, stack = set(), set(), list(entry_points)
    for name in stack:
        if name not in defs and name not in module_imports:
            raise KeyError(f"{module_file.name} defines no {name!r}")
    while stack:
        name = stack.pop()
        if name in reached:
            continue
        reached.add(name)
        if name in module_imports:
            _module_file_closure(module_imports[name], root, files)
            continue
        node = defs.get(name)
        if node is None:
            continue
        # Imports local to the definition (``from x import y`` inside a
        # function body) are part of what it executes.
        for path in _imported_files([node], root).values():
            _module_file_closure(path, root, files)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id not in reached:
                stack.append(sub.id)

    nodes = {id(defs[n]): defs[n] for n in reached if n in defs}
    return sorted(nodes.values(), key=lambda n: n.lineno), sorted(files)


def method_digest(module_file, entry_points: Iterable[str],
                  repo_root=None) -> str:
    """The identity of the code ``entry_points`` reach (see ``reach``)."""
    root = Path(repo_root) if repo_root is not None else REPO_ROOT
    nodes, files = reach(module_file, entry_points, root)
    digest = hashlib.sha256()
    for node in nodes:
        digest.update(ast.dump(node).encode("utf-8"))
    for path in files:
        digest.update(str(path.relative_to(root)).encode("utf-8"))
        digest.update(ast.dump(_parse(path)).encode("utf-8"))
    return digest.hexdigest()


# ── The store ──────────────────────────────────────────────────────


def layer_key(layer: str, source_digest: str, method: str,
              params: dict) -> str:
    payload = json.dumps(
        {"format": STORE_FORMAT, "layer": layer, "source": source_digest,
         "method": method, "params": params},
        sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


class LayerCache:
    """One source's layer entries: ``get`` a hit, ``put`` a measurement.

    ``methods`` is ``{layer: method digest}``; ``params`` is
    ``{layer: parameters}``. A layer named in neither is never cached.
    """

    def __init__(self, source_digest: str, methods: dict, params: dict,
                 force: bool = False, root=None):
        self.source_digest = source_digest
        self.keys = {layer: layer_key(layer, source_digest, methods[layer],
                                      params.get(layer, {}))
                     for layer in methods}
        self.force = force
        self.dir = (Path(root) if root is not None else store_root()
                    ) / source_digest / MEASUREMENTS_DIRNAME
        # {layer: "reused" | "measured"} - what this compose did.
        self.outcomes: dict = {}

    @classmethod
    def for_source(cls, source_file, methods: dict, params: dict,
                   force: bool = False, root=None) -> Optional["LayerCache"]:
        """A cache for ``source_file``, or None when it cannot be read.

        No identity means no cache: the layers are measured as they
        always were, and nothing is stored under a key nobody can
        recompute.
        """
        try:
            digest = footage_identity.fingerprint(str(source_file))
        except OSError:
            return None
        return cls(digest["content_digest"], methods, params, force, root)

    def _path(self, layer: str) -> Path:
        return self.dir / layer / f"{self.keys[layer]}.json"

    def get(self, layer: str):
        """The stored measurement, or None (miss, forced, or unreadable)."""
        if self.force or layer not in self.keys:
            return None
        try:
            with open(self._path(layer), encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            return None
        if entry.get("key") != self.keys[layer]:
            return None
        self.outcomes[layer] = "reused"
        return entry["value"]

    def put(self, layer: str, value) -> None:
        """Store a fresh measurement. Best-effort: a store that cannot be
        written costs the next run a re-measure, never this run its
        result."""
        if layer not in self.keys:
            return
        self.outcomes[layer] = "measured"
        path = self._path(layer)
        tmp = path.parent / f".{path.name}.{os.getpid()}.tmp"
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with open(tmp, "w", encoding="utf-8") as handle:
                json.dump({"key": self.keys[layer], "value": value}, handle)
            os.replace(tmp, path)
        except (OSError, TypeError, ValueError):
            try:
                os.remove(tmp)
            except OSError:
                pass

    def record(self) -> dict:
        """``{layer: {key, outcome}}`` for the profile's metadata."""
        return {layer: {"key": self.keys[layer],
                        "outcome": self.outcomes[layer]}
                for layer in sorted(self.outcomes)}


def canonical_digest(value) -> str:
    """A parameter too large to key by value, keyed by its content."""
    return hashlib.sha256(json.dumps(
        value, sort_keys=True, separators=(",", ":"),
        default=str).encode("utf-8")).hexdigest()
