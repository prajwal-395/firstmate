"""The typed, qualification-only Resolve reel promotion job.

This is the broker path for qualifying the shared promotion composition.
Its request contains data only: one qualification reel name pair and the
track plan the staging was built against. The broker owns the Resolve
connection and exclusive lease for the complete promotion plus version
record. No caller-supplied Python, project path, or promotion override
crosses the socket.
"""

from __future__ import annotations

import json
from pathlib import Path

from library.tools.resolved.jobs import JobRefused, QUALIFICATION_PROJECT

KIND = "reel.promote_staged"
STAGING_SUFFIX = " (rebuild staging)"
_PARAMS = frozenset({
    "project", "master_timeline_name", "final_timeline_name",
    "staging_timeline_name", "track_plan",
})


def _required_text(params: dict, key: str) -> str:
    value = params.get(key)
    if not isinstance(value, str) or not value.strip():
        raise JobRefused(f"{KIND} needs a non-empty {key!r}")
    return value.strip()


def prepare(params: dict) -> dict:
    """Validate the closed request and return its exclusive job shape."""
    if not isinstance(params, dict):
        raise JobRefused(f"{KIND} params must be an object")
    unknown = set(params) - _PARAMS
    if unknown:
        unsafe = unknown & {
            "allow_drops", "accept_editor_changes", "supersede", "retain",
            "project_folder", "organise",
        }
        if unsafe:
            raise JobRefused(
                f"{KIND} refuses promotion override(s) or paths: "
                f"{sorted(unsafe)}")
        raise JobRefused(f"{KIND} has unknown field(s): {sorted(unknown)}")

    project = _required_text(params, "project")
    if project != QUALIFICATION_PROJECT:
        raise JobRefused(
            f"{KIND} may only target {QUALIFICATION_PROJECT!r}, "
            f"not {project!r}")
    master = _required_text(params, "master_timeline_name")
    final = _required_text(params, "final_timeline_name")
    staging = _required_text(params, "staging_timeline_name")
    if not master.startswith("Q "):
        raise JobRefused(f"{KIND} master timeline must be a qualification reel")
    if (not final.startswith("Q ")
            or staging != final + STAGING_SUFFIX):
        raise JobRefused(
            f"{KIND} needs one Q reel and its exact staging name "
            f"{final + STAGING_SUFFIX!r}")
    if master in (final, staging):
        raise JobRefused(f"{KIND} master and promotion timelines must differ")

    plan = params.get("track_plan")
    if not isinstance(plan, dict):
        raise JobRefused(f"{KIND} needs a track_plan object")
    if not isinstance(plan.get("video_tracks"), list) or not isinstance(
            plan.get("audio_tracks"), list):
        raise JobRefused(
            f"{KIND} track_plan needs video_tracks and audio_tracks lists")

    return {
        "mode": "exclusive",
        "priority": "mutation",
        "executed": True,
        "project": QUALIFICATION_PROJECT,
        "timeline": staging,
        "locality": "project",
        "coalesce_key": None,
        "qualification": True,
    }


def _timeline_by_exact_name(project, name: str):
    matches = []
    count = int(project.GetTimelineCount() or 0)
    for index in range(1, count + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == name:
            matches.append(timeline)
    if len(matches) != 1:
        raise JobRefused(
            f"{KIND} expected exactly one timeline named {name!r}; "
            f"found {len(matches)}")
    return matches[0]


def _safe_filename(value: str) -> str:
    # Match reel_fusion_comps.safe_stem and record_reel_promotion's
    # timeline snapshot spelling so both records name the same reel.
    return "".join(
        character if character.isalnum() or character in "-_." else "_"
        for character in value) or "timeline"


def _relative_files(root: Path, paths) -> list[str]:
    relatives = []
    for raw in paths or ():
        try:
            path = Path(str(raw)).resolve()
            relative = path.relative_to(root.resolve())
        except (OSError, ValueError):
            continue
        if path.is_file():
            relatives.append(relative.as_posix())
    return sorted(relatives)


def _verified_record_evidence(project_folder: Path, review_dir: Path,
                              final: str, baseline: Path,
                              version_record: dict) -> dict:
    """Require the promotion's snapshot, matching comp exports and lineage."""
    snapshot_files = _relative_files(
        project_folder, version_record.get("snapshots"))
    fusion = version_record.get("fusion_comps") or {}
    comp_files = _relative_files(project_folder, fusion.get("files"))
    stem = _safe_filename(final)
    expected_snapshot = (
        Path("pipeline_output") / "review" / f"{stem}.timeline.json")
    expected_comp_dir = (
        Path("pipeline_output") / "steps" / "7_01_build_reels"
        / "fusion_comps")
    matching_comp_files = [
        path for path in comp_files
        if Path(path).parent == expected_comp_dir
        and Path(path).name.startswith(f"{stem}__")
        and Path(path).suffix == ".comp"
    ]
    supersession = (version_record.get("snapshot_supersession") or {}).get(
        final, {})
    baseline_name = baseline.relative_to(review_dir).as_posix()
    prior_snapshot_superseded = baseline_name in (
        supersession.get("superseded_now") or [])
    expected_snapshot_name = expected_snapshot.name

    failures = []
    if version_record.get("committed") is not True:
        failures.append("version record was not committed")
    if expected_snapshot.as_posix() not in snapshot_files:
        failures.append(f"snapshot missing: {expected_snapshot.as_posix()}")
    if not matching_comp_files:
        failures.append(
            f"matching {stem}__*.comp export missing from "
            f"{expected_comp_dir.as_posix()}")
    if fusion.get("errors"):
        failures.append("Fusion comp export reported errors")
    reel_export = (fusion.get("reels") or {}).get(final) or {}
    if reel_export.get("errors"):
        failures.append("the promoted reel's Fusion comp export reported errors")
    if version_record.get("snapshot_failed"):
        failures.append("timeline snapshot read failed")
    if version_record.get("supersession_failed"):
        failures.append("snapshot supersession record failed")
    if supersession.get("snapshot") != expected_snapshot_name:
        failures.append("snapshot supersession names a different snapshot")
    if not prior_snapshot_superseded:
        failures.append("pre-promotion snapshot was not marked superseded")

    evidence = {
        "snapshot_files": snapshot_files,
        "fusion_comp_files": matching_comp_files,
        "snapshot_supersession": supersession,
        "prior_snapshot_superseded": prior_snapshot_superseded,
        "artifacts_share_project_store": bool(
            expected_snapshot.as_posix() in snapshot_files
            and matching_comp_files),
        "all_artifacts_exist": bool(
            expected_snapshot.as_posix() in snapshot_files
            and matching_comp_files),
    }
    if failures:
        raise RuntimeError(
            f"{KIND} promoted {final!r}, but record verification failed: "
            + "; ".join(failures))
    return evidence


def run(job: dict, resolve, project) -> dict:
    """Promote one staged Q reel and record it on the broker connection."""
    params = job["params"]
    from library.tools.resolve_deadline import DeadlineProxy, deadline_proxy

    if not isinstance(resolve, DeadlineProxy):
        resolve = deadline_proxy(
            resolve, label=f"{KIND} {job.get('id', '')}")
    from library.tools import qualification_project
    with qualification_project.borrowed(
            resolve, qualification_only=True):
        project = resolve.GetProjectManager().GetCurrentProject()
        return _promote_and_record(job, params, resolve, project)


def _promote_and_record(job: dict, params: dict, resolve, project) -> dict:
    from library.tools.resolve_deadline import DeadlineProxy
    from library.tools import qualification_project

    if not isinstance(resolve, DeadlineProxy):
        raise JobRefused(f"{KIND} requires a deadline-wrapped broker handle")
    final = _required_text(params, "final_timeline_name")
    staging = _required_text(params, "staging_timeline_name")
    master = _required_text(params, "master_timeline_name")
    if project.GetName() != QUALIFICATION_PROJECT:
        raise JobRefused(
            f"{KIND} needs {QUALIFICATION_PROJECT!r} open; refusing "
            f"{project.GetName()!r}")

    retiring = _timeline_by_exact_name(project, final)
    staged = _timeline_by_exact_name(project, staging)
    _timeline_by_exact_name(project, master)
    staging_id = staged.GetUniqueId()
    retiring_id = retiring.GetUniqueId()
    if not staging_id:
        raise JobRefused(
            f"{KIND} staging {staging!r} has no Resolve unique id")
    if not retiring_id:
        raise JobRefused(
            f"{KIND} approved timeline {final!r} has no Resolve unique id")
    current = project.GetCurrentTimeline()
    current_id = current.GetUniqueId() if current else None
    if str(current_id or "") == str(retiring_id):
        raise JobRefused(
            f"{KIND} refuses while approved timeline {final!r} is current; "
            "promoting would delete the current timeline and move the cursor")

    from library.tools.project_layout import Area, ProjectLayout
    from library.tools.timeline_serializer import serialize_timeline_state
    from library.tools.versions import store

    project_folder = qualification_project.promotion_project_folder()
    project_folder.mkdir(parents=True, exist_ok=True)
    repo = store.init_project_repo(str(project_folder))
    if not repo.get("initialised"):
        raise JobRefused(
            f"{KIND} could not initialize its isolated qualification record: "
            f"{repo.get('reason', 'unknown reason')}")

    review_dir = ProjectLayout(project_folder).write_dir(Area.REVIEW)
    token = _safe_filename(str(job.get("id") or "take"))
    baseline = review_dir / (
        f"{_safe_filename(final)}.before-{token}.timeline.json")
    before = serialize_timeline_state(resolve_mock=resolve,
                                      timeline=retiring)
    baseline.write_text(json.dumps(
        before, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8")

    from library.tools import reel_build
    from library.tools.reel_replace_guard import timeline_inventory

    inventory = timeline_inventory(project)
    promotion = reel_build.promote_staged_reels(
        str(project_folder), QUALIFICATION_PROJECT, master,
        {final: staging}, organise=False,
        track_plans={staging: params["track_plan"]},
        timeline_inventory_before=inventory,
        staged_timeline_ids={staging: staging_id},
        resolve=resolve,
        allow_cursor_excursion=False)
    promoted = list(promotion.get("promoted") or [])
    if promoted != [final]:
        raise JobRefused(
            f"{KIND} did not promote exactly {final!r}",
            result={"promotion": promotion})
    replacement = _timeline_by_exact_name(project, final)
    if replacement.GetUniqueId() != staging_id:
        raise JobRefused(
            f"{KIND} promoted {final!r}, but its Resolve unique id "
            f"does not match staging {staging!r}",
            result={"promotion": promotion})
    staging_remains = False
    for index in range(1, int(project.GetTimelineCount() or 0) + 1):
        timeline = project.GetTimelineByIndex(index)
        if timeline and timeline.GetName() == staging:
            staging_remains = True
            break
    if staging_remains:
        raise JobRefused(
            f"{KIND} says {final!r} promoted but staging {staging!r} "
            "still exists",
            result={"promotion": promotion})
    current_after = project.GetCurrentTimeline()
    current_after_id = (current_after.GetUniqueId()
                        if current_after else None)
    if str(current_after_id or "") != str(current_id or ""):
        raise JobRefused(
            f"{KIND} promoted {final!r} but Resolve's current timeline "
            f"moved from id {current_id!r} to {current_after_id!r}; "
            "the cursor change is reported and the job refuses success",
            result={"promotion": promotion})

    version_record = store.record_reel_promotion(
        str(project_folder), QUALIFICATION_PROJECT, promoted,
        resolve=resolve)
    evidence = _verified_record_evidence(
        project_folder, review_dir, final, baseline, version_record)
    return {
        "project": QUALIFICATION_PROJECT,
        "master_timeline_name": master,
        "final_timeline_name": final,
        "staging_timeline_name": staging,
        "promotion": promotion,
        "version_record": version_record,
        "evidence": evidence,
    }
