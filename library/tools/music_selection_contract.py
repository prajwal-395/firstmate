"""The shape of a music selection, and what makes one rejectable.

Captain's ruling of 2026-08-20, verbatim: *"fix schema and let LLM choose
from both library or outside"*.  A hybrid, and neither offered option:
the library must actually be consulted, and choosing a track that is not
held locally stays legitimate.

What went wrong
---------------
Step 2.04's LLM received an **empty output schema**.  ``present_llm_step``
builds the schema from ``interface.outputs`` minus anything the bridge
already supplied, and the bridge supplied the step's only output -
``music_selection`` - so nothing at all was asked for and nothing at all
constrained the answer.  Meanwhile the bridge did not select so much as
sort: it took ``sorted(project_folder/music/*)[0]``.  On project 001 that
is a **3914-second** file called "Inspirational Motivational Music Video",
scoring a piece whose creative direction says in explicit terms that it
"never becomes triumphant ... and it should not be scored or cut as though
it does".  ``PIPELINE_MUSIC_LIBRARY`` was never opened.

Two halves, because that is what failed
---------------------------------------
* **The catalogue** - :func:`catalogue_sources` names every place a local
  track may come from.  The bridge lists them all and picks none.
* **The verdict** - :func:`validate_selection` is the one place a
  selection is judged.  It rejects on things a machine can actually know:
  a source that is not one of the three, a local path that is not on the
  catalogue or not on disk, a missing justification, a duration that
  cannot plausibly score the edit, and a title that names a register the
  model itself recorded as forbidden.

Duration sanity
---------------
The captain called this out by name: "a 65-minute track for a 55-second
edit was never a plausible answer and nothing caught it."  A track must be
long enough to cover the edit - ``compile_manifest`` places music at
``source_in`` 0 and runs it to the full timeline length, so a shorter
track ends in silence - and short enough to be a track rather than a
compilation.  The ceiling is :data:`MAX_TRACK_DURATION_MULTIPLE` times the
target with a :data:`MAX_TRACK_DURATION_FLOOR_SECONDS` floor, so a 60s
edit accepts anything up to ten minutes.  Real music is 2-5 minutes; a
65-minute "MIX" is a playlist, and the ratio is what tells them apart.

Taste is still the captain's
----------------------------
Nothing here decides which track is *good*.  It decides which answers are
**rejectable on recorded reasoning** rather than only on taste, which is
what the ruling asked for.  A selection that survives this module may
still be the wrong call, and that call remains a human one.


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**Music selection is one enumeration, `library/tools/music_selection_contract.py`.**
The bridge catalogues `PIPELINE_MUSIC_LIBRARY` **and** the project's `music/` and picks nothing.
The post-bridge judges source, catalogue membership, duration plausibility and a justification naming the registers the creative direction forbids.
Choosing from OUTSIDE the library is legitimate and stays allowed.
"""

from __future__ import annotations

import os
import re
from typing import Dict, List, Optional, Tuple

# Where a locally-held track may come from.  "external" is the fourth
# source and is deliberately NOT here: it has no directory to scan.
CATALOGUE_SOURCES = ("library", "project")

# Every source a selection may declare.  Unknown values are rejected -
# a silent fallback is how "external" quietly became "whatever was first
# in the folder" the last time.
VALID_SOURCES = ("library", "project", "external")

AUDIO_EXTENSIONS = frozenset(
    {".wav", ".mp3", ".m4a", ".aac", ".flac", ".ogg", ".opus"}
)

# A track may run up to this many times the target duration before it is
# a compilation rather than a track...
MAX_TRACK_DURATION_MULTIPLE = 10
# ...but never less than this, so a 20-second edit can still be scored
# with an ordinary three-minute song.
MAX_TRACK_DURATION_FLOOR_SECONDS = 600.0

# Music is placed at source_in 0 and run to the end of the timeline, so a
# track shorter than the edit ends in silence.  A hair of slack absorbs
# ffprobe rounding.
DURATION_SLACK_SECONDS = 0.5


def max_track_duration_seconds(target_duration: float) -> float:
    """The longest a track may run and still be scoring THIS edit."""
    return max(
        MAX_TRACK_DURATION_FLOOR_SECONDS,
        MAX_TRACK_DURATION_MULTIPLE * float(target_duration or 0.0),
    )


def catalogue_sources(project_folder: Optional[str]) -> Dict[str, Tuple[str, ...]]:
    """``{source_name: directories}`` for every local place tracks live.

    ``library`` is ``PIPELINE_MUSIC_LIBRARY``, the shared asset library
    the selection never used to open.  ``project`` is everything this
    project holds: the captain's own ``music/`` directory, plus the
    downloads area a fetched track lands in.

    Those are two directories under one label deliberately.  ``music/``
    is INPUT - the captain put those files there and the pipeline may not
    write to it - while a track step 2.04 downloaded is pipeline output
    and belongs in the output tree (``library/tools/project_layout.py``).
    Both are equally "held by this project" as far as a choice is
    concerned, and the source labels in :data:`CATALOGUE_SOURCES` are
    part of the contract with the model, so the split is invisible there.
    """
    from library.tools.paths import music_library_path
    from library.tools.project_layout import Area, ProjectLayout

    sources: Dict[str, Tuple[str, ...]] = {"library": (music_library_path(),)}
    if project_folder:
        layout = ProjectLayout(project_folder)
        sources["project"] = (
            str(layout.read_dir(Area.MUSIC)),
            str(layout.read_dir(Area.ACQUIRED_MEDIA)),
        )
    return sources


def acquired_media_dir(project_folder: str) -> str:
    """Where a track the pipeline FETCHED lands.  Created on demand."""
    from library.tools.project_layout import Area, ProjectLayout

    return str(ProjectLayout(project_folder).write_dir(
        Area.ACQUIRED_MEDIA, step="music_selection"))


def _tokens(text: str) -> set:
    return set(re.findall(r"[a-z]+", (text or "").lower()))


def validate_selection(
    selection: dict,
    candidates: List[dict],
    target_duration: float,
) -> List[str]:
    """Everything wrong with a selection, as sentences. Empty means valid.

    ``candidates`` is the bridge's catalogue - what a ``library`` or
    ``project`` selection must be drawn from.  An ``external`` selection
    is judged on its URL and its stated duration instead, because there
    is nothing local to check it against.
    """
    errors: List[str] = []

    if not isinstance(selection, dict):
        return [f"music_selection must be an object, got {type(selection).__name__}"]

    title = (selection.get("title") or "").strip()
    if not title:
        errors.append("music_selection.title is empty - name the track you chose.")

    source = (selection.get("source") or "").strip().lower()
    if source not in VALID_SOURCES:
        errors.append(
            f"music_selection.source is {selection.get('source')!r}; it must be "
            f"one of {list(VALID_SOURCES)}. 'library' is "
            f"PIPELINE_MUSIC_LIBRARY, 'project' is the project's music/ "
            f"folder, 'external' is a track not held locally."
        )

    audio_path = (selection.get("audio_path") or "").strip()
    source_url = (selection.get("source_url") or "").strip()

    if source in CATALOGUE_SOURCES:
        if not audio_path:
            errors.append(
                f"music_selection.source is {source!r} but audio_path is "
                f"empty. A local choice must name the file it chose."
            )
        else:
            known = {c.get("audio_path") for c in candidates}
            if audio_path not in known:
                errors.append(
                    f"music_selection.audio_path {audio_path!r} is not one of "
                    f"the {len(candidates)} catalogued candidates. A "
                    f"{source!r} choice must come from the catalogue; a track "
                    f"held nowhere locally is source 'external'."
                )
            elif not os.path.exists(audio_path):
                errors.append(
                    f"music_selection.audio_path {audio_path!r} does not exist."
                )
    elif source == "external":
        if not source_url:
            errors.append(
                "music_selection.source is 'external' but source_url is "
                "empty. An external choice must say where the track comes "
                "from, or it cannot be fetched or cleared for rights."
            )

    # ── Duration sanity ───────────────────────────────────────────────
    duration = selection.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        errors.append(
            f"music_selection.duration_seconds is {duration!r}; it must be a "
            f"positive number. This is the field that lets a 65-minute "
            f"compilation be caught before it scores a one-minute edit."
        )
    else:
        ceiling = max_track_duration_seconds(target_duration)
        if duration > ceiling:
            errors.append(
                f"{title or 'the chosen track'} runs {duration:.0f}s "
                f"({duration / 60:.1f} min) for a {target_duration:.0f}s edit. "
                f"The limit is {ceiling:.0f}s "
                f"({MAX_TRACK_DURATION_MULTIPLE}x the target, floor "
                f"{MAX_TRACK_DURATION_FLOOR_SECONDS:.0f}s). Something this "
                f"long is a compilation, not a track - choose a track, or a "
                f"named excerpt of one."
            )
        if duration + DURATION_SLACK_SECONDS < target_duration:
            errors.append(
                f"{title or 'the chosen track'} runs only {duration:.1f}s but "
                f"the edit is {target_duration:.0f}s. Music is placed at 0 and "
                f"run to the end of the timeline, so the last "
                f"{target_duration - duration:.1f}s would be silent."
            )

    # ── Justification against the stated creative direction ───────────
    just = selection.get("direction_justification")
    if not isinstance(just, dict):
        errors.append(
            "music_selection.direction_justification is missing. The ruling "
            "requires a recorded justification against the stated creative "
            "direction, so a contradicting choice is rejectable on reasoning "
            "rather than only on taste."
        )
        return errors

    if not (just.get("direction_mood") or "").strip():
        errors.append(
            "direction_justification.direction_mood is empty - quote the "
            "creative direction's target mood you are scoring against."
        )
    if not (just.get("why_it_fits") or "").strip():
        errors.append(
            "direction_justification.why_it_fits is empty - say why this "
            "track serves that mood."
        )

    forbidden = just.get("forbidden_registers")
    if not isinstance(forbidden, list) or not forbidden:
        errors.append(
            "direction_justification.forbidden_registers is empty. List the "
            "registers the creative direction says the piece must NOT be "
            "scored in - on project 001 that sentence is 'it should not be "
            "scored or cut as though it does'. This is the field the "
            "'Inspirational Motivational' failure had nowhere to fail."
        )
        forbidden = []

    why_not = just.get("why_not_forbidden")
    if not isinstance(why_not, dict):
        why_not = {}
    for register in forbidden:
        if not (why_not.get(register) or "").strip():
            errors.append(
                f"direction_justification.why_not_forbidden has no entry for "
                f"forbidden register {register!r} - say why this track does "
                f"not do that."
            )

    # The one mechanical catch on contradiction: a track whose own TITLE
    # names a register the model just recorded as forbidden.  Cheap,
    # grounded in what the model itself wrote, and it is exactly what the
    # shipped failure looked like - "Inspirational Motivational Music
    # Video" against a direction that forbids the motivational register.
    title_words = _tokens(title)
    clashes = sorted(
        {
            reg
            for reg in forbidden
            if isinstance(reg, str) and _tokens(reg) & title_words
        }
    )
    if clashes:
        errors.append(
            f"the chosen track is titled {title!r}, which names the register "
            f"{clashes} that direction_justification.forbidden_registers "
            f"itself declares off limits. Choose a track that does not "
            f"announce the thing the direction forbids."
        )

    return errors
