"""The SFX library, as a CATALOGUE the model chooses from.

The library is three files deep and every one of them says something the
planner needs:

  * ``sfx_index.json``        - path, folder category, and the technical
                                measurements (duration, envelope shape,
                                the file's own transient offset).
  * ``library_semantic.json`` - what the sound MEANS: ``description``,
                                ``source_object``, ``evokes``,
                                ``emotional_temperature``, ``works_when``
                                and ``avoid_when``.
  * ``profiles/<file>.json``  - a per-file technical profile carrying a
                                longer, spectrogram-level description.

``load_sfx_catalog`` merges the three into ONE row per playable file and
that row is what reaches the prompt.  The model answers with an
``sfx_id`` - a real filename out of that catalogue - and
``resolve_sfx_id`` turns it back into the entry, or refuses.

**A sound is chosen by the model, from what the library records about
it.**  It used to be chosen here, by ``match_sfx_file``, which counted
substring hits from eight hand-written keyword lists over each entry's
description, folder and filename and took the highest count.  What that
produced on the run of record (001, 2026-08-26):

  * the plan asked for a SUBTLE swish under a defocus blur and got
    ``whoosh_impact.mp3``, whose own library entry reads *"Avoid using
    this for subtle movements or slow, atmospheric transitions where the
    sharp transient would be jarring."*
  * the plan asked for a swell to mark a chosen silence in a
    self-deprecating piece and got ``Alien_racecar.wav`` - evokes
    *speed, sci-fi, acceleration, mechanical power* - whose entry reads
    *"Avoid using this in organic, grounded scenes."*
  * ``bass_impact`` resolved to ``riser_2.mp3``, a 5.3-second riser.
  * ``riser`` and ``swell`` resolved to the SAME file.

None of that is a bad creative call.  The word list never saw the fields
that would have refused those files, because 48 of the 78 entries carry
an EMPTY ``description`` in ``sfx_index.json`` - the only text the
matcher read - while the semantic index describes 74 of 78.

``TYPE_KEYWORDS``, ``match_sfx_file`` and ``available_sfx_types`` are
deleted, not unwired.  Nothing maps a type name to a file any more, and
there is no second word list anywhere: the eight types are gone from the
schema, from the placement code and from the manifest.

What this module still guarantees, and the reason it exists: **the model
cannot choose something that does not exist.**  The catalogue only ever
contains entries whose file is on disk, and ``resolve_sfx_id`` refuses an
id that is not in it - at PLAN time, in step 4.04's post-bridge, not
three steps later inside ``compile_manifest``.
"""

import glob
import json
import os
import subprocess

# Index files that are metadata about the library, not entries in it.
_NON_ENTRY_FILES = (
    "sfx_index.json", "library_analysis.json", "library_semantic.json",
)

# The catalogue's columns, in the order they are read.  Identity and
# playability first (what it is, where it sits, how long it runs, what
# shape it has), then meaning (what it sounds like, what it evokes, when
# the library says to use it and when not to).
#
# All ten ship.  Measured on the captain's library, 2026-08-28: the whole
# 78-entry catalogue is 44,411 B of TOON.  Dropping `works_when` and
# `avoid_when` would save 16.9 KB and would remove exactly the two fields
# that refuse both of 001's wrong choices, so they stay.
CATALOG_COLUMNS = (
    "sfx_id",
    "category",
    "duration_s",
    "envelope",
    "description",
    "source_object",
    "evokes",
    "emotional_temperature",
    "works_when",
    "avoid_when",
)


def resolve_library_path(library_path: str = "") -> str:
    """The library directory: the argument, the env var, or paths.py."""
    if library_path:
        return library_path
    library_path = os.environ.get("PIPELINE_SFX_LIBRARY", "")
    if library_path:
        return library_path
    try:
        from library.tools.paths import sfx_library_path
        return sfx_library_path()
    except ImportError:
        return ""


def load_sfx_index(library_path: str = "") -> list:
    """Load the SFX library index, or the per-file profiles under it."""
    library_path = resolve_library_path(library_path)
    if not library_path:
        return []

    index_path = os.path.join(library_path, "sfx_index.json")
    if os.path.exists(index_path):
        try:
            with open(index_path, encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, IOError):
            return []

    return _load_profiles(library_path)


def _read_json(path: str):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (json.JSONDecodeError, IOError, OSError):
        return None


def _load_profiles(library_path: str) -> list:
    """Every per-file profile under ``profiles/``, index files skipped."""
    entries = []
    profiles = os.path.join(library_path, "profiles")
    if not os.path.isdir(profiles):
        return entries
    for path in sorted(glob.glob(os.path.join(profiles, "*.json"))):
        if os.path.basename(path) in _NON_ENTRY_FILES:
            continue
        loaded = _read_json(path)
        # `profiles/` has been seen carrying a copy of the index itself,
        # under a name this list does not cover.  A profile is one entry,
        # so anything that is not a dict is not a profile.
        if isinstance(loaded, dict):
            entries.append(loaded)
    return entries


def _semantic_by_file(library_path: str) -> dict:
    loaded = _read_json(os.path.join(library_path, "library_semantic.json"))
    if not isinstance(loaded, list):
        return {}
    return {e["file"]: e for e in loaded
            if isinstance(e, dict) and e.get("file")}


def _profiles_by_file(library_path: str) -> dict:
    return {e["file"]: e for e in _load_profiles(library_path)
            if isinstance(e, dict) and e.get("file")}


def _probe_duration(path: str):
    """The file's real duration, measured with ffprobe.

    Used only where the index recorded none.  Two of the captain's 78
    entries carry a ``technical.basic`` holding nothing but a sample
    rate, and both are real audio files - refusing them because the
    profiler never finished would withhold a sound the library has.
    Returns None when ffprobe is absent or cannot read the file; the
    entry then reads ``unmeasured`` and is refused BY NAME if a plan
    picks it.
    """
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, encoding="utf-8", timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    try:
        return float(proc.stdout.strip())
    except ValueError:
        return None


def _first_text(*values) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def load_sfx_catalog(library_path: str = "") -> list:
    """One row per PLAYABLE sound, merged from all three index files.

    An entry whose ``path`` is missing from disk is not in the catalogue:
    the model must not be able to choose a sound that cannot be placed.
    """
    library_path = resolve_library_path(library_path)
    if not library_path:
        return []

    semantic = _semantic_by_file(library_path)
    profiles = _profiles_by_file(library_path)

    catalog = []
    for entry in load_sfx_index(library_path):
        if not isinstance(entry, dict):
            continue
        name = entry.get("file") or ""
        path = entry.get("path") or ""
        if not name or not path or not os.path.exists(path):
            continue

        sem = semantic.get(name, {})
        prof = profiles.get(name, {})
        technical = entry.get("technical") or prof.get("technical") or {}
        basic = technical.get("basic") or {}
        energy = technical.get("energy_profile") or {}

        duration = basic.get("duration")
        if not isinstance(duration, (int, float)):
            duration = _probe_duration(path)

        transient = entry.get("transient_offset_sec")
        if transient == "unknown":
            transient = None

        catalog.append({
            "sfx_id": name,
            "path": path,
            "category": entry.get("folder_category")
            or prof.get("folder_category") or "",
            "duration_seconds": duration,
            "envelope": energy.get("envelope_shape") or "",
            "transient_offset_sec": transient,
            "description": _first_text(sem.get("description"),
                                       entry.get("description"),
                                       prof.get("description")),
            "source_object": _first_text(sem.get("source_object")),
            "evokes": list(sem.get("evokes") or []),
            "emotional_temperature": _first_text(
                sem.get("emotional_temperature")),
            "works_when": _first_text(sem.get("works_when")),
            "avoid_when": _first_text(sem.get("avoid_when")),
        })
    return catalog


def resolve_sfx_id(sfx_id, catalog: list):
    """The catalogue entry an ``sfx_id`` names, or None.

    Exact match on the filename the catalogue published.  No fuzzy
    matching and no nearest neighbour: an id that is not in the catalogue
    is a plan naming a sound the library does not have, and the caller
    refuses it rather than substituting one.
    """
    if not isinstance(sfx_id, str) or not sfx_id:
        return None
    for entry in catalog:
        if entry["sfx_id"] == sfx_id:
            return entry
    return None


def catalog_rows(catalog: list) -> list:
    """The catalogue as prompt rows: ``CATALOG_COLUMNS``, in that order.

    ``path`` and ``transient_offset_sec`` are deliberately absent - the
    model names an id, and where the file sits on this machine and where
    its own transient falls are placement mechanics, not grounds for
    choosing a sound.
    """
    rows = []
    for entry in catalog:
        duration = entry.get("duration_seconds")
        rows.append({
            "sfx_id": entry["sfx_id"],
            "category": entry.get("category") or "",
            "duration_s": (round(float(duration), 2)
                           if isinstance(duration, (int, float))
                           else "unmeasured"),
            "envelope": entry.get("envelope") or "unmeasured",
            "description": entry.get("description") or "",
            "source_object": entry.get("source_object") or "",
            "evokes": ", ".join(entry.get("evokes") or []),
            "emotional_temperature": entry.get("emotional_temperature") or "",
            "works_when": entry.get("works_when") or "",
            "avoid_when": entry.get("avoid_when") or "",
        })
    return rows


def catalog_toon(catalog: list) -> str:
    """The catalogue as ONE TOON table, ready for a prompt.

    Rendered through ``library.tools.toon_serializer``, not by a
    hand-rolled joiner: these cells carry the library's own English
    prose, full of commas and apostrophes, and the serializer is the one
    place that knows a cell is quoted with a BACKTICK (AGENTS.md 10.1).
    A tab-joined row would put a description's comma into the next
    column.

    Columns come out in ``CATALOG_COLUMNS`` order because ``catalog_rows``
    builds each row in that order and the serializer never sorts.
    """
    from library.tools.toon_serializer import json_to_toon
    rows = catalog_rows(catalog)
    if not rows:
        return "[0]{" + ",".join(CATALOG_COLUMNS) + "}\n"
    return json_to_toon(rows)
