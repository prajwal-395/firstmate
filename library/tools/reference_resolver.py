"""Resolve a source reference to the artefact and bytes it names.

The canonical model had four resolvers, one per domain, and no single
way to ask "where are the bytes this claim was read from?". This module
is that single way: given any valid reference it returns the artefact
path and the byte range inside it, or it refuses with a NAMED reason.

The four reasons are the ones a reader can act on, and each one says
something different:

``missing``      the reference points at something that is not there -
                 an unknown asset, a file that is gone, a mask nobody
                 wrote.
``stale``        the thing is there, but it is not the thing the model
                 measured: the bytes changed under the reference.
``ambiguous``    the reference points at more than one thing, so
                 choosing one would be a guess.
``incompatible`` the reference cannot be expressed against this source:
                 the model carries no measured mapping from it to bytes.

A refusal is never a guess. A reader that cannot be told where the
bytes are is told that, not handed a plausible answer.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from library.tools import source_reference

MISSING = "missing"
STALE = "stale"
AMBIGUOUS = "ambiguous"
INCOMPATIBLE = "incompatible"

REASONS = (MISSING, STALE, AMBIGUOUS, INCOMPATIBLE)
"""Every reason a resolution can be refused for. The list IS the
contract: a refusal names one of these, never a free-text excuse."""


class ReferenceResolutionError(ValueError):
    """A reference that does not resolve, refused with its reason."""

    def __init__(self, reason: str, message: str) -> None:
        if reason not in REASONS:
            raise ValueError(f"unknown refusal reason {reason!r}")
        self.reason = reason
        super().__init__(f"{reason}: {message}")


@dataclass(frozen=True)
class ResolvedReference:
    """Where the bytes a reference names actually are."""

    kind: str
    path: Path
    byte_start: int
    byte_end: int
    detail: str

    @property
    def byte_range(self) -> tuple[int, int]:
        """The half-open byte range inside the artefact."""
        return (self.byte_start, self.byte_end)

    def to_dict(self) -> dict[str, Any]:
        """A JSON-safe record of the resolution."""
        return {"kind": self.kind, "path": str(self.path),
                "byte_start": self.byte_start, "byte_end": self.byte_end,
                "detail": self.detail}


def resolve(model: dict[str, Any], reference: Any, *,
            project_folder: str | Path | None = None,
            memory_root: str | Path | None = None) -> ResolvedReference:
    """Resolve one reference against a model.

    ``project_folder`` locates a relative asset uri and the artefacts a
    project keeps beside its model; ``memory_root`` locates the
    sidecars in source memory. Both are optional, and a reference that
    needs one it was not given is refused as ``incompatible`` rather
    than resolved against a guess.
    """
    try:
        parsed = source_reference.parse(reference)
    except source_reference.SourceReferenceError as exc:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"the reference is not a source reference: {exc}") from exc
    kind = parsed["kind"]
    if kind == source_reference.ASSET:
        return _resolve_asset(model, parsed, project_folder)
    if kind == source_reference.STREAM:
        return _resolve_stream(model, parsed, project_folder)
    if kind == source_reference.ELEMENT:
        return _resolve_element(model, parsed, project_folder)
    if kind == source_reference.INTERVAL:
        return _resolve_interval(model, parsed, project_folder)
    if kind == source_reference.REGION:
        return _resolve_region(model, parsed, project_folder)
    return _resolve_sub_element(model, parsed, project_folder, memory_root)


def _asset_row(model: dict[str, Any], asset_id: str) -> dict[str, Any]:
    for asset in model.get("assets") or []:
        if asset.get("id") == asset_id:
            return asset
    raise ReferenceResolutionError(
        MISSING, f"the model carries no asset {asset_id!r}")


def _stream_rows(model: dict[str, Any], stream_id: str) -> list[dict[str, Any]]:
    rows = [stream for stream in (model.get("streams") or [])
            if stream.get("id") == stream_id]
    if not rows:
        raise ReferenceResolutionError(
            MISSING, f"the model carries no stream {stream_id!r}")
    if len(rows) > 1:
        raise ReferenceResolutionError(
            AMBIGUOUS,
            f"the model carries {len(rows)} streams called {stream_id!r}")
    return rows


def _clock_row(model: dict[str, Any], clock_id: str) -> dict[str, Any]:
    for clock in model.get("clocks") or []:
        if clock.get("id") == clock_id:
            return clock
    raise ReferenceResolutionError(
        MISSING, f"the model carries no clock {clock_id!r}")


def _asset_path(asset: dict[str, Any],
                project_folder: str | Path | None) -> Path:
    uri = asset.get("uri")
    if not isinstance(uri, str) or not uri:
        raise ReferenceResolutionError(
            MISSING, f"asset {asset.get('id')!r} names no file")
    path = Path(uri)
    if path.is_absolute():
        return path
    if project_folder is None:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"asset {asset.get('id')!r} names the relative path {uri!r} and "
            "no project folder was given to locate it")
    return Path(project_folder) / uri


def _digest_of(path: Path, recorded: str) -> str:
    """The digest of a file, in the scheme the model recorded it in."""
    if recorded.startswith("sha256:"):
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        return "sha256:" + digest.hexdigest()
    from library.tools import footage_identity
    return str(footage_identity.fingerprint(str(path))["content_digest"])


def _checked_file(path: Path, asset: dict[str, Any]) -> int:
    """The size of the artefact, or a refusal naming why there is none."""
    if not path.exists():
        raise ReferenceResolutionError(
            MISSING, f"the artefact {path} is not on disk")
    if not path.is_file():
        raise ReferenceResolutionError(
            MISSING, f"the artefact {path} is not a file")
    size = path.stat().st_size
    recorded = asset.get("content_digest")
    if isinstance(recorded, str) and recorded:
        if _digest_of(path, recorded) != recorded:
            raise ReferenceResolutionError(
                STALE,
                f"the bytes at {path} are not the bytes the model measured "
                f"(recorded {recorded})")
    return size


def _resolve_asset(model: dict[str, Any], reference: dict[str, Any],
                   project_folder: str | Path | None) -> ResolvedReference:
    asset = _asset_row(model, reference["asset_id"])
    path = _asset_path(asset, project_folder)
    size = _checked_file(path, asset)
    return ResolvedReference(
        kind=source_reference.ASSET, path=path, byte_start=0, byte_end=size,
        detail=f"the whole file {path.name}")


def _stream_asset(model: dict[str, Any], stream: dict[str, Any]
                  ) -> dict[str, Any]:
    asset_id = stream.get("asset_id")
    if not asset_id:
        raise ReferenceResolutionError(
            INCOMPATIBLE, f"stream {stream.get('id')!r} names no asset")
    return _asset_row(model, asset_id)


def _packets(asset: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = asset.get("metadata")
    packets = metadata.get("packet_table") if isinstance(metadata, dict) else None
    return [row for row in (packets or []) if isinstance(row, dict)]


def _frames(asset: dict[str, Any]) -> list[dict[str, Any]]:
    metadata = asset.get("metadata")
    frames = metadata.get("frame_table") if isinstance(metadata, dict) else None
    return [row for row in (frames or []) if isinstance(row, dict)]


def _packet_range(packets: list[dict[str, Any]],
                  stream_index: int | None) -> tuple[int, int] | None:
    """The byte range the stream's own packets occupy."""
    selected = [row for row in packets
                if stream_index is None
                or row.get("stream_index") == stream_index]
    starts = [int(row["pos"]) for row in selected
              if isinstance(row.get("pos"), int)]
    if not starts:
        return None
    start = min(starts)
    end = start
    for row in selected:
        if not isinstance(row.get("pos"), int):
            continue
        size = row.get("size")
        if isinstance(size, int):
            end = max(end, int(row["pos"]) + size)
    return (start, end)


def _resolve_stream(model: dict[str, Any], reference: dict[str, Any],
                    project_folder: str | Path | None) -> ResolvedReference:
    stream = _stream_rows(model, reference["stream_id"])[0]
    asset = _stream_asset(model, stream)
    path = _asset_path(asset, project_folder)
    size = _checked_file(path, asset)
    byte_range = _packet_range(_packets(asset), stream.get("stream_index"))
    if byte_range is None:
        byte_range = (0, size)
    return ResolvedReference(
        kind=source_reference.STREAM, path=path,
        byte_start=byte_range[0], byte_end=byte_range[1],
        detail=f"stream {stream.get('stream_index')} of {path.name}")


def _video_frames(asset: dict[str, Any]) -> list[dict[str, Any]]:
    frames = [row for row in _frames(asset)
              if row.get("media_type") in (None, "video")]
    return sorted(frames, key=lambda row: _frame_time(row))


def _audio_frames(asset: dict[str, Any]) -> list[dict[str, Any]]:
    frames = [row for row in _frames(asset)
              if row.get("media_type") == "audio"]
    return sorted(frames, key=lambda row: _frame_time(row))


def _frame_time(row: dict[str, Any]) -> float:
    for key in ("best_effort_timestamp_time", "pts_time", "pkt_dts_time"):
        value = row.get(key)
        if isinstance(value, (int, float)):
            return float(value)
    return 0.0


def _frame_bytes(row: dict[str, Any]) -> tuple[int, int] | None:
    pos, size = row.get("pkt_pos"), row.get("pkt_size")
    if isinstance(pos, int) and isinstance(size, int):
        return (pos, pos + size)
    return None


def _resolve_element(model: dict[str, Any], reference: dict[str, Any],
                     project_folder: str | Path | None) -> ResolvedReference:
    stream = _stream_rows(model, reference["stream_id"])[0]
    asset = _stream_asset(model, stream)
    path = _asset_path(asset, project_folder)
    size = _checked_file(path, asset)
    element = reference["element"]
    if element["kind"] == source_reference.FRAME:
        frames = _video_frames(asset)
        if not frames:
            raise ReferenceResolutionError(
                INCOMPATIBLE,
                f"the model carries no measured frame table for {path.name}, "
                "so it cannot place a frame in the file")
        index = element["frame_index"]
        if index >= len(frames):
            raise ReferenceResolutionError(
                INCOMPATIBLE,
                f"the measured table covers {len(frames)} frames of "
                f"{path.name}; frame {index} is beyond it")
        byte_range = _frame_bytes(frames[index])
        if byte_range is None:
            raise ReferenceResolutionError(
                INCOMPATIBLE,
                f"the measured table does not say where frame {index} is in "
                f"{path.name}")
        return ResolvedReference(
            kind=source_reference.ELEMENT, path=path,
            byte_start=byte_range[0], byte_end=byte_range[1],
            detail=f"frame {index} of {path.name}")
    # A sample is finer than a frame: the audio frames carry how many
    # samples each one holds, so the sample is placed in the frame that
    # holds it.
    frames = _audio_frames(asset)
    if not frames:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"the model carries no measured audio frames for {path.name}, "
            "so it cannot place a sample in the file")
    index = element["sample_index"]
    seen = 0
    for row in frames:
        count = row.get("nb_samples")
        if not isinstance(count, int) or count <= 0:
            continue
        if index < seen + count:
            byte_range = _frame_bytes(row)
            if byte_range is None:
                raise ReferenceResolutionError(
                    INCOMPATIBLE,
                    f"the measured table does not say where sample {index} "
                    f"is in {path.name}")
            return ResolvedReference(
                kind=source_reference.ELEMENT, path=path,
                byte_start=byte_range[0], byte_end=byte_range[1],
                detail=f"sample {index} of {path.name}")
        seen += count
    raise ReferenceResolutionError(
        INCOMPATIBLE,
        f"the measured table covers {seen} samples of {path.name}; sample "
        f"{index} is beyond it")


def _resolve_interval(model: dict[str, Any], reference: dict[str, Any],
                      project_folder: str | Path | None) -> ResolvedReference:
    stream = _stream_rows(model, reference["stream_id"])[0]
    asset = _stream_asset(model, stream)
    path = _asset_path(asset, project_folder)
    size = _checked_file(path, asset)
    interval = reference["interval"]
    clock = _clock_row(model, interval["clock_id"])
    frame_rate = clock.get("frame_rate")
    if not isinstance(frame_rate, dict):
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"clock {interval['clock_id']!r} carries no measured frame rate, "
            "so a tick range cannot be placed in the file")
    from library.tools import source_clocks
    try:
        native = source_clocks.from_model_clock(
            str(asset.get("content_digest") or ""), clock)
    except ValueError as exc:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"clock {interval['clock_id']!r} cannot be read: {exc}") from exc
    try:
        first = source_clocks.tick_to_frame(native, interval["start"])
        last = source_clocks.tick_to_frame(native, interval["end"] - 1)
    except ValueError as exc:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"the interval cannot be placed on the frame grid: {exc}") from exc
    frames = _video_frames(asset)
    if not frames:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"the model carries no measured frame table for {path.name}, "
            "so it cannot place an interval in the file")
    if last >= len(frames):
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"the measured table covers {len(frames)} frames of "
            f"{path.name}; the interval reaches frame {last}")
    first_bytes = _frame_bytes(frames[first])
    last_bytes = _frame_bytes(frames[last])
    if first_bytes is None or last_bytes is None:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"the measured table does not say where the interval is in "
            f"{path.name}")
    return ResolvedReference(
        kind=source_reference.INTERVAL, path=path,
        byte_start=first_bytes[0], byte_end=last_bytes[1],
        detail=(f"ticks {interval['start']}..{interval['end']} of "
                f"{path.name} (frames {first}..{last})"))


def _resolve_region(model: dict[str, Any], reference: dict[str, Any],
                    project_folder: str | Path | None) -> ResolvedReference:
    space_id = reference["space_id"]
    spaces = [space for space in (model.get("coordinate_spaces") or [])
              if space.get("id") == space_id]
    if not spaces:
        raise ReferenceResolutionError(
            MISSING, f"the model carries no coordinate space {space_id!r}")
    if len(spaces) > 1:
        raise ReferenceResolutionError(
            AMBIGUOUS,
            f"the model carries {len(spaces)} spaces called {space_id!r}")
    space = spaces[0]
    from library.tools import spatial_tables
    if spatial_tables.needs_calibration(space.get("kind", "")) and not (
            space.get("calibration") or "").strip():
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"coordinate space {space_id!r} carries no calibration, so a "
            "place in it cannot be located")
    leaving = [transform for transform in (model.get("space_transforms") or [])
               if transform.get("from_space") == space_id]
    if len(leaving) > 1:
        raise ReferenceResolutionError(
            AMBIGUOUS,
            f"{len(leaving)} transforms leave coordinate space {space_id!r}, "
            "so a place in it has more than one answer")
    asset_id = space.get("asset_id")
    if not asset_id:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"coordinate space {space_id!r} names no source, so a place in "
            "it belongs to no file")
    asset = _asset_row(model, asset_id)
    path = _asset_path(asset, project_folder)
    size = _checked_file(path, asset)
    return ResolvedReference(
        kind=source_reference.REGION, path=path, byte_start=0, byte_end=size,
        detail=(f"a place in coordinate space {space_id} of {path.name}"))


def _resolve_sub_element(model: dict[str, Any], reference: dict[str, Any],
                          project_folder: str | Path | None,
                          memory_root: str | Path | None) -> ResolvedReference:
    parent = resolve(model, reference["parent"],
                     project_folder=project_folder, memory_root=memory_root)
    sub_kind = reference["sub_kind"]
    sub_id = reference["sub_id"]
    if sub_kind != "mask":
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"a sub-element of kind {sub_kind!r} has no resolver")
    masks = [mask for mask in (model.get("masks") or [])
             if mask.get("id") == sub_id]
    if not masks:
        raise ReferenceResolutionError(
            MISSING, f"the model carries no mask {sub_id!r}")
    if len(masks) > 1:
        raise ReferenceResolutionError(
            AMBIGUOUS, f"the model carries {len(masks)} masks called {sub_id!r}")
    representation = masks[0].get("representation")
    if not isinstance(representation, dict):
        raise ReferenceResolutionError(
            INCOMPATIBLE, f"mask {sub_id!r} carries no representation")
    return _resolve_mask_artefact(representation, sub_id, project_folder,
                                  memory_root)


def _resolve_mask_artefact(representation: dict[str, Any], mask_id: str,
                           project_folder: str | Path | None,
                           memory_root: str | Path | None) -> ResolvedReference:
    """The file a mask's bytes live in, or a refusal naming why not."""
    from library.tools import project_model_masks
    kind = representation.get("kind")
    if kind not in project_model_masks.MASK_KINDS:
        raise ReferenceResolutionError(
            INCOMPATIBLE,
            f"mask {mask_id!r} names the representation kind {kind!r}, which "
            "the mask registry does not list")
    if kind == project_model_masks.SAM2_RLE_TRACK_KIND:
        return _mask_file(representation, "segmentation_file", mask_id,
                          project_folder)
    if kind == project_model_masks.OCR_POLYGON_TRACK_KIND:
        return _mask_file(representation, "artifact_path", mask_id,
                          project_folder, hash_key="artifact_sha256")
    if kind == project_model_masks.REGION_RLE_KIND:
        return _mask_sidecar(representation, mask_id, memory_root)
    raise ReferenceResolutionError(
        INCOMPATIBLE,
        f"mask {mask_id!r} carries its pixels inside the model "
        f"({kind!r}); there is no separate artefact to point at")


def _mask_file(representation: dict[str, Any], key: str, mask_id: str,
               project_folder: str | Path | None,
               hash_key: str | None = None) -> ResolvedReference:
    reference = representation.get(key)
    if not isinstance(reference, str) or not reference:
        raise ReferenceResolutionError(
            INCOMPATIBLE, f"mask {mask_id!r} names no {key}")
    path = Path(reference)
    if not path.is_absolute():
        if project_folder is None:
            raise ReferenceResolutionError(
                INCOMPATIBLE,
                f"mask {mask_id!r} names the relative path {reference!r} and "
                "no project folder was given to locate it")
        path = Path(project_folder) / reference
    if not path.is_file():
        raise ReferenceResolutionError(
            MISSING, f"the artefact {path} of mask {mask_id!r} is not on disk")
    if hash_key:
        wanted = representation.get(hash_key)
        if isinstance(wanted, str) and wanted:
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            if digest != wanted:
                raise ReferenceResolutionError(
                    STALE,
                    f"the artefact {path} of mask {mask_id!r} is not the "
                    f"bytes the model measured")
    size = path.stat().st_size
    return ResolvedReference(
        kind=source_reference.SUB_ELEMENT, path=path, byte_start=0,
        byte_end=size, detail=f"the artefact of mask {mask_id}")


def _mask_sidecar(representation: dict[str, Any], mask_id: str,
                  memory_root: str | Path | None) -> ResolvedReference:
    digest = representation.get("content_digest")
    name = representation.get("sidecar_file")
    if not isinstance(digest, str) or not digest:
        raise ReferenceResolutionError(
            INCOMPATIBLE, f"mask {mask_id!r} names no content digest")
    if not isinstance(name, str) or not name:
        raise ReferenceResolutionError(
            INCOMPATIBLE, f"mask {mask_id!r} names no sidecar file")
    from library.tools import source_memory
    path = source_memory.source_dir(digest, Path(memory_root).expanduser()
                                    if memory_root else None) / name
    if not path.is_file():
        raise ReferenceResolutionError(
            MISSING, f"the sidecar {path} of mask {mask_id!r} is not on disk")
    wanted = representation.get("sidecar_sha256")
    if isinstance(wanted, str) and wanted:
        import gzip
        try:
            raw = gzip.decompress(path.read_bytes())
        except (OSError, EOFError) as exc:
            raise ReferenceResolutionError(
                INCOMPATIBLE,
                f"the sidecar {path} of mask {mask_id!r} cannot be read: "
                f"{exc}") from exc
        if hashlib.sha256(raw).hexdigest() != wanted:
            raise ReferenceResolutionError(
                STALE,
                f"the sidecar {path} of mask {mask_id!r} is not the bytes "
                "the model measured")
    size = path.stat().st_size
    return ResolvedReference(
        kind=source_reference.SUB_ELEMENT, path=path, byte_start=0,
        byte_end=size, detail=f"the sidecar of mask {mask_id}")
