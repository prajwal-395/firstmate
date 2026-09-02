"""Searching for music, and what a search costs.

Captain's ruling of 2026-08-28, verbatim: *"flush out the search
functionality, as far as music, just download what you need from youtube
and then use the sections from the music you find is best"*, and, on the
question this used to be blocked on: *"don't worry about that now, just
assume for everything that you already have a licence"*.

What was wrong
--------------
Fetching a **named** track worked - step 2.04's post-bridge calls
``download_track.download_audio`` when the model answers with a URL.
Searching for one never did.  ``search_youtube.py`` sat in the step
directory with **no caller anywhere in the repository** and ``yt-dlp`` was
in neither ``requirements.txt`` nor the venv, so the model was invited to
name a URL with no results in front of it.  On project 001 that left it
choosing between the seven files on disk - and two of those seven were the
same recording twice (see :mod:`library.tools.music_duplicates`).

An invitation is not a choice.  This module is the search half, and the
rule it follows is the one #296 established for the local catalogue: a
candidate the model cannot see MEASURED is not a candidate.  So a searched
result is fetched and measured before it is offered, and it then travels
through exactly the same columns a local track does.

Licence gates nothing
---------------------
The captain took it off the table by name.  What a search knows about
rights - YouTube's own ``license`` field, the channel, the URL - is
RECORDED on the candidate as provenance, because knowing where a file came
from is useful.  Nothing here reads it, nothing refuses on it, and no
rights model exists to build one out of.  A track is chosen on creative
grounds or not at all.

Queries are derived from creative_direction by default
------------------------------------------------------
Captain's ruling of 2026-09-02: search should run by default rather than
waiting on a flag nobody sets.  When no ``pipeline.music_search`` is
declared, the query is derived from creative_direction's ``target_mood``
and ``narrative_theme`` - the model's own words from step 2.01, not a
phrase this module composed.  The bridge calls
:func:`derive_queries_from_creative_direction` to extract them and hands
the result to :func:`resolve_declaration`.

A project that declares ``pipeline.music_search`` explicitly still gets
exactly what it asked for.  A project that declares
``pipeline.music_search: false`` declines search and the catalogue
says so.

Nothing here ranks.  Results come back in YouTube's order and are
handed over in it.

What a run costs, and how a project declines it
-----------------------------------------------
Search is ON by default.  The default bounds -
:data:`DEFAULT_RESULTS_PER_QUERY` and :data:`DEFAULT_FETCH_LIMIT` - are
resource bounds, not creative choices; a project that declares its own
numbers overrides them.  A project that sets
``pipeline.music_search: false`` declines entirely, and the run says so
loudly rather than quietly presenting the on-disk files as the whole menu.

Measured on this machine, 2026-08-28, ``yt-dlp`` 2026.08.19 over a
domestic connection:

====================================  ==========================
one query, 5 results, metadata only   1.97 s, ~0 bytes of media
one 3:45 track fetched to WAV         1.96-2.90 s, 3.6 MiB down
that track measured                   ~3 s of ffmpeg
====================================  ==========================

So a declaration of two queries at five results each, fetching four, costs
roughly ``2*2 + 4*3 + 4*3`` = 28 s and ~15 MiB.  The bound is the
declaration's, and :func:`search_and_report` returns what the run actually
spent so the number in this docstring can be checked rather than believed.

yt-dlp has to be current
------------------------
Measured the same day: 2026.03.17 answers every download with ``HTTP Error
403: Forbidden`` and warns that YouTube extraction without a JS runtime is
deprecated; 2026.08.19 downloads with no JS runtime and no flags.  A stale
extractor fails in a way that reads like a network problem, so
:func:`yt_dlp_version` is reported with every search and
:data:`KNOWN_STALE_BEFORE` is the version it is measured against.

The invocation is ``sys.executable -m yt_dlp`` whenever the module imports
in the running interpreter, and the bare binary only as a fallback - the
same reasoning as AGENTS.md section 4's "launch with ``sys.executable``,
never a bare ``python3``".  On this machine the ``yt-dlp`` on ``PATH`` is
the stale 2026.03.17 belonging to a different interpreter, which is
exactly the failure the rule prevents.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence, Tuple

# Where a project declares the search.  One key, under `pipeline:`, so it
# sits beside `delivery_format` and `framing_intent` rather than inventing
# a second place a project speaks from.
DECLARATION_KEY = "music_search"

# Every field a declaration may carry.  An unknown key is refused by name
# rather than ignored: a misspelled bound is a bound that silently is not
# there.
DECLARED_FIELDS = ("queries", "results_per_query", "fetch_limit")

# The bounds a project MAY override.  When the project does not declare
# them, the engine uses the defaults below - they are resource bounds,
# not creative choices, so having a sensible default is appropriate.
REQUIRED_BOUNDS = ("results_per_query", "fetch_limit")

# Default resource bounds when the project declares no music_search at
# all and the engine derives queries from creative_direction.  These are
# overridden by any explicit pipeline.music_search declaration.
# At these defaults, measured cost is roughly: 1*2 + 3*3 + 3*3 = 20s,
# ~10 MiB.
DEFAULT_RESULTS_PER_QUERY = 5
DEFAULT_FETCH_LIMIT = 3

# Measured 2026-08-28: 2026.03.17 returns HTTP 403 for every download.
# A floor on the tool, not on the answer.
KNOWN_STALE_BEFORE = "2026.08.19"

# yt-dlp's own search scheme.  `ytsearchN:<query>` is the documented form.
SEARCH_SCHEME = "ytsearch"

SEARCH_TIMEOUT_SECONDS = 120


class MusicSearchError(RuntimeError):
    """A search was asked for and could not be performed."""


@dataclass(frozen=True)
class SearchDeclaration:
    """What a project asked for, or the statement that it asked for nothing.

    When ``derive_from_direction`` is True, queries are empty and must be
    filled in from creative_direction before the search runs.  The bridge
    calls :func:`derive_queries_from_creative_direction` and then
    :func:`resolve_declaration` to produce a final, runnable declaration.
    """

    requested: bool
    queries: Tuple[str, ...] = ()
    results_per_query: int = 0
    fetch_limit: int = 0
    reason: str = ""
    derive_from_direction: bool = False

    def describe(self) -> str:
        if not self.requested:
            return f"music search: not run - {self.reason}"
        if self.derive_from_direction and not self.queries:
            return (
                f"music search: default-on, query to be derived from "
                f"creative_direction ({self.results_per_query} result(s), "
                f"fetching at most {self.fetch_limit})"
            )
        return (
            f"music search: {len(self.queries)} query(ies) x "
            f"{self.results_per_query} result(s), fetching at most "
            f"{self.fetch_limit}"
        )


def no_music_search(reason: str) -> SearchDeclaration:
    """The absence of a declaration, stated rather than left implicit."""
    return SearchDeclaration(requested=False, reason=reason)


def search_declaration(project_folder: Optional[str]) -> SearchDeclaration:
    """Read ``pipeline.music_search`` off the project's own ``project.yaml``.

    Absent means DEFAULT-ON: the query will be derived from
    creative_direction by the bridge.  ``pipeline.music_search: false``
    declines search explicitly.  Present but malformed RAISES - the same
    shape as ``bookends`` and ``timed_text_overlay``: a declaration nobody
    can act on is a mistake to report, not a thing to drop.
    """
    if not project_folder:
        return no_music_search("no project folder was given to the step")

    from library.tools.project_layout import ProjectLayout

    config_path = ProjectLayout(project_folder).project_config_path
    if not os.path.exists(config_path):
        return no_music_search(f"{config_path} does not exist")

    import yaml

    with open(config_path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}

    declared = (config.get("pipeline") or {}).get(DECLARATION_KEY)
    if declared is None:
        declared = config.get(DECLARATION_KEY)
    if declared is None:
        # Default-on: the bridge must derive queries from
        # creative_direction before calling search_all.
        return SearchDeclaration(
            requested=True,
            queries=(),
            results_per_query=DEFAULT_RESULTS_PER_QUERY,
            fetch_limit=DEFAULT_FETCH_LIMIT,
            reason="",
            derive_from_direction=True,
        )

    return parse_declaration(declared)


def parse_declaration(declared: Any) -> SearchDeclaration:
    """Turn a project's raw declaration into one, or raise saying why not."""
    if declared is False:
        return no_music_search(
            f"pipeline.{DECLARATION_KEY} is false - the project declined "
            f"search explicitly"
        )
    if not isinstance(declared, dict):
        raise MusicSearchError(
            f"pipeline.{DECLARATION_KEY} must be a mapping with "
            f"{list(DECLARED_FIELDS)}, or false to decline; got "
            f"{type(declared).__name__}"
        )

    unknown = sorted(set(declared) - set(DECLARED_FIELDS))
    if unknown:
        raise MusicSearchError(
            f"pipeline.{DECLARATION_KEY} carries unknown key(s) {unknown}. "
            f"The whole vocabulary is {list(DECLARED_FIELDS)}; a misspelled "
            f"bound is a bound that is not there."
        )

    raw_queries = declared.get("queries")
    if isinstance(raw_queries, str):
        raw_queries = [raw_queries]
    if not isinstance(raw_queries, list) or not raw_queries:
        raise MusicSearchError(
            f"pipeline.{DECLARATION_KEY}.queries is empty. An explicit "
            f"declaration must state its own queries. To use the default "
            f"(queries derived from creative_direction), omit "
            f"pipeline.{DECLARATION_KEY} entirely."
        )
    queries = []
    for entry in raw_queries:
        if not isinstance(entry, str) or not entry.strip():
            raise MusicSearchError(
                f"pipeline.{DECLARATION_KEY}.queries carries a non-string or "
                f"empty entry: {entry!r}"
            )
        queries.append(entry.strip())

    bounds = {}
    for name in REQUIRED_BOUNDS:
        value = declared.get(name)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise MusicSearchError(
                f"pipeline.{DECLARATION_KEY}.{name} must be a positive "
                f"integer; got {value!r}. An explicit declaration must state "
                f"its own bounds. To use the defaults "
                f"({DEFAULT_RESULTS_PER_QUERY} results, {DEFAULT_FETCH_LIMIT} "
                f"fetches), omit pipeline.{DECLARATION_KEY} entirely."
            )
        bounds[name] = value

    return SearchDeclaration(
        requested=True,
        queries=tuple(queries),
        reason="",
        **bounds,
    )


def derive_queries_from_creative_direction(
    creative_direction: Optional[Dict[str, Any]],
) -> Tuple[str, ...]:
    """Extract a search query from creative_direction's own fields.

    This uses the model's own words from step 2.01 - ``target_mood`` and
    ``narrative_theme`` - not a phrase this module composed.  The result
    is a single query combining what the model said about mood and theme.

    Returns an empty tuple when creative_direction is missing or carries
    nothing usable, so the caller can report the gap loudly.
    """
    if not isinstance(creative_direction, dict):
        return ()

    parts = []
    mood = (creative_direction.get("target_mood") or "").strip()
    if mood:
        parts.append(mood)
    theme = (creative_direction.get("narrative_theme") or "").strip()
    if theme:
        parts.append(theme)

    if not parts:
        return ()

    query = " ".join(parts) + " background music"
    return (query,)


def resolve_declaration(
    declaration: SearchDeclaration,
    creative_direction: Optional[Dict[str, Any]] = None,
) -> SearchDeclaration:
    """Produce a runnable declaration from one that may need query derivation.

    When ``declaration.derive_from_direction`` is True, this fills in the
    queries from creative_direction.  If creative_direction yields nothing
    usable, returns a declined declaration whose reason says exactly what
    went wrong - this is the LOUD gap the captain asked for.

    An already-resolved declaration (explicit queries or not requested)
    passes through unchanged.
    """
    if not declaration.requested:
        return declaration
    if not declaration.derive_from_direction:
        return declaration
    if declaration.queries:
        # Already has queries despite derive_from_direction - pass through.
        return declaration

    queries = derive_queries_from_creative_direction(creative_direction)
    if not queries:
        return no_music_search(
            "music search is default-on but creative_direction carries no "
            "target_mood or narrative_theme to derive a query from. "
            "The candidate menu is LIMITED TO WHAT IS ALREADY ON DISK. "
            "To search, either set pipeline.music_search with explicit "
            "queries in project.yaml, or ensure creative_direction produces "
            "a target_mood or narrative_theme."
        )

    return SearchDeclaration(
        requested=True,
        queries=queries,
        results_per_query=declaration.results_per_query,
        fetch_limit=declaration.fetch_limit,
        reason="",
        derive_from_direction=False,  # resolved now
    )


# ── The tool ─────────────────────────────────────────────────────────

def yt_dlp_command() -> List[str]:
    """How to invoke yt-dlp, preferring the running interpreter's own copy."""
    try:
        import yt_dlp  # noqa: F401
    except ImportError:
        return ["yt-dlp"]
    return [sys.executable, "-m", "yt_dlp"]


def yt_dlp_version() -> Optional[str]:
    """The version that will actually run, or None if there is none."""
    try:
        result = subprocess.run(
            [*yt_dlp_command(), "--version"],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return (result.stdout or "").strip() or None


def _stale_note(version: Optional[str]) -> str:
    if version is None:
        return (
            "yt-dlp is not installed for this interpreter and is not on PATH. "
            "It is in requirements.txt; install it into the run's venv."
        )
    if version < KNOWN_STALE_BEFORE:
        return (
            f"yt-dlp {version} is older than {KNOWN_STALE_BEFORE}, which is "
            f"the oldest version measured to download from YouTube on this "
            f"machine; older ones answer every fetch with HTTP 403 in a way "
            f"that reads like a network fault."
        )
    return ""


def _run(args: Sequence[str], timeout: int) -> subprocess.CompletedProcess:
    return subprocess.run(
        [*yt_dlp_command(), *args],
        capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=timeout,
    )


def search(query: str, max_results: int) -> List[Dict[str, Any]]:
    """``max_results`` results for ``query``, in the order YouTube returned.

    Metadata only - ``--flat-playlist`` fetches no media, which is what
    makes the duration filter free.  Nothing is ranked, scored or
    rejected here.
    """
    if max_results <= 0:
        raise MusicSearchError(f"max_results must be positive, got {max_results}")

    try:
        result = _run(
            ["--dump-json", "--flat-playlist", "--no-download",
             "--no-warnings", f"{SEARCH_SCHEME}{max_results}:{query}"],
            timeout=SEARCH_TIMEOUT_SECONDS,
        )
    except FileNotFoundError as exc:
        raise MusicSearchError(_stale_note(None)) from exc
    except subprocess.TimeoutExpired as exc:
        raise MusicSearchError(
            f"the search for {query!r} did not answer within "
            f"{SEARCH_TIMEOUT_SECONDS}s"
        ) from exc

    if result.returncode != 0:
        note = _stale_note(yt_dlp_version())
        raise MusicSearchError(
            f"yt-dlp failed searching for {query!r}: "
            f"{(result.stderr or '').strip()[:400]}"
            + (f"\n{note}" if note else "")
        )

    results: List[Dict[str, Any]] = []
    for line in (result.stdout or "").splitlines():
        if not line.strip():
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        video_id = entry.get("id") or ""
        url = (
            entry.get("webpage_url")
            or entry.get("url")
            or (f"https://www.youtube.com/watch?v={video_id}" if video_id else "")
        )
        if not url:
            continue
        duration = entry.get("duration")
        results.append({
            "title": entry.get("title") or "Untitled",
            "source_url": url,
            "video_id": video_id,
            # None means YouTube did not state one, never zero.
            "duration_seconds": (
                float(duration) if isinstance(duration, (int, float)) else None
            ),
            "channel": entry.get("channel") or entry.get("uploader") or "",
            "view_count": entry.get("view_count"),
            "search_query": query,
        })
    return results


@dataclass
class SearchCost:
    """What a search run actually spent, so the bound can be checked."""

    queries: int = 0
    results: int = 0
    search_seconds: float = 0.0
    considered: int = 0
    errors: List[str] = field(default_factory=list)

    def as_dict(self) -> Dict[str, Any]:
        return {
            "queries": self.queries,
            "results": self.results,
            "search_seconds": round(self.search_seconds, 2),
            "considered_after_duration": self.considered,
            "errors": list(self.errors),
        }


def provenance(result: Dict[str, Any],
               declaration: SearchDeclaration) -> Dict[str, Any]:
    """Where a searched candidate came from.  Recorded; never a gate.

    ``licence`` is whatever the platform states and is left exactly as
    stated, including "unstated".  Nothing in the pipeline reads it: the
    captain's ruling of 2026-08-28 took licensing out of the pipeline's
    hands, and a field that gated a choice would put it back.
    """
    return {
        "found_by": "youtube_search",
        "search_query": result.get("search_query", ""),
        "source_url": result.get("source_url", ""),
        "video_id": result.get("video_id", ""),
        "channel": result.get("channel", ""),
        "view_count": result.get("view_count"),
        "licence": result.get("licence") or "unstated by the platform",
        "licence_gates": (
            "nothing - recorded as provenance only, per the captain's "
            "ruling of 2026-08-28"
        ),
        "yt_dlp_version": result.get("yt_dlp_version") or "",
        "declared_queries": list(declaration.queries),
    }


def search_all(declaration: SearchDeclaration) -> Tuple[List[Dict[str, Any]],
                                                        SearchCost]:
    """Every query in the declaration, de-duplicated by video id.

    The same track surfacing under two queries is one candidate, not two -
    that much is free and needs no measurement.  The measurement-based
    duplicate check is a different question and lives in
    :mod:`library.tools.music_duplicates`.
    """
    cost = SearchCost()
    if not declaration.requested:
        return [], cost

    version = yt_dlp_version()
    note = _stale_note(version)
    if note:
        cost.errors.append(note)

    seen = set()
    collected: List[Dict[str, Any]] = []
    for query in declaration.queries:
        cost.queries += 1
        started = time.monotonic()
        try:
            results = search(query, declaration.results_per_query)
        except MusicSearchError as exc:
            cost.search_seconds += time.monotonic() - started
            cost.errors.append(str(exc))
            continue
        cost.search_seconds += time.monotonic() - started
        cost.results += len(results)
        for result in results:
            key = result.get("video_id") or result.get("source_url")
            if key in seen:
                continue
            seen.add(key)
            result["yt_dlp_version"] = version or ""
            collected.append(result)
    return collected, cost


def within_duration(result: Dict[str, Any],
                    target_duration: float,
                    ceiling: float,
                    slack: float) -> Tuple[bool, str]:
    """Whether a result can be chosen at all, from the free metadata.

    The same question the local catalogue asks, asked before a byte is
    downloaded.  A result YouTube gave no duration for is KEPT and said
    to be unstated: an absent measurement is not a measurement of
    unsuitability (AGENTS.md 10.3).
    """
    duration = result.get("duration_seconds")
    if not isinstance(duration, (int, float)) or duration <= 0:
        return True, "duration unstated by the platform - fetched to measure it"
    if duration > ceiling:
        return False, (
            f"TOO LONG: {duration / 60:.1f} min for a {target_duration:.0f}s "
            f"edit - this is a compilation, not a track"
        )
    if duration + slack < target_duration:
        return False, (
            f"TOO SHORT: {duration:.1f}s cannot cover a "
            f"{target_duration:.0f}s edit"
        )
    return True, ""


def _main(argv: List[str]) -> int:
    """``python3 -m library.tools.music_search <query> [n]`` - read only."""
    if not argv:
        print(__doc__)
        return 0
    query = argv[0]
    count = int(argv[1]) if len(argv) > 1 else 5
    started = time.monotonic()
    results = search(query, count)
    elapsed = time.monotonic() - started
    json.dump(
        {"query": query, "yt_dlp_version": yt_dlp_version(),
         "seconds": round(elapsed, 2), "results": results},
        sys.stdout, indent=2,
    )
    print()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
