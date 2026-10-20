"""`ren capabilities`: what Ren can do, by capability id, on THIS machine.

The captain's principle: *"Don't make the model responsible for
understanding the internal machinery of editing. Make the machinery
expose clear, deterministic, composable capabilities that the model can
reason about."*  The two machine-discoverable surfaces that answer it
were on the Resolve side only (`resolve-axi capabilities`) and on the
operation side only (`ren ops`).  Nothing answered it for the EDITOR:
which capabilities exist, what each one is for, what it needs, and
whether this machine can perform it right now.

This module is that answer, and it is ONE module so there is one
catalog rather than two that drift:

    the registry      `library/tools/capabilities.py` - the derived
                      `CapabilitySpec` view of the operation registry
                      (identity, scopes, executor, cost, requirements)
    the needs         `library/tools/machine_needs.py` - what each
                      capability requires of a machine, and what it
                      degrades without
    the verdict       `ren doctor`'s own checks, judged by
                      `machine_needs.availability` - the same verdict
                      `ren doctor --for <id>` gives, read from the same
                      place

`ren ops` remains the OPERATION view (what a scoped run can be asked to
do, and at what scope).  This is the CAPABILITY view: what the engine
can be asked to do at all, including the capabilities a `ren` verb
serves outside the operation registry.

Read-only.  Nothing here starts a model, writes a project, or touches
Resolve beyond the same read-only probe `ren doctor` already runs.

    ren capabilities                 every capability id and its verdict
    ren capabilities <id>           one capability in full
    ren capabilities --check         measure every capability here
    ren capabilities --json          the same, as a structure
"""

from __future__ import annotations

import argparse
import json
import sys

from library.tools import capability_guidance as guidance_module

#: The capability ids a `ren` verb serves outside the operation registry.
#: `machine_needs` owns the table and the needs; the summary is the
#: verb's own, read from the one verb registry, so a verb that changes
#: its summary changes this one.
FRONT_DOOR = "front_door"

#: A capability the operation registry owns.
CAPABILITY = "capability"

#: The verdict of a capability nobody measured on this machine.
NOT_MEASURED = "not_measured"
"""The catalog with no probe behind it says this, rather than claiming
a capability is available on a machine it never asked."""


class _View:
    """One pass over the registries, so a catalog costs one derivation.

    The registry's own view (`capabilities.all()`) derives requirements
    from every manifest, so it is built once here and read by every
    row rather than once per row.
    """

    def __init__(self, checks=None):
        from library.tools import capabilities
        self.checks = checks
        self.specs = {spec.id: spec for spec in capabilities.all()}
        self._records = None
        self._supported = None

    @property
    def measured(self) -> bool:
        """Whether any check answered anything on this machine.

        An empty check list is the same as no check list: nothing was
        probed, so nothing may be claimed.
        """
        return bool(self.checks)

    def ids(self) -> tuple:
        """Every capability id: the registry's, plus the front door's."""
        from library.tools import machine_needs
        ids = list(self.specs)
        for capability_id in machine_needs.FRONT_DOOR:
            if capability_id not in self.specs:
                ids.append(capability_id)
        return tuple(ids)

    def failing(self) -> tuple:
        """`(failing, undetermined)` need ids, from the checks.

        An undetermined need is not a failing one: a check that could
        not run is not a measured absence, and judging it as one is
        what made the worker manifest advertise a capable machine as
        missing macOS.
        """
        failing, undetermined = set(), set()
        for check in self.checks or ():
            if not check.need:
                continue
            if check.undetermined:
                undetermined.add(check.need)
            elif not check.ok:
                failing.add(check.need)
        return failing, undetermined

    def covered_needs(self) -> set:
        """The needs some check answered."""
        return {check.need for check in self.checks or () if check.need}

    def verdict(self, capability_id: str) -> dict:
        """The capability's verdict on this machine.

        A required need NO check answered is UNDETERMINED, not
        present: the verdict of a capability whose needs were not
        probed is unknown, and reading it as available is how a
        capability looks supported on a machine nobody measured.
        """
        from library.tools import machine_needs
        if not self.measured:
            return {"verdict": NOT_MEASURED, "missing": [], "degraded": {},
                    "undetermined": []}
        failing, undetermined = self.failing()
        try:
            needs = machine_needs.needs_of(capability_id)
        except machine_needs.UnknownCapability:
            return {"verdict": NOT_MEASURED, "missing": [], "degraded": {},
                    "undetermined": []}
        unmeasured = {need for need in needs.requires
                      if need not in self.covered_needs()}
        verdict, missing, degraded, undetermined_missing = \
            machine_needs.availability(capability_id, failing,
                                       undetermined | unmeasured)
        return {"verdict": verdict, "missing": list(missing),
                "degraded": dict(degraded),
                "undetermined": list(undetermined_missing)}

    def supported(self) -> list:
        """Every capability this machine can perform, in registry order."""
        if not self.measured:
            return []
        if self._supported is not None:
            return self._supported
        supported = [capability_id for capability_id in self.ids()
                     if self.verdict(capability_id)["verdict"] == "available"]
        self._supported = supported
        return supported

    def base_record(self, capability_id: str) -> dict:
        """One capability's record, without the guidance."""
        from library.tools import capabilities
        spec = self.specs.get(capability_id)
        if spec is not None:
            body = capabilities.spec_to_dict(spec)
            kind = CAPABILITY
            summary = body["summary"]
            scopes = body["scopes"]
        else:
            command = _front_door()[capability_id]
            body = {}
            kind = FRONT_DOOR
            summary = _front_door_summary(command)
            scopes = []
        verdict = self.verdict(capability_id)
        return {
            "id": capability_id,
            "kind": kind,
            "summary": summary,
            "scopes": scopes,
            "executor": body.get("executor"),
            "cost_class": body.get("cost_class"),
            "cost_basis": body.get("cost_basis"),
            "legacy": body.get("legacy"),
            "requires": body.get("requires", []),
            "produces": body.get("produces", []),
            "consumes": body.get("consumes", []),
            "assumes_machine": body.get("assumes_machine", []),
            "assumes_outside": body.get("assumes_outside", []),
            "needs_model_answer": body.get("needs_model_answer", False),
            "artifact_areas": body.get("artifact_areas", []),
            "needs": _needs(capability_id),
            "verdict": verdict["verdict"],
            "missing": verdict["missing"],
            "degraded": verdict["degraded"],
            "undetermined": verdict["undetermined"],
            "how_to_run": _how_to_run(capability_id, spec),
            "guidance": None,
        }

    def records(self) -> list:
        """Every capability's record, with its guidance attached."""
        if self._records is not None:
            return self._records
        rows = [self.base_record(capability_id) for capability_id in self.ids()]
        by_id = {row["id"]: row for row in rows}
        for row in rows:
            row["guidance"] = _guidance_for(row, self, by_id)
        self._records = rows
        return rows

    def record(self, capability_id: str) -> dict:
        """One capability's record, with its guidance attached."""
        for row in self.records():
            if row["id"] == capability_id:
                return row
        raise KeyError(capability_id)


def _front_door() -> dict:
    """The front-door capability ids, with the verb that serves each."""
    from library.tools import machine_needs
    return dict(machine_needs.FRONT_DOOR)


def _verb_summaries() -> dict:
    """`{verb name: summary}` off the one verb registry."""
    from ren.commands import VERBS
    return {verb.name: verb.summary for verb in VERBS}


def _front_door_summary(command: str) -> str:
    """The front-door capability's summary, from the verbs it names.

    `machine_needs.FRONT_DOOR` says which verbs serve a front-door
    capability ("ren status / ren info / ren check"); the summary is
    read off those verbs' own rows, so it cannot drift from `ren
    --help`.
    """
    summaries = _verb_summaries()
    parts = []
    for token in command.split():
        if token.startswith("ren") and token != "ren":
            continue
        if token in summaries:
            parts.append(summaries[token])
    if not parts:
        return command
    seen: list = []
    for part in parts:
        if part not in seen:
            seen.append(part)
    return "; ".join(seen)


def _needs(capability_id: str) -> dict:
    """What the capability requires, and what it degrades without."""
    from library.tools import machine_needs
    try:
        needs = machine_needs.needs_of(capability_id)
    except machine_needs.UnknownCapability:
        return {"required": [], "degrades": {}}
    return {"required": list(needs.requires),
            "degrades": dict(needs.degrades)}


def _how_to_run(capability_id: str, spec) -> list:
    """How this capability is actually run, as the commands that do it."""
    front = _front_door()
    if capability_id in front:
        return [front[capability_id]]
    if spec is None:
        return []
    node = spec.legacy.node_id if spec.legacy else ""
    run = []
    if node:
        run.append(f"ren edit <project> --only {node}")
    run.append(f"bin/vep -m library.tools.operations {capability_id} "
               f"--project <project>")
    return run


def _why_for(row: dict) -> str:
    """Why a capability is not simply available here."""
    if row["missing"]:
        return ("the needs it refuses without are missing: "
                + ", ".join(row["missing"]))
    if row["undetermined"]:
        return ("the needs behind it could not be measured: "
                + ", ".join(row["undetermined"]))
    if row["degraded"]:
        return ("an optional need it runs without is missing: "
                + ", ".join(row["degraded"]))
    return "this machine was never measured for it"


def _guidance_for(row: dict, view: _View, by_id: dict) -> dict | None:
    """Guidance for a capability this machine cannot simply perform.

    None when the capability is available and nothing degrades: there
    is nothing to guide.  Otherwise the record names what is missing,
    what still works, and what to run instead.
    """
    if row["verdict"] == "available" and not row["degraded"]:
        return None
    if row["verdict"] == "unavailable":
        status = guidance_module.UNAVAILABLE
        what = (f"this Mac cannot perform {row['id']} yet: "
                f"{', '.join(row['missing'])} is missing.")
        fix = (f"run `ren doctor --for {row['id']}` for what each missing "
               f"need is and how to get it.")
    elif row["verdict"] == "unknown":
        status = guidance_module.UNDETERMINED
        what = (f"this Mac could not be measured for {row['id']}: "
                f"{', '.join(row['undetermined'])} could not be determined.")
        fix = ("run `ren doctor` and read the UNKNOWN rows; an "
               "undeterminable answer is not a measured absence.")
    elif row["verdict"] == "degraded":
        status = guidance_module.DEGRADED
        what = (f"{row['id']} runs, degraded: "
                f"{', '.join(row['degraded'])} is missing.")
        fix = (f"run `ren doctor --for {row['id']}` for what each missing "
               f"need costs.")
    elif row["verdict"] == NOT_MEASURED:
        # Nothing was probed, so there is nothing to guide about: the
        # verdict says not_measured and the reader knows why.
        return None
    else:  # pragma: no cover - the verdicts above are exhaustive
        status = guidance_module.UNAVAILABLE
        what = f"{row['id']} is not available here."
        fix = f"run `ren doctor --for {row['id']}`."
    return guidance_module.guidance(
        status, row["id"], what, _why_for(row), fix,
        next_action=f"ren capabilities {row['id']}",
        alternatives=_alternatives(row["id"], by_id, exclude=row["id"]),
        supported=view.supported(),
    )


def _alternatives(asked: str, by_id: dict, exclude: str = "") -> list:
    """The nearest real capabilities to an id that is not one.

    Each row carries its own verdict, so a caller is told not only
    what exists but whether this machine can do it.  `exclude` drops
    the asked id itself: naming the capability the caller already named
    as an "alternative" is noise.
    """
    candidates = [name for name in by_id if name != exclude]
    nearest = guidance_module.closest_ids(asked, candidates)
    rows = []
    for capability_id in nearest:
        row = by_id[capability_id]
        rows.append({"id": capability_id, "summary": row["summary"],
                     "verdict": row["verdict"]})
    return rows


def catalog(checks=None) -> dict:
    """Every capability, with this machine's verdict."""
    view = _View(checks=checks)
    rows = view.records()
    return {"count": len(rows), "capabilities": rows}


def known_ids() -> tuple[str, ...]:
    """Every declared capability id, in the catalog's stable order."""
    return _View(checks=None).ids()


def record(capability_id: str, checks=None) -> dict:
    """One capability, in full.

    `checks` is the doctor's own check list; it is a parameter so a
    test can pin the verdict against a fixture machine instead of the
    one it runs on.
    """
    return _View(checks=checks).record(capability_id)


def unknown_guidance(asked: str, checks=None, view: _View | None = None) -> dict:
    """Structured guidance for an id Ren does not hold.

    The answer names the nearest real ids and the whole available set,
    so a caller is never left with "unknown" and nothing else.
    """
    view = view or _View(checks=checks)
    by_id = {row["id"]: row for row in view.records()}
    alternatives = _alternatives(asked, by_id)
    nearest = ", ".join(row["id"] for row in alternatives) or "none"
    return guidance_module.guidance(
        guidance_module.UNKNOWN, asked,
        f"{asked!r} is not a capability Ren holds.",
        ("the capability vocabulary is the operation registry's ids plus "
         "the capabilities a ren verb serves; the nearest ids are listed "
         "below"),
        f"ask for one of the nearest ids instead: {nearest}",
        next_action="ren capabilities",
        alternatives=alternatives,
        supported=view.supported(),
    )


def emit_reference() -> str:
    """The capability catalog as a reference document.

    GENERATED, never hand-written: a hand-written roster of what Ren
    can do is a second list, and a second list drifts - which is how
    the generated `pipeline_operations` skill shipped missing two
    operations that the registry held.

    Every row is derived: the id and summary from the operation
    registry, the scope and cost from the capability registry, the
    needs from `machine_needs`, and the front-door rows from the verb
    registry.  Nothing here is measured on a machine, so the document
    is the same on every machine.
    """
    view = _View(checks=None)
    rows = view.records()
    lines = [
        "# Ren capabilities",
        "",
        ("GENERATED from `library/tools/capability_discovery.py` - do not "
         "edit; run `python3 -m library.tools.capability_discovery "
         "--emit-reference` and write the result here."),
        "",
        ("Every capability id the engine holds, what it is for, where it "
         "runs, what it costs and what it needs. `ren capabilities` "
         "answers the same questions for the machine you are on, with "
         "this machine's verdict; `ren capabilities <id>` describes one "
         "in full, and `ren doctor --for <id>` says what is missing."),
        "",
        f"{len(rows)} capabilities.",
        "",
    ]

    lines += ["## The registry's capabilities", "",
              "| capability | scope | cost | what it does |",
              "|---|---|---|---|"]
    for row in rows:
        if row["kind"] != CAPABILITY:
            continue
        lines.append(f"| `{row['id']}` | {', '.join(row['scopes'])} | "
                     f"{row['cost_class']} | {row['summary']} |")

    lines += ["", "## The capabilities a `ren` verb serves", "",
              "| capability | the command | what it does |",
              "|---|---|---|"]
    for row in rows:
        if row["kind"] != FRONT_DOOR:
            continue
        lines.append(f"| `{row['id']}` | `{row['how_to_run'][0]}` | "
                     f"{row['summary']} |")

    lines += ["", "## What each capability needs", "",
              "| capability | required | runs without, degraded |",
              "|---|---|---|"]
    for row in rows:
        required = ", ".join(f"`{need}`" for need in row["needs"]["required"])
        degrades = "; ".join(f"`{need}` - {why}" for need, why
                             in row["needs"]["degrades"].items())
        lines.append(f"| `{row['id']}` | {required} | {degrades or '-'} |")

    lines += ["", "## How each capability is run", "",
              "| capability | the command |", "|---|---|"]
    for row in rows:
        commands = "<br>".join(f"`{command}`" for command in row["how_to_run"])
        lines.append(f"| `{row['id']}` | {commands} |")

    lines += ["", "## The vocabulary", "",
              ("A capability id is dotted (`reel.build`). `ren capabilities "
               "<id>` accepts the dotted spelling, the underscored one "
               "and the plain one, and an id it does not hold comes back "
               "with the nearest real ids rather than a bare 'unknown'."),
              ""]
    # The trailing newline is part of the document, so the checked-in
    # file is the emitter's return value byte for byte - the same
    # convention `operations.emit_resolve_axi_references` uses.
    return "\n".join(lines) + "\n"


def _run_checks() -> list:
    """The doctor's own checks - the same ones `ren doctor` runs."""
    from ren import doctor
    return doctor.run_checks()


def _checks_for(capability_id: str) -> list:
    """Only the checks this capability's needs are answered by.

    The same narrowing `ren doctor --for <id>` does: a capability that
    needs no Resolve does not pay for the Resolve probe, and one that
    does pays for it alone rather than for the whole machine's checks.
    """
    from library.tools import machine_needs
    from ren import doctor
    try:
        needs = machine_needs.needs_of(capability_id)
    except machine_needs.UnknownCapability:
        return []
    wanted = set(needs.requires) | set(needs.degrades)
    return doctor.run_checks(needs=wanted)


def render_catalog(payload: dict) -> str:
    """The catalog as prose: one row per capability."""
    rows = payload["capabilities"]
    width = max((len(row["id"]) for row in rows), default=4)
    lines = [f"{payload['count']} capabilities", ""]
    for row in rows:
        scope = ",".join(row["scopes"]) or row["kind"]
        lines.append(f"{row['id'].ljust(width)}  {scope:<12} "
                     f"{(row['cost_class'] or '-'):<6} "
                     f"{row['verdict']:<12} {row['summary']}")
    return "\n".join(lines)


def render_record(row: dict) -> str:
    """One capability as prose."""
    lines = [f"{row['id']}  ({row['verdict']})", "", f"  {row['summary']}", ""]
    lines.append(f"  kind:     {row['kind']}")
    if row["scopes"]:
        lines.append(f"  scope:    {', '.join(row['scopes'])}")
    if row["cost_class"]:
        lines.append(f"  cost:     {row['cost_class']} - {row['cost_basis']}")
    if row["legacy"]:
        lines.append(f"  legacy:   {row['legacy']['node_id']} "
                     f"({row['legacy']['process']})")
    if row["executor"]:
        lines.append(f"  executor: {row['executor']['path']}"
                     f"::{row['executor']['attr']}")
    if row["needs_model_answer"]:
        lines.append("  model:    needs a host model's answer")
    if row["requires"]:
        lines.append(f"  requires: {', '.join(row['requires'])}")
    if row["produces"]:
        lines.append(f"  produces: {', '.join(row['produces'])}")
    if row["consumes"]:
        lines.append(f"  consumes: {', '.join(row['consumes'])}")
    if row["needs"]["required"]:
        lines.append("  needs:")
        for need in row["needs"]["required"]:
            lines.append(f"    {need}")
    if row["needs"]["degrades"]:
        lines.append("  runs without, degraded:")
        for need, why in row["needs"]["degrades"].items():
            lines.append(f"    {need} - {why}")
    if row["missing"]:
        lines.append(f"  missing:  {', '.join(row['missing'])}")
    if row["undetermined"]:
        lines.append(f"  unknown:  {', '.join(row['undetermined'])}")
    if row["how_to_run"]:
        lines.append("  run:")
        for command in row["how_to_run"]:
            lines.append(f"    {command}")
    guidance = row.get("guidance")
    if guidance:
        lines.append("")
        lines.append(guidance_module.render(guidance))
    return "\n".join(lines)


def main(argv=None) -> int:
    # Same default as the front door, for direct `-m` use: under a
    # harness the host parses `REN_RESULT` instead of prose (LE-03).
    from ren import hosts
    hosts.ensure_harness_output_env()
    parser = argparse.ArgumentParser(
        prog="ren capabilities",
        description=("What Ren can do, by capability id, and whether this "
                     "machine can do it now."))
    parser.add_argument("capability", nargs="?",
                        help="one capability id to describe")
    parser.add_argument("--json", action="store_true",
                        help="print the answer as JSON")
    parser.add_argument("--check", action="store_true",
                        help="measure every capability on this machine "
                             "(slow: it runs the same probes `ren doctor` "
                             "runs)")
    parser.add_argument("--emit-reference", action="store_true",
                        help="print the generated capability reference")
    args = parser.parse_args(argv)

    if args.emit_reference:
        # Written, not printed: `print` would add a second trailing
        # newline, and the checked-in file must be the emitter's bytes
        # exactly, so the documented command reproduces it.
        sys.stdout.write(emit_reference())
        return 0

    if args.capability:
        view = _View(checks=None)
        if args.capability not in view.ids():
            guidance = unknown_guidance(args.capability, view=view)
            if args.json:
                print(json.dumps({"capability": args.capability,
                                  "guidance": guidance}, indent=2))
            else:
                print(guidance_module.render(guidance))
            return 2
        row = _View(checks=_checks_for(args.capability)).record(args.capability)
        if args.json:
            print(json.dumps({"capability": row}, indent=2))
        else:
            print(render_record(row))
        return 0

    checks = _run_checks() if args.check else None
    payload = catalog(checks=checks)
    if args.json:
        print(json.dumps(payload, indent=2))
    else:
        print(render_catalog(payload))
    return 0


if __name__ == "__main__":
    # ONE place renders every refusal on this verb's path, in the same
    # shape as the front door. With `REN_OUTPUT=json` both a refusal
    # and an uncaught error end with one final `REN_RESULT` line (LE-03).
    from library.tools.ren_refusal import (
        REFUSAL_EXIT_CODE,
        RenRefusal,
        emit_refusal_result,
        report_unexpected,
    )
    try:
        raise SystemExit(main())
    except RenRefusal as refused:
        print(refused.render(), file=sys.stderr)
        emit_refusal_result(refused)
        sys.exit(REFUSAL_EXIT_CODE)
    except Exception as exc:  # noqa: BLE001 - unexpected errors are data too
        sys.exit(report_unexpected(exc))
