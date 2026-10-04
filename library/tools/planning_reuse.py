"""Content identity for the edit decisions that feed ``compile_manifest``.

The edit ledger used to mean only "this step succeeded once". That is
enough for explicit preflight caching because preflight also checks the
footage and measurement code. It is not enough for planning: those steps
read changing project configuration, measured analysis, user notes,
references and prompts. This module gives each completed planning step a
closed, per-step identity. A missing or unreadable part means a cache
MISS, never an assumed match.

The key is deliberately based on the complete gathered input object, not
on a second handwritten list of selected fields. It also includes the
step's implementation/import closure, the runner code that assembles and
projects context, the step's incoming DAG mappings, referenced files and
the step's recorded output files. Separate keys let an unchanged branch
reuse its own output after a sibling changes.

One enumeration: ``library/tools/planning_reuse.py``.
"""

from __future__ import annotations

import ast
import hashlib
import json
import os
from pathlib import Path
from typing import Any

from library.tools.project_layout import Area, ProjectLayout

STATE_KEY = "planning_reuse"
FORMAT = "planning_reuse/1"

# These modules define the effective inputs and the request the model sees.
# Hash their import closure as a shared context version for every planning
# node. The step-specific code closure is added separately below.
_CONTEXT_CODE = (
    "library/processes/edit_video/run_pipeline.py",
    "library/tools/planning_reuse.py",
    "library/tools/code_identity.py",
    "library/tools/capability_outputs.py",
    "library/tools/step_ledger.py",
    "library/tools/context_projector.py",
    "library/tools/context_views.py",
    "library/tools/model_task.py",
    "library/tools/pipeline_skills.py",
    "library/tools/post_bridge_retry.py",
    "library/tools/second_pass.py",
    "library/tools/nothing_to_decide.py",
    "library/tools/decided_value.py",
    "library/tools/undetermined.py",
    "library/tools/direction_contradiction.py",
    "library/tools/brief_reference.py",
    "library/tools/brief_snapshot.py",
    "library/tools/project_context.py",
    "library/tools/marker_routing.py",
    "library/tools/brand_registry.py",
    "library/tools/project_layout.py",
    "library/tools/transcript_corrections.py",
    "library/tools/timeline_transcript.py",
    "library/tools/learned_context.py",
    "library/tools/stable_json.py",
    "library/tools/step_exporter.py",
)

_IGNORED_DIRS = frozenset({"__pycache__", ".git", ".venv"})
_IGNORED_FILES = frozenset({".DS_Store"})
_PATH_KEYS = frozenset({
    "path", "file", "filepath", "source_file", "source_path",
    "audio_path", "index_path", "profile_path", "transcript_path",
    "document_path", "asset_path", "font_path", "logo_path",
    "index_dir", "directory",
})


class PlanningReuseError(ValueError):
    """A cache key or artifact identity could not be proved."""


def planning_nodes(dag: dict, stage_by_node: dict[str, str],
                   final_node: str = "compile_manifest") -> set[str]:
    """The edit-stage ancestors of the final assembly manifest, inclusive."""
    nodes = {node["id"] for node in dag["nodes"]}
    if final_node not in nodes:
        return set()
    parents: dict[str, set[str]] = {node: set() for node in nodes}
    for edge in dag["edges"]:
        parents[edge["to"]].add(edge["from"])
    ancestors = {final_node}
    pending = [final_node]
    while pending:
        node = pending.pop()
        for parent in parents[node]:
            if parent not in ancestors:
                ancestors.add(parent)
                pending.append(parent)
    return {node for node in ancestors if stage_by_node.get(node) == "edit"}


def _json_bytes(value: Any) -> bytes:
    try:
        return json.dumps(
            value, sort_keys=True, separators=(",", ":"),
            ensure_ascii=True, allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise PlanningReuseError(
            f"planning input is not stable JSON: {exc}") from None


def _hash_json(value: Any) -> str:
    return hashlib.sha256(_json_bytes(value)).hexdigest()


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise PlanningReuseError(
            f"cannot read planning dependency {path}: {exc}") from None
    return digest.hexdigest()


def _add_tree_files(root: Path, found: set[Path]) -> None:
    if not root.exists():
        return
    if root.is_file():
        found.add(root.resolve())
        return
    pending = [root]
    visited: set[Path] = set()
    while pending:
        current = pending.pop()
        resolved = current.resolve()
        if resolved in visited:
            continue
        visited.add(resolved)
        try:
            children = sorted(current.iterdir(), key=lambda path: path.name)
        except OSError as exc:
            raise PlanningReuseError(
                f"cannot read planning dependency directory {current}: {exc}") \
                from None
        for path in children:
            if path.is_dir():
                if path.name not in _IGNORED_DIRS:
                    pending.append(path)
            elif path.name not in _IGNORED_FILES and path.is_file():
                found.add(path.resolve())


def _declared_skill_files(step_dir: Path, repo_root: Path) -> set[Path]:
    """Include each declared skill's instructions and executable code."""
    manifest_path = step_dir / "manifest.json"
    if not manifest_path.is_file():
        return set()
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanningReuseError(
            f"cannot inspect planner manifest {manifest_path}: {exc}") from None
    skills = manifest.get("skills", [])
    if not isinstance(skills, list):
        raise PlanningReuseError(
            f"planner manifest skills must be a list: {manifest_path}")

    from library.tools.pipeline_skills import SKILLS

    found: set[Path] = set()
    for name in skills:
        if not isinstance(name, str) or not name:
            raise PlanningReuseError(
                f"planner manifest has an invalid skill name: {manifest_path}")
        instructions = repo_root / ".agents" / "skills" / name / "SKILL.md"
        skill = SKILLS.get(name)
        if skill is None:
            raise PlanningReuseError(
                f"declared planner skill {name!r} is not registered")
        module_dir = repo_root.joinpath(*skill.module.split("."))
        if not instructions.is_file() or not module_dir.is_dir():
            raise PlanningReuseError(
                f"declared planner skill {name!r} is incomplete")
        found.add(instructions.resolve())
        _add_tree_files(module_dir, found)
    return found


def _module_files(module: str, repo_root: Path) -> set[Path]:
    """Resolve a local ``library.*`` import to its source file(s)."""
    if not module.startswith("library"):
        return set()
    base = repo_root.joinpath(*module.split("."))
    out = set()
    if base.with_suffix(".py").is_file():
        out.add(base.with_suffix(".py").resolve())
    if (base / "__init__.py").is_file():
        out.add((base / "__init__.py").resolve())
    # Importing `library.tools` also executes both package initializers.
    parts = module.split(".")
    for end in range(1, len(parts)):
        init = repo_root.joinpath(*parts[:end], "__init__.py")
        if init.is_file():
            out.add(init.resolve())
    return out


def _imports_from(path: Path, repo_root: Path) -> set[Path]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError, UnicodeDecodeError) as exc:
        raise PlanningReuseError(
            f"cannot inspect planner code {path}: {exc}") from None
    try:
        relative = path.resolve().relative_to(repo_root.resolve())
    except ValueError:
        return set()
    module_parts = list(relative.with_suffix("").parts)
    if module_parts[-1] == "__init__":
        package_parts = module_parts[:-1]
    else:
        package_parts = module_parts[:-1]

    found: set[Path] = set()
    for item in ast.walk(tree):
        if isinstance(item, ast.Import):
            for alias in item.names:
                found.update(_module_files(alias.name, repo_root))
        elif isinstance(item, ast.ImportFrom):
            if item.level:
                trim = max(0, len(package_parts) - item.level + 1)
                base = package_parts[:trim]
                if item.module:
                    base += item.module.split(".")
                module = ".".join(base)
            else:
                module = item.module or ""
            found.update(_module_files(module, repo_root))
            # `from library.tools import helper` can name a sibling module,
            # an attribute exported by __init__.py, or both. Cover both.
            if module.startswith("library"):
                for alias in item.names:
                    if alias.name != "*":
                        found.update(_module_files(
                            f"{module}.{alias.name}", repo_root))
    return found


def _code_files(step_dir: Path, repo_root: Path) -> set[Path]:
    found: set[Path] = set()
    _add_tree_files(step_dir, found)
    found.update(_declared_skill_files(step_dir, repo_root))
    queue = list(found)
    for relative in _CONTEXT_CODE:
        path = repo_root / relative
        if not path.is_file():
            raise PlanningReuseError(
                f"required planning context code is missing: {path}")
        found.add(path.resolve())
        queue.append(path.resolve())
    inspected: set[Path] = set()
    while queue:
        path = queue.pop()
        if path in inspected or path.suffix != ".py":
            continue
        inspected.add(path)
        if path == repo_root / "library/processes/edit_video/run_pipeline.py":
            # This file is hashed as a whole, but its module-level imports
            # include renderer, Resolve and QA code that planning does not
            # execute. The context helpers it calls are enumerated above.
            continue
        for dependency in _imports_from(path, repo_root):
            if dependency not in found:
                found.add(dependency)
                queue.append(dependency)
    return found


def _code_digest(step_dir: Path, repo_root: Path) -> str:
    files = _code_files(step_dir, repo_root)
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.as_posix()):
        try:
            rel = path.relative_to(repo_root).as_posix()
        except ValueError:
            rel = path.as_posix()
        digest.update(rel.encode("utf-8"))
        digest.update(b"\0")
        digest.update(_hash_file(path).encode("ascii"))
        digest.update(b"\0")
    return digest.hexdigest()


def _inside(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def _shared_library_roots(
        node_id: str, layout: ProjectLayout) -> list[tuple[str, str]]:
    roots: list[tuple[str, str]] = []
    if node_id == "music_selection":
        roots.append(("project_music", str(layout.read_dir(Area.MUSIC))))
        from library.tools.paths import music_library_path
        roots.append(("shared_music", music_library_path()))
    elif node_id == "plan_sfx":
        from library.tools.paths import sfx_library_path
        roots.append(("shared_sfx", sfx_library_path()))
    return roots


def _input_files(inputs: dict, project_dir: str, node_id: str) -> set[Path]:
    """Files a step can follow from its effective context, plus user inputs."""
    layout = ProjectLayout(project_dir)
    found: set[Path] = {layout.project_config_path.resolve()}
    raw_root = layout.read_dir(Area.RAW)

    # Maps and references deliberately let a model fetch document bodies
    # from disk. Hash the full source, not just its path, size or lede.
    def visit(value: Any, key: str = "") -> None:
        if isinstance(value, dict):
            for child_key, child in value.items():
                visit(child, str(child_key))
        elif isinstance(value, (list, tuple)):
            for child in value:
                visit(child, key)
        elif isinstance(value, str):
            candidate = Path(value.strip())
            if key in _PATH_KEYS or key.endswith(("_path", "_dir")):
                if not candidate.is_absolute():
                    candidate = layout.root / candidate
                if candidate.is_file() and not _inside(candidate, raw_root):
                    found.add(candidate.resolve())
                elif candidate.is_dir() and not _inside(candidate, raw_root):
                    _add_tree_files(candidate, found)
            for line in value.splitlines():
                stripped = line.strip()
                if stripped.startswith("FILE:"):
                    reference = stripped[5:].strip().strip("`\"'")
                    path = Path(reference)
                    if (path.is_absolute() and path.is_file()
                            and not _inside(path, raw_root)):
                        found.add(path.resolve())

    visit(inputs or {})

    # These are user-owned inputs whose maps can intentionally omit body
    # bytes because the model may fetch them later. Their content belongs
    # in the key whenever that step received the corresponding context.
    if "project_context" in (inputs or {}):
        for area in (Area.CONTEXT, Area.LEARNED_CONTEXT):
            _add_tree_files(layout.read_dir(area), found)
    if "timeline_notes" in (inputs or {}):
        _add_tree_files(layout.read_dir(Area.MARKER_FEEDBACK), found)
    if "semantic_analysis_documents" in (inputs or {}):
        _add_tree_files(layout.read_dir(Area.VISION_ANALYSIS), found)
    if {"temporal_index", "temporal_event_indices"}.intersection(inputs or {}):
        _add_tree_files(layout.read_dir(Area.TEMPORAL_INDEX), found)

    # Hash the project-owned visual source folders only for a step that
    # received a brand declaration. Other branches remain independent of
    # those assets. Referenced files outside these folders are discovered
    # from their absolute paths above.
    brand_inputs = {"brand_template", "brand_style", "brand_effect",
                    "brand_content"}
    if brand_inputs.intersection(inputs or {}):
        for area in (Area.ASSETS, Area.BRAND_ASSETS, Area.COMPOSITIONS):
            _add_tree_files(layout.read_dir(area), found)

    # Two planners consult shared media libraries by path, beyond the
    # catalog values routed through their input maps. Their concrete
    # inventory and bytes therefore belong to the key. Remote search
    # results are retained as part of the recorded music_selection output
    # and are refreshed by an explicit --rerun of that step.
    for _name, root in _shared_library_roots(node_id, layout):
        if root:
            _add_tree_files(Path(root), found)

    return found


def _external_digest(inputs: dict, project_dir: str, node_id: str) -> str:
    layout = ProjectLayout(project_dir)
    paths = _input_files(inputs, project_dir, node_id)
    rows = []
    for path in sorted(paths, key=lambda p: p.as_posix()):
        rows.append({
            "path": path.as_posix(),
            "size": path.stat().st_size,
            "sha256": _hash_file(path),
        })
    return _hash_json({
        "files": rows,
        "library_roots": _shared_library_roots(node_id, layout),
    })


def _incoming_digest(dag: dict, node_id: str) -> str:
    node = next((entry for entry in dag["nodes"]
                 if entry["id"] == node_id), None)
    if node is None:
        raise PlanningReuseError(f"planner node {node_id!r} is absent from DAG")
    incoming = [edge for edge in dag["edges"] if edge["to"] == node_id]
    return _hash_json({"node": node, "incoming": incoming})


def cache_identity(node_id: str, dag: dict, inputs: dict,
                   execution: dict, step_dir: str | Path,
                   project_dir: str, repo_root: str | Path,
                   source_fingerprints: dict | None = None) -> dict:
    """Return the versioned per-step key and its auditable components."""
    root = Path(repo_root).resolve()
    step_path = Path(step_dir).resolve()
    input_digest = _hash_json(inputs or {})
    external_digest = _external_digest(inputs or {}, project_dir, node_id)
    code_digest = _code_digest(step_path, root)
    incoming_digest = _incoming_digest(dag, node_id)
    execution_digest = _hash_json(execution or {})
    components = {
        "inputs": input_digest,
        "external_files": external_digest,
        "source_identity": _hash_json(source_fingerprints or {}),
        "planner_code": code_digest,
        "dag_inputs": incoming_digest,
        "execution": execution_digest,
    }
    key = _hash_json({"format": FORMAT, "components": components})
    return {"format": FORMAT, "key": key, "components": components}


def _step_output_dir(project_dir: str, node_id: str) -> Path:
    return ProjectLayout(project_dir).step_dir(node_id)


def artifact_digest(project_dir: str, node_id: str) -> str:
    """Hash every recorded file the step owns, including exported output."""
    root = _step_output_dir(project_dir, node_id)
    if not root.is_dir():
        raise PlanningReuseError(
            f"recorded output directory is missing for {node_id}: {root}")
    if not (root / "output.json").is_file():
        raise PlanningReuseError(
            f"recorded output.json is missing for {node_id}: {root}")
    rows = []
    for current, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(d for d in dirs if d not in _IGNORED_DIRS)
        for name in sorted(files):
            if name in _IGNORED_FILES:
                continue
            path = Path(current) / name
            rel = path.relative_to(root).as_posix()
            rows.append({
                "path": rel,
                "size": path.stat().st_size,
                "sha256": _hash_file(path),
            })
    return _hash_json(rows)


def output_digest(output: dict) -> str:
    return _hash_json(output or {})


def recorded_output(project_dir: str, node_id: str) -> dict:
    """Load the step's last exported result for a cleared capability slot."""
    path = _step_output_dir(project_dir, node_id) / "output.json"
    try:
        with path.open(encoding="utf-8") as handle:
            output = json.load(handle)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PlanningReuseError(
            f"cannot read recorded output for {node_id}: {exc}") from None
    if not isinstance(output, dict):
        raise PlanningReuseError(
            f"recorded output for {node_id} is not an object")
    return output


def reusable(state: dict, node_id: str, identity: dict,
             output: dict, project_dir: str) -> tuple[bool, str]:
    """Check all witnesses before calling a completed planning step reusable."""
    record = (state.get(STATE_KEY) or {}).get(node_id)
    if not isinstance(record, dict):
        return False, "no prior identity"
    if record.get("format") != FORMAT:
        return False, "cache format changed"
    if record.get("key") != identity.get("key"):
        previous = record.get("components") or {}
        current = identity.get("components") or {}
        changed = [name for name in current
                   if previous.get(name) != current.get(name)]
        return False, "changed " + (", ".join(changed) or "planning identity")
    if record.get("output_digest") != output_digest(output):
        return False, "recorded capability output changed"
    try:
        current_artifacts = artifact_digest(project_dir, node_id)
    except (OSError, PlanningReuseError) as exc:
        return False, str(exc)
    if record.get("artifact_digest") != current_artifacts:
        return False, "step output files changed or are missing"
    return True, "identical inputs, code and outputs"


def record_success(state: dict, node_id: str, identity: dict,
                   output: dict, project_dir: str) -> str | None:
    """Persist a cache witness after the step's exports are on disk.

    Returning a reason instead of raising keeps bookkeeping failure from
    converting a good planning result into a failed step. With no record,
    the next run re-derives it safely.
    """
    try:
        artifacts = artifact_digest(project_dir, node_id)
    except (OSError, PlanningReuseError) as exc:
        return str(exc)
    state.setdefault(STATE_KEY, {})[node_id] = {
        **identity,
        "output_digest": output_digest(output),
        "artifact_digest": artifacts,
    }
    return None


def refresh_output(state: dict, node_id: str, output: dict) -> None:
    """Keep a reviewer-approved output change attached to the same key."""
    record = (state.get(STATE_KEY) or {}).get(node_id)
    if isinstance(record, dict) and record.get("format") == FORMAT:
        record["output_digest"] = output_digest(output)
