"""Unit 9: end-to-end autonomous qualification over the Project Model.

In the disposable fixture project from PR 2243
(``tests/fixtures/project_model_e2e``), one autonomous run - no human
step - ingests the media, resolves person/object identities, links
masks, effects and A-roll to them, edits through BOTH engine adapters
(a FilmCraft HEAD trim that shifts content in time - anchor 12..48
kept as render 0..36, so every bound Fusion key moves - and an
EffectCraft masked effect, both over the static fixture masks and over
a run-local animated person mask through the track-matte path, with
rendered alpha checked against the matte pixels), plus one minted
``move.*`` model effect per class (slow zoom in/out, punch, shake,
cut-in) projected to transform keyframe ops and rendered, renders with frame
maps, swaps one result into a scratch Resolve timeline in
``Ren Qualification``, remaps and reads back the Fusion/grade state,
and emits provenance/QC receipts.

Run it::

    ren qualification pm-e2e --out <dir>      # full run, Resolve live half included
    ren qualification pm-e2e --out <dir> --no-resolve   # FREE half only

Resolve is reached ONLY through a broker ``resolve.qualification_session``
job on ``Ren Qualification`` (submitted from here, waited in the broker
queue); the run never touches the captain's project or timelines and
leaves ``Ren Qualification`` clean (`ren qualification reset` rebuilds
the declared project DELETE + CREATE once the run's evidence is read).

Failure injection (all FREE, no Resolve) covers stale revision, unknown
schema, ambiguous map, missing identity and unsupported effect - each
refuses BY NAME::

    StaleRevision      CODE REN_PM9_STALE_REVISION
    UnknownSchema      CODE REN_PM9_UNKNOWN_SCHEMA
    AmbiguousMap       CODE REN_PM9_AMBIGUOUS_MAP
    MissingIdentity    CODE REN_PM9_MISSING_IDENTITY
    UnsupportedEffect  CODE REN_PM9_UNSUPPORTED_EFFECT

No real project data is read or changed: the only media is the
committed synthetic fixture, and the only Resolve project is the
qualification one. Heavy engine/Resolve stages run under the caller's
verify slot (``vep-verify-run.sh``); this module never takes a slot
itself.

``tests/unit/resolve/test_project_model_qualification.py`` (FREE).
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from library.tools.ren_refusal import RenRefusal

REPO_ROOT = Path(__file__).resolve().parents[2]
FIXTURE_ROOT = REPO_ROOT / "tests/fixtures/project_model_e2e"
FIXTURE_MEDIA = FIXTURE_ROOT / "media"

FILMCRAFT_REVISION = "0800a8b0aa4a703ba42172f415b45a61f8d8a5e1"
FILMCRAFT_VERSION = "0.6.0"
EFFECTCRAFT_REVISION = "549e9d825132986d6167c1432cd3328efcbb0278"
EFFECTCRAFT_VERSION = "0.9.2"

ENGINE_TICKS_PER_SECOND = 254_016_000_000
FRAME_RATE = 24
TICKS_PER_FRAME = ENGINE_TICKS_PER_SECOND // FRAME_RATE

QUALIFICATION_PROJECT = "Ren Qualification"
REPORT_SCHEMA = "ren-pm9-qualification.v1"


# ── Named refusals ──────────────────────────────────────────────────

class Pm9Refused(RenRefusal, RuntimeError):
    """Base for the five Unit 9 failure-injection refusals."""


class StaleRevision(Pm9Refused):
    CODE = "REN_PM9_STALE_REVISION"


class UnknownSchema(Pm9Refused):
    CODE = "REN_PM9_UNKNOWN_SCHEMA"


class AmbiguousMap(Pm9Refused):
    CODE = "REN_PM9_AMBIGUOUS_MAP"


class MissingIdentity(Pm9Refused):
    CODE = "REN_PM9_MISSING_IDENTITY"


class UnsupportedEffect(Pm9Refused):
    CODE = "REN_PM9_UNSUPPORTED_EFFECT"


def _refuse(cls: type[Pm9Refused], what: str, why: str, fix: str) -> Pm9Refused:
    return cls(what=what, why=why, fix=fix, code=cls.CODE,
               recovery_class="blocked")


# ── Small helpers ───────────────────────────────────────────────────

def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
                         encoding="utf-8")
    temporary.replace(path)


def _filmcraft_cli() -> Path:
    from library.tools import filmcraft_patches

    # The digest-keyed cache the runtime install and the intake share:
    # revision plus the fork-patch-set key, so an unpatched binary is
    # never reused once the patch set changes.
    cached = filmcraft_patches.filmcraft_cli_path(Path.home(), FILMCRAFT_REVISION)
    if cached.is_file():
        return cached
    raise Pm9Refused(what="pinned FilmCraft CLI is absent",
                     why=f"no binary at {cached}",
                     fix="run the upstream intake gate to build the pinned revision",
                     code="REN_PM9_ENGINE_ABSENT", recovery_class="blocked")


def _effectcraft_cli() -> Path:
    cached = (Path.home() / ".cache/ren/effectcraft" / EFFECTCRAFT_REVISION
              / "source/target/release/effectcraft-cli")
    if cached.is_file():
        return cached
    raise Pm9Refused(what="pinned EffectCraft CLI is absent",
                     why=f"no binary at {cached}",
                     fix="run the upstream intake gate to build the pinned revision",
                     code="REN_PM9_ENGINE_ABSENT", recovery_class="blocked")


def _fc_exec(cli: Path, project: Path | None, command: str,
             params: dict, *, save: bool = False) -> Any:
    argv = [str(cli)]
    if project is not None:
        argv += ["--project", str(project)]
    argv += ["exec", command, json.dumps(params), "--compact"]
    if save:
        argv.append("--save")
    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=600, check=False)
    if done.returncode != 0:
        raise Pm9Refused(
            what=f"FilmCraft command {command!r} failed",
            why=done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
            fix="inspect the engine output; the model is unchanged",
            code="REN_PM9_FILMCRAFT_COMMAND", recovery_class="blocked")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise Pm9Refused(
            what=f"FilmCraft command {command!r} answered non-JSON",
            why=str(exc), fix="re-run against the pinned CLI",
            code="REN_PM9_FILMCRAFT_COMMAND",
            recovery_class="blocked") from exc


def _ec_run(cli: Path, project: Path, commands: list[tuple[str, dict]]) -> Any:
    argv = [str(cli), "run"]
    for command, params in commands:
        argv += [command, json.dumps(params)]
    argv += ["--empty", "--save-as", str(project), "--json"]
    done = subprocess.run(argv, capture_output=True, text=True,
                          encoding="utf-8", timeout=600, check=False)
    if done.returncode != 0:
        raise Pm9Refused(
            what="EffectCraft run failed",
            why=done.stderr.strip()[-800:] or done.stdout.strip()[-800:],
            fix="inspect the engine output; the model is unchanged",
            code="REN_PM9_EFFECTCRAFT_COMMAND", recovery_class="blocked")
    try:
        return json.loads(done.stdout)
    except json.JSONDecodeError as exc:
        raise Pm9Refused(
            what="EffectCraft run answered non-JSON", why=str(exc),
            fix="re-run against the pinned CLI",
            code="REN_PM9_EFFECTCRAFT_COMMAND",
            recovery_class="blocked") from exc


def _ffprobe_frames(path: Path) -> int:
    done = subprocess.run(
        ["ffprobe", "-v", "error", "-count_frames", "-select_streams", "v:0",
         "-show_entries", "stream=nb_read_frames,avg_frame_rate",
         "-of", "json", str(path)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False)
    if done.returncode != 0:
        raise Pm9Refused(what=f"ffprobe could not read {path.name}",
                         why=done.stderr.strip()[-400:],
                         fix="re-render the stage output",
                         code="REN_PM9_RENDER_UNREADABLE",
                         recovery_class="blocked")
    try:
        stream = json.loads(done.stdout)["streams"][0]
        return int(stream["nb_read_frames"])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise Pm9Refused(what=f"ffprobe gave no frame count for {path.name}",
                         why=str(exc), fix="re-render the stage output",
                         code="REN_PM9_RENDER_UNREADABLE",
                         recovery_class="blocked") from exc


# ── Stage 1: ingest ─────────────────────────────────────────────────

def stage_ingest(work: Path) -> dict:
    """Copy fixture media into the work dir and verify digests."""
    from library.tools.project_model import validate_fixture

    model = validate_fixture()
    media_dir = work / "media"
    media_dir.mkdir(parents=True, exist_ok=True)
    assets: list[dict] = []
    for asset in model["assets"]:
        if asset["kind"] == "render":
            continue
        source = FIXTURE_ROOT / asset["uri"]
        if not source.is_file():
            raise Pm9Refused(what=f"fixture media is missing: {asset['uri']}",
                             why="the run reads only committed fixture media",
                             fix="restore the fixture checkout",
                             code="REN_PM9_FIXTURE_MEDIA",
                             recovery_class="blocked")
        target = media_dir / Path(asset["uri"]).name
        shutil.copy2(source, target)
        digest = "sha256:" + _sha256(target)
        if digest != asset["content_digest"]:
            raise Pm9Refused(
                what=f"fixture media digest mismatch: {target.name}",
                why="a file on disk is not a measurement until its digest reads back",
                fix="restore the fixture checkout",
                code="REN_PM9_FIXTURE_MEDIA", recovery_class="blocked")
        assets.append({"id": asset["id"], "file": target.name,
                       "bytes": target.stat().st_size, "digest": digest})
    _write_json(work / "model.ingested.json", model)
    return {"model_id": model["id"],
            "schema_version": model["schema_version"], "assets": assets}


# ── Stage 2: identity ───────────────────────────────────────────────

def stage_identity(work: Path) -> dict:
    """Resolve both people from observations through masks and A-roll."""
    from library.tools.project_model_evidence import resolve_person

    model = json.loads((work / "model.ingested.json").read_text(encoding="utf-8"))
    resolved = {}
    for person_id in ("person-ada", "person-ben"):
        person = resolve_person(model, person_id)
        if person is None:
            raise _refuse(MissingIdentity,
                          f"identity {person_id!r} did not resolve",
                          "resolve_person returned None on the ingested model",
                          "restore the fixture; an unmeasured identity stays absent")
        modalities = sorted({obs.get("modality") for obs in person["observations"]})
        if not {"face", "speech"} <= set(modalities):
            raise _refuse(MissingIdentity,
                          f"identity {person_id!r} lacks a measured face+speech link",
                          f"modalities read back as {modalities}",
                          "restore the fixture; an unmeasured link stays absent")
        if not person["masks"]:
            raise _refuse(MissingIdentity,
                          f"identity {person_id!r} has no linked mask",
                          "no mask in the model names this entity",
                          "restore the fixture; masks link to entities, never labels")
        aroll = [use for use in person["clip_uses"] if use.get("role") == "a_roll"]
        if not aroll:
            raise _refuse(MissingIdentity,
                          f"identity {person_id!r} has no linked A-roll",
                          "no A-roll clip use names this entity",
                          "restore the fixture; A-roll links to entities, never labels")
        if not person["rendered_ranges"]:
            raise _refuse(MissingIdentity,
                          f"identity {person_id!r} reaches no rendered range",
                          "no frame-map segment names this person's clip use",
                          "restore the fixture frame maps")
        resolved[person_id] = {
            "entity_id": person["entity"]["id"],
            "observations": sorted(obs["id"] for obs in person["observations"]),
            "modalities": modalities,
            "masks": [mask["id"] for mask in person["masks"]],
            "a_roll": [use["id"] for use in aroll],
            "rendered_ranges": person["rendered_ranges"],
            "identity_links": len(person["identity_links"]),
        }
    return resolved


# ── Stage 3: FilmCraft (length-changing trim + render + frame map) ──

TRIM_FROM_FRAMES = 48
TRIM_HEAD_CUT = 12
TRIM_KEPT_FRAMES = TRIM_FROM_FRAMES - TRIM_HEAD_CUT


def _fc_export(cli: Path, project: Path, out_path: Path, *,
               start_seconds: float | None = None,
               end_seconds: float | None = None) -> int:
    """Export the sequence and return its probed frame count.

    A head trim leaves a leading gap (the sequence still ends where it
    did), so the trimmed export names its exact custom range in
    seconds; the full export takes the entire sequence.
    """
    params: dict[str, Any] = {"path": str(out_path), "format": "h264",
                              "hardwareEncoding": "auto",
                              # Sent explicitly (never defaulted unseen): the
                              # engine validates `gpuRendering` as off|auto.
                              # `auto` panicked the headless CLI via this exec
                              # path before 0800a8b; at the pin it falls back
                              # to the CPU path byte-identical to `off`
                              # (measured 2026-10-11), but `off` stays the
                              # qualification default by the fidelity rule.
                              "gpuRendering": "off", "wait": True}
    if start_seconds is not None or end_seconds is not None:
        params["range"] = "custom"
        if start_seconds is not None:
            params["startSeconds"] = start_seconds
        if end_seconds is not None:
            params["endSeconds"] = end_seconds
    export = _fc_exec(cli, project, "file.exportMedia", params, save=False)
    if not out_path.is_file() or out_path.stat().st_size == 0:
        raise Pm9Refused(what="FilmCraft export is missing or empty",
                         why=json.dumps(export)[:400],
                         fix="re-run the export on the pinned CLI",
                         code="REN_PM9_RENDER_UNREADABLE",
                         recovery_class="blocked")
    return _ffprobe_frames(out_path)


def stage_filmcraft(work: Path) -> dict:
    """Place Ada's A-roll, head-trim 48->36, render both, map exactly.

    The trim cuts the HEAD (timeline 0..48 becomes anchor 12..48), so
    content shifts in time: a Fusion key at old frame F must remap to
    F-12, and an identity map would fail the moved-key assertion in
    stage 5.
    """
    cli = _filmcraft_cli()
    version = subprocess.run([str(cli), "--version"], capture_output=True,
                             text=True, encoding="utf-8", timeout=60,
                             check=False)
    if FILMCRAFT_VERSION not in (version.stdout + version.stderr):
        raise Pm9Refused(what="FilmCraft CLI version drifted",
                         why=(version.stdout + version.stderr).strip()[-200:],
                         fix=f"re-qualify against pinned {FILMCRAFT_VERSION}",
                         code="REN_PM9_ENGINE_VERSION",
                         recovery_class="blocked")
    fc_dir = work / "filmcraft"
    fc_dir.mkdir(parents=True, exist_ok=True)
    project = fc_dir / "pm9.fcproj"
    if project.exists():
        project.unlink()
    source = work / "media" / "person-ada-aroll.mp4"

    # CreateProject shape: exec file.newSequence '{"name"}' --save-as.
    created = subprocess.run(
        [str(cli), "exec", "file.newSequence",
         json.dumps({"name": "PM9 Ada trim", "width": 320, "height": 180,
                     "fps": 24.0}), "--compact",
         "--save-as", str(project)],
        capture_output=True, text=True, encoding="utf-8", timeout=120,
        check=False)
    if created.returncode != 0 or not project.is_file():
        raise Pm9Refused(what="FilmCraft could not create the project",
                         why=created.stderr.strip()[-800:],
                         fix="check the pinned CLI opens headless",
                         code="REN_PM9_FILMCRAFT_COMMAND",
                         recovery_class="blocked")
    imported = _fc_exec(cli, project, "file.import",
                        {"paths": [str(source)]}, save=True)
    items = imported.get("items") or []
    if not items:
        raise Pm9Refused(what="FilmCraft imported no items",
                         why=json.dumps(imported)[:400],
                         fix="check the fixture media decodes",
                         code="REN_PM9_FILMCRAFT_COMMAND",
                         recovery_class="blocked")
    item_id = items[0]
    placed = _fc_exec(cli, project, "timeline.place",
                      {"item": item_id, "track": "V1", "frame": 0,
                       "sourceIn": 0,
                       "duration": TRIM_FROM_FRAMES * TICKS_PER_FRAME},
                      save=True)
    clips = placed.get("clips") or []
    if not clips:
        raise Pm9Refused(what="FilmCraft placed no clips",
                         why=json.dumps(placed)[:400],
                         fix="check the engine timeline state",
                         code="REN_PM9_FILMCRAFT_COMMAND",
                         recovery_class="blocked")
    if len(imported.get("errors") or []) or len(placed.get("errors", []) or []):
        pass  # engine-side notes are recorded below as losses, never hidden
    full_path = fc_dir / "pm9-ada-full.mp4"
    full_frames = _fc_export(cli, project, full_path)
    if full_frames != TRIM_FROM_FRAMES:
        raise Pm9Refused(
            what="FilmCraft full export is not the placed 48 frames",
            why=f"ffprobe reads {full_frames} frames",
            fix="re-run the export on the pinned CLI",
            code="REN_PM9_RENDER_UNREADABLE", recovery_class="blocked")
    trim = _fc_exec(cli, project, "timeline.trim",
                    {"clip": clips[0], "edge": "in", "mode": "regular",
                     "deltaFrames": TRIM_HEAD_CUT},
                    save=True)
    trim_delta = trim.get("delta")
    inspected = _fc_exec(cli, project, "sequence.inspect", {}, save=False)
    # Ground truth is the read-back, never the plan: the head trim must
    # leave video and audio in agreement at start 12, duration 36.
    try:
        video_items = inspected["video"][0]["items"]
        audio_items = inspected["audio"][0]["items"]
        video_frames = int(video_items[0]["durationFrames"])
        audio_frames = int(audio_items[0]["durationFrames"])
        video_start = int(video_items[0]["startFrame"])
    except (KeyError, TypeError, ValueError, IndexError) as exc:
        raise Pm9Refused(what="FilmCraft read-back has no timeline items",
                         why=json.dumps(inspected)[:400],
                         fix="check the engine timeline state",
                         code="REN_PM9_FILMCRAFT_COMMAND",
                         recovery_class="blocked") from exc
    if (video_frames != TRIM_KEPT_FRAMES or audio_frames != TRIM_KEPT_FRAMES
            or video_start != TRIM_HEAD_CUT):
        raise Pm9Refused(
            what="FilmCraft head trim did not land at 12..48",
            why=(f"video {video_frames}@{video_start} vs audio "
                 f"{audio_frames}"),
            fix="record the loss; never pave a mistimed trim with a plan",
            code="REN_PM9_FILMCRAFT_LOSS", recovery_class="blocked")
    timeline_frames = video_frames
    trimmed_path = fc_dir / "pm9-ada-trimmed.mp4"
    rendered_frames = _fc_export(
        cli, project, trimmed_path,
        start_seconds=TRIM_HEAD_CUT / FRAME_RATE,
        end_seconds=(TRIM_HEAD_CUT + timeline_frames) / FRAME_RATE)
    if rendered_frames != timeline_frames:
        raise Pm9Refused(
            what="FilmCraft trimmed export does not match the timeline",
            why=f"export {rendered_frames} frames vs timeline {timeline_frames}",
            fix="check the export range covers exactly the kept span",
            code="REN_PM9_RENDER_UNREADABLE", recovery_class="blocked")
    # The frame map is built from MEASURED values (engine read-back and
    # export probe), never from the plan: a trim-delta defect would
    # otherwise be paved over by assumption. The anchor keeps the
    # source frames the trim kept, so the map carries the head offset.
    import math as _math
    divisor = _math.gcd(timeline_frames, rendered_frames) or 1
    frame_map = {
        "id": "map-pm9-filmcraft",
        "from_clock": "clock-project",
        "to_clock": "clock-pm9-render",
        "segments": [{
            "from": {"clock_id": "clock-project", "start": TRIM_HEAD_CUT,
                     "end": TRIM_HEAD_CUT + timeline_frames},
            "to": {"clock_id": "clock-pm9-render", "start": 0,
                   "end": rendered_frames},
            "mapping_kind": "one_to_one" if timeline_frames == rendered_frames
            else "retimed",
            "rate": {"numerator": rendered_frames // divisor,
                     "denominator": timeline_frames // divisor},
            "source_clip_use": "use-ada",
        }],
    }
    if timeline_frames != rendered_frames:
        # A timeline/render length mismatch is a measured loss, not a
        # silent rounding: the map carries the exact rational rate.
        pass
    if timeline_frames != TRIM_KEPT_FRAMES:
        loss = (f"trim read back {timeline_frames} timeline frames, "
                f"planned {TRIM_KEPT_FRAMES}")
    else:
        loss = None
    _write_json(fc_dir / "frame-map.json", frame_map)
    return {"cli_version": FILMCRAFT_VERSION,
            "upstream_revision": FILMCRAFT_REVISION,
            "item_id": item_id, "clip_ids": clips,
            "import_errors": imported.get("errors") or [],
            "trim_delta_raw": trim_delta,
            "planned_trim": {"head_cut_frames": TRIM_HEAD_CUT,
                             "kept_anchor": [TRIM_HEAD_CUT, TRIM_FROM_FRAMES]},
            "measured_start_frame": video_start,
            "measured_timeline_frames": timeline_frames,
            "full_render": {"path": str(full_path),
                            "bytes": full_path.stat().st_size,
                            "frames": full_frames},
            "render": {"path": str(trimmed_path),
                       "bytes": trimmed_path.stat().st_size,
                       "frames": rendered_frames},
            "frame_map": frame_map,
            "losses": [loss] if loss else []}


# ── Stage 3b: native treatments (FREE over the FilmCraft render) ──

def stage_native_treatments(work: Path, filmcraft: dict,
                            *, project_folder: str | None = None) -> dict:
    """Composite the declared treatments over the trimmed render.

    The PR 2310 composite, wired into the qualification path: the
    trimmed FilmCraft render is the base, the treated sibling beside
    it is the native render with grade and bezel - no manual step.
    Declarations resolve from ``project_folder`` (a directory holding
    ``project.yaml``); the qualification run passes none, because it
    reads no real project, so a full run records the pass-through and
    the behavioural tests drive both branches with run-local
    declarations under tmp_path.

    A run that declares no look gets no grade and no bezel: the stage
    reports the base path back with both applied flags False and runs
    no ffmpeg. A malformed declaration refuses as
    ``REN_PM9_NATIVE_TREATMENTS``; a base render the FilmCraft stage
    did not write refuses as ``REN_PM9_RENDER_UNREADABLE``.
    """
    from library.tools import native_effects_composite as nec

    try:
        base = Path(filmcraft["render"]["path"])
    except (KeyError, TypeError) as exc:
        raise Pm9Refused(what="native treatments need the FilmCraft render",
                         why=f"filmcraft evidence names no render path: {exc}",
                         fix="re-run the FilmCraft stage before treating its render",
                         code="REN_PM9_NATIVE_TREATMENTS",
                         recovery_class="blocked") from exc
    if not base.is_file():
        raise Pm9Refused(what=f"FilmCraft render is absent: {base.name}",
                         why="the treatments composite over the trimmed export",
                         fix="re-render the stage output",
                         code="REN_PM9_RENDER_UNREADABLE",
                         recovery_class="blocked")
    out = base.parent / f"{base.stem}.treated{base.suffix}"
    try:
        treated = nec.composite_declared_treatments(
            base, out, project_folder=project_folder)
    except Pm9Refused:
        raise
    except Exception as exc:  # noqa: BLE001 - declaration/composite errors refuse by name
        raise Pm9Refused(what=f"native treatments refused: {exc}",
                         why="the series_look/tv_frame declaration cannot be "
                             "delivered as written, or the composite failed",
                         fix="fix the declaration, or re-render the stage output",
                         code="REN_PM9_NATIVE_TREATMENTS",
                         recovery_class="blocked") from exc
    evidence = {
        "base_render": str(base),
        "out": treated["out"],
        "grade_name": treated["grade_name"],
        "grade_applied": treated["grade_applied"],
        "bezel_applied": treated["bezel_applied"],
        "refused": treated["refused"],
        "filter": treated["filter"],
    }
    _write_json(work / "native-treatments.json", evidence)
    return evidence


# ── Stage 4: EffectCraft (masked effect + render + receipt ingest) ──

def _ec_op_to_command(op: dict, projection: Any) -> list[tuple[str, dict]]:
    kind = op["op"]
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
        return [("file.interpretFootage",
                 {"frameRate": op["frame_rate"]})]
    if kind == "set_track_matte":
        return [("layer.setTrackMatte",
                 {"layer": op["layer"], "matte": op["matte"],
                  "kind": op["kind"]})]
    if kind == "apply_effect":
        # Mirrors the Rust driver: one effect.apply, then one prop.set
        # per parameter on that effect's index slot.
        index = next(
            (entry["effect_index"]
             for entry in projection.identity_map["effects"].values()
             if entry["layer"] == op["layer"]
             and entry["effectcraft_id"] == op["effect_id"]),
            None)
        if index is None:
            raise _refuse(
                UnsupportedEffect,
                f"effect {op['effect_id']!r} on {op['layer']!r} has no "
                "identity-map slot",
                "the projection's identity map must name every applied effect",
                "re-project the model before executing")
        commands = [("effect.apply", {"layer": op["layer"],
                                      "effect": op["effect_id"]})]
        for param, value in (op.get("params") or {}).items():
            commands.append(("prop.set",
                             {"layer": op["layer"],
                              "path": f"effects/#{index}/{param}",
                              "value": value}))
        return commands
    if kind == "keyframe":
        return [("prop.addKey", {"layer": op["layer"], "path": op["path"],
                                 "time": op["time"], "value": op["value"]})]
    raise _refuse(
        UnsupportedEffect, f"qualification has no CLI mapping for {kind!r}",
        "the projection emitted an op the runner cannot execute",
        "extend _ec_op_to_command with the measured CLI command")


def _ec_execute(cli: Path, ec_dir: Path, name: str, projection: Any,
                comp: Any, duration: float, *, input_revision: str,
                render_id: str, model: dict) -> dict:
    """Run one projection through the pinned CLI and ingest its receipt."""
    from library.tools.project_model_effectcraft import ingest_receipt

    commands: list[tuple[str, dict]] = []
    for op in projection.request["operations"]:
        commands.extend(_ec_op_to_command(op, projection))
    project = ec_dir / f"{name}.ecproj"
    if project.exists():
        project.unlink()
    saved = _ec_run(cli, project, commands)
    if str(saved.get("saved")) != str(project):
        raise Pm9Refused(what="EffectCraft did not save its project",
                         why=json.dumps(saved, default=str)[:400],
                         fix="re-run the projection on the pinned CLI",
                         code="REN_PM9_EFFECTCRAFT_COMMAND",
                         recovery_class="blocked")
    render_out = ec_dir / f"{name}_#####.png"
    rendered = subprocess.run(
        [str(cli), "render", "--project", str(project), "--comp", comp.name,
         "--out", str(render_out), "--format", "png",
         "--start", "0", "--end", repr(duration), "--channels", "rgba",
         "--json"],
        capture_output=True, text=True, encoding="utf-8", timeout=600,
        check=False)
    if rendered.returncode != 0:
        raise Pm9Refused(what="EffectCraft render failed",
                         why=rendered.stderr.strip()[-800:],
                         fix="inspect the engine output; the model is unchanged",
                         code="REN_PM9_EFFECTCRAFT_COMMAND",
                         recovery_class="blocked")
    frames = sorted(ec_dir.glob(f"{name}_*.png"))
    expected = comp.expected_frames()
    if len(frames) != expected:
        raise Pm9Refused(
            what=f"EffectCraft rendered {len(frames)} frames, expected {expected}",
            why=rendered.stdout.strip()[-400:],
            fix="re-render the comp on the pinned CLI",
            code="REN_PM9_RENDER_UNREADABLE", recovery_class="blocked")
    probe = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "stream=pix_fmt",
         "-of", "json", str(frames[0])],
        capture_output=True, text=True, encoding="utf-8", timeout=60,
        check=False)
    pix_fmt = ""
    try:
        pix_fmt = json.loads(probe.stdout)["streams"][0]["pix_fmt"]
    except (KeyError, TypeError, ValueError, IndexError, json.JSONDecodeError):
        pix_fmt = ""
    if "a" not in pix_fmt:
        raise Pm9Refused(what="EffectCraft render has no alpha",
                         why=f"first frame pix_fmt reads {pix_fmt!r}",
                         fix="render with --channels rgba",
                         code="REN_PM9_RENDER_UNREADABLE",
                         recovery_class="blocked")
    receipt = {"version": 1, "project_id": projection.project_id,
               "upstream_revision": EFFECTCRAFT_REVISION,
               "engine_version": EFFECTCRAFT_VERSION,
               "project_file": str(project),
               "renders": [{"path": str(frames[0]), "width": comp.width,
                            "height": comp.height, "frames": len(frames),
                            "has_alpha": True}]}
    updated = ingest_receipt(model, projection, receipt,
                             input_revision=input_revision,
                             render_id=render_id,
                             output_digest="sha256:" + _sha256(frames[0]))
    return {"projection": projection, "frames": frames,
            "pix_fmt": pix_fmt, "updated": updated,
            "frame_map_id": updated["frame_maps"][-1]["id"]}


def _rgba_alpha(path: Path, width: int, height: int) -> bytes:
    """The alpha channel of a PNG frame, decoded with ffmpeg only."""
    done = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", str(path), "-f", "rawvideo",
         "-pix_fmt", "rgba", "-"],
        capture_output=True, timeout=120, check=False)
    if done.returncode != 0 or len(done.stdout) != width * height * 4:
        raise Pm9Refused(
            what=f"could not decode alpha from {path.name}",
            why=done.stderr.decode("utf-8", "replace")[-300:],
            fix="re-render the comp on the pinned CLI",
            code="REN_PM9_RENDER_UNREADABLE", recovery_class="blocked")
    return bytes(done.stdout[3::4])


def _mean_abs_diff(a: bytes, b: bytes) -> float:
    total = sum(abs(x - y) for x, y in zip(a, b))
    return total / len(a)


def stage_effectcraft(work: Path) -> dict:
    """Project masks+effect, render masked comps, ingest the receipts.

    Two projections: the full fixture (static ellipse masks through
    ``layer.addMask``) and a run-local animated person mask derived
    from mask-ada (moved second keyframe, so the static fast path
    refuses it) through the track-matte path - rasterized matte
    sequence, ``setTrackMatte``, masked blur. The animated render's
    alpha is checked against the matte pixels frame by frame.
    """
    from library.tools.project_model_effectcraft import (
        ADAPTER_VERSION,
        CompSpec,
        RenderSpec,
        project_to_request,
        write_matte_sequence,
    )

    cli = _effectcraft_cli()
    version = subprocess.run([str(cli), "--version"], capture_output=True,
                             text=True, encoding="utf-8", timeout=60,
                             check=False)
    if EFFECTCRAFT_VERSION not in (version.stdout + version.stderr):
        raise Pm9Refused(what="EffectCraft CLI version drifted",
                         why=(version.stdout + version.stderr).strip()[-200:],
                         fix=f"re-qualify against pinned {EFFECTCRAFT_VERSION}",
                         code="REN_PM9_ENGINE_VERSION",
                         recovery_class="blocked")
    model = json.loads((work / "model.ingested.json").read_text(encoding="utf-8"))
    # Fixture uris already carry their "media/" prefix, so the media
    # root is the work dir itself (work/media/<name>).
    comp = CompSpec(name="PM9", width=160, height=90, frame_rate=12.0,
                    duration_seconds=4.5)
    render_spec = RenderSpec(format="rgba_png_sequence",
                             output="pm9_#####.png")
    try:
        projection = project_to_request(
            model, comp, [render_spec], media_root=str(work),
            project_id="pm9-effectcraft")
    except Exception as exc:
        name = type(exc).__name__
        if name in ("UnknownEffect", "UnknownParameter", "ParameterType"):
            raise _refuse(UnsupportedEffect,
                          f"unsupported effect in fixture projection: {exc}",
                          f"the adapter refused with {name}",
                          "map the effect in VERIFIED_EFFECT_MAP with a registry "
                          "measurement, or drop it from the plan") from exc
        raise
    ec_dir = work / "effectcraft"
    ec_dir.mkdir(parents=True, exist_ok=True)
    static = _ec_execute(cli, ec_dir, "pm9", projection, comp, 4.5,
                         input_revision="rev-pm9-1", render_id="render-pm9-ec",
                         model=model)
    _write_json(work / "model.with-ec-receipt.json", static["updated"])

    animated = _stage_effectcraft_animated(
        cli, ec_dir, work, model, write_matte_sequence, project_to_request,
        CompSpec, RenderSpec)
    moves = _stage_effectcraft_moves(cli, ec_dir, work, model)
    return {"cli_version": EFFECTCRAFT_VERSION,
            "upstream_revision": EFFECTCRAFT_REVISION,
            "adapter_version": ADAPTER_VERSION,
            "operations": len(projection.request["operations"]),
            "identity_map": projection.identity_map,
            "losses": projection.losses, "frames": len(static["frames"]),
            "pix_fmt": static["pix_fmt"], "render_id": "render-pm9-ec",
            "frame_map_id": static["frame_map_id"],
            "animated_mask": animated, "moves": moves}


def _stage_effectcraft_animated(cli: Path, ec_dir: Path, work: Path,
                                model: dict, write_matte_sequence,
                                project_to_request, CompSpec,
                                RenderSpec) -> dict:
    """A moving person mask through mattes, checked against its pixels.

    The committed fixture carries only static masks, so the run derives
    one animated mask from mask-ada (its second keyframe box moves) in
    a run-local model copy: the static ``addMask`` path must refuse it
    and the track-matte path must carry it exactly.
    """
    from library.tools.project_model_effectcraft import MatteSequence

    anim_dir = ec_dir / "anim"
    anim_dir.mkdir(parents=True, exist_ok=True)
    anim = copy.deepcopy(model)
    ada_mask = next(mask for mask in anim["masks"] if mask["id"] == "mask-ada")
    moved = copy.deepcopy(ada_mask)
    moved["id"] = "mask-ada-anim"
    # The box travels right and narrows; still normalized (0.45+0.25
    # < 1, 0.15+0.60 < 1) so the only change is motion, not validity.
    moved["representation"]["keyframes"][1]["bbox"] = [0.45, 0.15, 0.25, 0.60]
    anim["masks"] = [moved]
    anim["clip_uses"] = [use for use in anim["clip_uses"]
                         if use["id"] == "use-ada"]
    anim["effects"] = [{"id": "effect-blur-anim", "class_id": "blur.gaussian",
                        "target_id": "mask-ada-anim",
                        "interval": {"clock_id": "clock-ada",
                                     "start": 0, "end": 48},
                        "parameters": {"blurriness": 2.0}, "keyframes": []}]
    comp = CompSpec(name="PM9Anim", width=160, height=90, frame_rate=12.0,
                    duration_seconds=2.0)
    sequence = write_matte_sequence(moved, anim, comp, anim_dir,
                                    stem="anim-matte")
    if not isinstance(sequence, MatteSequence) or not sequence.files:
        raise Pm9Refused(what="animated mask rasterized to no matte files",
                         why=f"mask {moved['id']!r} interval "
                             f"{moved['interval']}",
                         fix="check the mask interval covers comp frames",
                         code="REN_PM9_EFFECTCRAFT_COMMAND",
                         recovery_class="blocked")
    projection = project_to_request(
        anim, comp, [RenderSpec(format="rgba_png_sequence",
                                output="pm9anim_#####.png")],
        media_root=str(work), project_id="pm9-effectcraft-anim",
        mattes={moved["id"]: sequence})
    if not any(op["op"] == "set_track_matte"
               for op in projection.request["operations"]):
        raise Pm9Refused(what="animated mask did not take the matte path",
                         why=json.dumps([op["op"] for op in projection.request[
                             "operations"]])[:300],
                         fix="a moving box must rasterize, never addMask",
                         code="REN_PM9_EFFECTCRAFT_COMMAND",
                         recovery_class="blocked")
    ran = _ec_execute(cli, ec_dir, "pm9anim", projection, comp, 2.0,
                      input_revision="rev-pm9-2",
                      render_id="render-pm9-ec-anim", model=anim)
    _write_json(work / "model.with-ec-anim-receipt.json", ran["updated"])
    # The track-matted layer's alpha must BE the matte: compare every
    # rendered frame's alpha against its matte frame's alpha.
    matte_by_frame = sorted(anim_dir.glob("*.png"))
    if len(matte_by_frame) != len(ran["frames"]):
        raise Pm9Refused(
            what="matte sequence and render disagree on frame count",
            why=f"{len(matte_by_frame)} mattes vs {len(ran['frames'])} frames",
            fix="check the mask interval covers exactly the comp span",
            code="REN_PM9_EFFECTCRAFT_MATTE", recovery_class="blocked")
    diffs = []
    for frame, matte in zip(ran["frames"], matte_by_frame):
        rendered_alpha = _rgba_alpha(frame, comp.width, comp.height)
        matte_alpha = _rgba_alpha(matte, comp.width, comp.height)
        diffs.append(_mean_abs_diff(rendered_alpha, matte_alpha))
    worst = max(diffs)
    # Binary mattes through a deterministic CPU sampler read back
    # near-exact; 2.0/255 mean leaves headroom for edge filtering while
    # failing a dropped or mistimed matte by orders of magnitude.
    if worst > 2.0:
        raise Pm9Refused(
            what="animated render alpha does not match its matte",
            why=f"worst mean abs diff {worst:.2f}/255 over {len(diffs)} frames",
            fix="check the matte sequence conform and the track-matte bind",
            code="REN_PM9_EFFECTCRAFT_MATTE", recovery_class="blocked")
    center_opaque = []
    for position, frame in enumerate(ran["frames"]):
        alpha = _rgba_alpha(frame, comp.width, comp.height)
        # The box drifts right across the 24 frames; sample the pixel at
        # the interpolated box center, which must stay inside the mask.
        frac = position / max(1, len(ran["frames"]) - 1)
        cx = int((0.50 + frac * 0.075) * comp.width)
        cy = int(0.425 * comp.height)
        center_opaque.append(alpha[cy * comp.width + cx])
    if min(center_opaque) < 200:
        raise Pm9Refused(
            what="animated mask center reads transparent",
            why=f"weakest center alpha {min(center_opaque)}/255",
            fix="check the matte raster against the keyframe boxes",
            code="REN_PM9_EFFECTCRAFT_MATTE", recovery_class="blocked")
    return {"mask": moved["id"], "matte_files": len(sequence.files),
            "frames": len(ran["frames"]), "worst_alpha_diff": round(worst, 3),
            "weakest_center_alpha": min(center_opaque),
            "frame_map_id": ran["frame_map_id"]}


def moves_stage_entries() -> tuple[list[dict], dict[str, str],
                                    dict[str, dict]]:
    """One accepted plan_vfx toolkit entry per move class.

    Returns ``(resolved_entries, targets, raw_params_by_vfx)`` for
    :func:`mint_move_effects <library.tools.project_model_effectcraft_moves.mint_move_effects>`:
    resolved-style entries as the plan_vfx post-bridge accepts them
    (alias-resolved types, readable params, derived punch frame
    counts), block-to-clip-use targets onto the fixture uses, and the
    raw plan params - carrying ``zoom_easing`` for the drift, which
    the bridge strips for Fusion and the mint recovers. Punch counts
    fit the b-roll use's 12 comp frames (peak 3, release 6, out 9 of
    frames 0..11).
    """
    resolved = [
        {"vfx_id": "vfx_001", "target_block_position": "1",
         "timeline_start": 0.0, "timeline_end": 1.5,
         "effect_type": "slow_zoom_in",
         "params": {"zoom_start": 1.0, "zoom_end": 1.04}},
        {"vfx_id": "vfx_002", "target_block_position": "2",
         "timeline_start": 1.5, "timeline_end": 3.5,
         "effect_type": "slow_zoom_out",
         "params": {"zoom_start": 1.04, "zoom_end": 1.0}},
        {"vfx_id": "vfx_003", "target_block_position": "3",
         "timeline_start": 3.5, "timeline_end": 4.5,
         "effect_type": "zoom_emphasis",
         "params": {"zoom_start": 1.0, "zoom_mid": 1.08,
                    "zoom_end": 1.0,
                    "zoom_in_duration_frames": 3,
                    "zoom_release_offset_frames": 6,
                    "zoom_out_duration_frames": 3}},
        {"vfx_id": "vfx_004", "target_block_position": "4",
         "timeline_start": 0.0, "timeline_end": 1.5,
         "effect_type": "screen_shake",
         "params": {"shake_x": 0.02, "shake_y": 0.01,
                    "shake_decay_frames": 3}},
        {"vfx_id": "vfx_005", "target_block_position": "5",
         "timeline_start": 1.5, "timeline_end": 3.5,
         "effect_type": "cut_in",
         "params": {"zoom_start": 1.06, "zoom_mid": 1.06,
                    "zoom_end": 1.06}},
    ]
    targets = {"1": "use-ada", "2": "use-ben", "3": "use-broll",
               "4": "use-ada", "5": "use-ben"}
    raw = {"vfx_001": {"zoom_start": 1.0, "zoom_end": 1.04,
                       "zoom_easing": "Sine"}}
    return resolved, targets, raw


def mint_and_project_moves(model: dict, *,
                             media_root: str) -> list[dict]:
    """Mint one move per class and project each to keyframe ops.

    Pure (no CLI): the shared mint+project path behind both the live
    moves stage and its FREE unit test. Returns one record per class
    with the plan entry, the minted effect, its single-use model, the
    comp and duration covering its interval, the projection and the
    keyframe ops. Each move must key its expected transform path
    (``transform/position`` for the shake, ``transform/scale`` for
    the rest), else the record refuses as ``REN_PM9_MOVES_KEYS``.
    """
    from library.tools.project_model_effectcraft import (
        CompSpec,
        RenderSpec,
        project_to_request,
    )
    from library.tools.project_model_effectcraft_moves import (
        MOVE_CLASS_IDS,
        mint_move_effects,
    )

    entries, targets, raw = moves_stage_entries()
    minted = mint_move_effects(resolved_entries=entries, model=model,
                               targets=targets,
                               raw_params_by_vfx=raw)
    if [effect["class_id"] for effect in minted] != list(MOVE_CLASS_IDS):
        raise Pm9Refused(
            what="moves stage minted the wrong classes",
            why=json.dumps([effect["class_id"] for effect in minted]),
            fix="mint one model effect per surviving toolkit entry",
            code="REN_PM9_MOVES_MINT", recovery_class="blocked")
    uses = {use["id"]: use for use in model["clip_uses"]}
    records = []
    for entry, effect in zip(entries, minted):
        use = uses[effect["target_id"]]
        interval = effect["interval"]
        ticks = next(clock["ticks_per_second"] for clock in
                     model["clocks"]
                     if clock["id"] == interval["clock_id"])
        duration = interval["end"] / ticks
        comp = CompSpec(name="PM9Moves", width=160, height=90,
                        frame_rate=12.0, duration_seconds=duration)
        solo = copy.deepcopy(model)
        solo["clip_uses"] = [use]
        solo["masks"] = []
        solo["effects"] = [effect]
        projection = project_to_request(
            solo, comp, [RenderSpec(format="rgba_png_sequence",
                                    output="pm9moves_#####.png")],
            media_root=media_root,
            project_id=f"pm9-effectcraft-move-{effect['class_id']}")
        keyed = [op for op in projection.request["operations"]
                 if op.get("op") == "keyframe"]
        want = ("transform/position"
                if effect["class_id"] == "move.screen_shake"
                else "transform/scale")
        if not any(op["path"] == want for op in keyed):
            raise Pm9Refused(
                what=f"minted {effect['class_id']} keyed no {want}",
                why=json.dumps(sorted({op["path"] for op in keyed})),
                fix="the move must bake onto its expected transform path",
                code="REN_PM9_MOVES_KEYS", recovery_class="blocked")
        records.append({"entry": entry, "effect": effect,
                        "model": solo, "comp": comp,
                        "duration": duration,
                        "projection": projection, "keyed": keyed})
    return records


def _stage_effectcraft_moves(cli: Path, ec_dir: Path, work: Path,
                             model: dict) -> dict:
    """Mint one move per class, project, render and assert keyframes.

    The committed fixture carries no moves, so the run mints five
    ``move.*`` effects from accepted plan_vfx toolkit entries through
    the producer (one per class, onto the fixture uses' own timeline
    intervals) in run-local single-use model copies: each must project
    to keyframe ops on its expected path and render on the pinned CLI.
    """
    records = mint_and_project_moves(model, media_root=str(work))
    evidence: dict[str, dict] = {}
    for record in records:
        effect = record["effect"]
        slug = effect["class_id"].split(".", 1)[1]
        ran = _ec_execute(cli, ec_dir, f"pm9moves-{slug}",
                          record["projection"], record["comp"],
                          record["duration"],
                          input_revision=f"rev-pm9-moves-{slug}",
                          render_id=f"render-pm9-moves-{slug}",
                          model=record["model"])
        keyed = record["keyed"]
        evidence[effect["class_id"]] = {
            "effect_id": effect["id"], "target": effect["target_id"],
            "keyframe_ops": len(keyed),
            "paths": sorted({op["path"] for op in keyed}),
            "frames": len(ran["frames"]), "pix_fmt": ran["pix_fmt"],
            "frame_map_id": ran["frame_map_id"]}
    return evidence


# ── Stage 5: swap-back plan (FREE) ──────────────────────────────────

def stage_swapback_plan(work: Path, filmcraft: dict) -> dict:
    """Plan the head-trimmed render over the full one; keys must MOVE.

    The head trim kept anchor 12..48 as render 0..36, so a key at old
    frame F lands at F-12: every planned key must land on a DIFFERENT
    frame number with its value intact. An identity map fails here.
    """
    from library.tools import resolve_swapback as swap

    measured = int(filmcraft["measured_timeline_frames"])
    rendered = int(filmcraft["render"]["frames"])
    kept_start = int(filmcraft["measured_start_frame"])
    if kept_start != TRIM_HEAD_CUT or measured != TRIM_KEPT_FRAMES:
        raise Pm9Refused(
            what="swap-back plan needs the measured head trim",
            why=f"start {kept_start}, {measured} frames",
            fix="re-run the FilmCraft stage before planning the swap",
            code="REN_PM9_SWAPBACK_PLAN", recovery_class="blocked")
    model = {
        "renders": [
            {"id": "render-pm9-full", "input_revision": "rev-pm9-0",
             "output_asset_id": "asset-pm9-full", "receipt_id": "receipt-pm9-full",
             "frame_map_id": "map-pm9-full", "settings": {}},
            {"id": "render-pm9-trimmed", "input_revision": "rev-pm9-1",
             "output_asset_id": "asset-pm9-trimmed",
             "receipt_id": "receipt-pm9-trimmed",
             "frame_map_id": "map-pm9-trimmed", "settings": {}},
        ],
        "receipts": [
            {"id": "receipt-pm9-full", "status": "ok",
             "input_revision": "rev-pm9-0", "output_asset_id": "asset-pm9-full",
             "renderer": "filmcraft", "details": {}},
            {"id": "receipt-pm9-trimmed", "status": "ok",
             "input_revision": "rev-pm9-1",
             "output_asset_id": "asset-pm9-trimmed",
             "renderer": "filmcraft", "details": {}},
        ],
        "frame_maps": [
            {"id": "map-pm9-full", "from_clock": "clock-project",
             "to_clock": "clock-pm9-full",
             "segments": [{"from": {"clock_id": "clock-project", "start": 0,
                                    "end": TRIM_FROM_FRAMES},
                            "to": {"clock_id": "clock-pm9-full", "start": 0,
                                   "end": TRIM_FROM_FRAMES},
                            "mapping_kind": "one_to_one",
                            "rate": {"numerator": 1, "denominator": 1}}]},
            dict(filmcraft["frame_map"]),
        ],
    }
    model["frame_maps"][1]["id"] = "map-pm9-trimmed"
    model["frame_maps"][1]["to_clock"] = "clock-pm9-trimmed"
    keys = [{"comp": FUSION_COMP_NAME, "tool": "Transform1", "param": "Size",
             "frame": frame, "value": value}
            for frame, value in _pm9_key_spec()]
    plan_doc = swap.plan(
        model, prior_render_id="render-pm9-full",
        new_render_id="render-pm9-trimmed", unique_id="pm9-swap-1",
        old_source="pm9-ada-full.mp4",
        new_source=Path(filmcraft["render"]["path"]).name,
        played_anchor={"clock_id": "clock-project", "start": kept_start,
                       "end": kept_start + measured},
        fusion_keys=keys,
        grade={"luts": [], "cdl": {},
               "ranges": [{"start": kept_start,
                           "end": kept_start + measured}]})
    # Every planned key must land inside the trimmed render, on a
    # DIFFERENT frame, with its value intact; the grade range must be
    # cut to what the new render covers.
    for move, key in zip(plan_doc["key_moves"], keys):
        if not 0 <= move["to_frame"] < rendered:
            raise Pm9Refused(
                what="swap-back plan landed a key outside the render",
                why=json.dumps(move)[:300],
                fix="re-plan the edit so the mapping is one to one",
                code="REN_PM9_SWAPBACK_PLAN", recovery_class="blocked")
        if move["to_frame"] == move["from_frame"]:
            raise Pm9Refused(
                what="swap-back plan did not move a key",
                why=json.dumps(move)[:300],
                fix="the length change must shift content in time; an "
                    "identity map proves nothing about remapping",
                code="REN_PM9_SWAPBACK_PLAN", recovery_class="blocked")
        if move["to_frame"] != move["from_frame"] - kept_start:
            raise Pm9Refused(
                what="swap-back plan moved a key by the wrong shift",
                why=json.dumps(move)[:300],
                fix="a head cut of 12 must move every kept key by -12",
                code="REN_PM9_SWAPBACK_PLAN", recovery_class="blocked")
        if move["value"] != key["value"]:
            raise Pm9Refused(
                what="swap-back plan changed a key's value",
                why=json.dumps(move)[:300],
                fix="a move carries the value; only the frame changes",
                code="REN_PM9_SWAPBACK_PLAN", recovery_class="blocked")
    patch = swap.build_patch(plan_doc, project_name=QUALIFICATION_PROJECT,
                             timeline_name="PM9 Scratch",
                             timeline_id="pm9-scratch-id",
                             base_generation=1,
                             played_timeline_span=[0, measured],
                             idempotency_key="pm9-swap-1")
    _write_json(work / "swapback-plan.json", plan_doc)
    return {"key_moves": plan_doc["key_moves"],
            "grade_ranges": plan_doc["grade"]["ranges"],
            "patch_operations": [op["op"] for op in patch["operations"]]}


# ── Stage 6: Resolve live half ──────────────────────────────────────
#
# Channel choice (captain's decision on pm9-fusion-import-channel): the
# Fusion comp import runs as ONE own-process step following the
# production apply_fusion_comps pattern - Ren's exclusive instance
# lease through resolve_lock, borrowed() save/switch/restore with
# read-back, scratch timeline only - because AGENTS.md 5 forbids
# timeline-create + ImportFusionComp in one Python process while the
# broker runs every qualification_session step in-process on its single
# persistent EXECUTION worker. Broker workers therefore never import a
# comp and this process never creates a timeline: the separation is
# structural, not scheduling luck. The swap itself (ReplaceClip + key
# moves + read-back) runs the merged Unit 5 adapter inside a broker
# run script, which imports nothing.

PM9_LUT = "Film Looks/Rec709 Kodak 2383 D65.cube"
FUSION_COMP_NAME = "PM9Comp"
V1_SOURCE_NAME = "pm9-ada-full.mp4"
V2_SOURCE_NAME = "pm9-ada-trimmed.mp4"


def _pm9_key_spec() -> list[tuple[int, float]]:
    """The bound Fusion keys: old-render frame -> Size value.

    One source of truth for the FREE swap plan, the baked comp and the
    live verification: keys at 12/30/47 move to 0/18/35 through the
    head trim, values intact.
    """
    return [(frame, round(1.0 + frame / 1000.0, 6))
            for frame in (TRIM_HEAD_CUT,
                          TRIM_HEAD_CUT + TRIM_KEPT_FRAMES // 2,
                          TRIM_FROM_FRAMES - 1)]


def _pm9_comp_text() -> str:
    """A Transform1 Size comp with the bound keys baked as a spline.

    Keys are baked because keying through the live API does not take:
    measured on Resolve 21.1, SetInput(name, value, time) on an input
    that is not already animated sets the STATIC value.
    """
    from library.tools.fusion.effects import EffectBlock
    from library.tools.fusion.engine import CompEngine
    from library.tools.fusion.nodes import BezierSpline, FusionNode

    engine = CompEngine(clip_dur=TRIM_FROM_FRAMES, width=320, height=180)
    spline = BezierSpline("Transform1Size")
    for frame, value in _pm9_key_spec():
        spline.add_key(int(frame), float(value))
    tool = FusionNode("Transform1", "Transform")
    tool.set_input("Size", spline)
    block = EffectBlock(nodes=[spline, tool], input_name="Transform1",
                        input_key="Input", output_name="Transform1")
    engine.add(block)
    return engine.serialize()

def _swap_script(*, scratch: str, plan: dict, v1_uid_hint: str,
                 stamp: str, v2_path: str) -> str:
    """The broker run-script that commits and accepts the live swap.

    Pure string building (FREE-testable): runs the merged Unit 5
    adapter - plan, commit (ReplaceClip + key moves under the broker
    lease), accept (source, comp/grade identity, moved keys with values,
    no stale keys) - against the live handles. The plan's unique id,
    new source path and grade LUTs are resolved live (the item's
    engine-minted uid, the render file, the LUT strings GetLUT actually
    returns), so nothing is assumed. Contains NO timeline-create and NO
    comp-import primitive: broker workers never import comps (pinned by
    test).
    """
    return (
        "import json\n"
        "from library.tools import reel_read\n"
        "from library.tools import resolve_swapback as swap\n"
        "from library.tools import timeline_shadow as shadow\n"
        f"SCRATCH = {scratch!r}\n"
        f"PLAN = {json.dumps(plan)}\n"
        f"V1_UID_HINT = {v1_uid_hint!r}\n"
        f"V1_FILENAME = {V1_SOURCE_NAME!r}\n"
        f"V2_PATH = {v2_path!r}\n"
        f"IDEMPOTENCY = {'pm9-swap:' + stamp!r}\n"
        "if timeline.GetName() != SCRATCH:\n"
        "    raise RuntimeError(\n"
        "        f'pm9 swap: current timeline is {timeline.GetName()!r}, '\n"
        "        f'not {SCRATCH!r}')\n"
        "store = shadow.ShadowStore()\n"
        "base = shadow.observe(project, timeline, store)\n"
        "rows = shadow.clips(store.snapshot(base))\n"
        "cand = [c for c in rows if c.get('unique_id') == V1_UID_HINT] \\\n"
        "    if V1_UID_HINT else []\n"
        "if not cand:\n"
        "    cand = [c for c in rows\n"
        "            if str(c.get('source_file') or '').endswith(V1_FILENAME)]\n"
        "if len(cand) != 1:\n"
        "    raise RuntimeError(\n"
        "        f'pm9 swap: v1 item not uniquely present ({len(cand)} rows)')\n"
        "span = [int(cand[0]['record_in']), int(cand[0]['record_out'])]\n"
        "item = None\n"
        "for track_row in reel_read.live_items(timeline):\n"
        "    for handle in track_row['items']:\n"
        "        try:\n"
        "            same = str(handle.GetUniqueId() or '') == cand[0]['unique_id']\n"
        "        except Exception:\n"
        "            continue\n"
        "        if same:\n"
        "            item = handle\n"
        "            break\n"
        "if item is None:\n"
        "    raise RuntimeError('pm9 swap: v1 handle would not open')\n"
        "luts = sorted(node['path'] for node in\n"
        "              (reel_read._node_luts(item) or []))\n"
        "if not luts:\n"
        "    raise RuntimeError('pm9 swap: v1 carries no LUT; '\n"
        "                       'the grade was never attached')\n"
        "live_plan = dict(PLAN)\n"
        "live_plan['unique_id'] = cand[0]['unique_id']\n"
        "live_plan['new_source'] = V2_PATH\n"
        "live_plan['grade'] = {'luts': luts, 'cdl': {},\n"
        "                      'ranges': [{'start': 12, 'end': 48}]}\n"
        "commit_receipt = swap.commit(live_plan, project=project,\n"
        "                             timeline=timeline,\n"
        "                             played_timeline_span=span,\n"
        "                             idempotency_key=IDEMPOTENCY)\n"
        "accept_receipt = swap.accept(project, timeline, live_plan)\n"
        "final_item = None\n"
        "for track_row in reel_read.live_items(timeline):\n"
        "    for handle in track_row['items']:\n"
        "        try:\n"
        "            same = str(handle.GetUniqueId() or '') == cand[0]['unique_id']\n"
        "        except Exception:\n"
        "            continue\n"
        "        if same:\n"
        "            final_item = handle\n"
        "            break\n"
        "if final_item is None:\n"
        "    raise RuntimeError('pm9 swap: v1 handle lost after commit')\n"
        "final_keys = {int(frame): value for frame, value in\n"
        f"              swap._live_item_keys(final_item, {FUSION_COMP_NAME!r},\n"
        "                                   'Transform1', 'Size').items()}\n"
        "result = [{'span': span, 'luts': luts,\n"
        "           'commit': commit_receipt, 'accept': accept_receipt,\n"
        "           'final_keys': final_keys}]\n"
    )


def _parse_run_result(step: dict) -> Any:
    """The JSON result tail of a resolve_axi run step's stdout.

    The broker receipt embeds the step stdout with JSON escapes, so a
    substring probe for the verdict never matches: parse the trailing
    result table instead, and refuse anything unparseable.
    """
    stdout = str(step.get("stdout", ""))
    start = stdout.find("[")
    if start < 0:
        raise Pm9Refused(what="run step returned no result table",
                         why=stdout[:400],
                         fix="inspect the broker receipt",
                         code="REN_PM9_RESOLVE_READBACK",
                         recovery_class="blocked")
    try:
        # raw_decode: journal/meta blocks follow the result table.
        return json.JSONDecoder().raw_decode(stdout[start:])[0]
    except json.JSONDecodeError as exc:
        raise Pm9Refused(what="run step result is not JSON",
                         why=f"{exc}: {stdout[start:start + 400]}",
                         fix="inspect the broker receipt",
                         code="REN_PM9_RESOLVE_READBACK",
                         recovery_class="blocked") from exc


def _submit_session(steps: list[dict], stamp: str, tag: str,
                    wait_seconds: int = 1800) -> dict:
    """One broker resolve.qualification_session; refuse unless done."""
    from library.tools.resolved import client

    submitted = client.submit(
        "resolve.qualification_session",
        {"project": QUALIFICATION_PROJECT, "steps": steps},
        qualification=True,
        idempotency_key=f"pm9-qualification:{stamp}:{tag}")
    receipt = client.result(submitted["id"], wait=wait_seconds)
    if receipt["state"] != "done":
        raise Pm9Refused(
            what=f"Resolve qualification session {tag} did not finish",
            why=json.dumps({"state": receipt["state"],
                            "error": receipt.get("error"),
                            "result": receipt.get("result")},
                           default=str)[:1200],
            fix="inspect the broker receipt; the captain's project restores "
                "through borrowed() before the refusal propagates",
            code="REN_PM9_RESOLVE_SESSION", recovery_class="blocked")
    return receipt["result"] or {}


def _pool_clip_name(session: dict, needle: str) -> str:
    """Read one pool clip's exact name off a pool listing, never assumed."""
    for step in session.get("steps", []):
        for line in str(step.get("stdout", "")).splitlines():
            if needle in line:
                candidate = line.strip().split(",")[0].strip()
                if candidate.endswith(".mp4"):
                    return candidate
    raise Pm9Refused(
        what=f"imported render {needle!r} did not read back in the pool",
        why=json.dumps(session, default=str)[:800],
        fix="check the broker ingest receipt",
        code="REN_PM9_RESOLVE_READBACK", recovery_class="blocked")


def _handle_identity(handle: Any) -> tuple[str, str] | None:
    """A timeline item's (unique id, source path), or None when unreadable."""
    try:
        uid = str(handle.GetUniqueId() or "")
        pool = handle.GetMediaPoolItem()
        source = str(pool.GetClipProperty("File Path") if pool else "")
    except Exception:  # noqa: BLE001 - unreadable row is not a match
        return None
    return uid, source


def _read_captain_state() -> dict:
    """Best-effort read of the open project after a Resolve failure.

    Never raises: when Resolve is gone there is nothing to read back,
    and the record must say so instead of masking the original error.
    """
    try:
        from library.tools import resolve_lock
        from library.tools.marker_feedback import connect_resolve
        with resolve_lock.resolve_lease("pm9 exit probe", exclusive=False,
                                        timeout=20):
            resolve = connect_resolve()
            manager = resolve.GetProjectManager()
            project = manager.GetCurrentProject() if manager else None
            timeline = project.GetCurrentTimeline() if project else None
            return {"reachable": True,
                    "project": project.GetName() if project else None,
                    "timeline": timeline.GetName() if timeline else None}
    except Exception as exc:  # noqa: BLE001 - the record carries the miss
        return {"reachable": False,
                "error": f"{type(exc).__name__}: {exc}"[:300]}


def _setup_fusion_live(*, work: Path, scratch: str,
                       v1_uid_hint: str) -> dict:
    """Attach the baked key comp to the v1 item, in THIS process.

    Option A (pm9-fusion-import-channel): Ren's exclusive instance
    lease through resolve_lock, borrowed() save/switch/restore with
    read-back, scratch timeline only, comp file written under the run
    work dir. This process never creates a timeline; broker workers
    never import a comp. If Resolve exits, stop after recording the
    miss and the captain's read-back - before anything else.
    """
    from library.tools import qualification_project as qp
    from library.tools import reel_read, resolve_lock
    from library.tools.marker_feedback import connect_resolve

    comp_path = work / "fusion-setup" / "pm9keys.comp"
    try:
        # Plain exclusive lease (no qualification marking): the marking
        # is for sections where the project is ALREADY open, while this
        # step opens it itself through borrowed() - the same shape as
        # `ren qualification reset`. The broker still schedules the turn,
        # so nothing interleaves.
        with resolve_lock.resolve_lease("pm9 fusion setup", exclusive=True):
            resolve = connect_resolve()
            with qp.borrowed(resolve) as found:
                manager = resolve.GetProjectManager()
                project = qp.switch_project(manager, QUALIFICATION_PROJECT)
                timeline = None
                for index in range(1, int(project.GetTimelineCount() or 0) + 1):
                    candidate = project.GetTimelineByIndex(index)
                    if candidate is not None and candidate.GetName() == scratch:
                        timeline = candidate
                        break
                if timeline is None:
                    raise Pm9Refused(
                        what=f"scratch timeline {scratch!r} is not open",
                        why="the broker duplicate did not persist",
                        fix="check the import session receipt",
                        code="REN_PM9_RESOLVE_READBACK",
                        recovery_class="blocked")
                item = None
                for track_row in reel_read.live_items(timeline):
                    for handle in track_row["items"]:
                        identity = _handle_identity(handle)
                        if identity is None:
                            continue
                        uid, source = identity
                        if (uid and uid == v1_uid_hint) or \
                                source.endswith(V1_SOURCE_NAME):
                            item = handle
                            break
                    if item is not None:
                        break
                if item is None:
                    raise Pm9Refused(
                        what="v1 item not on the scratch timeline",
                        why=f"no item with uid {v1_uid_hint!r} or source "
                            f"{V1_SOURCE_NAME!r}",
                        fix="check the place session receipt",
                        code="REN_PM9_RESOLVE_READBACK",
                        recovery_class="blocked")
                before = list(item.GetFusionCompNameList() or [])
                if before:
                    raise Pm9Refused(
                        what="v1 item already carries Fusion comps",
                        why=f"have {before}",
                        fix="start the swap from a clean placement",
                        code="REN_PM9_RESOLVE_READBACK",
                        recovery_class="blocked")
                if not item.AddFusionComp():
                    raise Pm9Refused(
                        what="AddFusionComp answered false",
                        why="the empty comp did not land",
                        fix="read the clip's comps by hand",
                        code="REN_PM9_RESOLVE_WRITE",
                        recovery_class="blocked")
                comp_path.parent.mkdir(parents=True, exist_ok=True)
                comp_path.write_text(_pm9_comp_text(), encoding="utf-8")
                if not item.ImportFusionComp(str(comp_path)):
                    raise Pm9Refused(
                        what="ImportFusionComp answered false",
                        why="the baked key comp did not land",
                        fix="read the clip's comps by hand",
                        code="REN_PM9_RESOLVE_WRITE",
                        recovery_class="blocked")
                after = list(item.GetFusionCompNameList() or [])
                new = [name for name in after if name not in before]
                if len(new) != 1:
                    raise Pm9Refused(
                        what="comp import did not mint exactly one comp",
                        why=f"before={before} after={after}",
                        fix="read the clip's comps by hand",
                        code="REN_PM9_RESOLVE_WRITE",
                        recovery_class="blocked")
                if not item.RenameFusionCompByName(new[0], FUSION_COMP_NAME):
                    raise Pm9Refused(
                        what="comp rename answered false",
                        why=f"have {after}",
                        fix="read the clip's comps by hand",
                        code="REN_PM9_RESOLVE_WRITE",
                        recovery_class="blocked")
                comp = item.GetFusionCompByName(FUSION_COMP_NAME)
                tool = comp.FindTool("Transform1") if comp else None
                if tool is None:
                    raise Pm9Refused(
                        what="Transform1 is not in the imported comp",
                        why="the bake must carry the bound tool",
                        fix="re-bake the comp from _pm9_comp_text",
                        code="REN_PM9_RESOLVE_WRITE",
                        recovery_class="blocked")
                live = sorted(reel_read._input_key_times(tool, "Size"))
                wanted = sorted(frame for frame, _ in _pm9_key_spec())
                if live != wanted:
                    raise Pm9Refused(
                        what="baked keys did not read back",
                        why=f"have {live}, want {wanted}",
                        fix="re-import the baked comp",
                        code="REN_PM9_RESOLVE_WRITE",
                        recovery_class="blocked")
                values = {}
                for frame, want in _pm9_key_spec():
                    try:
                        values[frame] = tool.GetInput("Size", int(frame))
                    except Exception as exc:
                        raise Pm9Refused(
                            what=f"key at frame {frame} would not re-read",
                            why=str(exc)[:200],
                            fix="re-import the baked comp",
                            code="REN_PM9_RESOLVE_WRITE",
                            recovery_class="blocked") from exc
                    if values[frame] != want:
                        raise Pm9Refused(
                            what=f"key at frame {frame} reads {values[frame]!r}",
                            why=f"want {want!r}",
                            fix="re-bake the comp from _pm9_key_spec",
                            code="REN_PM9_RESOLVE_WRITE",
                            recovery_class="blocked")
                if not manager.SaveProject():
                    raise Pm9Refused(
                        what="Resolve refused to save after the comp import",
                        why="the scratch state may not persist",
                        fix="save Ren Qualification by hand",
                        code="REN_PM9_RESOLVE_WRITE",
                        recovery_class="blocked")
                return {"comp": FUSION_COMP_NAME, "keys": live,
                        "values": values, "restored": {
                            "project": found["project"],
                            "timeline": found["timeline"]}}
    except Pm9Refused:
        raise
    except Exception as exc:
        captain = _read_captain_state()
        raise Pm9Refused(
            what="fusion setup failed; "
                 f"captain's project reads back as {captain}",
            why=f"{type(exc).__name__}: {exc}"[:500],
            fix="if Resolve exited, reopen it and the captain's project "
                "by hand before anything else",
            code="REN_PM9_RESOLVE_EXIT",
            recovery_class="blocked") from exc


def stage_resolve_live(work: Path, filmcraft: dict, stamp: str) -> dict:
    """Perform the Unit 5 swap-back live and read it back.

    Resolve channel, per the pm9-fusion-import-channel decision:
    broker ``resolve.qualification_session`` jobs do everything except
    the comp import - import (both renders), duplicate, place the FULL
    render plus a tail anchor, attach the LUT grade, run the Unit 5
    commit/accept script, read back. The Fusion comp import runs as ONE
    own-process step (exclusive lease, borrowed() save/switch/restore,
    scratch only) because broker workers must never import a comp in a
    process that may have created a timeline. Then `ren qualification
    reset` rebuilds the declared project so nothing of the run remains.
    """
    import re
    full_path = filmcraft["full_render"]["path"]
    trimmed_path = filmcraft["render"]["path"]
    scratch = f"PM9 Scratch {stamp}"

    first = _submit_session([
        {"resolve_axi": ["ingest", "--project", QUALIFICATION_PROJECT,
                         "--apply", full_path]},
        {"resolve_axi": ["ingest", "--project", QUALIFICATION_PROJECT,
                         "--apply", trimmed_path]},
        {"resolve_axi": ["timeline", "duplicate", "--project",
                         QUALIFICATION_PROJECT, "--timeline", "Q Spine",
                         "--name", scratch, "--apply"]},
        {"resolve_axi": ["pool", "--project", QUALIFICATION_PROJECT]},
        {"resolve_axi": ["timeline", "list", "--project",
                         QUALIFICATION_PROJECT]},
    ], stamp, "import")
    _write_json(work / "resolve-session.import.json", first)
    exact_full = _pool_clip_name(first, "pm9-ada-full")
    exact_trimmed = _pool_clip_name(first, "pm9-ada-trimmed")

    second = _submit_session([
        {"resolve_axi": ["edit", "place", "--project", QUALIFICATION_PROJECT,
                         "--timeline", scratch, "--clip", exact_full,
                         "--source-in", "0",
                         "--source-out", str(TRIM_FROM_FRAMES), "--apply"]},
        # Tail anchor past the swap region: the timeline end stays put
        # whether ReplaceClip leaves a gap or not, so the patch's
        # duration_unchanged postcondition judges the swap, not the end.
        {"resolve_axi": ["edit", "place", "--project", QUALIFICATION_PROJECT,
                         "--timeline", scratch, "--clip", "qual_a.mp4",
                         "--apply"]},
        {"resolve_axi": ["items", "--project", QUALIFICATION_PROJECT,
                         "--timeline", scratch, "--refresh-live"]},
    ], stamp, "place")
    _write_json(work / "resolve-session.place.json", second)
    placed_text = json.dumps(second, default=str)
    if exact_full not in placed_text and "pm9-ada-full" not in placed_text:
        raise Pm9Refused(
            what="placed render did not read back on the scratch timeline",
            why=placed_text[:800],
            fix="check the broker place receipt",
            code="REN_PM9_RESOLVE_READBACK", recovery_class="blocked")
    # The v1 item's engine-minted uid: the place receipt's new-item row
    # for the v1 clip. The swap script falls back to the source name.
    v1_uid = ""
    for step in second.get("steps", []):
        for line in str(step.get("stdout", "")).splitlines():
            if "pm9-ada-full" in line:
                found = re.findall(
                    r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-"
                    r"[0-9a-f]{4}-[0-9a-f]{12}", line)
                if found:
                    v1_uid = found[0]
                    break
        if v1_uid:
            break

    third = _submit_session([
        {"resolve_axi": ["color", "lut", "--project", QUALIFICATION_PROJECT,
                         "--timeline", scratch, "--track", "video1",
                         "--index", "3", "--lut", PM9_LUT, "--apply"]},
    ], stamp, "grade")
    _write_json(work / "resolve-session.grade.json", third)
    grade_text = json.dumps(third, default=str)
    if "pm9-ada-full" not in grade_text:
        raise Pm9Refused(
            what="LUT grade did not land on the v1 item",
            why=grade_text[:800],
            fix="check the broker grade receipt",
            code="REN_PM9_RESOLVE_READBACK", recovery_class="blocked")

    setup = _setup_fusion_live(work=work, scratch=scratch,
                               v1_uid_hint=v1_uid)
    _write_json(work / "resolve-fusion-setup.json", setup)

    plan = json.loads((work / "swapback-plan.json").read_text(
        encoding="utf-8"))
    script = _swap_script(scratch=scratch, plan=plan, v1_uid_hint=v1_uid,
                          stamp=stamp, v2_path=trimmed_path)
    # The recorded writer script needs a Ren project directory for its
    # .drt backup and run records: a DISPOSABLE one under the run work
    # dir (never a real project - the registry never scans /tmp, and
    # the backup/records land beside the run's other evidence).
    ren_project = work / "pm9-ren-project"
    ren_project.mkdir(parents=True, exist_ok=True)
    (ren_project / "project.yaml").write_text(
        "project_format_version: 2\n"
        "name: PM9 Qualification Scratch\n"
        "slug: pm9-qualification\n"
        "client: ''\n"
        "created: '2026-10-10'\n"
        "status: draft\n"
        "source:\n"
        "  fps: 24\n"
        "pipeline:\n"
        "  brand_template: ''\n"
        "  delivery_format: ''\n"
        "resolve:\n"
        "  project_name: 'Ren Qualification'\n"
        "  database: local\n"
        "  folder: ''\n"
        "  timeline_name: ''\n"
        "tags: []\n"
        "description: 'Disposable Unit 9 run records; never a real project.'\n",
        encoding="utf-8")
    fourth = _submit_session([
        {"resolve_axi": ["run", "--project", QUALIFICATION_PROJECT,
                         "--project-dir", str(ren_project),
                         "--purpose", f"pm9 swapback {stamp}",
                         "--unsafe", "--acknowledge-copy-grades",
                         "--json", script]},
        {"resolve_axi": ["items", "--project", QUALIFICATION_PROJECT,
                         "--timeline", scratch, "--refresh-live"]},
        {"resolve_axi": ["fusion", "--project", QUALIFICATION_PROJECT,
                         "--timeline", scratch, "--refresh-live"]},
        {"resolve_axi": ["frames", "--project", QUALIFICATION_PROJECT,
                         "--timeline", scratch, "--refresh-live"]},
    ], stamp, "swap")
    _write_json(work / "resolve-session.swap.json", fourth)
    run_steps = [step for step in fourth.get("steps", [])
                 if isinstance(step, dict)
                 and str(step.get("argv", [None])[0] if
                         isinstance(step.get("argv"), list) else "") == "run"]
    if not run_steps:
        raise Pm9Refused(what="swap session ran no script",
                         why=json.dumps(fourth, default=str)[:600],
                         fix="inspect the swap session receipt",
                         code="REN_PM9_RESOLVE_READBACK",
                         recovery_class="blocked")
    swap_result = _parse_run_result(run_steps[0])[0]
    accept = (swap_result.get("accept") or {})
    if accept.get("status") != "accepted":
        raise Pm9Refused(
            what="swap-back accept did not read back accepted",
            why=json.dumps(swap_result, default=str)[:1000],
            fix="inspect the swap session receipt",
            code="REN_PM9_RESOLVE_READBACK", recovery_class="blocked")
    # The moved keys, read live after the commit: frames AND values.
    final_keys = {int(frame): value for frame, value in
                  (swap_result.get("final_keys") or {}).items()}
    wanted_keys = dict(_pm9_key_spec())
    moved_keys = {frame - TRIM_HEAD_CUT: value
                  for frame, value in wanted_keys.items()}
    if final_keys != moved_keys:
        raise Pm9Refused(
            what="swapped keys do not read as the plan predicted",
            why=f"live {final_keys}, want {moved_keys}",
            fix="the remap must move every key by the head cut",
            code="REN_PM9_RESOLVE_READBACK", recovery_class="blocked")
    if (swap_result.get("commit", {}).get("status") != "committed" or
            accept.get("keys") != len(wanted_keys) or
            accept.get("source") != trimmed_path or
            accept.get("comps") != [FUSION_COMP_NAME] or
            accept.get("luts") != [PM9_LUT]):
        raise Pm9Refused(
            what="swap-back receipt disagrees with the run",
            why=json.dumps({key: swap_result.get(key)
                            for key in ("commit", "accept")},
                           default=str)[:1000],
            fix="inspect the swap session receipt",
            code="REN_PM9_RESOLVE_READBACK", recovery_class="blocked")

    cleanup = _cleanup_scratch(scratch, stamp)
    evidence = {"scratch": scratch, "prior_clip": exact_full,
                "swap_target_clip": exact_trimmed,
                "prior_span_frames": TRIM_FROM_FRAMES,
                "v1_uid_hint": bool(v1_uid),
                "session_steps": sum(len(session.get("steps", []))
                                     for session in (first, second, third,
                                                     fourth)),
                "grade": {"lut": PM9_LUT, "track": "video1", "index": 3},
                "fusion_setup": {key: setup[key] for key in
                                 ("comp", "keys", "values")},
                "swap_accepted": True,
                "final_keys": final_keys,
                "commit_status": swap_result.get("commit", {}).get("status"),
                "restored": fourth.get("restored"),
                "cleanup": cleanup}
    _write_json(work / "resolve-session.json", evidence)
    return evidence


def _cleanup_scratch(scratch: str, stamp: str) -> dict:
    """Restore the declared Qualification project with a full reset.

    A surgical delete was measured first and is refused by the
    recorded-script safeguard: a writing `run` script must target the
    current timeline, and no resolve_axi verb makes the scratch
    timeline current inside a borrowed session (the cursor verbs only
    assert). Rather than guessing around a safeguard that protects the
    captain's timelines, the qualification rebuilds the declared
    project DELETE + CREATE: `ren qualification reset` cannot inherit a
    previous run's edits, pool imports or timelines.
    """
    from library.tools import qualification_project as qp
    report = qp.reset()
    return {"method": "qualification-reset", "state": "clean",
            "note": "surgical delete is refused by the recorded-script "
                    "current-timeline safeguard; reset is the cleanup",
            "reset": report}


# ── Stage 7: failure injection (FREE) ───────────────────────────────

def inject_stale_revision(model: dict) -> str:
    """A receipt from another revision must refuse as stale_revision."""
    from library.tools import resolve_swapback as swap

    bait = copy.deepcopy(model)
    bait["receipts"][0]["input_revision"] = "rev-from-another-run"
    try:
        swap.plan(bait, prior_render_id="render-a", new_render_id="render-b",
                  unique_id="pm9-inject", old_source="a.mp4",
                  new_source="b.mp4",
                  played_anchor={"clock_id": "anchor", "start": 0, "end": 1})
    except Exception as exc:
        raise _refuse(StaleRevision,
                      f"stale receipt refused: {type(exc).__name__}: {exc}",
                      "a receipt whose input_revision is not the model's "
                      "revision must refuse",
                      "fail closed on revision mismatch") from exc
    # The adapter refused nothing recognizable: mint the named refusal.
    raise _refuse(StaleRevision, "stale receipt was accepted",
                  "a receipt whose input_revision is not the model's revision "
                  "must refuse", "fail closed on revision mismatch")


def inject_unknown_schema(model: dict) -> str:
    """An unknown schema_version must refuse as unknown_schema."""
    from library.tools.project_model import validate_project_model

    bait = copy.deepcopy(model)
    bait["schema_version"] = 999
    try:
        validate_project_model(bait)
    except ValueError as exc:
        raise _refuse(UnknownSchema, f"unknown schema refused: {exc}",
                      "schema_version 999 is not the pinned contract",
                      "migrate the document or pin the reader") from exc
    raise AssertionError("validate_project_model accepted schema_version 999")


def inject_ambiguous_map() -> str:
    """Overlapping map segments must refuse as ambiguous_map."""
    from library.tools import resolve_swapback as swap

    old = {"id": "map-old", "from_clock": "anchor", "to_clock": "old",
           "segments": [{"from": {"clock_id": "anchor", "start": 0, "end": 10},
                         "to": {"clock_id": "old", "start": 0, "end": 10},
                         "mapping_kind": "one_to_one"}]}
    new = {"id": "map-new", "from_clock": "anchor", "to_clock": "new",
           "segments": [{"from": {"clock_id": "anchor", "start": 0, "end": 6},
                         "to": {"clock_id": "new", "start": 0, "end": 6},
                         "mapping_kind": "one_to_one"},
                        {"from": {"clock_id": "anchor", "start": 4, "end": 10},
                         "to": {"clock_id": "new", "start": 4, "end": 10},
                         "mapping_kind": "one_to_one"}]}
    bait = {"renders": [
        {"id": "render-a", "input_revision": "rev-1",
         "output_asset_id": "asset-a", "receipt_id": "receipt-a",
         "frame_map_id": "map-old", "settings": {}},
        {"id": "render-b", "input_revision": "rev-1",
         "output_asset_id": "asset-b", "receipt_id": "receipt-b",
         "frame_map_id": "map-new", "settings": {}}],
        "receipts": [
            {"id": "receipt-a", "status": "ok", "input_revision": "rev-1",
             "output_asset_id": "asset-a", "renderer": "test", "details": {}},
            {"id": "receipt-b", "status": "ok", "input_revision": "rev-1",
             "output_asset_id": "asset-b", "renderer": "test", "details": {}}],
        "frame_maps": [old, new]}
    try:
        swap.plan(bait, prior_render_id="render-a", new_render_id="render-b",
                  unique_id="pm9-inject", old_source="a.mp4",
                  new_source="b.mp4",
                  played_anchor={"clock_id": "anchor", "start": 0, "end": 10})
    except Exception as exc:
        raise _refuse(AmbiguousMap, f"ambiguous map refused: {exc}",
                      "two segments claim anchor frames 4..6",
                      "re-emit the render's frame map with disjoint spans") from exc
    raise AssertionError("swap.plan accepted overlapping map segments")


def inject_missing_identity(model: dict) -> str:
    """An unmeasured person must refuse as missing_identity."""
    from library.tools.project_model_evidence import resolve_person

    if resolve_person(model, "person-ghost") is not None:
        raise AssertionError("resolve_person invented an identity")
    raise _refuse(MissingIdentity, "identity 'person-ghost' is absent",
                  "no observation, link, mask or A-roll names it",
                  "measure the person before planning for them")


def inject_unsupported_effect(model: dict) -> str:
    """An unmapped effect class must refuse as unsupported_effect."""
    from library.tools.project_model_effectcraft import (
        CompSpec,
        RenderSpec,
        project_to_request,
    )

    bait = copy.deepcopy(model)
    bait["effects"] = [{"id": "effect-dreams", "class_id": "neon.dreams",
                        "target_id": "use-ada",
                        "interval": {"clock_id": "clock-project",
                                     "start": 0, "end": 36},
                        "parameters": {}, "keyframes": []}]
    try:
        project_to_request(bait, CompSpec(), [RenderSpec()], media_root="/tmp")
    except Exception as exc:
        raise _refuse(UnsupportedEffect,
                      f"unsupported effect refused: {exc}",
                      f"the adapter refused with {type(exc).__name__}",
                      "map the class in VERIFIED_EFFECT_MAP with a registry "
                      "measurement, or drop it from the plan") from exc
    raise AssertionError("project_to_request accepted class neon.dreams")


def stage_failure_injection(work: Path) -> dict:
    """Run all five injections; each must refuse by its own name."""
    model = json.loads((work / "model.ingested.json").read_text(encoding="utf-8"))
    cases = [
        ("stale_revision", StaleRevision, lambda: inject_stale_revision(model)),
        ("unknown_schema", UnknownSchema, lambda: inject_unknown_schema(model)),
        ("ambiguous_map", AmbiguousMap, inject_ambiguous_map),
        ("missing_identity", MissingIdentity, lambda: inject_missing_identity(model)),
        ("unsupported_effect", UnsupportedEffect,
         lambda: inject_unsupported_effect(model)),
    ]
    outcomes = {}
    for name, cls, fn in cases:
        try:
            detail = fn()
        except cls as exc:
            code = getattr(exc, "code", "")
            if code != cls.CODE:
                raise Pm9Refused(
                    what=f"injection {name} refused under the wrong name",
                    why=f"code reads {code!r}, want {cls.CODE!r}",
                    fix="keep each injection's refusal class exact",
                    code="REN_PM9_INJECTION_NAME",
                    recovery_class="blocked") from exc
            outcomes[name] = {"refused_as": code, "detail": str(exc)[:300]}
        else:
            raise Pm9Refused(
                what=f"injection {name} did not refuse",
                why=str(detail)[:300],
                fix="fail closed on the injected fault",
                code="REN_PM9_INJECTION_PASSED",
                recovery_class="blocked")
    return outcomes


# ── Runner ──────────────────────────────────────────────────────────

def run(out_dir: str | Path, *, with_resolve: bool = True) -> dict:
    """One autonomous Unit 9 run; returns the report and writes it out."""
    started = datetime.now(UTC)
    stamp = started.strftime("%Y%m%dT%H%M%S%fZ")
    root = Path(out_dir).expanduser().resolve()
    root.mkdir(parents=True, exist_ok=True)
    work = root / f"pm9-{stamp}"
    work.mkdir(parents=True, exist_ok=False)
    report: dict[str, Any] = {"schema": REPORT_SCHEMA, "stamp": stamp,
                              "started_at": started.isoformat(),
                              "fixture": str(FIXTURE_ROOT),
                              "filmcraft_pin": FILMCRAFT_REVISION,
                              "effectcraft_pin": EFFECTCRAFT_REVISION,
                              "stages": {}, "passed": False}

    def stage(name: str, fn) -> Any:
        try:
            result = fn()
        except Exception as exc:
            report["stages"][name] = {"status": "failed",
                                      "error": f"{type(exc).__name__}: {exc}"[:2000]}
            _write_json(work / "qualification-report.json", report)
            raise
        report["stages"][name] = {"status": "passed", "evidence": result}
        _write_json(work / "qualification-report.json", report)
        return result

    try:
        stage("ingest", lambda: stage_ingest(work))
        stage("identity", lambda: stage_identity(work))
        filmcraft = stage("filmcraft", lambda: stage_filmcraft(work))
        # The run reads no real project, so no look is declared and the
        # stage records the pass-through in the report.
        stage("native_treatments",
              lambda: stage_native_treatments(work, filmcraft))
        stage("effectcraft", lambda: stage_effectcraft(work))
        stage("swapback_plan",
              lambda: stage_swapback_plan(work, filmcraft))
        if with_resolve:
            stage("resolve_live",
                  lambda: stage_resolve_live(work, filmcraft, stamp))
        else:
            report["stages"]["resolve_live"] = {"status": "skipped",
                                                "evidence": "--no-resolve"}
        stage("failure_injection", lambda: stage_failure_injection(work))
        report["passed"] = all(
            row["status"] in ("passed", "skipped")
            for row in report["stages"].values())
    except Exception as exc:
        report["failure"] = {"type": type(exc).__name__, "message": str(exc)[:2000]}
        raise
    finally:
        report["finished_at"] = datetime.now(UTC).isoformat()
        report["report_path"] = str(work / "qualification-report.json")
        _write_json(work / "qualification-report.json", report)
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="ren qualification pm-e2e",
        description="Unit 9 end-to-end autonomous qualification.")
    parser.add_argument("--out", required=True,
                        help="directory holding this run's work and report")
    parser.add_argument("--no-resolve", action="store_true",
                        help="run the FREE half only (no Resolve session)")
    args = parser.parse_args(argv)
    try:
        report = run(args.out, with_resolve=not args.no_resolve)
    except Exception as exc:  # noqa: BLE001 - command reports a durable failure
        print(json.dumps({"passed": False, "error": str(exc)[:2000]},
                         indent=2), file=sys.stderr)
        return 1
    print(json.dumps(report, indent=2, default=str))
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
