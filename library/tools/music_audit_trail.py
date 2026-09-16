"""Where the music audit trail lives, now that it is out of the spines.

The record of how each track was found and why it was chosen -
`candidates_evaluated`, `direction_justification` (including
`why_not_forbidden`), `splices`, `section`, `provenance`,
`measurements` - used to ride inside `audio_spine` / `timed_spine`
as `music_selection`, copied there by `mesh_spine`'s post-bridge.
That put ~24 kB of audit prose into `pipeline_data.json` on every
run and into every prompt routed a spine, while no code ever read
the nested copy: every operational consumer (`music_analysis`,
`mesh_spine` itself, `plan_transitions`, `plan_sfx`, `audio_mix`,
`compile_manifest`) is routed the top-level `music_selection` and
reads that.

Captain's ruling, 2026-09-16: move it out to its own file, then
confirm on a real run. The reasoning matters: the record is KEPT -
being able to ask later why a particular track was picked is worth
having - it just stops travelling through every step that never
reads it.

So this module owns the file:

- `AUDIT_FILENAME` is the name: `music_audit_trail.json`.
- It lives in step 2.04's own directory
  (`pipeline_output/steps/2_04_music_selection/`), which is where a
  person asking "why was this track chosen" looks, and which keeps
  the layout rule that a step writes only inside its own directory:
  the file is written by `step_2_04_music_selection`'s post-bridge,
  the step that made the choice.
- The content is the FULL `music_selection` dict, complete and
  untruncated - the same object `output.json` carries under its
  `music_selection` key. Nothing is summarised away: the point of
  the move is a change of carrier, not a change of content.
- `mesh_spine` writes no `music_selection` key into either spine.
  Its conducting (`music_bed.resolve_bed`) still reads the
  top-level `music_selection` input, which still reaches it.

Readers: anything that once reached for
`audio_spine["music_selection"]` (or `timed_spine`'s) reads
`read_audit_trail(project_folder)` instead. A sweep at the time of
the move found no such code reader - only prompts carried the
nested copy, as dead weight - so there was nothing to repoint;
`tests/test_music_audit_trail.py` pins both halves.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Optional
import json

AUDIT_FILENAME = "music_audit_trail.json"

#: The step that made the choice, and therefore the step whose
#: directory holds the file. See `library/tools/project_layout.py`:
#: a step writes only inside its own directory.
OWNING_STEP = "music_selection"


def audit_path(project_folder: str) -> Path:
    """The audit file's path, for reading. Nothing is created."""
    from library.tools.project_layout import ProjectLayout

    layout = ProjectLayout(project_folder)
    return layout.step_dir(OWNING_STEP) / AUDIT_FILENAME


def write_audit_trail(project_folder: str,
                      music_selection: Dict[str, Any]) -> Path:
    """Write the full selection as the audit trail. Returns the path.

    The content is complete - the whole `music_selection` dict, the
    same object the step's `output.json` carries. Abridging it here
    would trade the captain's "why was this track chosen" for a
    summary nobody asked for.
    """
    from library.tools.project_layout import ProjectLayout
    from library.tools.stable_json import write_stable

    if not isinstance(music_selection, dict) or not music_selection:
        raise ValueError(
            "music_audit_trail needs the resolved music_selection dict; "
            f"got {type(music_selection).__name__}. The trail is the "
            "record of the choice, and there is no trail without one."
        )
    layout = ProjectLayout(project_folder)
    path = layout.step_dir(OWNING_STEP, create=True) / AUDIT_FILENAME
    # The layout guard for a path from outside the layout: the file
    # must be somewhere a step may write.
    layout.assert_step_owns(OWNING_STEP, path)
    write_stable(path, dict(music_selection))
    return path


def read_audit_trail(project_folder: str) -> Optional[Dict[str, Any]]:
    """The recorded selection, or None when no run has written one."""
    path = audit_path(project_folder)
    if not path.is_file():
        return None
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else None
