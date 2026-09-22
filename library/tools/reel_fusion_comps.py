"""Per-reel Fusion comp exports for version control (AGENTS.md 3, 10.4).

At the end of a reel build, each promoted reel timeline's Fusion comps
are exported with `TimelineItem.ExportFusionComp` into
`pipeline_output/steps/7_01_build_reels/fusion_comps/` - one plain-text
`.comp` file per comp per reel, committed by the per-project git repo
(`library/tools/build_version_control.py`) in the same commit as the
timeline snapshot.

Why this exists: the timeline record reduces 76 Fusion tools to a comp
count plus a name, which is neither diffable nor mergeable. The export
is PLAIN LUA TEXT - diffable and mergeable, which is what makes
"branch / merge reels and edits just like normal code" reach the layer
where most of the visual work lives. That property is load-bearing:
this module writes whatever the exporter produced VERBATIM - never
compressed, re-encoded, summarised or normalised. A byte the exporter
wrote and this module rewrote is a diff nobody made.

Granularity is PER REEL: every file carries its reel's name as a
prefix, so reverting one reel's comps touches only that reel's files
while the wave still commits as one commit touching N files - the code
model, where a FILE is the unit of revert and a COMMIT is the unit of
change.

Never fails the build: an export that breaks the build is worse than
no export, so every failure is recorded on the report and the build
carries on. A reel with no comps records none - zero files, said
plainly - rather than refusing.
"""

from __future__ import annotations

from pathlib import Path

STEP_DIRNAME = "7_01_build_reels"
FUSION_COMPS_DIRNAME = "fusion_comps"


def fusion_comps_dir(project_folder: str) -> Path:
    """Where this reel's exports land, creating it."""
    out = (Path(project_folder) / "pipeline_output" / "steps"
           / STEP_DIRNAME / FUSION_COMPS_DIRNAME)
    out.mkdir(parents=True, exist_ok=True)
    return out


def safe_stem(name: str) -> str:
    """A filename stem for a reel name. Same spelling as the timeline
    snapshot beside it (`build_version_control.record_reel_promotion`),
    so the two records for one reel sort together."""
    return "".join(
        c if c.isalnum() or c in "-_." else "_"
        for c in (name or "")) or "timeline"


def _track_items(timeline, kind: str, index: int) -> list:
    try:
        return list(timeline.GetItemListInTrack(kind, index) or [])
    except Exception:  # noqa: BLE001 - an unreadable row reads as empty,
        # never as a failed export: the report covers what was exported.
        return []


def _comp_count(item) -> int:
    try:
        count = item.GetFusionCompCount()
    except Exception:  # noqa: BLE001 - an unreadable item carries no
        # comps this export can name; the build verdict is elsewhere.
        return 0
    return count if isinstance(count, int) and count > 0 else 0


def export_reel_comps(timeline, reel_name: str,
                      project_folder: str) -> dict:
    """Export every Fusion comp off one reel timeline, verbatim.

    `timeline` is the timeline's OWN handle - the caller already holds
    it, so the cursor never moves. Files are named
    `<reel>__V<track>_item<iii>_c<n>.comp`: the reel prefix is what
    makes a single reel revertible without touching another reel's
    files. Stale files under this reel's prefix from a previous build
    are removed first, so a rebuild that drops a comp does not leave
    its ghost behind.

    Never raises: failures are carried on the report.
    """
    report: dict = {"reel": reel_name, "files": [], "comp_count": 0,
                    "items_with_comps": 0, "errors": []}
    try:
        out_dir = fusion_comps_dir(project_folder)
    except Exception as exc:  # noqa: BLE001 - nowhere to write, so the
        # report says so and the build carries on without an export.
        report["errors"].append(f"fusion_comps dir unavailable: {exc!r}")
        return report
    stem = safe_stem(reel_name)
    # Clear this reel's previous exports first: without it a rebuild
    # carrying fewer comps leaves stale files that read as still live.
    try:
        for stale in sorted(out_dir.glob(f"{stem}__*.comp")):
            try:
                stale.unlink()
            except OSError as exc:
                report["errors"].append(
                    f"stale {stale.name} not removed: {exc!r}")
    except Exception as exc:  # noqa: BLE001 - a listing failure is not
        # an export failure; whatever is on disk stays, said plainly.
        report["errors"].append(f"stale listing failed: {exc!r}")
    try:
        video_tracks = timeline.GetTrackCount("video") or 0
    except Exception as exc:  # noqa: BLE001 - an unreadable timeline
        # exports nothing; said on the report, never raised.
        report["errors"].append(f"video track count unreadable: {exc!r}")
        return report
    try:
        video_tracks = int(video_tracks)
    except (TypeError, ValueError):
        return report
    for track in range(1, video_tracks + 1):
        for item_index, item in enumerate(_track_items(
                timeline, "video", track)):
            count = _comp_count(item)
            if not count:
                continue
            report["items_with_comps"] += 1
            for comp_index in range(1, count + 1):
                filename = (f"{stem}__V{track:02d}_item{item_index:03d}"
                            f"_c{comp_index}.comp")
                path = out_dir / filename
                try:
                    exported = item.ExportFusionComp(
                        str(path), comp_index)
                except Exception as exc:  # noqa: BLE001 - one comp's
                    # failure must not cost its siblings their export.
                    report["errors"].append(
                        f"{filename}: ExportFusionComp raised {exc!r}")
                    continue
                # Judge the call by what it returns AND what it left:
                # a True with no file is a refusal wearing a pass.
                if not exported or not path.exists():
                    report["errors"].append(
                        f"{filename}: ExportFusionComp returned "
                        f"{exported!r}")
                    try:
                        if path.exists():
                            path.unlink()
                    except OSError:
                        pass
                    continue
                # VERBATIM: the bytes are never read back, normalised
                # or re-encoded - see the module docstring.
                report["files"].append(str(path))
                report["comp_count"] += 1
    report["files"] = sorted(report["files"])
    return report


def export_built_reels(project, reel_names, project_folder: str) -> dict:
    """Export comps off each named reel timeline. Never raises.

    Timelines are found by EXACT name (AGENTS.md 5) through their OWN
    handles, so the cursor never moves. Returns
    `{"reels": {name: report}, "files": [...], "errors": [...]}`.
    A reel Resolve no longer holds is recorded, not raised: the
    promotion already landed, and a missing export must not read as a
    failed build.
    """
    out: dict = {"reels": {}, "files": [], "errors": []}
    names = [n for n in (reel_names or []) if n]
    if not names:
        return out
    try:
        count = project.GetTimelineCount() + 1
    except Exception as exc:  # noqa: BLE001 - an unreadable project
        # exports nothing; said on the report, never raised.
        out["errors"].append(f"timeline count unreadable: {exc!r}")
        return out
    by_name = {}
    try:
        count = int(count)
    except (TypeError, ValueError):
        return out
    for index in range(1, count):
        try:
            candidate = project.GetTimelineByIndex(index)
        except Exception:  # noqa: BLE001, S112 - one unreadable slot
            continue  # skips; a named reel missed this way is said
        try:  # below as "not in the Resolve project", never silent.
            name = candidate.GetName() if candidate else None
        except Exception:  # noqa: BLE001, S112 - a nameless handle
            continue  # matches nothing by exact name, so it is skipped.
        if name:
            by_name.setdefault(name, candidate)
    for name in names:
        timeline = by_name.get(name)
        if timeline is None:
            out["errors"].append(
                f"{name!r}: not in the Resolve project - no comps "
                f"exported for it")
            continue
        report = export_reel_comps(timeline, name, project_folder)
        out["reels"][name] = report
        out["files"].extend(report.get("files") or [])
        out["errors"].extend(
            f"{name}: {error}" for error in report.get("errors") or [])
    out["files"] = sorted(out["files"])
    return out
