"""A card the captain placed by hand, reproducible by the build.

The captain's own logo card on Reel 09, the tail card he keeps
re-adding: media laid onto a timeline by hand has no owning layer.
The template declares *template* cards (`content.bookends`, staged
verbatim); a hand-placed asset is a decision about THIS video, so it
is declared per project, not per template - and a rebuild that cannot
see it rebuilds without it, which is exactly how the tail card kept
vanishing.

The declaration lives at `<project>/external/placed_assets.json`
(the `overlay_intent.json` precedent: checked, never asserted)::

    {"version": 1,
     "assets": [{"slot": "tail", "asset": "/abs/path/tailcard.mov",
                 "duration_seconds": 5.0, "has_audio": false,
                 "label": "tail card",
                 "reason": "captain 2026-09-11: every episode ends here"}]}

`slot` is `head` or `tail` ONLY. A mid-reel insertion shifts the
picture against the sound and re-decides pacing, so it is REFUSED -
the spine owns the middle, and a card that belongs there is a spine
block, not a pin. `asset` is an ABSOLUTE PATH to a file that exists
(a reference is a path plus a map - AGENTS.md 10.1 - and the map is
this file); a missing file refuses the compile by name rather than
building a timeline with a hole in it.

`carry_into_manifest` runs after the manifest is assembled and before
it is validated: the card rides the same V1 clip shape a declared
bookend rides (`source_in` 0, `source_out` duration, `bookend` slot,
`video_only` where silent), so every gate that judges cards judges
this one - coverage, no-B-roll-over-cards, render QA. A head card
shifts every track later by its duration (the `lead_frames`
arithmetic, at manifest level); a tail card appends at the end and
extends the project duration. The compile that cannot read this file
refuses rather than building silently past it.

`tests/test_orphan_owners.py`.
"""

from __future__ import annotations

import json
import os

#: Schema version this reader honours.
ASSETS_VERSION = 1

#: The file basename, under the project's external-inputs area.
ASSETS_FILENAME = "placed_assets.json"

#: The only slots a pin may take. The middle belongs to the spine.
SLOTS = ("head", "tail")


class PlacedAssetError(ValueError):
    """The declared placed assets cannot be honoured as written."""


# ── Recording: validation ──────────────────────────────────────────

def validate_assets(value) -> list:
    """Structural check. Raises `PlacedAssetError` naming what is wrong.

    Existence of the media is checked at CARRY time, not here: the
    file may be recorded before the render lands, and drift reports
    loudly at the compile instead.
    """
    if not isinstance(value, list) or not value:
        raise PlacedAssetError(
            "placed_assets must be a non-empty list of assets. An empty "
            "one is the absence of assets - leave the file out instead.")
    for index, asset in enumerate(value):
        label = f"placed_assets[{index}]"
        if not isinstance(asset, dict):
            raise PlacedAssetError(f"{label} is not an object")
        slot = asset.get("slot")
        if slot not in SLOTS:
            raise PlacedAssetError(
                f"{label} names slot {slot!r}: one of "
                f"{', '.join(SLOTS)}. A mid-reel card re-decides pacing "
                f"and shifts picture against sound - the spine owns the "
                f"middle, so a card that belongs there is a spine block, "
                f"not a pin.")
        path = asset.get("asset")
        if not isinstance(path, str) or not path.strip():
            raise PlacedAssetError(
                f"{label} names no asset file. A reference is an "
                f"absolute path.")
        if not os.path.isabs(path):
            raise PlacedAssetError(
                f"{label} carries asset {path!r}: relative to what? A "
                f"reference is an ABSOLUTE path plus a map, and this "
                f"file is the map - name the path outright.")
        duration = asset.get("duration_seconds")
        if (isinstance(duration, bool)
                or not isinstance(duration, (int, float))
                or not float(duration) > 0):
            raise PlacedAssetError(
                f"{label} carries duration_seconds {duration!r}: a card "
                f"names how long it plays.")
        if not isinstance(asset.get("has_audio", False), bool):
            raise PlacedAssetError(
                f"{label} carries has_audio "
                f"{asset.get('has_audio')!r}: a boolean, so the build "
                f"knows whether to place A1 under it.")
        reason = asset.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise PlacedAssetError(
                f"{label} carries no 'reason' - the captain's own "
                f"words, which is what any listing reads back.")
    return value


def parse_assets(body: dict, source: str = ASSETS_FILENAME) -> list:
    """Validated assets from a decoded file, or a refusal."""
    if not isinstance(body, dict):
        raise PlacedAssetError(
            f"{source} must be a JSON object, not "
            f"{type(body).__name__}.")
    if body.get("version") != ASSETS_VERSION:
        raise PlacedAssetError(
            f"{source} declares version {body.get('version')!r}: this "
            f"reader honours version {ASSETS_VERSION}.")
    assets = body.get("assets", [])
    if not isinstance(assets, list):
        raise PlacedAssetError(
            f"{source} carries assets={assets!r}, which is not a list.")
    if not assets:
        return []
    return validate_assets(assets)


def load_assets(project_folder=None,
                assets_file: str | None = None) -> list:
    """Validated assets for a project, or `[]` where none are declared."""
    if assets_file:
        if not os.path.isfile(assets_file):
            raise PlacedAssetError(
                f"assets file {assets_file!r} does not exist: refusing "
                f"rather than compiling without the declared cards.")
        with open(assets_file, encoding="utf-8") as handle:
            try:
                body = json.load(handle)
            except json.JSONDecodeError as exc:
                raise PlacedAssetError(
                    f"assets file {assets_file!r} is not JSON: "
                    f"{exc}") from exc
        return parse_assets(body, source=assets_file)
    if not project_folder:
        return []
    from library.tools.external_inputs import external_dir

    path = os.path.join(str(external_dir(project_folder)), ASSETS_FILENAME)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        try:
            body = json.load(handle)
        except json.JSONDecodeError as exc:
            raise PlacedAssetError(
                f"{path} is not JSON: {exc}") from exc
    return parse_assets(body, source=path)


# ── Carrying: into the assembled manifest ──────────────────────────

def _shift_clip(clip: dict, seconds: float, fps: float) -> None:
    for key in ("timeline_in", "timeline_out"):
        if key in clip and isinstance(clip[key], (int, float)):
            clip[key] = round(float(clip[key]) + seconds, 3)
    for key in ("timeline_in_frame", "timeline_out_frame"):
        if key in clip and isinstance(clip[key], int):
            clip[key] += int(round(seconds * fps))


def carry_into_manifest(manifest: dict, assets: list,
                        fps: float = 30.0) -> dict:
    """Append the captain's cards to an assembled manifest. In place.

    Returns `{"carried": [...], "refused": [...]}`: `carried` names
    each card with its span; `refused` carries assets whose media is
    not on disk, LOUDLY - a card the compile cannot see must refuse
    the compile, never build a video with a hole in it. A head card
    shifts every track later (the lead arithmetic); a tail card opens
    where the longest video track ends. The project duration extends
    by every carried card.
    """
    report = {"carried": [], "refused": []}
    tracks = (manifest.get("tracks") or {})
    v1 = ((tracks.get("V1") or {}).get("clips")) or []
    for asset in assets or []:
        path = asset["asset"]
        if not os.path.isfile(path):
            report["refused"].append(
                {**asset, "reason": (
                    f"REFUSED: asset {path!r} is not on disk - the "
                    f"compile cannot place a card it cannot see. "
                    f"Original request: "
                    f"{asset.get('reason', '')}".strip())})
            continue
        duration = round(float(asset["duration_seconds"]), 3)
        slot = asset["slot"]
        name = (asset.get("label") or os.path.basename(path))
        if slot == "head":
            for track in tracks.values():
                for clip in (track or {}).get("clips", []) or []:
                    if isinstance(clip, dict):
                        _shift_clip(clip, duration, fps)
            # The plan rides the same seconds the picture does: spine
            # blocks and the bed automation shift with the clips, or
            # every subtitle and every duck lands one card early.
            for block in manifest.get("_spine_blocks", []) or []:
                if not isinstance(block, dict):
                    continue
                for key in ("timeline_start", "timeline_end"):
                    if isinstance(block.get(key), (int, float)):
                        block[key] = round(float(block[key])
                                           + duration, 3)
            automation = ((manifest.get("audio_mix") or {})
                          .get("music_automation", [])) or []
            for entry in automation:
                if not isinstance(entry, dict):
                    continue
                for key in ("timeline_start", "timeline_end"):
                    if isinstance(entry.get(key), (int, float)):
                        entry[key] = round(float(entry[key])
                                           + duration, 3)
            timeline_in, timeline_out = 0.0, duration
        else:
            end = 0.0
            for track_name, track in tracks.items():
                if not str(track_name).startswith("V"):
                    continue
                for clip in (track or {}).get("clips", []) or []:
                    if isinstance(clip, dict) and isinstance(
                            clip.get("timeline_out"), (int, float)):
                        end = max(end, float(clip["timeline_out"]))
            timeline_in, timeline_out = round(end, 3), round(end, 3) + duration
        clip = {
            "source_file": path,
            "source_in": 0.0,
            "source_out": duration,
            "timeline_in": timeline_in,
            "timeline_out": timeline_out,
            "timeline_in_frame": int(round(timeline_in * fps)),
            "timeline_out_frame": int(round(timeline_out * fps)),
            "link_group_id": None,
            "label": f"placed_{slot}_{name}",
            "bookend": f"{slot}_card",
            "video_only": not asset.get("has_audio", False),
            "provenance": "declared",
        }
        v1.append(clip)
        if isinstance(manifest.get("project"), dict):
            current = manifest["project"].get("duration_seconds") or 0.0
            manifest["project"]["duration_seconds"] = round(
                float(current) + duration, 3)
        report["carried"].append(
            {"slot": slot, "label": clip["label"],
             "span": [timeline_in, timeline_out],
             "reason": asset.get("reason", "")})
    v1.sort(key=lambda c: (c.get("timeline_in", 0)
                           if isinstance(c, dict) else 0))
    return report
