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
        if isinstance(duration, (int, float)):
            # Truncate to 2 decimal places instead of rounding. Rounding up (e.g. 0.459 -> 0.46)
            # causes the model to ask for 0.46s, which the strict sfx_duration contract refuses
            # because it exceeds the measured 0.459s.
            import math
            duration_s = math.floor(float(duration) * 100) / 100.0
        else:
            duration_s = "unmeasured"

        rows.append({
            "sfx_id": entry["sfx_id"],
            "category": entry.get("category") or "",
            "duration_s": duration_s,
            "envelope": entry.get("envelope") or "unmeasured",
            "description": entry.get("description") or "",
            "source_object": entry.get("source_object") or "",
            "evokes": ", ".join(entry.get("evokes") or []),
            "emotional_temperature": entry.get("emotional_temperature") or "",
            "works_when": entry.get("works_when") or "",
            "avoid_when": entry.get("avoid_when") or "",
        })
    return rows


# The document's filename inside the step's own directory.  One name,
# because the step writes it and the prompt points at it.
CATALOG_DOCUMENT_NAME = "sfx_catalogue.md"

# How each catalogue column is labelled in the document.  The four
# measurable ones open the section as its identity line - which is also
# what `brief_reference` lifts as the section's lede, so the map names
# every sound with its category, length, envelope and temperature
# without the body being read.
_IDENTITY_COLUMNS = ("category", "duration_s", "envelope",
                     "emotional_temperature")
"""The columns `_identity_line` carries, and the whole of what the map
names without the body being read."""
_BODY_LABELS = (
    ("description", "description"),
    ("source_object", "source object"),
    ("evokes", "evokes"),
    ("works_when", "works when"),
    ("avoid_when", "avoid when"),
)


def catalog_document(catalog: list) -> str:
    """The whole catalogue as ONE markdown document, one section per sound.

    This is what step 4.04 writes to disk and hands the model a
    REFERENCE to, through the mechanism ``library/tools/brief_reference.py``
    already built for the captain's channel brief (#295, AGENTS.md 10.1).
    The catalogue was 44,575 B and 44.1% of that step's whole context -
    the biggest single item in the pipeline after the brief stopped
    being copied - and #299 named it.  Measured on the captain's library
    2026-08-29: 54,080 B of document on disk, 13,351 B of map in the
    context, a 31,224 B saving and 30.9% off the whole step.

    The shape is chosen to fit the mechanism rather than to fork it.
    ``build_reference`` splits a document on its ``##`` headings and
    prints, per section, the heading, its size, its LINE RANGE and its
    first line of prose.  So:

      * one ``##`` section per sound, titled with the ``sfx_id`` - which
        is the exact string the answer has to name, so the map alone
        names all 78 of them;
      * the section opens with its MEASURABLE facts on one line, so that
        line becomes the lede and the map carries category, length,
        envelope and emotional temperature inline for every sound;
      * the library's prose - description, source object, evokes,
        works_when, avoid_when, which is 38,975 of the table's 44,397 B -
        is the body, behind a line range one ``sed`` reaches.

    Nothing is shortlisted, filtered, ranked or truncated: every sound
    the library can play has a section, and the whole document is at the
    path.  A reference that narrowed the menu would be the shortlist
    problem wearing a third hat (AGENTS.md 10.5).

    The prose is markdown, not TOON, and that is the second reason this
    replaces ``catalog_toon``: a TOON cell has to quote a description
    full of commas and apostrophes, and here every field is its own
    line, so nothing is escaped at all.
    """
    rows = catalog_rows(catalog)
    out = [
        "# The SFX library",
        "",
        f"{len(rows)} playable sound{'' if len(rows) == 1 else 's'}. Every "
        f"one of them is on disk, and the heading of each section below is "
        f"exactly the `sfx_id` a plan must name - there is no other "
        f"spelling and no near match.",
        "",
        "Nothing here is shortlisted or ranked. One section per sound, in "
        "the library's own order. Each opens with what was MEASURED about "
        "the file - its folder category, its length in seconds, the "
        "envelope shape the profiler measured, and the emotional "
        "temperature the library's semantic index recorded - and then "
        "carries what the library says the sound means and when it says "
        "to use it or not.",
        "",
        "A length is the plan's to choose inside the number below: a sound "
        "may be asked to play for less than it measures, never more.",
        "",
    ]
    for row in rows:
        out.append(f"## {row['sfx_id']}")
        out.append(_identity_line(row))
        missing = []
        for column, label in _BODY_LABELS:
            value = " ".join(str(row[column]).split())
            if value:
                out.append(f"- {label}: {value}")
            else:
                missing.append(label)
        if missing:
            # An absence is STATED. A field the library records nothing
            # for is not the same as a field nobody looked at, and a
            # blank line would read as either.
            out.append(f"- the library records no {', '.join(missing)}")
        out.append("")
    return "\n".join(out).rstrip() + "\n"


def _identity_line(row: dict) -> str:
    """The measured facts, on the one line that becomes the map's lede.

    An unmeasured value SAYS SO rather than being left out: a sound the
    profiler never finished is a different thing from one the map had no
    room for.
    """
    duration = row["duration_s"]
    parts = [
        f"category {row['category'] or 'uncategorised'}",
        ("length unmeasured" if duration == "unmeasured"
         else f"plays for {duration} s"),
        f"envelope {row['envelope'] or 'unmeasured'}",
        f"temperature {row['emotional_temperature'] or 'not recorded'}",
    ]
    return " | ".join(parts)
