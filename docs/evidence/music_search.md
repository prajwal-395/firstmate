# `library.tools.music_search` - the history behind its contract

This is the module docstring of `library/tools/music_search.py` as it stood on
2026-10-01, moved here verbatim when the module kept only its current
contract (punch list 17). The module's docstring is the contract; this
is the evidence and the story of how each rule was found. Where the two
disagree, the code and its docstring win.

```text
Searching for music, and what a search costs.

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


Rules relocated from AGENTS.md 10.5
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.5 keeps the headline
and points here.

**Nothing refuses a track on rights, and no rights model may be built.**
Captain's ruling 2026-08-28: *"just assume for everything that you already have a licence ... so song choices need to be made on creative decisions - not if a license exists or not"*.
Where a track came from is RECORDED as `provenance` - the query, the URL, the channel, and whatever the platform stated - and read by nothing.
`tests/test_music_search.py` fails if any code path branches on a licence.

**Search is one enumeration, `library/tools/music_search.py`, and it runs by default.**
Captain's ruling of 2026-09-02: search should run by default rather than waiting on a flag nobody sets.  When a project declares no `pipeline.music_search`, the query is derived from creative_direction's `target_mood` and `narrative_theme` - the model's own words from step 2.01.  A project that sets `pipeline.music_search: false` declines search explicitly.  When search does not run for any reason, the run says so LOUDLY rather than quietly presenting the on-disk files as the whole menu.
- **Queries default to the model's words from creative_direction.** A project that declares `pipeline.music_search` with explicit `queries`, `results_per_query` and `fetch_limit` still gets exactly what it asked for and overrides the defaults.
- **A result is judged on duration BEFORE anything is downloaded**, off the metadata the search returns for free.
- **A fetched candidate is MEASURED before the model sees it**, through the same `measure_candidates` pass a local track takes, and it is fetched through `download_track.download_audio` - the one fetch path.
- `yt-dlp` is in `requirements.txt` with a measured version floor. [why](docs/RULE_EVIDENCE.md#what-searching-for-music-costs)
- `tests/test_music_search.py`, and [`docs/MUSIC_SOURCING.md`](docs/MUSIC_SOURCING.md) §5 for the table.
```
