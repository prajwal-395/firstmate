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
`tests/unit/audio/test_music_selection.py` pins both halves.
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


class AuditTrailMissing(ValueError):
    """A resolved music selection with no readable audit sidecar.

    Step 2.04's post-bridge writes the sidecar warn-and-continue: a
    failed write prints one WARNING line into a long stderr stream and
    the run goes on. That trade is deliberate - failing a long run on
    the captain's own machine over a sidecar is worse than a warning -
    but it means a missing file is a silently half-reverted ruling
    (2026-09-16: the record is KEPT, only the carrier changes) unless
    something downstream says so loudly. This error is that something.
    It is raised by the pre-render check, never at the write site.
    """


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


def is_resolved(music_selection: Any) -> bool:
    """Whether 2.04 resolved a track - the condition the audit covers.

    A resolved selection names the file that will play (`audio_path`
    at the top level, the key every downstream consumer reads). An
    empty selection, or one naming no track, leaves no record to
    keep and the sidecar is not expected.
    """
    if not isinstance(music_selection, dict):
        return False
    path = music_selection.get("audio_path")
    return isinstance(path, str) and bool(path.strip())


def assert_audit_trail_present(
    project_folder: str,
    music_selection: Any,
) -> Optional[Dict[str, Any]]:
    """The sidecar exists and parses whenever a selection was resolved.

    The other half of 2.04's warn-and-continue write: the post-bridge
    keeps the run alive over a failed write with one WARNING line
    nobody is looking for, so the pre-render check (`compile_manifest`)
    calls this and REFUSES loudly when the file is gone. Silence here
    would quietly revert half of the captain's 2026-09-16 ruling while
    reporting success.

    Returns the parsed record when it is there, None when no selection
    was resolved and no record is owed. Raises `AuditTrailMissing`
    when a resolved selection has no file, no parse, or no record in
    it - never at the write site, always here.
    """
    if not is_resolved(music_selection):
        return None
    path = audit_path(project_folder)
    if not path.is_file():
        raise AuditTrailMissing(
            f"music_selection names {music_selection.get('audio_path')!r} "
            f"but the audit sidecar is missing: {path} does not exist. "
            f"Step 2.04 writes this file warn-and-continue, so a missing "
            f"file means that WARNING fired unseen - the run kept the "
            f"choice and lost the record of why it was chosen, which "
            f"reverts half of the 2026-09-16 ruling (the record is KEPT, "
            f"only the carrier changes). Re-run step 2.04 so the choice "
            f"is recorded: "
            f"manage_project.py run <project> --rerun music_selection"
        )
    try:
        with open(path, encoding="utf-8") as handle:
            stored = json.load(handle)
    except json.JSONDecodeError as exc:
        raise AuditTrailMissing(
            f"the music audit sidecar does not parse: {path} "
            f"({exc}). A resolved selection "
            f"({music_selection.get('audio_path')!r}) owes a readable "
            f"record of why it was chosen - re-run step 2.04: "
            f"manage_project.py run <project> --rerun music_selection"
        ) from exc
    if not isinstance(stored, dict) or not stored:
        raise AuditTrailMissing(
            f"the music audit sidecar holds no record: {path} parsed "
            f"as {type(stored).__name__}, not the resolved selection. "
            f"A resolved selection "
            f"({music_selection.get('audio_path')!r}) owes the whole "
            f"choice - candidates, justification, measurements - in "
            f"that file. Re-run step 2.04: "
            f"manage_project.py run <project> --rerun music_selection"
        )
    return stored
