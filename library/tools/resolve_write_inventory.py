"""Generate the operation-to-Resolve execution inventory from edit_algebra."""
from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from library.tools import edit_algebra

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "RESOLVE_WRITE_INVENTORY.md"
START = "<!-- BEGIN GENERATED EDITORIAL OPERATION INVENTORY -->"
END = "<!-- END GENERATED EDITORIAL OPERATION INVENTORY -->"

BACKEND_ENTRYPOINTS = {
    "edit_patch": "library/tools/edit_patch.py",
    "feedback_patch_apply": "library/tools/feedback_patch_apply.py",
    "reel_touchup": "library/tools/reel_touchup.py",
    "composed_edit": "library/tools/composed_edit.py",
    "resolve_axi": "library/tools/resolve_axi.py",
    "reel_append_placement": "library/tools/reel_append_placement.py",
    "reel_otio_placement": "library/tools/reel_otio_placement.py",
}

BACKEND_CALLERS = {
    "edit_patch": (
        "library/tools/resolved/jobs.py", "library/tools/undo_journal.py",
        "library/tools/assignment_revision.py",
        "library/tools/feedback_patch_apply.py",
        "library/tools/resolve_axi_core_commands.py",
        "library/tools/resolve_swapback.py",
    ),
    "feedback_patch_apply": (
        "library/tools/feedback_patch_flow.py", "library/tools/feedback_patch.py",
    ),
    "reel_touchup": (
        "manage_project.py", "library/tools/undo_journal.py",
        "library/tools/accepted_edit_replay.py", "library/tools/revise.py",
    ),
    "composed_edit": (
        "library/tools/reel_touchup.py", "library/tools/master_touchup.py",
        "library/tools/undo_journal.py", "library/tools/editor_edit_carry.py",
    ),
    "resolve_axi": ("library/tools/resolve_axi_core_commands.py",),
    "reel_append_placement": (
        "library/tools/reel_build.py", "library/steps/step_6_01_render/resolve_build_timeline.py",
    ),
    "reel_otio_placement": ("library/tools/reel_build.py",),
}


def _cell(value: str) -> str:
    return str(value).replace("|", "\\|").replace("\n", " ")


def render_operation_inventory() -> str:
    lines = [
        START,
        "## Editorial operation execution inventory (generated)",
        "",
        "Generated from `edit_algebra.OP_STATUS` by `python3 -m library.tools.resolve_write_inventory --write`. `implemented` means a code path exists; production routing additionally requires complete operation/backend qualification. Unrecorded qualification fields are gaps, not wildcards.",
        "",
        "Current live qualification status: existing evidence paths remain visible, but incomplete records do not qualify a backend. New Resolve qualification runs are deferred under the captain's standing no-local-testing rule; this inventory does not claim a new live run.",
        "",
        "| operation | classification | entry point / backend | production callers | inputs | media | timeline conditions | qualification | verification | recovery | preservation limits | performance evidence |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for op_id in sorted(edit_algebra.OP_STATUS):
        status = edit_algebra.OP_STATUS[op_id]
        if status.backends:
            backend_text = []
            for backend in status.backends:
                entrypoint = BACKEND_ENTRYPOINTS.get(backend.backend, "unmapped")
                refs = ", ".join(backend.qualification_evidence) or "none"
                implementation_refs = ", ".join(backend.implementation_evidence) or entrypoint
                qualification = (
                    f"{backend.implementation_status}; qualification="
                    f"{backend.qualification_status}; Resolve="
                    f"{backend.resolve_version or 'unrecorded'}; evidence={refs}; "
                    f"media={','.join(backend.qualification_media_types) or 'unrecorded'}; "
                    f"timeline={','.join(backend.qualification_timeline_conditions) or 'unrecorded'}; "
                    f"preservation={','.join(backend.preservation_limits) or 'unrecorded'}; "
                    f"visual={','.join(backend.visual_verification) or 'not specified'}; "
                    f"audio={','.join(backend.audio_verification) or 'not specified'}; "
                    f"recovery={backend.recovery_qualification}")
                backend_text.append(
                    f"`{entrypoint}` ({backend.backend}; code evidence={implementation_refs}; {qualification})")
            entrypoint = "; ".join(backend_text)
        elif status.workflow_evidence:
            entrypoint = "; ".join(status.workflow_evidence)
        else:
            entrypoint = "none registered"
        callers = "; ".join(sorted({
            caller
            for row in status.backends
            for caller in BACKEND_CALLERS.get(row.backend, (row.backend,))
        })) or "none"
        inputs = ", ".join(status.param_schema["required"]) or "none (planning operation)"
        conditions = "; ".join(status.timeline_conditions) or "unrecorded"
        qualification = status.qualification_status
        verification = status.verification_contract
        recovery = str(status.inverse_recovery.get("op") or "none")
        proven = [row.backend for row in status.backends
                  if edit_algebra.proven_inverse(op_id, row.backend)]
        recovery += "; proven on " + (", ".join(proven) if proven else "no backend")
        preservation = "; ".join(
            f"{row.backend}: {', '.join(row.preservation_limits) or 'unrecorded'}"
            for row in status.backends) or "unrecorded"
        cost = status.measured_cost or {"status": "unmeasured"}
        wall = f"{cost['wall_s']}s" if "wall_s" in cost else "not recorded"
        resolve_time = (f"{cost['resolve_ops_s']}s"
                        if "resolve_ops_s" in cost else "not recorded")
        performance = (f"{cost.get('scenario', cost.get('status', 'unmeasured'))}; "
                       f"wall={wall}; Resolve={resolve_time}")
        values = (op_id, status.implementation_status, entrypoint, callers,
                  inputs, ", ".join(status.media_types), conditions, qualification,
                  verification, recovery, preservation, performance)
        lines.append("| " + " | ".join(_cell(value) for value in values) + " |")
    lines.append(END)
    return "\n".join(lines)


def write_inventory(path: Path = DOC) -> None:
    text = path.read_text(encoding="utf-8")
    generated = render_operation_inventory()
    registry = ROOT / "tests" / "contracts" / "resolve_write_registry.json"
    rows = json.loads(registry.read_text(encoding="utf-8"))
    counts = {method: sum(row[2] == method for row in rows)
              for method in ("AppendToTimeline", "SetCurrentTimeline")}
    # The sentence's NUMBERS move with the registry; the sentence itself
    # is the document's, so it is matched and rewritten in place. The old
    # spelling replaced a sentence the document no longer carried, so the
    # counts never moved.
    counts_sentence = re.compile(
        r"(Current-tree counts from the static-call registry: )`\d+`"
        r"( `AppendToTimeline` sites and )`\d+`( `SetCurrentTimeline` sites)")
    text, replaced = counts_sentence.subn(
        rf"\g<1>`{counts['AppendToTimeline']}`\g<2>"
        rf"`{counts['SetCurrentTimeline']}`\g<3>", text)
    if not replaced:
        text = text.replace(
            "Current-tree counts from the static-call registry:",
            f"Current-tree counts from the static-call registry: "
            f"`{counts['AppendToTimeline']}` `AppendToTimeline` sites and "
            f"`{counts['SetCurrentTimeline']}` `SetCurrentTimeline` sites"
            ".", 1)
    if START in text and END in text:
        before, rest = text.split(START, 1)
        _old, after = rest.split(END, 1)
        text = before + generated + after
    else:
        text = text.rstrip() + "\n\n" + generated + "\n"
    path.write_text(text, encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--write", action="store_true", help="update the inventory document")
    args = parser.parse_args()
    if args.write:
        write_inventory()
    else:
        print(render_operation_inventory())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
