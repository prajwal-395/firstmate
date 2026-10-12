"""`ren asks <project> [--json]`: what the host model owes, each shared text once.

The reading surface of the file handshake (`library/tools/llm_handshake.py`).
A pending ask is a request file under `pipeline_output/llm_requests/` with
no response beside it in `llm_responses/`. A batch of reels files three asks
per reel, and before LE-11 every one of them restated the same prompt, the
same roster and the same schema: 31 reels were 2.38 MB, 20% unique. Requests
now carry that text by reference (`prompt_ref`, `context_refs`), and this
verb prints it ONCE:

* Asks are grouped by prompt, in file order of each prompt's first ask.
* A shared text - a prompt, a constraint block, a shared context section,
  an expected schema - is printed the first time it is needed and named by
  its short hash after that.
* Each ask prints only what is its own: the response path to write and its
  inline context, with each shared section shown as a `<<shared HASH>>`
  marker where it is spliced in.

The model input an ask stands for is exactly `llm_handshake.read_request`:
the prompt, then the constraints, then the context with its shared sections
in place. A response is validated exactly as it always was; this changes
what is READ, never what is accepted.

Read only: no model call, no Resolve, no project write.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path

#: Hex digits a shared text is named by in the text output.
SHORT = 12

#: Keys an ask's own block does not restate: shared (printed once by hash),
#: carried by the header, or the same for every ask of the project.
_NOT_ASK_FIELDS = {"prompt", "prompt_ref", "constraints", "context",
                   "context_refs", "expected_schema", "project_folder",
                   "timestamp", "step_id", "kind"}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def requests_dirs(root: Path) -> tuple[Path, Path]:
    """`(requests, responses)` directories of a project root."""
    from library.tools import llm_handshake
    return (root / llm_handshake.REQUESTS_SUBDIR,
            root / llm_handshake.RESPONSES_SUBDIR)


def pending_request_paths(root: Path) -> list[Path]:
    """Request files with no response file beside them, in name order."""
    requests, responses = requests_dirs(root)
    if not requests.is_dir():
        return []
    return [path for path in sorted(requests.glob("*.json"))
            if not (responses / path.name).exists()]


def _context_text(value) -> str:
    if isinstance(value, str):
        return value
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _ask(path: Path, responses: Path, shared: dict) -> dict:
    """One pending ask, its shared texts registered in `shared`."""
    from library.tools import llm_handshake

    wire = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(wire, dict):
        raise ValueError(f"{path} is not a JSON object")
    requests_dir = path.parent

    def register(role: str, text: str, digest: str = "") -> str:
        digest = digest or _sha(text)
        entry = shared.setdefault(digest, {"role": role, "text": text,
                                           "asks": 0})
        entry["asks"] += 1
        return digest

    ask = {
        "id": path.stem,
        "kind": str(wire.get("kind") or "llm_step"),
        "step_id": str(wire.get("step_id") or path.stem),
        "request": str(path),
        "response": str(responses / path.name),
        "requested_at": wire.get("timestamp"),
    }
    if isinstance(wire.get("prompt_ref"), dict):
        ref = wire["prompt_ref"]
        ask["prompt"] = register(
            "prompt", llm_handshake.read_shared(ref, requests_dir),
            str(ref["sha256"]))
    elif isinstance(wire.get("prompt"), str):
        # Written before shared text existed: the prompt still carries its
        # constraints, so it is grouped as one text.
        ask["prompt"] = register("prompt", wire["prompt"])
    else:
        ask["prompt"] = None
    constraints = wire.get("constraints")
    ask["constraints"] = (register("constraints", constraints)
                          if isinstance(constraints, str) and constraints
                          and "prompt_ref" in wire else None)
    schema = wire.get("expected_schema")
    ask["expected_schema"] = (register("expected_schema", schema)
                              if isinstance(schema, str) and schema else None)
    ask["context_shared"] = []
    for ref in wire.get("context_refs") or ():
        ask["context_shared"].append({
            "sha256": register("context", llm_handshake.read_shared(
                ref, requests_dir), str(ref["sha256"])),
            "offset": int(ref["offset"])})
    ask["context"] = wire.get("context", "")
    ask["fields"] = {key: value for key, value in wire.items()
                     if key not in _NOT_ASK_FIELDS}
    if wire.get("project_folder"):
        ask["project_folder"] = wire["project_folder"]
    return ask


def collect(project_root) -> dict:
    """Every pending ask of a project, grouped by prompt; shared text once."""
    root = Path(project_root)
    _, responses = requests_dirs(root)
    shared: dict = {}
    asks, unreadable = [], []
    for path in pending_request_paths(root):
        try:
            asks.append(_ask(path, responses, shared))
        except (OSError, ValueError, KeyError, TypeError) as exc:
            unreadable.append({"id": path.stem, "request": str(path),
                               "error": f"{type(exc).__name__}: {exc}"})
    groups: dict = {}
    for ask in asks:
        group = groups.setdefault(ask["prompt"], {
            "prompt": ask["prompt"], "step_ids": [], "asks": []})
        if ask["step_id"] not in group["step_ids"]:
            group["step_ids"].append(ask["step_id"])
        group["asks"].append(ask["id"])
    return {"project": str(root), "pending": len(asks),
            "groups": list(groups.values()),
            "shared": {digest: {"role": entry["role"], "asks": entry["asks"],
                                "text": entry["text"]}
                       for digest, entry in shared.items()},
            "asks": asks, "unreadable": unreadable}


def _context_with_markers(ask: dict, lengths: dict) -> str:
    """The ask's inline context with each shared section marked in place.

    A `context_refs` offset is a position in the FULL context, so the
    inline text is walked alongside it: each shared section's length is
    added back as it is passed.
    """
    text = _context_text(ask["context"])
    out, cursor, full = [], 0, 0
    for ref in ask["context_shared"]:
        take = ref["offset"] - full
        out.append(text[cursor:cursor + take])
        cursor += take
        out.append(f"<<shared {ref['sha256'][:SHORT]}>>")
        full = ref["offset"] + lengths[ref["sha256"]]
    out.append(text[cursor:])
    return "".join(out)


def _relative(path: str, root: str) -> str:
    """`path` relative to the project root the header names, if under it."""
    try:
        return str(Path(path).relative_to(root))
    except ValueError:
        return path


def render_text(report: dict) -> str:
    """The text an agent reads: each shared text once, then each ask."""
    asks = {ask["id"]: ask for ask in report["asks"]}
    shared = report["shared"]
    lengths = {digest: len(entry["text"]) for digest, entry in shared.items()}
    printed: set = set()
    lines = []
    folders = sorted({a.get("project_folder") for a in report["asks"]
                      if a.get("project_folder")})
    lines.append(
        f"ren asks: {report['pending']} ask(s) owed in {report['project']}; "
        f"{len(report['groups'])} prompt(s), {len(shared)} shared text(s), "
        f"each printed once.")
    if folders and folders != [report["project"]]:
        lines.append(f"project_folder: {', '.join(folders)}")
    lines.append(
        "An ask's model input is its prompt, then its constraints, then its "
        "context with each <<shared HASH>> replaced by that text. Answer "
        "each by writing one JSON object matching its schema to its "
        "`answer ->` path (relative to the project).")

    def emit(digest, label):
        if digest is None or digest in printed:
            return
        printed.add(digest)
        entry = shared[digest]
        lines.append("")
        lines.append(f"=== {label} {digest[:SHORT]} "
                     f"({entry['asks']} ask(s)) ===")
        lines.append(entry["text"].rstrip("\n"))

    for group in report["groups"]:
        emit(group["prompt"], "prompt")
        for ask_id in group["asks"]:
            ask = asks[ask_id]
            emit(ask["constraints"], "constraints")
            emit(ask["expected_schema"], "schema")
            for ref in ask["context_shared"]:
                emit(ref["sha256"], "shared context")
            lines.append("")
            uses = [f"prompt {ask['prompt'][:SHORT]}" if ask["prompt"] else
                    "no prompt"]
            if ask["constraints"]:
                uses.append(f"constraints {ask['constraints'][:SHORT]}")
            if ask["expected_schema"]:
                uses.append(f"schema {ask['expected_schema'][:SHORT]}")
            fields = " ".join(f"{key}={json.dumps(value, ensure_ascii=False)}"
                              for key, value in ask["fields"].items())
            lines.append(f"--- ask {ask['id']} [{ask['kind']} "
                         f"{ask['step_id']}] {'; '.join(uses)}"
                         + (f" {fields}" if fields else ""))
            lines.append(f"answer -> {_relative(ask['response'], report['project'])}")
            lines.append(_context_with_markers(ask, lengths))
    for bad in report["unreadable"]:
        lines.append("")
        lines.append(f"--- ask {bad['id']} UNREADABLE: {bad['error']} "
                     f"({bad['request']})")
    return "\n".join(lines) + "\n"


def _resolve_root(project_ref: str) -> Path:
    from library.tools.project_registry import resolve_project_path, scan_projects
    yaml_path = resolve_project_path(project_ref)
    if yaml_path is None:
        match = next((c for c in scan_projects() if c.slug == project_ref), None)
        if match is None:
            raise FileNotFoundError(f"No Ren project found for {project_ref!r}")
        yaml_path = match.project_root / "project.yaml"
    return Path(yaml_path).parent


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren asks",
        description="What the host model owes, each shared prompt once (read only)")
    parser.add_argument("project", help="project slug or path")
    parser.add_argument("--json", action="store_true",
                        help="print the whole structure as JSON")
    args = parser.parse_args(list(argv) if argv is not None else sys.argv[1:])
    try:
        root = _resolve_root(args.project)
    except (FileNotFoundError, ValueError) as exc:
        print(f"ren asks: {exc}", file=sys.stderr)
        return 2
    report = collect(root)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=False))
    else:
        sys.stdout.write(render_text(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
