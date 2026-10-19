"""VC-14: a versioned component manifest per reel, attached to the creative revision.

A reel's approved timeline is produced from six derived components -
A-roll assembly, captions, motion graphics, Fusion, audio, timeline -
and until now the only per-revision account of what landed is the
whole-timeline rows snapshot (`reel_read.rows_of`, stored on the
`reel_versions` entry). Comparing two revisions therefore means
comparing two whole timelines. This module records, per revision, one
small manifest per reel that says what each component was built from,
so Ren can answer "which components differ between these two
revisions" without comparing whole timelines.

What a manifest holds, per component: a stable id, the dependency
fingerprints its build read, content hashes of generated assets it
placed, and the producing implementation version. The manifest's own
identity is `manifest_digest`, over the canonical components - never
a Resolve timeline name, so a rename, duplicate or replace cannot
change what the manifest says the reel IS.

Sources are existing records only: the version entry's own rows, the
assembly manifest, provenance (`built_with`, `caption_hashes`,
`footage_binding_hashes`), the recorded Fusion manifest, the subtitle
render ledger. Nothing here reads Resolve and nothing calls a model,
so the builder runs inside the build/touch-up commit path for the
cost of a few kilobytes of hashing. A source that is absent is
recorded absent (`status: absent|unkeyed` with a detail naming it) -
never invented, never a hollow digest.

VC-06 (new format only with its reader): the reader lives here beside
the writer - `manifest_of_version`, `latest_manifest` - and the format
is versioned (`reel_component_manifest/1`). A corrupt attached
manifest raises `ManifestUnreadable` rather than reading as empty;
an entry that predates manifests reads as None, which every differ
treats as "unknown", never as "unchanged".

This module decides nothing about invalidation: it only records what
a revision holds and diffs two records. `cascade_graph` /
`reel_rebuild_need` stay the owners of every rebuild decision.
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

FORMAT = "reel_component_manifest/1"

#: The derived components one reel is built from, in plan order.
COMPONENTS = ("aroll", "captions", "motion_graphics", "fusion", "audio", "timeline")

#: A component whose inputs were all readable.
RECORDED = "recorded"
#: A component whose source record is not on disk. Named, never hollow.
ABSENT = "absent"
#: The assembly manifest is on disk but names no entry for this reel,
#: so slice-derived components cannot be attributed to it.
UNKEYED = "unkeyed"

#: Generated overlay assets hashed per manifest build, at most. Source
#: footage is NEVER hashed here - gigabytes per reel, and an input, not
#: a derived asset. Footage identity arrives as the footage binding
#: fingerprint the provenance already records.
MAX_HASHED_ASSETS = 32
MAX_ASSET_BYTES = 256 * 1024 * 1024


class ManifestUnreadable(RuntimeError):
    """An attached component manifest exists and cannot be read."""


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _digest_file(path: Path) -> dict | None:
    """`{"sha256", "bytes"}` for a file, or None where it cannot be read."""
    try:
        hasher = hashlib.sha256()
        size = 0
        with open(path, "rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                hasher.update(chunk)
                size += len(chunk)
        return {"sha256": hasher.hexdigest(), "bytes": size}
    except OSError:
        return None


def _read_json(path: Path) -> dict | None:
    """The JSON document at `path`, or None where it is missing/invalid."""
    try:
        with open(path, encoding="utf-8") as handle:
            document = json.load(handle)
    except (OSError, ValueError):
        return None
    return document if isinstance(document, dict) else None


def _reel_key(final: str) -> tuple[str, str]:
    """The reel's stable key and how it was derived.

    The Resolve timeline name (`final`) is a label - renames,
    duplicates and replacements change it while the reel stays the
    reel. The key prefers the parsed reel number (`reel-07`), which
    survives all three, and falls back to the final name only where
    no number parses, saying so in the source.
    """
    try:
        from library.tools.reel_state import reel_number_of

        number = reel_number_of(final)
    except Exception:  # noqa: BLE001 - a key derivation never fails a build
        number = None
    if number is not None:
        return f"reel-{int(number):02d}", "reel-number"
    return str(final), "final-fallback"


def _produced_by() -> str | None:
    """The implementation version that built this reel, or None.

    `None` where the engine tree is absent (tests hashing a scratch
    root): recorded absent, never a hollow digest - the same rule
    `plan_provenance.reel_code_hash` already holds.
    """
    try:
        from library.tools.plan_provenance import reel_code_hash

        return reel_code_hash()
    except Exception:  # noqa: BLE001 - provenance never fails a manifest
        return None


def _provenance_maps(project_folder: str) -> dict:
    """`built_with` / `caption_hashes` / `footage_binding_hashes`, or {}."""
    try:
        from library.tools.plan_provenance import provenance_with_registry

        provenance = (
            provenance_with_registry(
                os.path.join(str(project_folder), "pipeline_output", "review")
            )
            or {}
        )
        return {
            "built_with": dict(provenance.get("built_with") or {}),
            "caption_hashes": dict(provenance.get("caption_hashes") or {}),
            "footage_binding_hashes": dict(
                provenance.get("footage_binding_hashes") or {}
            ),
        }
    except Exception:  # noqa: BLE001 - absent provenance is absent input
        return {}


def _assembly_manifest(project_folder: str) -> tuple[dict | None, str]:
    """The consolidated assembly manifest and where it was read from."""
    try:
        from library.tools.project_layout import Area, ProjectLayout

        path = ProjectLayout(project_folder).read_path(
            Area.ASSEMBLY_MANIFEST, "assembly_manifest.json"
        )
    except Exception:  # noqa: BLE001 - layout failure is absent input
        return None, "unresolved-layout-path"
    document = _read_json(Path(str(path)))
    return document, str(path)


def _recorded_fusion_manifest(
    project_folder: str, final: str
) -> tuple[dict | None, str]:
    """The last build's recorded Fusion manifest and its path, or absent."""
    try:
        from library.tools import reel_touchup as _touchup

        document = _touchup.recorded_fusion_manifest(project_folder, final)
        return document, "recorded-fusion-manifest"
    except Exception:  # noqa: BLE001 - absent fusion record is absent input
        return None, "recorded-fusion-manifest-unreadable"


def _render_ledger_digest(project_folder: str) -> tuple[str | None, str]:
    """A file-level fingerprint of the subtitle render ledger, or absent."""
    try:
        from library.tools import caption_asset_gc as _ledger
        from library.tools.project_layout import ProjectLayout

        path = (
            ProjectLayout(project_folder).step_dir(_ledger.STEP_NODE_ID)
            / _ledger.RENDER_LEDGER_NAME
        )
    except Exception:  # noqa: BLE001 - layout failure is absent input
        return None, "unresolved-layout-path"
    digest = _digest_file(Path(str(path)))
    if digest is None:
        return None, str(path)
    return digest["sha256"], str(path)


def _reel_slice(assembly: dict, final: str) -> tuple[list, bool]:
    """Assembly entries attributed to this reel, and whether any matched.

    Attribution reads the `reel` / `timeline` keys the reel-aware
    writers file (`timeline_decisions.placements`); an assembly that
    predates those keys matches nothing, which the caller records as
    `unkeyed` rather than as an empty reel.
    """
    matched: list = []

    def _claims(entry: dict) -> bool:
        return entry.get("reel") == final or entry.get("timeline") == final

    for clips in (assembly.get("tracks") or {}).values():
        for entry in (clips or {}).get("clips") or []:
            if isinstance(entry, dict) and _claims(entry):
                matched.append(entry)
    for container in (
        assembly.get("subtitle_overlay"),
        assembly.get("motion_graphics_overlay"),
        assembly.get("timed_text_overlay"),
    ):
        for entry in (container or {}).get("segments") or []:
            if isinstance(entry, dict) and _claims(entry):
                matched.append(entry)
    for entry in assembly.get("generator_overlays") or []:
        if isinstance(entry, dict) and _claims(entry):
            matched.append(entry)
    return matched, bool(matched)


def _track_slice(assembly: dict, final: str, tracks: tuple) -> list:
    """Assembly clips on `tracks` attributed to this reel."""
    out: list = []
    for name in tracks:
        container = (assembly.get("tracks") or {}).get(name) or {}
        for entry in container.get("clips") or []:
            if not isinstance(entry, dict):
                continue
            if entry.get("reel") == final or entry.get("timeline") == final:
                out.append(
                    {
                        key: entry.get(key)
                        for key in (
                            "source_file",
                            "overlay_path",
                            "source_in",
                            "source_out",
                            "timeline_in",
                            "timeline_out",
                            "timeline_in_frame",
                            "timeline_out_frame",
                            "label",
                            "name",
                            "effect",
                            "enabled",
                        )
                    }
                )
    return out


def _overlay_slice(assembly: dict, final: str, key: str) -> list:
    """Overlay segments under `key` attributed to this reel."""
    out: list = []
    for entry in ((assembly.get(key) or {}).get("segments")) or []:
        if not isinstance(entry, dict):
            continue
        if entry.get("reel") == final or entry.get("timeline") == final:
            out.append(
                {
                    slot: entry.get(slot)
                    for slot in (
                        "overlay_path",
                        "source_file",
                        "timeline_start",
                        "timeline_end",
                        "label",
                        "name",
                        "effect",
                    )
                }
            )
    return out


def _hash_generated_assets(project_folder: str, entries: list) -> tuple[list, int]:
    """Content hashes of generated overlay assets this reel placed.

    Only `overlay_path` files inside the project folder are hashed -
    rendered overlays and motion-graphics carriers, kilobytes to
    megabytes. Source footage is skipped by construction: it is an
    input, fingerprinted by the footage binding hash, and hashing
    gigabytes into a commit path is how a manifest becomes a render.
    Returns the asset records and the count skipped as non-generated.
    """
    root = str(project_folder)
    assets: list = []
    skipped = 0
    for entry in entries:
        if len(assets) >= MAX_HASHED_ASSETS:
            break
        candidate = entry.get("overlay_path") or ""
        if not candidate:
            skipped += 1
            continue
        path = candidate if os.path.isabs(candidate) else os.path.join(root, candidate)
        try:
            inside = os.path.commonpath(
                [os.path.realpath(path), os.path.realpath(root)]
            )
        except (OSError, ValueError):
            skipped += 1
            continue
        if inside != os.path.realpath(root):
            skipped += 1
            continue
        try:
            if os.path.getsize(path) > MAX_ASSET_BYTES:
                assets.append(
                    {"path": os.path.relpath(path, root), "status": "unhashed-oversize"}
                )
                continue
        except OSError:
            skipped += 1
            continue
        hashed = _digest_file(Path(path))
        if hashed is None:
            skipped += 1
            continue
        assets.append(
            {
                "path": os.path.relpath(path, root),
                "sha256": hashed["sha256"],
                "bytes": hashed["bytes"],
            }
        )
    return assets, skipped


def _component(
    reel_key: str,
    name: str,
    *,
    status: str,
    dependencies: dict | None = None,
    assets: list | None = None,
    detail: str = "",
) -> dict:
    body: dict = {
        "id": f"{reel_key}:{name}",
        "status": status,
        "dependencies": dict(dependencies or {}),
        "assets": list(assets or []),
    }
    if detail:
        body["detail"] = detail
    body["digest"] = _digest(
        {key: body[key] for key in ("id", "dependencies", "assets")}
    )
    return body


def build_manifest(
    project_folder: str, final: str, rows, rows_digest: str | None = None
) -> dict:
    """Build this reel's component manifest from existing records.

    `rows` is the promoted rows snapshot the commit path already
    holds - the same object the version entry stores - so the
    timeline component costs no Resolve read. Every other source is
    read off disk as it stands; a source that is missing degrades
    its component to `absent` (or `unkeyed` where the assembly
    cannot attribute entries to this reel), never to a guess.
    """
    from library.tools.versions import rounds as _rounds

    reel_key, key_source = _reel_key(final)
    rows_digest = rows_digest or _rounds.digest_rows(rows or {})
    provenance = _provenance_maps(project_folder)
    assembly, assembly_path = _assembly_manifest(project_folder)
    fusion_manifest, _fusion_path = _recorded_fusion_manifest(project_folder, final)
    ledger_digest, ledger_path = _render_ledger_digest(project_folder)
    produced = _produced_by()

    caption_hash = (provenance.get("caption_hashes") or {}).get(final)
    footage_binding = (provenance.get("footage_binding_hashes") or {}).get(final)

    components: dict = {}
    if assembly is None:
        for name in COMPONENTS:
            if name == "timeline":
                continue
            components[name] = _component(
                reel_key,
                name,
                status=ABSENT,
                detail=f"no assembly manifest at {assembly_path}",
            )
    else:
        _matched, any_matched = _reel_slice(assembly, final)
        if not any_matched:
            for name in ("aroll", "captions", "motion_graphics", "audio"):
                components[name] = _component(
                    reel_key,
                    name,
                    status=UNKEYED,
                    detail=(
                        f"assembly manifest at {assembly_path} names "
                        f"no entry for {final!r}"
                    ),
                )
        else:
            aroll = _track_slice(assembly, final, ("V1", "V2"))
            components["aroll"] = _component(
                reel_key,
                "aroll",
                status=RECORDED,
                dependencies={
                    "v1_v2_slice": _digest(aroll),
                    "footage_binding_hash": footage_binding,
                },
            )
            captions = _overlay_slice(assembly, final, "subtitle_overlay")
            caption_deps: dict = {
                "subtitle_slice": _digest(captions),
                "caption_hash": caption_hash,
            }
            if ledger_digest is not None:
                caption_deps["render_ledger"] = ledger_digest
            components["captions"] = _component(
                reel_key,
                "captions",
                status=RECORDED,
                dependencies=caption_deps,
                detail=(
                    ""
                    if ledger_digest is not None
                    else f"render ledger absent at {ledger_path}"
                ),
            )
            motion = _overlay_slice(
                assembly, final, "motion_graphics_overlay"
            ) + _overlay_slice(assembly, final, "timed_text_overlay")
            components["motion_graphics"] = _component(
                reel_key,
                "motion_graphics",
                status=RECORDED,
                dependencies={"overlay_slice": _digest(motion)},
            )
            audio_tracks = tuple(
                sorted(
                    name
                    for name in (assembly.get("tracks") or {})
                    if str(name).upper().startswith("A")
                )
            )
            audio = _track_slice(assembly, final, audio_tracks)
            components["audio"] = _component(
                reel_key,
                "audio",
                status=RECORDED,
                dependencies={"audio_slice": _digest(audio)},
            )
        generator_slice = [
            entry
            for entry in (assembly.get("generator_overlays") or [])
            if isinstance(entry, dict)
            and (entry.get("reel") == final or entry.get("timeline") == final)
        ]
        fusion_deps: dict = {
            "generator_slice": _digest(generator_slice),
        }
        if fusion_manifest is None:
            components["fusion"] = _component(
                reel_key,
                "fusion",
                status=ABSENT,
                dependencies=fusion_deps,
                detail="no recorded fusion manifest for this reel",
            )
        else:
            fusion_deps["fusion_manifest"] = _digest(fusion_manifest)
            components["fusion"] = _component(
                reel_key, "fusion", status=RECORDED, dependencies=fusion_deps
            )

    row_inventory = {}
    try:
        row_inventory = {
            str(key): len(value or []) for key, value in dict(rows or {}).items()
        }
    except (AttributeError, TypeError, ValueError):
        row_inventory = {}
    components["timeline"] = _component(
        reel_key,
        "timeline",
        status=RECORDED,
        dependencies={
            "rows_digest": rows_digest,
            "row_inventory": _digest(row_inventory),
        },
    )

    overlay_entries = []
    if assembly is not None:
        overlay_entries = _overlay_slice(
            assembly, final, "motion_graphics_overlay"
        ) + _overlay_slice(assembly, final, "subtitle_overlay")
    assets, skipped = _hash_generated_assets(project_folder, overlay_entries)
    if assets or skipped:
        components.setdefault(
            "motion_graphics",
            _component(
                reel_key,
                "motion_graphics",
                status=ABSENT,
                detail="no assembly manifest to attribute overlays",
            ),
        )
        components["motion_graphics"]["assets"] = assets
        if skipped:
            note = (
                f"{skipped} overlay entr"
                f"{'y' if skipped == 1 else 'ies'} without a hashable "
                f"generated asset"
            )
            prior = components["motion_graphics"].get("detail") or ""
            components["motion_graphics"]["detail"] = f"{prior} {note}".strip()
        components["motion_graphics"]["digest"] = _digest(
            {
                "id": components["motion_graphics"]["id"],
                "dependencies": components["motion_graphics"]["dependencies"],
                "assets": assets,
            }
        )

    manifest: dict = {
        "format": FORMAT,
        "reel_key": reel_key,
        "reel_key_source": key_source,
        "resolve_timeline": str(final),
        "produced_by": produced,
        "rows_digest": rows_digest,
        "components": components,
    }
    manifest["manifest_digest"] = _digest(
        {name: components[name]["digest"] for name in sorted(components)}
    )
    return manifest


def diff_manifests(old: dict | None, new: dict | None) -> dict:
    """Which components differ between two revisions, without timelines.

    Either side may be None (a revision that predates manifests):
    None reads as "unknown", so every component the other side
    records reports `changed: unknown`, never `changed: False`.
    """
    if old is None or new is None:
        old_names = set(((old or {}).get("components") or {}).keys())
        new_names = set(((new or {}).get("components") or {}).keys())
        names = sorted(old_names | new_names | set(COMPONENTS))
        return {
            "changed": names,
            "unchanged": [],
            "detail": {
                name: {"changed": "unknown", "reason": "a revision predates manifests"}
                for name in names
            },
        }
    old_components = old.get("components", {})
    new_components = new.get("components", {})
    changed: list = []
    unchanged: list = []
    detail: dict = {}
    for name in sorted(set(old_components) | set(new_components)):
        before = old_components.get(name) or {}
        after = new_components.get(name) or {}
        if before.get("digest") != after.get("digest"):
            changed.append(name)
            fields = sorted(
                {
                    key
                    for key in set(before) | set(after)
                    if before.get(key) != after.get(key) and key != "digest"
                }
            )
            detail[name] = {
                "changed": True,
                "fields": fields,
                "before_status": before.get("status"),
                "after_status": after.get("status"),
            }
        else:
            unchanged.append(name)
            detail[name] = {"changed": False}
    return {
        "changed": sorted(changed),
        "unchanged": sorted(unchanged),
        "detail": detail,
    }


def _entry_manifest(entry: dict) -> dict | None:
    """The manifest attached to one version entry, or None where absent."""
    manifest = (entry or {}).get("component_manifest")
    if manifest is None:
        return None
    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != FORMAT
        or not isinstance(manifest.get("components"), dict)
    ):
        raise ManifestUnreadable(
            "the version's component_manifest is not a "
            f"{FORMAT} document; it cannot be diffed and it cannot be "
            "treated as empty."
        )
    return manifest


def manifest_of_version(project_folder, final: str, version: int) -> dict | None:
    """This reel's component manifest at one revision, or None."""
    from library.tools.versions import reel_versions as _versions

    for entry in _versions.versions_of(project_folder, final):
        if int(entry.get("version", 0)) == int(version):
            return _entry_manifest(entry)
    return None


def latest_manifest(project_folder, final: str) -> dict | None:
    """This reel's newest recorded component manifest, or None."""
    from library.tools.versions import reel_versions as _versions

    for entry in reversed(_versions.versions_of(project_folder, final)):
        manifest = _entry_manifest(entry)
        if manifest is not None:
            return manifest
    return None


def attach_to_version(project_folder, final: str, version: int, manifest: dict) -> None:
    """Attach a built manifest to its exact creative revision."""
    from library.tools.versions import reel_versions as _versions

    if (
        not isinstance(manifest, dict)
        or manifest.get("format") != FORMAT
        or not isinstance(manifest.get("components"), dict)
    ):
        raise ManifestUnreadable(
            f"refusing to attach a manifest that is not a {FORMAT} document."
        )
    _versions.attach_component_manifest(project_folder, final, int(version), manifest)


def backfill_project(project_folder, finals=None) -> dict:
    """Write manifests from existing records for reels that lack them.

    Only revisions whose version entry already holds a rows snapshot
    are backfilled - the rows ARE the revision's own record, so the
    timeline component is exact. Assembly, provenance, Fusion and
    ledger inputs are read as they stand on disk NOW, which may
    postdate the revision: every backfilled manifest carries
    `backfilled: True` and names that staleness risk, so no reader
    mistakes it for a commit-time record. Revisions without a rows
    snapshot are reported, never guessed.
    """
    from library.tools.versions import reel_versions as _versions

    ledger = _versions.read(project_folder)
    names = [str(name) for name in (finals or [])] or sorted(
        (ledger.get("reels") or {}).keys()
    )
    attached: list = []
    skipped: list = []
    for final in names:
        for entry in _versions.versions_of(project_folder, final):
            if entry.get("component_manifest") is not None:
                continue
            rows = entry.get("rows")
            if not isinstance(rows, dict) or not rows:
                skipped.append(
                    {
                        "final": final,
                        "version": entry.get("version"),
                        "reason": "no rows snapshot on the entry",
                    }
                )
                continue
            manifest = build_manifest(
                project_folder, final, rows, rows_digest=entry.get("rows_digest")
            )
            manifest["backfilled"] = True
            manifest["backfill_note"] = (
                "rows come from this revision's own snapshot; assembly, "
                "provenance, Fusion and ledger inputs were read from disk "
                "at backfill time and may postdate the revision."
            )
            manifest["manifest_digest"] = _digest(
                {
                    name: manifest["components"][name]["digest"]
                    for name in sorted(manifest["components"])
                }
            )
            attach_to_version(project_folder, final, int(entry["version"]), manifest)
            attached.append({"final": final, "version": entry.get("version")})
    return {"attached": attached, "skipped": skipped}


__all__ = [
    "ABSENT",
    "COMPONENTS",
    "FORMAT",
    "RECORDED",
    "UNKEYED",
    "ManifestUnreadable",
    "attach_to_version",
    "backfill_project",
    "build_manifest",
    "diff_manifests",
    "latest_manifest",
    "manifest_of_version",
]
