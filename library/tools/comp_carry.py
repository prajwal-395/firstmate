"""Carry a Fusion composition across a clip's re-place.

A re-placed item is a NEW object and a composition is keyed to the item
it was written for, so a trim - which the Resolve scripting API can only
do by re-placing the clip - DROPS the clip's treatment.  On a built reel
every picture clip carries a treatment, so this refusal met every trim
the engine could receive:

    the item has Fusion compositions that this replacement path cannot
    carry

The composition is not carried by copying pixels: it is RE-DERIVED from
the reel's own record.  The recorded Fusion manifest says what each
clip's treatment IS (the effects, the grade, the window), and the
build's own comp pass writes it keyed to the length the clip NOW plays.
So the carry is three steps:

1. the reel's RECORD is read before the write - the manifest, and the
   label of the clip the trim names;
2. the clip is re-placed, and the composition goes with the old item;
3. the comp pass writes the treatment again, keyed to the new length,
   and the write is PROVED: the comp is counted on the new item and its
   media window is read back against the frames the item plays.

A record that does not describe this reel is a REFUSAL, not a guess:
the comp pass maps specs to items by source path and window, so a
manifest from another cut would write treatments onto the wrong clips.
"""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass


class CompCarryRefused(RuntimeError):
    """The composition cannot be carried, and the reel is unchanged."""


@dataclass(frozen=True)
class CarryPlan:
    """What the reel's own record says about the clip's treatment."""

    label: str
    effects: dict
    source_file: str
    row: str
    source_in: float
    source_out: float
    played_frames: int
    manifest: dict
    project_folder: str = ""

    def one_clip_manifest(self) -> dict:
        """The manifest the comp pass writes from: THIS clip only.

        The reel's own record stays where it is - the pass would
        otherwise overwrite it with a manifest that describes one clip,
        and the reel's record is what a later reader looks its
        treatments up by.
        """
        return {
            "tracks": {self.row: {"clips": [{
                "label": self.label,
                "source_file": self.source_file,
                "source_in": self.source_in,
                "source_out": self.source_out,
            }]}},
            "fusion_effects": {"per_clip": {self.label: dict(self.effects)}},
        }


def _row_key(row: str) -> str:
    return str(row).upper()


def _clips_of(manifest: Mapping, row: str) -> list:
    tracks = manifest.get("tracks") or {}
    return list((tracks.get(_row_key(row)) or {}).get("clips") or ())


def plan_carry(*, project_folder: str, timeline_name: str,
               manifest: Mapping | None, row: str, item_index: int,
               items: Sequence, source_file: str,
               source_in_frame: int, source_out_frame: int,
               played_frames: int, source_fps: float) -> CarryPlan:
    """The reel's record of this clip's treatment, or a refusal.

    `items` is the live row's items in timeline order, so the manifest's
    clip specs can be matched to them the way the comp pass matches them
    (by source path AND window, never by position alone - the index is
    what decides which clip a comp lands on).

    `source_in_frame`/`source_out_frame` are the NEW window, in frames.
    """
    if manifest is None:
        raise CompCarryRefused(
            "this reel's treatments are not on disk, so a trim cannot "
            "re-derive the composition it would drop",
            "the composition is re-derived from the reel's own record, "
            "and a reel with no record has nothing to re-derive from",
            "build the reel once so its Fusion manifest is recorded, "
            "then trim it")
    specs = _clips_of(manifest, row)
    if not specs:
        raise CompCarryRefused(
            f"the reel's recorded manifest declares no clips on {row}, "
            "so it does not say what this clip's treatment is",
            "the comp pass maps a clip spec to an item by source path "
            "and window, and a spec that is not there reaches nothing",
            "rebuild the reel so its manifest describes this timeline, "
            "then trim it")
    from library.tools.execution.apply_fusion_comps import (
        map_clips_to_items,
    )

    mapping = map_clips_to_items(specs, items)
    label = None
    for spec_index, item_position in mapping.items():
        if item_position == int(item_index):
            label = str(specs[spec_index].get("label") or "")
            break
    if not label:
        raise CompCarryRefused(
            f"the reel's recorded manifest does not describe {row}"
            f"[{item_index}]",
            "the comp pass maps a clip spec to an item by source path "
            "and window; a manifest that does not describe this item "
            "would write treatments onto the wrong clips",
            "rebuild the reel so its manifest describes this timeline, "
            "then trim it")
    per_clip = (manifest.get("fusion_effects") or {}).get("per_clip") or {}
    effects = per_clip.get(label)
    if not effects:
        raise CompCarryRefused(
            f"the reel's recorded manifest says nothing about "
            f"{label!r}'s treatment",
            "the comp pass writes what the manifest states, and a clip "
            "the manifest does not describe gets no composition",
            "rebuild the reel so its manifest describes this timeline, "
            "then trim it")
    fps = float(source_fps or 0) or 1.0
    return CarryPlan(
        label=label,
        effects=dict(effects),
        source_file=source_file,
        row=_row_key(row),
        source_in=int(source_in_frame) / fps,
        source_out=int(source_out_frame) / fps,
        played_frames=int(played_frames),
        manifest=dict(manifest),
        project_folder=str(project_folder))


def apply_carry(plan: CarryPlan, *, project_folder: str,
                resolve_project_name: str, timeline_name: str,
                item, python_executable: str | None = None) -> dict:
    """Re-derive the composition onto the re-placed clip, and prove it.

    The pass runs in its OWN process (AGENTS.md 5: never create a
    timeline and use `ImportFusionComp` in the same Python process), and
    it writes the manifest it is given to a path of its own, so the
    reel's own record is not overwritten by a one-clip manifest.
    """
    from library.tools import reel_look

    directory = os.path.join(
        str(_scratch_dir(project_folder)), "reel_look")
    os.makedirs(directory, exist_ok=True)
    manifest_path = os.path.join(
        directory, f"carry_{plan.label}_fusion_manifest.json")
    ok = reel_look.apply_comps(
        plan.one_clip_manifest(), project_folder, resolve_project_name,
        timeline_name, python_executable=python_executable,
        step_id="comp_carry", manifest_path=manifest_path)
    if not ok:
        raise CompCarryRefused(
            "the comp pass refused or failed, so the re-placed clip "
            "carries no composition",
            "a clip whose composition was dropped renders the picture "
            "the treatment used to draw over",
            "read the comp pass's own report above; the trim was "
            "rolled back")
    return {"manifest_path": manifest_path,
            "comp_count": int(item.GetFusionCompCount() or 0)}


def verify_carry(item, played_frames: int) -> dict:
    """What the re-placed clip's composition reads back as.

    The comp COUNT is the first half; the media WINDOW is the second:
    a comp whose MediaIn does not cover the frames the item plays draws
    nothing over them, and the reel renders black there.
    """
    from library.tools import comp_media_window

    names = list(item.GetFusionCompNameList() or [])
    count = int(item.GetFusionCompCount() or 0)
    window = comp_media_window.read_window(item) if count else None
    uncovered = (comp_media_window.uncovered_reason(window, played_frames)
                 if window is not None else None)
    return {
        "comp_names": names,
        "comp_count": count,
        "media_window": window,
        "uncovered_reason": uncovered,
        "carried": bool(count) and uncovered is None,
    }


def _scratch_dir(project_folder: str) -> str:
    from library.tools.project_layout import Area, ProjectLayout

    return str(ProjectLayout(project_folder).read_dir(Area.SCRATCH))


__all__ = [
    "CarryPlan", "CompCarryRefused", "apply_carry", "plan_carry",
    "verify_carry",
]
