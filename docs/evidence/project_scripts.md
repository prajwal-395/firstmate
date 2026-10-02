# Standalone scripts beside the footage

Moved from `tests/unit/context/test_project_scripts.py`.

The gap, recorded 2026-09-06: three standalone scripts lived in the captain's
project folder (`place_subtitles.py`, `generate_podcast_subtitles.py`,
`export_audio.py`) - re-implementing steps 4.01/4.05 and placement outside
the pipeline - and every repository-side guard (the Ruling-1
no-second-implementation check, the import greps, the operation registry)
looks at the REPOSITORY, so none of them could see a script living next to
the footage. A later look found a FOURTH (`render_subtitle_segments.py`),
which is why the finder matches the general shape - any loose `*.py` - and
never the three names the record happened to observe.

The check REPORTS them; it does not refuse them. Refusing would dictate how
the captain works in their own directories, and that call is the captain's.
What the pipeline owes is visibility: a run beside an unseen parallel
implementation must say so.

Sweep, 2026-09-08: the bare identifiers appear nowhere else executable.
`library/tools/timeline_transcript.py` and
`docs/FIELD_TEST_PODCAST_FINDINGS.md` name the scripts in comments and prose
to explain what was carried across from them (interpolation of untimed
WhisperX words) - references, not routes.
