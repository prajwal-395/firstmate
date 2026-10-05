"""Portable projects: machine-local paths that survive a move (AGENTS.md 8).

A project folder moves between Macs and users - a new disk, a new
username, a new checkout - and what breaks is every absolute path the
project recorded: `source.footage_root` in `project.yaml`, the clip
paths in `source_fingerprints` and the catalog in `pipeline_data.json`,
the `project_folder` the state remembers.  The version store commits
those bytes verbatim (`library/tools/versions/store.py`), so the fix
is not in the store: it is in what gets stored and what happens on
open.

Two halves, and they are not each other:

1. **Declarations are stored portable.**  An absolute path under the
   project folder is stored as `$PROJECT/...`; one under a user's home
   as `$HOME/...`; `~` and environment references keep working.  What
   is already relative (the default `raw/`, a project-local brief)
   stays relative.  `expand` resolves a stored declaration to this
   machine's absolute at read time; `portablize` is the inverse the
   format 1 -> 2 migration applies once.  A path on a volume no token
   names (`/Volumes/...`) stays absolute and is reported as unportable
   rather than rewritten into a lie.

2. **Recorded state is rebound on open.**  `ensure_portable_paths`
   runs inside `project_format.ensure_project_format` - the one gate
   every open passes through (`ren info`, `ren edit`, the reels build;
   listing stays read-only).  When the project moved it rewrites the
   stale absolutes in `pipeline_data.json`: paths under the old project
   folder are prefix-swapped (the same project at a new address),
   footage outside it is relinked by basename and verified by size, else
   by content digest (the same bytes at a new address), library records
   refresh from the live declarations, and anything still missing is
   reported, never guessed.  The rewrite is atomic, backed up under
   `pipeline_output/backups/portable/` with a manifest beside the
   format ones, and a no-op when nothing moved - so mid-run opens,
   which also pass through the gate, never write.

What this deliberately does NOT do:

- Per-clip artifacts under `pipeline_output/steps/` keep the absolute
  `source_file` the step that wrote them recorded.  The live state -
  fingerprints, catalog, project folder - is what every open and every
  identity check reads; a stale `source_file` inside a cached artifact
  is refreshed the next time its step runs, the way any other cached
  measurement is.
- Provenance, run archives and review notes are history: a path there
  names where a thing WAS, and rewriting history would be the lie.
- A copy whose originals still exist keeps pointing at the shared
  media until the originals go away.  Identity is content-based, so
  shared bytes are semantically shared; the next open after they move
  relinks.  Only a path that is missing is ever relinked.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import time
from pathlib import Path

PROJECT_TOKEN = "$PROJECT"
"""The project folder itself.  `$PROJECT/raw/clip.mp4` moves with the project."""

HOME_TOKEN = "$HOME"
"""The current user's home.  `$HOME/Documents/media` survives a new username."""

ENGINE_TOKEN = "$ENGINE"
"""The engine installation.  Named for declarations that reach outside
the project into the installed tree; no project state points there
today, and the token exists so the vocabulary does not have to grow
the day one does."""

_TOKENS = (PROJECT_TOKEN, HOME_TOKEN, ENGINE_TOKEN)

# A user-home prefix on this machine or any other: /Users/<name>/ on
# macOS, /home/<name>/ on Linux.  Two components only - the username
# is whatever sits between the slashes, never assumed to be ours.
_USER_HOME_RE = re.compile(r"^/(Users|home)/[^/]+/")

# One declaration line in project.yaml: indent, key, value, comment.
# Only the four path declarations migrate; every other line - and
# every comment - passes through byte for byte.
_DECL_LINE_RE = re.compile(
    r"^(?P<indent>[ \t]*)"
    r"(?P<key>footage_root|sfx_library|music_library|creative_brief)"
    r"(?P<sep>[ \t]*:[ \t]*)"
    r"(?P<value>.*?)"
    r"(?P<comment>[ \t]*(?:\#.*)?)$"
)

_MIGRATING_KEYS_SOURCE = {"footage_root"}
_MIGRATING_KEYS_PIPELINE = {"sfx_library", "music_library", "creative_brief"}


def _home() -> str:
    return os.path.expanduser("~")


def _engine_root() -> str:
    """This engine's installation root, without importing it.

    `paths.PILOT_ROOT` resolves through `ren.engine_root` (the
    packaged tree) with a checkout fallback.  Repeating that import
    here would pull `paths` - and its config load - into every
    declaration read, so the token expands to the checkout fallback
    only; a packaged install that needs more teaches this function,
    not every reader.
    """
    return str(Path(__file__).resolve().parent.parent.parent)


def _token_roots(project_folder) -> dict:
    raw = str(project_folder or "").strip()
    root = str(Path(raw).expanduser()) if raw else ""
    return {
        PROJECT_TOKEN: root,
        HOME_TOKEN: _home(),
        ENGINE_TOKEN: _engine_root(),
        "~": _home(),
    }


def expand(value, project_folder=None):
    """This machine's absolute for a stored path declaration.

    A leading `$PROJECT`, `$HOME`, `$ENGINE` or `~` resolves against
    the project folder, the current home and the engine checkout; a
    `$VAR` or `${VAR}` resolves against the environment (the
    `PipelineConfig.resolve_paths` half); anything else passes through
    untouched - relative stays relative, absolute stays absolute.
    Non-strings pass through: an absent declaration is not a path.
    """
    if not isinstance(value, str):
        return value
    text = value.strip()
    if not text:
        return value
    roots = _token_roots(project_folder or "")
    for token in (PROJECT_TOKEN, HOME_TOKEN, ENGINE_TOKEN):
        root = roots[token]
        if not root:
            continue
        if text == token:
            return root
        if text.startswith(token + "/"):
            return root + text[len(token):]
    if text == "~" or text.startswith("~/"):
        return roots["~"] + text[1:]
    if "$" in text:
        expanded = os.path.expandvars(text)
        if expanded != text:
            return os.path.expanduser(expanded)
    return value


def portablize(value, project_folder=None):
    """The portable spelling of an absolute path declaration.

    Under the project folder becomes `$PROJECT/...`; under any user's
    home (`/Users/<name>/`, `/home/<name>/`) becomes `$HOME/...`;
    anything else - a volume path, an already-portable token, a
    relative path, an empty declaration - is returned unchanged.
    """
    if not isinstance(value, str):
        return value
    original = value
    text = value.strip()
    if not text:
        return original
    if text.startswith(_TOKENS) or text.startswith("~"):
        return text
    if text.startswith("$"):
        return text
    inner = text
    if (len(inner) >= 2 and inner[0] == inner[-1]
            and inner[0] in ("'", '"')):
        inner = inner[1:-1]
    if not inner:
        return original
    if not os.path.isabs(inner):
        return original
    if project_folder:
        root = os.path.normpath(str(Path(str(project_folder)).expanduser()))
        if inner == root:
            return PROJECT_TOKEN
        if inner.startswith(root + os.sep):
            return PROJECT_TOKEN + "/" + inner[len(root + os.sep):]
    match = _USER_HOME_RE.match(inner)
    if match:
        return HOME_TOKEN + "/" + inner[match.end():]
    return original


def read_declarations(project_folder) -> dict:
    """The four path declarations off project.yaml, raw and portable.

    Never raises, never migrates, never imports the schema: the open
    gate calls this while the schema is what is being opened, and a
    missing or unreadable project.yaml reads as "nothing declared".
    Each value is the file's own spelling - tokens included - or "".
    """
    out = {"footage_root": "", "sfx_library": "",
           "music_library": "", "creative_brief": ""}
    if not project_folder:
        return out
    yaml_path = os.path.join(str(project_folder), "project.yaml")
    if not os.path.isfile(yaml_path):
        return out
    try:
        import yaml
    except ImportError:
        return out
    try:
        with open(yaml_path, encoding="utf-8") as handle:
            data = yaml.safe_load(handle) or {}
    except (OSError, ValueError):
        return out
    if not isinstance(data, dict):
        return out
    source = data.get("source") or {}
    pipeline = data.get("pipeline") or {}
    if isinstance(source, dict) and isinstance(source.get("footage_root"), str):
        out["footage_root"] = source["footage_root"]
    if isinstance(pipeline, dict):
        for key in ("sfx_library", "music_library", "creative_brief"):
            if isinstance(pipeline.get(key), str):
                out[key] = pipeline[key]
    return out


def portablize_declarations_text(text: str, project_root) -> tuple:
    """The format 1 -> 2 transform over raw project.yaml text.

    Rewrites the four path declarations to their portable spelling
    and nothing else: indentation, quoting, comments and every other
    line pass through byte for byte, the way the 0 -> 1 stamp does.
    Returns (new_text, notes) where notes names each rewritten key and
    each absolute left in place because no token names it.
    """
    notes: list = []
    section = ""
    lines = text.split("\n")
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped and not line[:1].isspace() and stripped.endswith(":"):
            section = stripped[:-1]
            continue
        match = _DECL_LINE_RE.match(line)
        if not match:
            continue
        key = match.group("key")
        if section == "source" and key not in _MIGRATING_KEYS_SOURCE:
            continue
        if section == "pipeline" and key not in _MIGRATING_KEYS_PIPELINE:
            continue
        if section not in ("source", "pipeline"):
            continue
        raw_value = match.group("value").strip()
        if not raw_value:
            continue
        quote = ""
        value = raw_value
        if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
            quote = value[0]
            value = value[1:-1]
        if value in ("", "''", '""', "null", "~", "None"):
            continue
        portable = portablize(value, project_root)
        if portable == value:
            if os.path.isabs(value):
                notes.append(f"{key}: left absolute ({value}) - "
                             f"no token names that volume")
            continue
        new_value = f"{quote}{portable}{quote}" if quote else portable
        lines[i] = (match.group("indent") + key + match.group("sep")
                    + new_value + match.group("comment"))
        notes.append(f"{key}: {value} -> {portable}")
    return "\n".join(lines), notes


# ── Rebinding recorded state on open ─────────────────────────────────

STATE_FILE = "pipeline_data.json"

_BACKUP_BUCKET = "portable"

_MANIFEST_KIND = "portable_path_rebind"


def _norm(path) -> str:
    return os.path.normpath(str(path))


def _walk_strings(node, visit):
    """Every string inside a JSON document, with its container and key."""
    if isinstance(node, dict):
        for key, item in node.items():
            if isinstance(item, str):
                visit(node, key, item)
            else:
                _walk_strings(item, visit)
    elif isinstance(node, list):
        for index, item in enumerate(node):
            if isinstance(item, str):
                visit(node, index, item)
            else:
                _walk_strings(item, visit)


def _collect_footage_paths(state: dict) -> list:
    """Absolute media paths the state records, with where each lives."""
    found: list = []

    def visit(container, key, item):
        if isinstance(item, str) and os.path.isabs(item):
            found.append((container, key, item))

    fingerprints = state.get("source_fingerprints") or {}
    if isinstance(fingerprints, dict):
        for clip_id, record in fingerprints.items():
            if isinstance(record, dict):
                path = record.get("path")
                if isinstance(path, str) and os.path.isabs(path):
                    found.append((record, "path", path))
    outputs = state.get("capability_outputs") or {}
    if isinstance(outputs, dict):
        for holder in outputs.values():
            payload = holder
            if isinstance(holder, dict) and len(holder) == 1:
                sole = next(iter(holder.values()))
                if isinstance(sole, dict):
                    payload = sole
            if not isinstance(payload, dict):
                continue
            for catalog_key in ("clip_catalog", "raw_footage_files",
                                "raw_audio_files"):
                entries = payload.get(catalog_key)
                if not isinstance(entries, list):
                    continue
                for entry in entries:
                    if not isinstance(entry, dict):
                        continue
                    for field in ("path", "source_file"):
                        path = entry.get(field)
                        if isinstance(path, str) and os.path.isabs(path):
                            found.append((entry, field, path))
    return found


def _index_by_basename(root: str) -> dict:
    """{basename: [absolute paths]} for every file under root."""
    index: dict = {}
    if not root or not os.path.isdir(root):
        return index
    for current, _dirs, names in os.walk(root):
        for name in names:
            index.setdefault(name, []).append(os.path.join(current, name))
    return index


def _digest_matches(path: str, recorded: dict) -> bool:
    """Whether this file is the bytes the record describes.

    Size first (cheap, decisive), then the content digest of both file
    ends (`footage_identity.fingerprint`).  A record with neither size
    nor digest cannot prove identity, so it never matches: relinking
    to unverified bytes would be the guess this module refuses.
    """
    if not isinstance(recorded, dict) or not recorded:
        return False
    try:
        size = os.path.getsize(path)
    except OSError:
        return False
    expected = recorded.get("size_bytes")
    if expected is None:
        expected = recorded.get("file_size_bytes")
    if expected is not None and size != expected:
        return False
    want = recorded.get("content_digest")
    if not want:
        return expected is not None
    try:
        from library.tools import footage_identity
        return (footage_identity.fingerprint(path).get("content_digest")
                == want)
    except Exception:
        return False


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def ensure_portable_paths(project_folder) -> dict | None:
    """Rebind machine-local paths after a move; None when nothing moved.

    Reads `pipeline_data.json`, rewrites the stale absolutes and writes
    the file back atomically beside a backup and a manifest - or writes
    nothing at all when every recorded path already resolves.  The
    report names each rebound path, each path still missing, and the
    backup that undoes the rewrite.
    """
    from library.tools.project_layout import Area, ProjectLayout

    layout = ProjectLayout(project_folder)
    root = layout.root
    state_path = layout.pipeline_data_path
    if not state_path.is_file():
        return None
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(state, dict):
        return None

    rebound: list = []
    unrebound: list = []
    notes: list = []

    old_project = state.get("project_folder") or ""
    if old_project and _norm(old_project) != _norm(root):
        state["project_folder"] = str(root)
        notes.append(f"project_folder: {old_project} -> {root}")
        prefix = _norm(old_project) + os.sep
        swapped = []

        def swap(container, key, item):
            if item == _norm(old_project):
                container[key] = str(root)
                swapped.append(item)
            elif item.startswith(prefix):
                container[key] = str(root) + item[len(_norm(old_project)):]
                swapped.append(item)

        _walk_strings(state, swap)
        if swapped:
            rebound.append(f"{len(swapped)} in-project path(s) "
                           f"{old_project} -> {root}")
        old_project = str(root)

    declared = read_declarations(root)
    raw_footage = (declared.get("footage_root") or "").strip()
    if raw_footage:
        footage_root = str(expand(raw_footage, root))
        if not os.path.isabs(footage_root):
            footage_root = os.path.join(str(root), footage_root)
    else:
        footage_root = os.path.join(str(root), "raw")

    for key in ("sfx_library", "music_library"):
        recorded = state.get(key) or ""
        live = (declared.get(key) or "").strip()
        if live:
            live_expanded = str(expand(live, root))
        else:
            live_expanded = os.environ.get(
                f"PIPELINE_{key.upper()}", "")
        if (isinstance(recorded, str) and recorded
                and not os.path.exists(recorded)
                and live_expanded and os.path.exists(live_expanded)
                and _norm(recorded) != _norm(live_expanded)):
            state[key] = live_expanded
            rebound.append(f"{key}: {recorded} -> {live_expanded}")

    footage_entries = _collect_footage_paths(state)
    missing = [(c, k, p) for (c, k, p) in footage_entries
               if not os.path.exists(p)]
    if missing:
        index = _index_by_basename(footage_root)
        fingerprints = state.get("source_fingerprints") or {}
        records_by_path: dict = {}
        if isinstance(fingerprints, dict):
            for record in fingerprints.values():
                if isinstance(record, dict) and record.get("path"):
                    records_by_path[_norm(record["path"])] = record
        for container, field, old in missing:
            if os.path.exists(old):
                continue
            base = os.path.basename(old)
            candidates = index.get(base, [])
            record = records_by_path.get(_norm(old))
            if record is None and isinstance(container, dict):
                record = {k: v for k, v in container.items()
                          if k in ("size_bytes", "file_size_bytes",
                                   "content_digest")}
            picked = ""
            if len(candidates) == 1:
                only = candidates[0]
                if _norm(only) == _norm(old):
                    continue
                if _digest_matches(only, record):
                    picked = only
            elif len(candidates) > 1:
                for candidate in sorted(candidates):
                    if _digest_matches(candidate, record):
                        picked = candidate
                        break
            if picked:
                container[field] = picked
                rebound.append(f"{base}: {old} -> {picked}")
            else:
                unrebound.append(old)

    leftovers: list = []

    def home_swap(container, key, item):
        if not isinstance(item, str) or not os.path.isabs(item):
            return
        if os.path.exists(item):
            return
        match = _USER_HOME_RE.match(item)
        if not match:
            if item not in unrebound and item not in leftovers:
                leftovers.append(item)
            return
        candidate = os.path.join(_home(), item[match.end():])
        if os.path.exists(candidate):
            container[key] = candidate
            rebound.append(f"{item} -> {candidate}")
            for bucket in (unrebound, leftovers):
                while item in bucket:
                    bucket.remove(item)
        elif item not in unrebound and item not in leftovers:
            leftovers.append(item)

    _walk_strings(state, home_swap)
    unrebound.extend(path for path in leftovers if path not in unrebound)

    if not rebound and not notes:
        # Nothing moved and nothing refreshed: stay read-only.  A file
        # that went missing without a move is the identity check's to
        # report at run time, not this gate's on every open.
        return None

    stamp_base = time.strftime("%Y%m%dT%H%M%S")
    stamp = stamp_base
    suffix = 2
    while ((layout.read_path(Area.BACKUPS, _BACKUP_BUCKET, stamp,
                             STATE_FILE).exists())
           or (layout.read_path(Area.MIGRATIONS,
                                f"portable_{stamp}.json").exists())):
        stamp = f"{stamp_base}-{suffix}"
        suffix += 1
    backup_path = layout.backup_file(
        state_path, _BACKUP_BUCKET, stamp, STATE_FILE)
    manifest = {
        "manifest_kind": _MANIFEST_KIND,
        "manifest_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "project": str(root),
        "layout_owner": "library/tools/project_layout.py",
        "backup_path": str(backup_path),
        "backup_sha256": _sha256_file(backup_path),
        "rebound": rebound,
        "notes": notes,
        "unrebound": sorted(set(unrebound)),
    }
    manifest_path = layout.write_path(
        Area.MIGRATIONS, f"portable_{stamp}.json")
    manifest["manifest_path"] = str(manifest_path)
    from library.tools.stable_json import dumps_stable
    manifest_path.write_text(dumps_stable(manifest), encoding="utf-8")

    tmp = None
    try:
        with tempfile.NamedTemporaryFile(
                dir=str(state_path.parent),
                prefix=f".{state_path.name}.", suffix=".tmp",
                delete=False, mode="w",
                encoding="utf-8") as handle:
            tmp = Path(handle.name)
            handle.write(dumps_stable(state))
        tmp.replace(state_path)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()
    manifest_path.write_text(dumps_stable(manifest), encoding="utf-8")

    _report_rebound(root, rebound, notes, unrebound, backup_path)
    manifest["applied"] = True
    return manifest


def _report_rebound(root, rebound, notes, unrebound, backup_path) -> None:
    import sys
    lines = [f"Portable paths: rebound {len(rebound)} path(s) "
             f"for {root} (backup {backup_path})."]
    for line in notes:
        lines.append(f"  {line}")
    for line in rebound[:10]:
        lines.append(f"  {line}")
    if len(rebound) > 10:
        lines.append(f"  ... and {len(rebound) - 10} more")
    for line in sorted(set(unrebound))[:10]:
        lines.append(f"  still missing: {line}")
    if len(set(unrebound)) > 10:
        lines.append(f"  ... and {len(set(unrebound)) - 10} more missing")
    print("\n".join(lines), file=sys.stderr)
