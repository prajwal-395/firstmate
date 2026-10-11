"""Thin `ren project-model` entry point over the headless engine adapters.

Audit gap G8: ``project_model_effectcraft.py`` ships an argparse ``main``
but only ``ren qualification pm-e2e`` was reachable, so scouts drove
modules by path and the capability matrix had no user-facing route.
This module is that route: ``ren project-model project --engine
filmcraft|effectcraft --out <plan.json>`` plans the model onto the named
engine, writes the plan, and prints a summary - and it executes the
plan against the pinned headless CLI only with ``--execute``.

Read-only until explicit: the plan path runs no engine subprocess, takes
no resolve slot, and writes nothing but ``--out``. The project itself is
opened read-only (registry lookup with no format migration, like
``project_work_state``); ``--execute`` additionally writes the engine
project/receipt files it names in the plan and nothing else.

No second implementation of either adapter lives here. The FilmCraft
half plans through the real Rust adapter
(``native/ren-filmcraft/src/adapter.rs``) exposed on the ``ren_native``
extension: the projection, the per-edit translation, the receipt
reconciliation, the media verification and the operation-to-command
mapping all run there, so a plan this verb prints and the run the
driver executes cannot drift. A stale or missing extension is refused,
never worked around. The EffectCraft half delegates to the real
projector (``project_model_effectcraft.project_to_request``); execution
mirrors ``project_model_qualification._ec_execute`` (run, rgba render,
ingest). No axi-style reads: this verb plans and executes engine
work, it never serves live-project state.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

from library.tools.ren_refusal import REFUSAL_EXIT_CODE, RenRefusal

FILMCRAFT_REVISION = "98ba9acbcedcbb630ba2a07b782176b66835edd2"
FILMCRAFT_VERSION = "0.5.0"
EFFECTCRAFT_REVISION = "813c7c4650f0d4c805da620c8d5837c9600ad2e3"
EFFECTCRAFT_VERSION = "0.7.0"

FILMCRAFT_FUNCTIONS = (
    "filmcraft_plan_projection",
    "filmcraft_plan_ren_edit",
    "filmcraft_describe_operation",
    "filmcraft_reconcile_clip_ids",
    "filmcraft_verify_media",
    "filmcraft_import_operation",
    "filmcraft_placement_operations",
)
"""The ``ren_native`` entry points the FilmCraft half plans through."""

EXPORT_FORMATS = {"h264": "h264", "prores": "pro_res", "wav": "wav"}
"""User-facing export names to ``ExportFormat`` JSON (serde snake_case)."""


class ProjectModelCliRefused(RenRefusal, RuntimeError):
    """This verb refused before running engine work."""

    CODE = "REN_PM_CLI_REFUSED"
    USER_MESSAGE = ("I couldn't safely project this model. Nothing ran "
                    "and nothing changed; fix what follows and retry.")
    RECOVERY_CLASS = "blocked"


def _refuse(what: str, why: str, fix: str, code: str = "") -> ProjectModelCliRefused:
    return ProjectModelCliRefused(what=what, why=why, fix=fix,
                                 code=code or ProjectModelCliRefused.CODE)


_ADAPTER_CODE = re.compile(r"FilmCraft projection refused \(([A-Za-z0-9_]+)\)")


def _adapter_code(message: str) -> str | None:
    """The adapter's own refusal code out of its Display text, if any."""
    match = _ADAPTER_CODE.search(message or "")
    return match.group(1) if match else None


def _refuse_from_adapter(action: str, exc: Exception) -> ProjectModelCliRefused:
    """Refuse with the adapter's own words and code; nothing ran."""
    message = str(exc)
    code = _adapter_code(message)
    return _refuse(f"{action}: {message[:500]}",
                   "the pinned FilmCraft adapter refused the model",
                   "fix the model per the named code in native/ren-filmcraft/src/adapter.rs"
                   + (f" (code {code})" if code else ""),
                   code=code or "REN_PM_CLI_ADAPTER_REFUSED")


# ── Project + model loading (read-only) ──────────────────────────


def resolve_project_root(ref: str) -> Path:
    """The project's root directory, read-only: no format migration.

    Mirrors ``project_work_state``: a filesystem path resolves in place,
    otherwise the registry slug index answers. Raises
    :class:`ProjectModelCliRefused` when nothing answers.
    """
    from library.tools.project_registry import resolve_project_path, scan_projects

    yaml_path = resolve_project_path(ref)
    if yaml_path is not None:
        return yaml_path.parent
    for config in scan_projects():
        if config.slug == ref:
            return Path(config.project_root)
    raise _refuse(f"no Ren project found for {ref!r}",
                  "neither a project path nor a registry slug answered",
                  "pass a project directory holding project.yaml, or a slug from `ren projects`",
                  code="REN_PM_CLI_NO_PROJECT")


def load_model(project_root: Path, model_arg: str | None) -> tuple[dict[str, Any], str]:
    """The canonical model plus where it came from.

    ``--model`` names an explicit model JSON (validated). Otherwise the
    project's own model store answers; a project with no store refuses
    with the init command rather than an empty model.
    """
    from library.tools.project_model import read_project_model

    if model_arg:
        path = Path(model_arg).expanduser()
        try:
            return read_project_model(path), str(path)
        except ValueError as exc:
            raise _refuse(f"cannot read project model {path}",
                          str(exc)[:400],
                          "pass a valid Project Model JSON, or omit --model to use the project's store",
                          code="REN_PM_CLI_BAD_MODEL") from exc
    from library.tools.project_model_store import load_project_model as load_store

    try:
        revision, model = load_store(project_root)
    except (FileNotFoundError, ValueError) as exc:
        raise _refuse(f"no canonical Project Model in {project_root}",
                      str(exc)[:400],
                      "commit one with `python3 -m library.tools.project_model_store init <project> <model.json>`, or pass --model <model.json>",
                      code="REN_PM_CLI_NO_MODEL") from exc
    return model, f"{project_root}/project_model (revision {revision})"


# ── FilmCraft through the real adapter ───────────────────────────
#
# Every function below calls the pinned Rust adapter on ren_native and
# translates only its answers into plan/receipt JSON. No placement
# math, no edit translation, no refusal codes live here.


def filmcraft_native():
    """The ren_native extension with the FilmCraft adapter, or refuse.

    Selected the way production selects it (this tree's per-tree cache
    entry); a missing or stale build is refused, never worked around
    with a Python copy of the adapter.
    """
    from ren.engine_root import find_engine_root

    from library.tools import ren_native_cache

    module = ren_native_cache.select(find_engine_root())
    if module is not None and all(hasattr(module, name) for name in FILMCRAFT_FUNCTIONS):
        return module
    raise _refuse("the ren_native extension for this tree is not built",
                  "the FilmCraft plan runs in the pinned Rust adapter, and no matching build is cached",
                  "rebuild with `ren setup` (scripts/build_ren_native.sh <venv-bin-dir> <cache-dir>), then retry",
                  code="REN_PM_CLI_NO_NATIVE")


def plan_filmcraft_projection(model: dict, native) -> dict:
    """The adapter's own projection for this model, as a dict."""
    try:
        return native.filmcraft_plan_projection(model)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("FilmCraft projection refused", exc) from exc


def classify_filmcraft_edit(model: dict, projection: dict, edit: dict, native) -> tuple[bool, str | None]:
    """Whether --execute can translate this edit, and the adapter's code.

    Planning runs the real translation with placeholder clip ids (every
    placed use maps to id 0): parameter validation is id-agnostic, so
    the verdict is exactly what execution would get.
    """
    dummy_ids = {placement["clip_use_id"]: [0] for placement in projection["placements"]}
    try:
        native.filmcraft_plan_ren_edit(model, projection, edit, dummy_ids, "")
    except Exception as exc:  # noqa: BLE001 - the code is the verdict
        return False, _adapter_code(str(exc)) or "adapter_refused"
    return True, None


def plan_filmcraft(model: dict, native) -> dict:
    """The plan: the adapter's projection plus per-edit executability."""
    projection = plan_filmcraft_projection(model, native)
    edit_rows = []
    for edit in model.get("edits", []):
        executable, code = classify_filmcraft_edit(model, projection, edit, native)
        row: dict[str, Any] = {"id": edit["id"], "operation": edit.get("operation"),
                               "executable": executable}
        if code:
            row["code"] = code
        edit_rows.append(row)
    return {"engine": "filmcraft",
            "adapter_version": projection["adapter_version"],
            "engine_revision": projection["engine_revision"],
            "timeline_clock": projection["timeline_clock"],
            "imports": projection["imports"],
            "placements": projection["placements"],
            "losses": projection["losses"],
            "edits": edit_rows,
            "executed": False}


def translate_filmcraft_edits(model: dict, projection: dict, clip_ids: dict[str, list[int]],
                              project: str, native) -> list[dict]:
    """Engine commands for every model edit, in model order.

    Raises with the adapter's named code before returning anything
    partial: callers translate the whole model before executing any of
    it, so an untranslatable model refuses with nothing mutated.
    """
    commands: list[dict] = []
    for edit in model.get("edits", []):
        try:
            operations = native.filmcraft_plan_ren_edit(
                model, projection, edit, clip_ids, project)
        except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
            raise _refuse_from_adapter(f"edit `{edit.get('id')}` refused", exc) from exc
        for operation in operations:
            try:
                described = native.filmcraft_describe_operation(operation)
            except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
                raise _refuse_from_adapter(
                    f"edit `{edit.get('id')}` describes no engine command", exc) from exc
            commands.append({"command": described["command"],
                             "params": described["params"],
                             "save": described["save"]})
    return commands


# ── Engine CLIs ──────────────────────────────────────────────


def _filmcraft_cli() -> Path:
    import os

    override = os.environ.get("REN_FILMCRAFT_CLI") or os.environ.get("FILMCRAFT_CLI")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise _refuse(f"REN_FILMCRAFT_CLI does not name a file: {candidate}",
                      "the override must point at the pinned CLI",
                      "point REN_FILMCRAFT_CLI at the pinned filmcraft-cli, or unset it for the cache lookup",
                      code="REN_PM_CLI_ENGINE_ABSENT")
    cached = (Path.home() / ".cache/ren/filmcraft" / FILMCRAFT_REVISION
              / "bin" / "filmcraft-cli")
    if cached.is_file():
        return cached
    raise _refuse("pinned FilmCraft CLI is absent",
                  f"no binary at {cached}",
                  "run the upstream intake gate to build the pinned revision, then retry with --execute",
                  code="REN_PM_CLI_ENGINE_ABSENT")


def _effectcraft_cli() -> Path:
    import os

    override = os.environ.get("REN_EFFECTCRAFT_CLI")
    if override:
        candidate = Path(override).expanduser()
        if candidate.is_file():
            return candidate
        raise _refuse(f"REN_EFFECTCRAFT_CLI does not name a file: {candidate}",
                      "the override must point at the pinned CLI",
                      "point REN_EFFECTCRAFT_CLI at the pinned effectcraft-cli, or unset it for the cache lookup",
                      code="REN_PM_CLI_ENGINE_ABSENT")
    cached = (Path.home() / ".cache/ren/effectcraft" / EFFECTCRAFT_REVISION
              / "source/target/release/effectcraft-cli")
    if cached.is_file():
        return cached
    raise _refuse("pinned EffectCraft CLI is absent",
                  f"no binary at {cached}",
                  "run the upstream intake gate to build the pinned revision, then retry with --execute",
                  code="REN_PM_CLI_ENGINE_ABSENT")


def _check_cli_version(cli: Path, expect: str, engine: str) -> None:
    done = subprocess.run([str(cli), "--version"], capture_output=True,
                          text=True, encoding="utf-8", timeout=60, check=False)
    if expect not in (done.stdout + done.stderr):
        raise _refuse(f"{engine} CLI version drifted",
                      (done.stdout + done.stderr).strip()[-200:] or f"exit {done.returncode}",
                      f"re-qualify against pinned {expect}",
                      code="REN_PM_CLI_ENGINE_VERSION")


def _fc_exec(cli: Path, project: Path | None, command: str,
             params: dict, *, save: bool, save_as: Path | None = None) -> Any:
    argv = [str(cli)]
    if save_as is not None:
        argv += ["exec", command, json.dumps(params), "--compact", "--save-as", str(save_as)]
    else:
        if project is not None:
            argv += ["--project", str(project)]
        argv += ["exec", command, json.dumps(params), "--compact"]
        if save:
            argv.append("--save")
    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=600, check=False)
    if done.returncode != 0:
        raise _refuse(f"FilmCraft command {command!r} failed",
                      done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise _refuse(f"FilmCraft command {command!r} answered non-JSON",
                      str(exc),
                      "re-run against the pinned CLI",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND") from exc


def _batch_enabled() -> bool:
    """One batched `run` session per phase, or the legacy per-`exec` loop.

    `REN_FILMCRAFT_BATCH=0` restores the legacy loop: the same commands,
    one process per command. The equivalence proof runs both and diffs the
    inspected timeline, so the hatch is the before/after switch as well as
    the emergency fallback.
    """
    import os

    return os.environ.get("REN_FILMCRAFT_BATCH", "1") != "0"


def _fc_run_batch(cli: Path, project: Path, commands: list[dict],
                  *, save: bool = True) -> list[Any]:
    """Run engine commands through one open backend (`filmcraft-cli run`).

    `commands` carries {"command", "params"} dicts in execution order - the
    same dicts the legacy loop feeds `_fc_exec` one at a time, so the batch
    sends the same bytes the per-`exec` path sent. Every entry becomes one
    `{"id", "params"}` line of a batch script; the CLI answers one
    `{"id", "line", "ok", "result"|"error"}` line per command and stops at
    the first failure (no `--keep-going`: the legacy loop aborts there
    too). A failing command is named by its engine id plus its batch line,
    so per-command error attribution survives the batching.

    Requires a non-empty batch; callers skip empty phases (a model with no
    edits runs no edit session).
    """
    import tempfile

    if not commands:
        raise _refuse("FilmCraft batch names no commands",
                      "the caller skipped no empty batch",
                      "run the batch only for a non-empty phase",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    script_lines = [json.dumps({"id": entry["command"], "params": entry["params"]},
                               ensure_ascii=False)
                    for entry in commands]
    with tempfile.TemporaryDirectory(prefix="ren-filmcraft-batch-") as tmp:
        script = Path(tmp) / "batch.jsonl"
        script.write_text("\n".join(script_lines) + "\n", encoding="utf-8")
        argv = [str(cli), "--project", str(project), "run", str(script), "--compact"]
        if save:
            argv.append("--save")
        done = subprocess.run(argv, capture_output=True, text=True,
                              encoding="utf-8",
                              timeout=300 + 5 * len(commands), check=False)
    if done.returncode != 0:
        raise _refuse("FilmCraft batch run failed",
                      done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    answers: list[Any] = []
    for raw in done.stdout.splitlines():
        if not raw.strip():
            continue
        try:
            answers.append(json.loads(raw))
        except json.JSONDecodeError as exc:
            raise _refuse("FilmCraft batch answered non-JSON",
                          str(exc),
                          "re-run against the pinned CLI",
                          code="REN_PM_CLI_FILMCRAFT_COMMAND") from exc
    # Fail closed: every answered line must pair with its command in order.
    results: list[Any] = []
    for index, entry in enumerate(answers):
        line = index + 1
        command = commands[index]["command"] if index < len(commands) else None
        if not isinstance(entry, dict) or entry.get("id") != command \
                or entry.get("line") != line:
            raise _refuse(f"FilmCraft batch line {line} answered for "
                          f"{entry.get('id') if isinstance(entry, dict) else entry!r}, "
                          f"expected {command!r}",
                          json.dumps(entry, default=str)[:400],
                          "re-run against the pinned CLI",
                          code="REN_PM_CLI_FILMCRAFT_COMMAND")
        if not entry.get("ok"):
            raise _refuse(f"FilmCraft command {command!r} failed "
                          f"(batch line {line} of {len(commands)})",
                          str(entry.get("error") or "")[:800]
                          or json.dumps(entry, default=str)[:800],
                          "inspect the engine output; the model is unchanged",
                          code="REN_PM_CLI_FILMCRAFT_COMMAND")
        results.append(entry.get("result"))
    if len(results) != len(commands):
        missing = commands[len(results)]["command"]
        raise _refuse(f"FilmCraft batch stopped after {len(results)} of "
                      f"{len(commands)} commands; {missing!r} never answered",
                      done.stderr.strip()[-400:] or "the CLI ended mid-batch",
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    return results


def _describe(native, operation: dict) -> dict:
    """The engine invocation for one adapter operation."""
    try:
        described = native.filmcraft_describe_operation(operation)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("operation describes no engine command", exc) from exc
    return {"command": described["command"], "params": described["params"],
            "save": described["save"]}


def _placement_clips(use_id: str, placed: Any) -> list[int]:
    """The minted clip ids from one place receipt, or refuse like before."""
    clips = placed.get("clips") or []
    if not clips:
        raise _refuse(f"FilmCraft placed no clips for `{use_id}`",
                      json.dumps(placed)[:400],
                      "check the engine timeline state",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    return list(clips)


def execute_filmcraft(model: dict, plan: dict, *, project_root: Path,
                      out: Path, media_root: Path | None,
                      filmcraft_project: Path | None,
                      export_path: Path | None,
                      export_format: str, native) -> dict:
    """Build the adapter-planned timeline on the pinned FilmCraft CLI."""
    projection = {key: plan[key] for key in
                  ("adapter_version", "engine_revision", "timeline_clock",
                   "imports", "placements", "losses")}
    project = filmcraft_project or (out.parent / f"{out.stem}.fcproj")
    project_str = str(project)
    if project.suffix != ".fcproj":
        raise _refuse(f"FilmCraft project path must use the `.fcproj` extension: {project}",
                      "Save As assigns the project name from the file stem",
                      "rename the project path to end in .fcproj",
                      code="REN_PM_CLI_BAD_PROJECT_PATH")
    if project.exists():
        raise _refuse(f"refusing to replace existing project {project}",
                      "an execute run never overwrites an engine project",
                      "remove the file or pass --filmcraft-project at a fresh path",
                      code="REN_PM_CLI_PROJECT_EXISTS")

    # Fail closed before any mutation: the create description, the media
    # check and every edit translate now, with placeholder ids; the run
    # below only substitutes reconciled clip ids for the same commands.
    # The native treatments resolve here too, for the same reason: a
    # malformed series_look/tv_frame declaration refuses before an
    # engine project exists, and the post-export composite reuses this
    # plan so one declaration cannot read two ways.
    create = _describe(native, {"kind": "create_project",
                                "path": project_str, "name": project.stem})
    media_dir = str(media_root or project_root)
    try:
        resolved = native.filmcraft_verify_media(projection, media_dir)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("media verification refused", exc) from exc
    dummy_ids = {placement["clip_use_id"]: [0] for placement in projection["placements"]}
    translate_filmcraft_edits(model, projection, dummy_ids, project_str, native)
    treatments_plan = _plan_native_treatments(project_root)

    cli = _filmcraft_cli()
    _check_cli_version(cli, FILMCRAFT_VERSION, "FilmCraft")

    _fc_exec(cli, None, create["command"], create["params"],
             save=False, save_as=project)
    if not project.is_file():
        raise _refuse("FilmCraft could not create the project",
                      f"no file at {project} after file.newSequence",
                      "check the pinned CLI opens headless",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    try:
        import_operation = native.filmcraft_import_operation(
            projection, {key: str(value) for key, value in resolved.items()},
            project_str)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("import operation refused", exc) from exc
    described_import = _describe(native, import_operation)
    imported = _fc_exec(cli, project, described_import["command"],
                        described_import["params"],
                        save=described_import["save"])
    items = imported.get("items") or []
    if len(items) != len(projection["imports"]):
        raise _refuse("FilmCraft import receipt does not pair with the plan",
                      f"{len(items)} items for {len(projection['imports'])} imports: {json.dumps(imported)[:400]}",
                      "reconcile the import receipt before placing",
                      code="REN_PM_CLI_FILMCRAFT_COMMAND")
    item_by_asset = {imp["asset_id"]: item
                     for imp, item in zip(projection["imports"], items)}

    try:
        pairs = native.filmcraft_placement_operations(
            projection, item_by_asset, project_str)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("placement operations refused", exc) from exc
    receipts: list[list[int]] = []
    described_pairs = [(use_id, _describe(native, operation))
                       for use_id, operation in pairs]
    if _batch_enabled():
        # One batched session for every placement: the same commands the
        # legacy loop sent one process at a time, through one open backend.
        if described_pairs:
            placed_answers = _fc_run_batch(
                cli, project,
                [{"command": described["command"], "params": described["params"]}
                 for _, described in described_pairs],
                save=any(described["save"] for _, described in described_pairs))
            for (use_id, _), placed in zip(described_pairs, placed_answers):
                receipts.append(_placement_clips(use_id, placed))
    else:
        for use_id, described in described_pairs:
            placed = _fc_exec(cli, project, described["command"],
                              described["params"], save=described["save"])
            receipts.append(_placement_clips(use_id, placed))
    try:
        clip_ids = native.filmcraft_reconcile_clip_ids(projection, receipts)
    except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
        raise _refuse_from_adapter("clip id reconciliation refused", exc) from exc

    real_commands = translate_filmcraft_edits(
        model, projection,
        {key: list(value) for key, value in clip_ids.items()},
        project_str, native)
    engine_losses = list(imported.get("errors") or [])
    if _batch_enabled():
        # One batched session for every translated edit, as for placements.
        edit_answers = _fc_run_batch(cli, project, real_commands,
                                     save=any(command["save"]
                                              for command in real_commands)) \
            if real_commands else []
    else:
        edit_answers = [_fc_exec(cli, project, command["command"],
                                 command["params"], save=command["save"])
                        for command in real_commands]
    for answer in edit_answers:
        if isinstance(answer, dict) and answer.get("errors"):
            engine_losses.extend(answer["errors"])

    inspected = _fc_exec(cli, project, "sequence.inspect", {}, save=False)
    receipt: dict[str, Any] = {
        "engine": "filmcraft",
        "engine_revision": FILMCRAFT_REVISION,
        "engine_version": FILMCRAFT_VERSION,
        "project": project_str,
        "plan": {"imports": projection["imports"],
                 "placements": projection["placements"]},
        "clip_ids": clip_ids,
        "edit_commands": [{"command": c["command"], "params": c["params"]}
                          for c in real_commands],
        "inspected": inspected,
        "losses": projection["losses"] + [{"code": "engine_note", "detail": str(note)[:300]}
                                           for note in engine_losses],
        "executed": True,
    }
    if export_path is not None:
        try:
            export_op = _describe(native, {"kind": "export", "project": project_str,
                                           "path": str(export_path),
                                           "format": EXPORT_FORMATS[export_format],
                                           "hardware_encoder": "auto"})
        except Exception as exc:  # noqa: BLE001 - adapter errors become refusals
            raise _refuse_from_adapter("export operation refused", exc) from exc
        exported = _fc_exec(cli, project, export_op["command"],
                            export_op["params"], save=export_op["save"])
        receipt["export"] = {"path": str(export_path), "answer": exported}
        # The export is the native render, so it carries the declared
        # treatments: grade and bezel composite over the engine base in
        # place (via a temp file), without a manual step. A project
        # that declares neither keeps the engine bytes untouched.
        receipt["native_treatments"] = _composite_export_treatments(
            export_path, planned=treatments_plan)
    else:
        receipt["native_treatments"] = {
            "out": None,
            "grade_name": None,
            "grade_applied": False,
            "bezel_applied": False,
            "refused": [],
            "filter": "",
            "skipped": "no export was requested, so there is no render to treat",
        }
    return receipt


def _plan_native_treatments(project_root: Path) -> dict:
    """The declared grade/bezel plan, or refuse before any mutation."""
    from library.tools import native_effects_composite as nec

    try:
        return nec.plan_declared_treatments(str(project_root))
    except Exception as exc:  # noqa: BLE001 - declaration errors become refusals
        raise _refuse(f"native treatments refused: {exc}",
                      "the project's series_look/tv_frame declaration cannot be delivered as written",
                      "fix the declaration in project.yaml (or the named brand template) and retry",
                      code="REN_PM_CLI_NATIVE_TREATMENTS") from exc


def _composite_export_treatments(export_path: Path, *, planned: dict) -> dict:
    """Composite the planned treatments over the export, in place.

    The treated picture lands AT the export path (composited to a temp
    sibling, then moved over), so the export this verb names is the
    native render with treatments, not the bare engine base. A plan
    with no grade and no bezel moves no pixels: the engine bytes stay
    exactly as exported.
    """
    from library.tools import native_effects_composite as nec

    try:
        if planned["grade"] is None and planned["bezel"] is None:
            return {
                "out": str(export_path),
                "grade_name": None,
                "grade_applied": False,
                "bezel_applied": False,
                "refused": [],
                "filter": "",
            }
        tmp = export_path.with_name(export_path.stem + ".treatments-tmp"
                                    + export_path.suffix)
        try:
            composited = nec.composite_declared_treatments(
                export_path, tmp, planned=planned)
        except Exception:
            tmp.unlink(missing_ok=True)
            raise
        Path(composited["out"]).replace(export_path)
        composited["out"] = str(export_path)
        return composited
    except ProjectModelCliRefused:
        raise
    except Exception as exc:  # noqa: BLE001 - composite errors become refusals
        raise _refuse(f"native treatments failed: {exc}",
                      "the engine export is on disk; only the grade/bezel composite failed",
                      "check the declared tv_frame asset reads and ffmpeg runs, then re-export",
                      code="REN_PM_CLI_NATIVE_TREATMENTS") from exc


# ── EffectCraft plan + execute ───────────────────────────────


def _ec_op_to_command(op: dict, identity_map: dict) -> list[tuple[str, dict]]:
    kind = op.get("op")
    if kind == "new_comp":
        return [("comp.new", {"name": op["name"], "width": op["width"],
                              "height": op["height"],
                              "frameRate": op["frame_rate"],
                              "duration": op["duration"]})]
    if kind == "import_footage":
        return [("file.import", {"paths": [op["path"]]})]
    if kind == "add_footage_layer":
        params: dict[str, Any] = {"item": op["footage"]}
        if "time" in op:
            params["time"] = op["time"]
        return [("layer.addItem", params)]
    if kind == "add_mask":
        return [("layer.addMask", {"layer": op["layer"], "shape": op["shape"],
                                   "rect": op["rect"]})]
    if kind == "interpret_footage":
        return [("file.interpretFootage", {"frameRate": op["frame_rate"]})]
    if kind == "set_track_matte":
        return [("layer.setTrackMatte", {"layer": op["layer"], "matte": op["matte"],
                                         "kind": op["kind"]})]
    if kind == "apply_effect":
        index = next(
            (entry["effect_index"]
             for entry in (identity_map.get("effects") or {}).values()
             if entry["layer"] == op["layer"]
             and entry["effectcraft_id"] == op["effect_id"]),
            None)
        if index is None:
            raise _refuse(f"effect {op['effect_id']!r} on {op['layer']!r} has no identity-map slot",
                          "the projection's identity map must name every applied effect",
                          "re-project the model before executing",
                          code="REN_PM_CLI_BAD_PROJECTION")
        commands = [("effect.apply", {"layer": op["layer"], "effect": op["effect_id"]})]
        for param, value in (op.get("params") or {}).items():
            commands.append(("prop.set", {"layer": op["layer"],
                                          "path": f"effects/#{index}/{param}",
                                          "value": value}))
        return commands
    if kind == "keyframe":
        return [("prop.addKey", {"layer": op["layer"], "path": op["path"],
                                 "time": op["time"], "value": op["value"]})]
    if kind == "add_text":
        params = {"layer": op.get("layer"), "text": op.get("text")}
        params.update({k: v for k, v in op.items()
                       if k not in ("op", "layer", "text")})
        return [("layer.addText", params)]
    if kind == "add_shape":
        params = {"layer": op.get("layer")}
        params.update({k: v for k, v in op.items()
                       if k not in ("op", "layer")})
        return [("layer.addShape", params)]
    raise _refuse(f"projection op {kind!r} has no CLI mapping",
                  "the projection emitted an op the runner cannot execute",
                  "extend the op mapping with the measured CLI command",
                  code="REN_PM_CLI_BAD_PROJECTION")


def execute_effectcraft(model: dict, *, project_root: Path, out: Path,
                        media_root: Path | None, comp_name: str,
                        width: int, height: int, fps: float,
                        duration: float, render_output: str,
                        render_format: str, project_id: str | None) -> dict:
    """Run the EffectCraft projection on the pinned CLI. Mutating."""
    from library.tools.project_model_effectcraft import (
        CompSpec, RenderSpec, ingest_receipt, project_to_request,
    )

    comp = CompSpec(name=comp_name, width=width, height=height,
                    frame_rate=fps, duration_seconds=duration)
    expected_frames = comp.expected_frames()
    renders = [RenderSpec(format=render_format, output=render_output)]
    try:
        projection = project_to_request(
            model, comp, renders,
            media_root=str(media_root or project_root),
            project_id=project_id)
    except Exception as exc:
        raise _refuse(f"EffectCraft projection refused: {exc}",
                      "the model carries something the pinned registry cannot represent",
                      "re-cut the model onto verified effects and projectable masks",
                      code="REN_PM_CLI_UNPROJECTABLE") from exc

    cli = _effectcraft_cli()
    _check_cli_version(cli, EFFECTCRAFT_VERSION, "EffectCraft")

    commands: list[tuple[str, dict]] = []
    for op in projection.request["operations"]:
        commands.extend(_ec_op_to_command(op, projection.identity_map))

    ecproj = out.parent / f"{out.stem}.ecproj"
    if ecproj.exists():
        raise _refuse(f"refusing to replace existing project {ecproj}",
                      "an execute run never overwrites an engine project",
                      "remove the file or plan to a fresh --out path",
                      code="REN_PM_CLI_PROJECT_EXISTS")
    argv = [str(cli), "run"]
    for command, params in commands:
        argv += [command, json.dumps(params)]
    argv += ["--empty", "--save-as", str(ecproj), "--json"]
    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=600, check=False)
    if done.returncode != 0:
        raise _refuse("EffectCraft run failed",
                      done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND")
    try:
        saved = json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise _refuse("EffectCraft run answered non-JSON",
                      str(exc),
                      "re-run against the pinned CLI",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND") from exc
    if str(saved.get("saved")) != str(ecproj):
        raise _refuse("EffectCraft did not save its project",
                      json.dumps(saved, default=str)[:400],
                      "re-run the projection on the pinned CLI",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND")

    render_out = out.parent / render_output
    rendered = subprocess.run(
        [str(cli), "render", "--project", str(ecproj), "--comp", comp.name,
         "--out", str(render_out), "--format", "png",
         "--start", "0", "--end", repr(duration), "--channels", "rgba",
         "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        check=False)
    if rendered.returncode != 0:
        raise _refuse("EffectCraft render failed",
                      rendered.stderr.strip()[-800:],
                      "inspect the engine output; the model is unchanged",
                      code="REN_PM_CLI_EFFECTCRAFT_COMMAND")
    frames = sorted(render_out.parent.glob(render_out.name.replace("#####", "*")))
    frames = [p for p in frames if p.is_file()]
    if len(frames) != expected_frames:
        raise _refuse(f"EffectCraft rendered {len(frames)} frames, expected {expected_frames}",
                      rendered.stdout.strip()[-400:],
                      "re-render the comp on the pinned CLI",
                      code="REN_PM_CLI_RENDER_UNREADABLE")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=pix_fmt",
         "-of", "json", str(frames[0])],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        check=False)
    try:
        pix_fmt = json.loads(probe.stdout)["streams"][0]["pix_fmt"]
    except (KeyError, TypeError, ValueError, IndexError):
        pix_fmt = ""
    if "a" not in pix_fmt:
        raise _refuse("EffectCraft render has no alpha",
                      f"first frame pix_fmt reads {pix_fmt!r}",
                      "render with --channels rgba",
                      code="REN_PM_CLI_RENDER_UNREADABLE")

    import hashlib

    receipt = {"version": 1, "project_id": projection.project_id,
               "upstream_revision": EFFECTCRAFT_REVISION,
               "engine_version": EFFECTCRAFT_VERSION,
               "project_file": str(ecproj),
               "renders": [{"path": str(frames[0]), "width": comp.width,
                            "height": comp.height, "frames": len(frames),
                            "has_alpha": True}]}
    try:
        updated = ingest_receipt(model, projection, receipt,
                                 input_revision=f"cli:{out.stem}",
                                 render_id=None,
                                 output_digest="sha256:" + hashlib.sha256(
                                     frames[0].read_bytes()).hexdigest())
    except Exception as exc:
        raise _refuse(f"EffectCraft receipt ingest refused: {exc}",
                      "the render did not match the projection it was rendered from",
                      "re-render from the same projection",
                      code="REN_PM_CLI_RECEIPT_MISMATCH") from exc
    ingested_path = out.parent / f"{out.stem}.ingested.json"
    ingested_path.write_text(json.dumps(updated, indent=2, ensure_ascii=False) + "\n",
                             encoding="utf-8")
    return {"engine": "effectcraft",
            "engine_revision": EFFECTCRAFT_REVISION,
            "engine_version": EFFECTCRAFT_VERSION,
            "project": str(ecproj),
            "operations": len(projection.request["operations"]),
            "losses": projection.losses,
            "frames": len(frames),
            "pix_fmt": pix_fmt,
            "ingested_model": str(ingested_path),
            "executed": True}


# ── CLI ──────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ren project-model",
        description="Plan a project's model onto a headless engine (print); --execute runs it.")
    sub = parser.add_subparsers(dest="command", required=True)
    project = sub.add_parser("project", help="project a model onto an engine plan")
    project.add_argument("project", help="project slug or path")
    project.add_argument("--engine", required=True, choices=("filmcraft", "effectcraft"),
                         help="which headless engine to project onto")
    project.add_argument("--out", required=True, type=Path,
                         help="where to write the plan JSON")
    project.add_argument("--model", default=None,
                         help="explicit model JSON path (default: the project's model store)")
    project.add_argument("--execute", action="store_true",
                         help="run the plan on the pinned engine CLI (default: plan and print only)")
    project.add_argument("--media-root", default=None, type=Path,
                         help="footage directory for relative asset URIs (default: the project root)")
    project.add_argument("--comp", default="Main", help="EffectCraft comp name")
    project.add_argument("--width", type=int, default=96)
    project.add_argument("--height", type=int, default=64)
    project.add_argument("--fps", type=float, default=10.0)
    project.add_argument("--duration", type=float, default=0.5)
    project.add_argument("--render-output", default="comp_#####.png")
    project.add_argument("--render-format", default="rgba_png_sequence")
    project.add_argument("--project-id", default=None)
    project.add_argument("--filmcraft-project", default=None, type=Path,
                         help="engine project path for --execute (default: <out>.fcproj)")
    project.add_argument("--export", default=None, type=Path,
                         help="with --execute on filmcraft, also export the built timeline here")
    project.add_argument("--export-format", default="h264",
                         choices=tuple(sorted(EXPORT_FORMATS)))
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "project":
            return cmd_project(parser, args)
        parser.error(f"unknown command {args.command}")
    except ProjectModelCliRefused as refused:
        print(refused.render(), file=sys.stderr)
        return REFUSAL_EXIT_CODE
    return 0


def cmd_project(parser: argparse.ArgumentParser, args: argparse.Namespace) -> int:
    try:
        root = resolve_project_root(args.project)
    except ProjectModelCliRefused as exc:
        parser.error(str(exc.what))
    model, source = load_model(root, args.model)
    media_root = args.media_root

    if args.engine == "effectcraft":
        if args.execute:
            report = execute_effectcraft(
                model, project_root=root, out=args.out, media_root=media_root,
                comp_name=args.comp, width=args.width, height=args.height,
                fps=args.fps, duration=args.duration,
                render_output=args.render_output,
                render_format=args.render_format, project_id=args.project_id)
            args.out.write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n",
                                encoding="utf-8")
            print(json.dumps(report, indent=2, ensure_ascii=False))
            return 0
        from library.tools.project_model_effectcraft import (
            CompSpec, RenderSpec, project_to_request,
        )
        comp = CompSpec(name=args.comp, width=args.width, height=args.height,
                        frame_rate=args.fps, duration_seconds=args.duration)
        renders = [RenderSpec(format=args.render_format, output=args.render_output)]
        try:
            projection = project_to_request(
                model, comp, renders,
                media_root=str(media_root or root),
                project_id=args.project_id)
        except Exception as exc:
            raise _refuse(f"EffectCraft projection refused: {exc}",
                          "the model carries something the pinned registry cannot represent",
                          "re-cut the model onto verified effects and projectable masks",
                          code="REN_PM_CLI_UNPROJECTABLE") from exc
        plan = {"engine": "effectcraft",
                "engine_revision": EFFECTCRAFT_REVISION,
                "engine_version": EFFECTCRAFT_VERSION,
                "model_source": source,
                "project_id": projection.project_id,
                "comp": {"name": comp.name, "width": comp.width,
                         "height": comp.height, "frame_rate": comp.frame_rate,
                         "duration_seconds": comp.duration_seconds},
                "renders": [{"format": r.format, "output": r.output,
                             "start_seconds": r.start_seconds,
                             "end_seconds": r.end_seconds}
                            for r in projection.renders],
                "request": projection.request,
                "identity_map": projection.identity_map,
                "losses": projection.losses,
                "executed": False}
        args.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(json.dumps({"engine": "effectcraft", "project_id": projection.project_id,
                          "operations": len(projection.request["operations"]),
                          "losses": projection.losses, "out": str(args.out),
                          "executed": False}))
        return 0

    native = filmcraft_native()
    plan = plan_filmcraft(model, native)
    plan["model_source"] = source
    if args.execute:
        receipt = execute_filmcraft(
            model, plan, project_root=root, out=args.out,
            media_root=media_root, filmcraft_project=args.filmcraft_project,
            export_path=args.export, export_format=args.export_format,
            native=native)
        args.out.write_text(json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
                            encoding="utf-8")
        print(json.dumps({k: v for k, v in receipt.items() if k != "inspected"},
                         indent=2, ensure_ascii=False, default=str))
        return 0
    args.out.write_text(json.dumps(plan, indent=2, ensure_ascii=False) + "\n",
                        encoding="utf-8")
    print(json.dumps({"engine": "filmcraft",
                      "placements": len(plan["placements"]),
                      "edits": plan["edits"],
                      "losses": plan["losses"], "out": str(args.out),
                      "executed": False}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
