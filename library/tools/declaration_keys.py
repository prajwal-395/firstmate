"""Two agents, one declaration file: what makes a conflict VISIBLE.

The other shared resource
-------------------------
Resolve is one instance, but it is not the only thing two agents write
at once. The keyed JSON stores - `external/captain_edits.json`, the
five `external/` DECLARATIONS, and the reel-keyed records under
`pipeline_output/review/` - are ordinary files, which is what makes a
real answer possible here where Resolve only allows a convention.

The measured defect
-------------------
`captain_edits.record_edit` (captain_edits.py) reads the whole store,
drops the entry whose `_edit_identity` matches, appends, and writes the
whole file back. No lock, no atomic rename, no revision. Two agents
recording two DIFFERENT edits at the same moment lose one of them,
silently and permanently, and the loser looks exactly like a successful
run.

It is not hypothetical. On 2026-09-12 the live store's own `source`
field read:

    "firstmate vep-restore-reel13-and-reel28, 2026-09-11;
     firstmate vep-sync-the-captains-manual-edits, 2026-09-12"

- two lanes, concatenated by hand into one field, because the file has
no other way to say that two writers touched it.

What contention is worth building for
--------------------------------------
Measured on `lucie/geo-podcast`, 2026-09-12: four declaration files,
26 `overlay_intent` targets, 11 captain edits, 2 caption pins, 1 reel
ending, and mtimes a day apart. Writes are RARE. So the answer is not
an elaborate one - no per-reel file split, no journal, no server. It is
the cheapest thing that cannot lose an update:

1. **A key.** `stores()` below says what makes two entries the SAME
   entry, per file. `reel_ending` is keyed by its reel; `overlay_intent`
   by its target id; `captain_edits` by the owner's own
   `_edit_identity`. The key is the unit of contention, and it is
   already the unit each owner reasons in - this module names it, it
   does not invent it.

2. **A merge, not a write.** A writer hands over the entries it
   touched. Everything it did NOT touch is taken from disk at write
   time, so an agent editing Reel 03 cannot clobber an agent's Reel 07
   even if it read the file before that entry existed. Different keys
   never contend. That is the horizontal scaling the captain asked for.

3. **A conflict that surfaces.** Where both writers touched the SAME
   key and the on-disk value is no longer the one this writer read,
   `DeclarationConflict` is raised naming the key and both values.
   Last-write-wins is refused, because the losing edit was somebody's
   decision and a silently dropped decision is indistinguishable from
   one that was never made.

4. **An atomic write.** Temp plus `os.replace` in the same directory,
   so a reader never sees half a file - the rest of this repo's keyed
   stores already do this (`stable_json.write_stable`), and the two
   `external/` writers did not.

A short exclusive `flock` is held across the read-merge-write so two
cooperating writers do not interleave. It does NOT depend on
cooperation for correctness: the base-revision check catches a hand
edit made in a text editor, which takes no lock and never will.

`tests/unit/context/test_concurrency.py`.
"""

from __future__ import annotations

import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Dict, Optional, Tuple

# ── The key scheme ──────────────────────────────────────────────────


class DeclarationConflict(RuntimeError):
    """Two writers changed the same entry key. Neither is dropped."""


class DeclarationKeyError(RuntimeError):
    """The file is not the keyed store this module can merge."""


def _normalised(text) -> str:
    return " ".join(str(text or "").lower().split())


def _reel_key(entry: dict) -> str:
    return str(entry.get("reel", ""))


def _mix_pin_key(entry: dict) -> str:
    return f"{_normalised(entry.get('anchor_phrase'))}|{entry.get('target')}"


def _placed_asset_key(entry: dict) -> str:
    return f"{entry.get('slot')}|{entry.get('asset')}"


def _caption_pin_key(entry: dict) -> str:
    scope = entry.get("scope") or {}
    return json.dumps(scope, sort_keys=True, ensure_ascii=False)


def _captain_edit_key(entry: dict) -> str:
    """The owner's OWN identity, imported rather than restated.

    `captain_edits._edit_identity` already decides what makes two
    decisions the same - a re-ruling SUPERSEDES, a different scope is a
    different edit. Copying that rule here would be a second answer to
    one question, and the two would drift.
    """
    from library.tools.captain_edits import _edit_identity
    return json.dumps(list(_edit_identity(entry)), ensure_ascii=False)


class KeyedStore:
    """One file this module can merge, and how to take it apart."""

    def __init__(self, stem: str, container: str, envelope: dict,
                 key_of: Optional[Callable[[dict], str]],
                 relative_dir: str = "external"):
        self.stem = stem
        self.container = container
        self.envelope = envelope
        self.key_of = key_of
        self.relative_dir = relative_dir

    @property
    def is_mapping(self) -> bool:
        """True where the container is already keyed on disk."""
        return self.key_of is None

    def filename(self) -> str:
        return f"{self.stem}.json"


#: Every keyed store, and what makes two of its entries the same one.
#:
#: `container` is the field the entries live under. A `key_of` of None
#: means the container is ALREADY a mapping and its own keys are the
#: entry keys - `overlay_intent.targets` is the one such store, and it
#: needs no key function because the captain writes the key.
#:
#: `envelope` is what the file carries besides the entries, so a store
#: created from nothing is a file its OWNER's reader accepts. The
#: version numbers are read from the owner rather than restated here,
#: at import, so a schema bump cannot leave this module writing the old
#: one (AGENTS.md 10.1, key-name mismatches).
def _ledger_key(entry: dict) -> str:
    """The ledger's OWN identity, imported rather than restated -
    the same rule `declaration_keys` merges by, so a re-stating
    supersedes in place rather than duplicating."""
    from library.tools.edit_ledger import _row_identity
    return json.dumps(list(_row_identity(entry)), ensure_ascii=False)


def _stores() -> Dict[str, KeyedStore]:
    from library.tools import (
        caption_timing,
        mix_intent,
        overlay_intent,
        placed_assets,
        reel_ending,
    )
    from library.tools.captain_edits import CAPTAIN_EDITS_KEY
    from library.tools.edit_ledger import EDIT_LEDGER_VERSION

    return {
        "captain_edits": KeyedStore(
            "captain_edits", "value",
            {"key": CAPTAIN_EDITS_KEY, "source": ""}, _captain_edit_key),
        "reel_ending": KeyedStore(
            "reel_ending", "endings",
            {"version": reel_ending.ENDING_VERSION}, _reel_key),
        "caption_timing": KeyedStore(
            "caption_timing", "pins",
            {"version": caption_timing.CAPTION_TIMING_VERSION},
            _caption_pin_key),
        "mix_intent": KeyedStore(
            "mix_intent", "pins",
            {"version": mix_intent.INTENT_VERSION}, _mix_pin_key),
        "placed_assets": KeyedStore(
            "placed_assets", "assets",
            {"version": placed_assets.ASSETS_VERSION}, _placed_asset_key),
        "overlay_intent": KeyedStore(
            "overlay_intent", "targets",
            {"version": overlay_intent.INTENT_VERSION}, None),
        "edit_ledger": KeyedStore(
            "edit_ledger", "rows",
            {"version": EDIT_LEDGER_VERSION}, _ledger_key),
    }


_STORES_CACHE: Optional[Dict[str, KeyedStore]] = None


def stores() -> Dict[str, KeyedStore]:
    global _STORES_CACHE
    if _STORES_CACHE is None:
        _STORES_CACHE = _stores()
    return _STORES_CACHE


def store_for(stem: str) -> KeyedStore:
    found = stores().get(stem)
    if found is None:
        raise DeclarationKeyError(
            f"{stem!r} is not a keyed store. `declaration_keys.stores()` "
            f"enumerates them: {', '.join(sorted(stores()))}. A file with "
            f"no key scheme cannot be merged, only overwritten, and "
            f"overwriting is the defect this module exists to stop.")
    return found


def entry_key(stem: str, entry: dict) -> str:
    """What makes two entries of this store the SAME entry."""
    store = store_for(stem)
    if store.is_mapping:
        raise DeclarationKeyError(
            f"{stem} is a mapping on disk - its entry keys are its own "
            f"keys, so there is nothing to derive.")
    return store.key_of(entry)


def store_path(project_folder, stem: str) -> Path:
    """Where the store lives, asked of the LAYOUT rather than guessed.

    `project_layout` owns the project-side layout (AGENTS.md 8), and
    `external_inputs.external_dir` is its reading of the external area -
    the same directory the owner's reader opens. Composing the path here
    would be a second answer to where a file lives, and the two would
    drift the first time the layout moved.
    """
    store = store_for(stem)
    if store.relative_dir == "external":
        from library.tools.external_inputs import external_dir
        try:
            return Path(external_dir(project_folder)) / store.filename()
        except (KeyError, ValueError):
            pass
    return Path(project_folder) / store.relative_dir / store.filename()


# ── Reading a revision, and merging onto it ─────────────────────────

def read_entries(project_folder, stem: str) -> Tuple[dict, dict]:
    """`(entries_by_key, envelope)` as they are on disk right now.

    An absent file reads as no entries and the store's own envelope, so
    the first writer into a project creates a file the owner's reader
    accepts rather than a bare list.

    Deliberately does NOT run the owner's validator: a store that is
    already malformed must still be mergeable, or one bad entry locks
    every agent out of a file they each own a different part of. The
    owner's reader is what refuses it at run time, in the owner's own
    words, unchanged.
    """
    store = store_for(stem)
    path = store_path(project_folder, stem)
    if not path.is_file():
        return {}, dict(store.envelope)
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as unreadable:
        raise DeclarationKeyError(
            f"{path} cannot be read ({unreadable}). A declaration the "
            f"engine cannot parse is refused, never merged past.") \
            from unreadable
    if not isinstance(document, dict):
        raise DeclarationKeyError(f"{path} is not an object.")
    envelope = {k: v for k, v in document.items() if k != store.container}
    raw = document.get(store.container)
    if store.is_mapping:
        if raw is None:
            return {}, envelope
        if not isinstance(raw, dict):
            raise DeclarationKeyError(
                f"{path}: {store.container!r} is not a mapping.")
        return dict(raw), envelope
    if raw is None:
        return {}, envelope
    if not isinstance(raw, list):
        raise DeclarationKeyError(
            f"{path}: {store.container!r} is not a list.")
    keyed: dict = {}
    for entry in raw:
        if not isinstance(entry, dict):
            raise DeclarationKeyError(
                f"{path}: {store.container!r} holds a non-object entry.")
        keyed[store.key_of(entry)] = entry
    return keyed, envelope


def merge(stem: str, base: dict, mine: dict, disk: dict) -> dict:
    """Three-way merge by entry key. Raises on a genuine conflict.

    `base` is what this writer READ, `mine` what it wants, `disk` what
    is there now. An entry only this writer changed lands; an entry
    only the other writer changed survives; an entry BOTH changed, to
    different values, raises.

    A deletion is expressed by a key absent from `mine` that is present
    in `base` - the same three-way rule, so one agent retiring Reel 03's
    ending does not resurrect it when another writes Reel 07.
    """
    keys = set(base) | set(mine) | set(disk)
    merged: dict = {}
    conflicts = []
    for key in sorted(keys):
        in_base, in_mine, in_disk = base.get(key), mine.get(key), disk.get(key)
        mine_changed = in_mine != in_base
        disk_changed = in_disk != in_base
        if mine_changed and disk_changed and in_mine != in_disk:
            conflicts.append((key, in_disk, in_mine))
            continue
        chosen = in_mine if mine_changed else in_disk
        if chosen is not None:
            merged[key] = chosen
    if conflicts:
        raise DeclarationConflict(
            f"{stem}: {len(conflicts)} entr"
            f"{'y' if len(conflicts) == 1 else 'ies'} changed by another "
            f"writer since this one read the file, and both changes are "
            f"real decisions. Nothing was written. Re-read and re-apply "
            f"yours on top:\n" + "\n".join(
                f"  key {key}\n"
                f"    on disk now: {json.dumps(theirs, ensure_ascii=False)}\n"
                f"    yours:       {json.dumps(ours, ensure_ascii=False)}"
                for key, theirs, ours in conflicts))
    return merged


# ── The write ───────────────────────────────────────────────────────

def _lock_path(project_folder, stem: str) -> Path:
    path = store_path(project_folder, stem)
    return path.with_name(path.name + ".lock")


@contextmanager
def _file_lock(project_folder, stem: str):
    """Exclusive across cooperating writers; absence is not correctness.

    Held only across the read-merge-write, which is milliseconds. A
    writer that does NOT take it - the captain in a text editor - is
    caught by the base-revision check in `merge` instead, which is why
    this is the cheap half and that is the load-bearing one.
    """
    path = _lock_path(project_folder, stem)
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = open(path, "a+", encoding="utf-8")
    try:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        except ImportError:  # pragma: no cover - not POSIX
            pass
        yield
    finally:
        try:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        except ImportError:  # pragma: no cover - not POSIX
            pass
        handle.close()


def _write_atomic(path: Path, document: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    body = json.dumps(document, indent=2, ensure_ascii=False) + "\n"
    handle, tmp = tempfile.mkstemp(dir=str(path.parent),
                                   prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as out:
            out.write(body)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:  # pragma: no cover
            pass
        raise


def write_entries(project_folder, stem: str, entries: dict, base: dict,
                  envelope: Optional[dict] = None) -> dict:
    """Merge `entries` onto whatever is on disk now, and write.

    `base` is what THIS writer read (`read_entries`'s first return).
    Returns the merged mapping that landed. Raises
    `DeclarationConflict` naming every contended key, having written
    nothing - a partial write of a store somebody else is also editing
    is worse than a refusal, because the refusal can be retried and the
    partial cannot be told from a whole one.
    """
    store = store_for(stem)
    path = store_path(project_folder, stem)
    with _file_lock(project_folder, stem):
        disk, disk_envelope = read_entries(project_folder, stem)
        merged = merge(stem, base, entries, disk)
        document = dict(store.envelope)
        document.update(disk_envelope)
        if envelope:
            document.update(envelope)
        if store.is_mapping:
            document[store.container] = {k: merged[k]
                                         for k in sorted(merged)}
        else:
            document[store.container] = [merged[k] for k in sorted(merged)]
        _write_atomic(path, document)
    return merged


@contextmanager
def edit_declaration(project_folder, stem: str,
                     envelope: Optional[dict] = None):
    """Read, mutate, write - the three steps that must not come apart.

        with edit_declaration(project, "reel_ending") as endings:
            endings["Reel 03 - ..."] = {...}

    The mapping yielded is keyed by `entry_key`. Whatever it holds at
    exit is merged onto disk under the file lock, so a caller cannot
    write back a revision it read a minute ago and lose what landed in
    between. Deleting a key deletes the entry; leaving one untouched
    leaves it exactly as another writer left it.
    """
    base, _ = read_entries(project_folder, stem)
    working = {key: json.loads(json.dumps(value))
               for key, value in base.items()}
    yield working
    write_entries(project_folder, stem, working, base, envelope=envelope)
