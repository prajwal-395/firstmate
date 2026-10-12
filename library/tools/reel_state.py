"""One read of every reel's state: approval, build, version, sign-off, delivery.

A read-only verb composing existing records, so learning what state
reel N is in costs one call instead of a dozen direct reads of
`pipeline_data.json`, the phase log, the proposals file, the version
ledger, sign-offs and delivery sidecars. Per reel it reports:

- approval (`reel_proposal.read_proposal`)
- the latest `build_summary` (`reel_phase_log.summarize`): outcome,
  staging, final, decision, owed answers, verify
- version (`review/reel_versions.json`), sign-off, last delivery file
  (`.deliver.json`)
- `awaiting_model_answers.collect(...)`

Text default is at most 1 line per reel; `--json` gives everything.
Exact Resolve names come from the records. Read only: never calls a
model or Resolve, never writes.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

REEL_NUMBER_RE = re.compile(r"Reel\s+0*(\d+)", re.IGNORECASE)


class UnknownReel(RenRefusal):
    """A `--reel` filter named nothing the builds recorded."""

    USER_MESSAGE = (
        "That reel is not recorded for this project, so nothing was shown. "
        "List the recorded reels, then ask me to look again."
    )


def reel_number_of(name) -> int | None:
    """The reel number inside a Resolve timeline name, or None."""
    try:
        match = REEL_NUMBER_RE.search(str(name or ""))
    except (TypeError, ValueError):
        return None
    if not match:
        return None
    try:
        return int(match.group(1))
    except (TypeError, ValueError):
        return None


def _json_file(path: Path):
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value


def resolve_root(project_ref: str) -> tuple:
    """The project root and slug for a slug or path, read-only."""
    from library.schemas.project_config import (
        load_project_config,
        project_config_to_dict,
    )
    from library.tools.project_registry import resolve_project_path, scan_projects

    yaml_path = resolve_project_path(project_ref)
    if yaml_path is None:
        match = next((c for c in scan_projects() if c.slug == project_ref), None)
        if match is None:
            raise FileNotFoundError(f"No Ren project found for {project_ref!r}")
        yaml_path = match.project_root / "project.yaml"
    config = load_project_config(yaml_path, migrate_format=False)
    config_data = project_config_to_dict(config)
    root = Path(config.project_root)
    return root, config_data.get("slug") or config.slug, config_data.get("name")


def _rows() -> dict:
    return {}


def _ensure(rows: dict, reel=None, name: str = "") -> dict:
    number = None
    if isinstance(reel, bool):
        reel = None
    if isinstance(reel, (int, float)) and reel:
        number = int(reel)
    if number is None:
        number = reel_number_of(name)
    if number is None and isinstance(reel, str) and reel.strip():
        try:
            number = int(reel.strip())
        except (TypeError, ValueError):
            number = None
    key = number if number is not None else str(name or reel or "")
    row = rows.get(key)
    if row is None:
        row = {
            "reel": number if number is not None else str(name or reel or ""),
            "timeline_name": str(name or ""),
            "approval": None,
            "approval_note": "",
            "outcome": None,
            "decision": None,
            "staging": None,
            "final": None,
            "verify": None,
            "owed_answers": [],
            "version": None,
            "signoff": None,
            "delivery": None,
            "phases": [],
            "build_summary_at": None,
            "build_summary": None,
        }
        rows[key] = row
    if name and not row["timeline_name"]:
        row["timeline_name"] = str(name)
    return row


def reels_state(project_ref: str, only_reel=None) -> dict:
    """Every reel's state, composed from the records the builds wrote."""
    from library.tools import (
        awaiting_model_answers,
        reel_phase_log,
        reel_proposal,
        reel_signoff,
    )
    from library.tools.versions import reel_versions

    root, slug, name = resolve_root(project_ref)
    rows: dict = _rows()

    try:
        proposal_path = reel_proposal.proposal_path(str(root))
        moments = reel_proposal.read_proposal(str(proposal_path))
    except (OSError, ValueError, reel_proposal.ProposalError):
        moments = []
    for moment in moments or ():
        try:
            number = moment.number
            timeline = moment.timeline_name
            approval = moment.approval.value
            note = moment.approval_note
        except AttributeError:
            continue
        row = _ensure(rows, reel=number, name=timeline)
        row["approval"] = approval
        row["approval_note"] = str(note or "")
        row["timeline_name"] = str(timeline or row["timeline_name"])

    try:
        events = reel_phase_log.read_events(str(root))
        summary = reel_phase_log.summarize(events)
    except (OSError, ValueError):
        events, summary = [], {}
    for slot in (summary or {}).values():
        if not isinstance(slot, dict):
            continue
        payload = slot.get("build_summary")
        phases = sorted((slot.get("phases") or {}).keys())
        names = [slot.get("reel") or ""]
        if isinstance(payload, dict):
            for key in ("staging", "final"):
                value = payload.get(key)
                if value and value not in names:
                    names.append(value)
        number = slot.get("reel_number") or reel_number_of(names[0])
        row = _ensure(rows, reel=number, name=names[0])
        row["phases"] = phases
        row["build_summary_at"] = slot.get("build_summary_at")
        if isinstance(payload, dict):
            row["build_summary"] = payload
            row["outcome"] = payload.get("outcome")
            row["decision"] = payload.get("decision")
            row["staging"] = payload.get("staging")
            row["final"] = payload.get("final") or row["final"]
            row["verify"] = payload.get("verify")
            owed = [str(layer) for layer in (payload.get("answers_owed") or ())]
            row["owed_answers"] = sorted(set(row["owed_answers"]) | set(owed))
            if payload.get("final"):
                row["timeline_name"] = str(payload["final"])
            elif payload.get("staging") and not row["timeline_name"]:
                row["timeline_name"] = str(payload["staging"])

    try:
        ledger = reel_versions.read(str(root))
    except (OSError, ValueError, reel_versions.ReelVersionsUnreadable):
        ledger = {}
    for final, history in ((ledger or {}).get("reels") or {}).items():
        if not isinstance(history, list) or not history:
            continue
        row = _ensure(rows, name=str(final))
        latest = history[-1] if isinstance(history[-1], dict) else {}
        row["version"] = {
            "count": len(history),
            "latest_version": latest.get("version"),
            "latest_kind": latest.get("kind"),
            "latest_at": latest.get("at"),
        }
        if not row["timeline_name"]:
            row["timeline_name"] = str(final)

    try:
        signoffs = reel_signoff.signed_off(str(root))
    except (OSError, ValueError, reel_signoff.SignOffsUnreadable):
        signoffs = {}
    for base, entry in (signoffs or {}).items():
        if not isinstance(entry, dict):
            continue
        row = _ensure(rows, name=str(base))
        row["signoff"] = {
            "reel": entry.get("reel"),
            "signed_off_at": entry.get("signed_off_at"),
            "by": entry.get("by"),
            "round": entry.get("round"),
            "note": entry.get("note"),
        }

    try:
        from library.tools import render_watch

        deliveries = render_watch.delivered_reels(str(root))
    except (OSError, ValueError):
        deliveries = []
    seen_delivery: set = set()
    for record in deliveries or ():
        if not isinstance(record, dict):
            continue
        key = record.get("reel")
        number = None
        if isinstance(key, (int, float)) and not isinstance(key, bool):
            number = int(key)
        if number is None:
            number = reel_number_of(record.get("timeline_name"))
        row = _ensure(rows, reel=number, name=str(record.get("timeline_name") or ""))
        row_key = id(row)
        if row_key in seen_delivery:
            continue
        seen_delivery.add(row_key)
        row["delivery"] = {
            "timeline_name": str(record.get("timeline_name") or ""),
            "video_path": str(record.get("video_path") or ""),
            "sidecar": str(record.get("sidecar") or ""),
            "delivered": bool(record.get("delivered")),
        }
        if record.get("timeline_name") and not row["timeline_name"]:
            row["timeline_name"] = str(record["timeline_name"])

    try:
        state = _json_file(root / "pipeline_data.json")
        owed = awaiting_model_answers.collect(str(root), state=state)
    except (OSError, ValueError):
        owed = {}
    for entry in (owed or {}).get("reels") or ():
        if not isinstance(entry, dict):
            continue
        layers = sorted(str(layer) for layer in (entry.get("layers") or ()))
        if not layers:
            continue
        row = _ensure(rows, reel=entry.get("reel"))
        row["owed_answers"] = sorted(set(row["owed_answers"]) | set(layers))

    reels = sorted(
        rows.values(),
        key=lambda r: (
            0 if isinstance(r["reel"], int) else 1,
            r["reel"] if isinstance(r["reel"], int) else 0,
            str(r["reel"]),
        ),
    )
    if only_reel is not None:
        wanted = str(only_reel).strip()
        wanted_number = None
        try:
            wanted_number = int(wanted)
        except (TypeError, ValueError):
            wanted_number = reel_number_of(wanted)
        kept = []
        for row in reels:
            if isinstance(row["reel"], int) and wanted_number is not None:
                if row["reel"] == wanted_number:
                    kept.append(row)
            elif (
                str(row["reel"]) == wanted
                or row["timeline_name"] == wanted
                or row.get("final") == wanted
            ):
                kept.append(row)
        reels = kept
    return {
        "project": {"slug": slug, "name": name, "path": str(root)},
        "reel_count": len(reels),
        "reels": reels,
    }


def _verify_word(verify) -> str:
    if verify is None:
        return "-"
    if isinstance(verify, dict):
        for key in ("verdict", "passed", "outcome", "status"):
            value = verify.get(key)
            if isinstance(value, bool):
                return "pass" if value else "fail"
            if isinstance(value, str) and value:
                return value
        return "recorded"
    return str(verify)


def format_line(row: dict) -> str:
    """One reel on one line: names, approval, outcome, verify, owes, gates."""
    reel = row.get("reel")
    label = f"Reel {reel}" if isinstance(reel, int) else str(reel or "?")
    name = str(row.get("final") or row.get("timeline_name") or "")
    head = f"{label} ({name})" if name else label
    approval = str(row.get("approval") or "-")
    outcome = str(row.get("outcome") or "-")
    verify = _verify_word(row.get("verify"))
    owed = ",".join(row.get("owed_answers") or []) or "-"
    signoff = "yes" if row.get("signoff") else "no"
    delivery = "yes" if (row.get("delivery") or {}).get("delivered") else "no"
    version = "-"
    if isinstance(row.get("version"), dict):
        version = str(row["version"].get("count") or "-")
    return (
        f"{head}: approval={approval} outcome={outcome} verify={verify} "
        f"owed={owed} sign-off={signoff} delivered={delivery} "
        f"versions={version}"
    )


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren reels",
        description="Read every reel's state in one call (read only)",
    )
    parser.add_argument("project", help="project slug or path")
    parser.add_argument(
        "--reel",
        default=None,
        help="Only this reel: its number (e.g. 7) or its exact timeline name",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="print the full per-reel records as JSON",
    )
    args = parser.parse_args(argv)
    try:
        state = reels_state(args.project, only_reel=args.reel)
    except FileNotFoundError as exc:
        parser.error(str(exc))
    if args.reel is not None and not state["reels"]:
        try:
            raise UnknownReel(
                what=f"no reel {args.reel!r} is recorded for this project",
                why="the reel number names nothing the builds wrote",
                fix="run `ren reels <project>` to list the recorded reels",
            )
        except RenRefusal as refused:
            print(refused.render(), file=__import__("sys").stderr)
            return REFUSAL_EXIT_CODE
    if args.json:
        print(json.dumps(state, indent=2, ensure_ascii=False, default=str))
    else:
        if not state["reels"]:
            print("No reels recorded.")
        for row in state["reels"]:
            print(format_line(row))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
