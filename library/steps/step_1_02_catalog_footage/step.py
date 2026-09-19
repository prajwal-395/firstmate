#!/usr/bin/env python3
"""
Step 1.2: Catalog Raw Footage

For each video file in the inventory, extracts technical metadata using
ffprobe: duration, resolution, frame rate, codec, audio channels, file size,
creation timestamp, and rotation. Stores the catalog in chronological order
by creation timestamp and assigns sequential source_order integers.

Classification: Deterministic / Information Retrieval
Archetype: Information Retrieval
Idempotent: Yes

Input:  { "raw_footage_files": [{ path, filename, extension, size_bytes }, ...] }
Output: { "clip_catalog": [{ clip_id, source_order, path, ..., has_audio }, ...] }

Requires: ffprobe (from ffmpeg) available on PATH.
"""
import json
import os
import subprocess
import sys
from datetime import datetime


class ProgramStreamRefused(ValueError):
    """No stream reaches the timeline by default.

    Which stream of a multi-stream source is the program mix is a
    project declaration, never a heuristic and never stream 0. When the
    metadata cannot supply the answer and no declaration names it, the
    catalog refuses - naming the source and everything ffprobe saw - so
    a non-program stream can never leak onto a timeline unchosen.
    """


# A measurement operating point, not taste. The mix bus of a field
# recorder carries (at least) the energy of everything it mixes, so a
# stream that is not clearly louder than every other stream is not
# clearly the mix: twice the power (3 dB) is the bar for "clearly".
# Below it the measurement refuses and the project must declare.
MEASURE_MARGIN_DB = 3.0
# Below this a stream is room tone off, not a candidate for anything:
# the captain's MXF carry an empty stream at about -69 dB, so -60 dB
# excludes exactly the nothing while keeping any real ISO.
MEASURE_SILENCE_DB = -60.0


def describe_audio_streams(probe: dict) -> list:
    """Every audio stream ffprobe reports, with whatever tells them
    apart: ffprobe index, 1-based channel ordinal among audio streams,
    codec, channel count and layout, sample rate, language, stream
    title, handler name, and disposition flags."""
    streams = []
    channel = 0
    for stream in probe.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        channel += 1
        tags = stream.get("tags") or {}
        disposition = stream.get("disposition") or {}
        streams.append({
            "index": stream.get("index"),
            "channel": channel,
            "codec": stream.get("codec_name", "unknown"),
            "channels": stream.get("channels"),
            "channel_layout": stream.get("channel_layout"),
            "sample_rate": (
                int(stream["sample_rate"])
                if stream.get("sample_rate") not in (None, "")
                else None
            ),
            "language": tags.get("language"),
            "title": tags.get("title"),
            "handler": stream.get("codec_tag_string"),
            "disposition_default": disposition.get("default"),
        })
    return streams


def _stream_signature(stream: dict) -> str:
    """One line saying what ffprobe saw for one stream, for refusals."""
    return (
        f"audio stream {stream.get('index')} (CH{stream.get('channel')}): "
        f"{stream.get('codec')}, channels={stream.get('channels')}, "
        f"layout={stream.get('channel_layout')}, "
        f"lang={stream.get('language')}, title={stream.get('title')}, "
        f"handler={stream.get('handler')}, "
        f"default={stream.get('disposition_default')}"
    )


def select_program_stream(audio_streams: list, declaration=None,
                           source: str = "",
                           measured_selection: dict | None = None) -> dict:
    """Which recorded stream reaches the timeline.

    - No streams: returns None (the source is silent).
    - One stream: it is the program, basis "single".
    - More than one: `declaration` - the 1-based channel ordinal the
      project declares - names it, basis "declared". Failing that, a
      `measured_selection` the pipeline measured off the footage names
      it, basis "measured-loudest" with its levels as evidence. A
      declaration always wins over a measurement. Anything else is
      a ProgramStreamRefused naming the source and every stream seen.
      Even distinguishable metadata does not choose: the mix is
      declared or measured, never inferred from labels.
    """
    if not audio_streams:
        return None
    if len(audio_streams) == 1:
        chosen = dict(audio_streams[0])
        chosen["basis"] = "single"
        return chosen
    if declaration is not None:
        for stream in audio_streams:
            if stream.get("channel") == declaration:
                chosen = dict(stream)
                chosen["basis"] = "declared"
                return chosen
        raise ProgramStreamRefused(
            f"Refusal: project declares program stream CH{declaration} "
            f"for {source!r}, but it carries {len(audio_streams)} audio "
            f"streams and none is CH{declaration}: "
            + "; ".join(_stream_signature(s) for s in audio_streams)
        )
    if measured_selection is not None:
        for stream in audio_streams:
            if stream.get("channel") == measured_selection.get("channel"):
                chosen = dict(stream)
                chosen["basis"] = measured_selection.get(
                    "basis", "measured")
                evidence = measured_selection.get("measured_levels_db")
                if evidence is None:
                    evidence = measured_selection.get("levels")
                if evidence is not None:
                    chosen["measured_levels_db"] = dict(evidence)
                return chosen
        raise ProgramStreamRefused(
            f"Refusal: measured program stream "
            f"CH{measured_selection.get('channel')} for {source!r} "
            f"matches none of its {len(audio_streams)} audio streams: "
            + "; ".join(_stream_signature(s) for s in audio_streams)
        )
    return _refuse_program_stream(audio_streams, source)


def measure_stream_levels(filepath: str, audio_streams: list) -> dict:
    """Mean volume per audio stream, in dB, keyed by channel ordinal.

    One ffmpeg pass PER stream (`-map` by ffprobe index): a single
    pass over all streams reports per-stream statistics in frame
    order, not stream order, so assigning them positionally scrambles
    the answer (measured: four streams came back 2,1,3,0). A stream
    with no measurable signal reads -inf. Raises RuntimeError when
    ffmpeg itself fails - a broken measurement is not a quiet one.
    """
    levels = {}
    for stream in audio_streams:
        index = stream.get("index")
        channel = stream.get("channel")
        try:
            result = subprocess.run(
                ["ffmpeg", "-hide_banner", "-i", filepath,
                 "-map", f"0:{index}", "-af", "volumedetect",
                 "-f", "null", "/dev/null"],
                capture_output=True, text=True, encoding="utf-8",
                errors="replace", timeout=600,
            )
        except FileNotFoundError:
            raise RuntimeError(
                "ffmpeg not found. Install ffmpeg: brew install ffmpeg"
            )
        if result.returncode != 0:
            raise RuntimeError(
                f"ffmpeg volumedetect failed (exit {result.returncode}) "
                f"for {filepath} stream {index}: "
                f"{result.stderr[-500:]}")
        level = float("-inf")
        for line in result.stderr.splitlines():
            if "mean_volume" in line:
                try:
                    level = float(line.split("mean_volume:")[1]
                                  .split("dB")[0].strip())
                except ValueError:
                    level = float("-inf")
        levels[channel] = level
    return levels


def measure_program_selection(filepath: str, audio_streams: list,
                               source: str = "") -> dict:
    """The program stream as MEASURED off the footage, or a refusal.

    The uniquely loudest stream is the mix: a mix bus carries the
    energy of everything it mixes. "Uniquely" is MEASURE_MARGIN_DB -
    anything closer refuses, because a hot ISO over a quiet mix is a
    human's call, not the pipeline's. All streams below
    MEASURE_SILENCE_DB refuses too: there is no mix to find. The
    returned selection carries every stream's level, so the decision
    is auditable per file rather than a rule inferred from one file.
    """
    levels = measure_stream_levels(filepath, audio_streams)
    if not levels:
        raise ProgramStreamRefused(
            f"Refusal: no audio levels could be measured for "
            f"{source or filepath!r}.")
    ranked = sorted(levels.items(), key=lambda kv: kv[1], reverse=True)
    loudest_channel, loudest_level = ranked[0]
    if loudest_level < MEASURE_SILENCE_DB:
        raise ProgramStreamRefused(
            f"Refusal: every audio stream of {source or filepath!r} "
            f"is below {MEASURE_SILENCE_DB} dB "
            + ", ".join(f"CH{c} {v:.1f} dB" for c, v in ranked)
            + " - silence has no program stream.")
    if len(ranked) > 1 and (loudest_level - ranked[1][1]
                            < MEASURE_MARGIN_DB):
        raise ProgramStreamRefused(
            f"Refusal: no uniquely loudest audio stream on "
            f"{source or filepath!r} "
            + ", ".join(f"CH{c} {v:.1f} dB" for c, v in ranked)
            + f" - the top two are within {MEASURE_MARGIN_DB} dB, so "
            f"the mix is not decisive. Declare source.program_stream.")
    for stream in audio_streams:
        if stream.get("channel") == loudest_channel:
            chosen = dict(stream)
            chosen["basis"] = "measured-loudest"
            chosen["measured_levels_db"] = {
                f"CH{c}": round(v, 1) for c, v in ranked}
            return chosen
    raise ProgramStreamRefused(  # pragma: no cover - defensive
        f"Refusal: measured CH{loudest_channel} for "
        f"{source or filepath!r} matches no described stream.")


def _refuse_program_stream(audio_streams: list, source: str) -> dict:
    """The refusal both selection paths share: say the source and what
    was seen, rather than defaulting to stream 0 and calling it the mix."""
    raise ProgramStreamRefused(
        f"Refusal: {source!r} carries {len(audio_streams)} audio streams "
        f"with no declared program stream, so none is selected: "
        + "; ".join(_stream_signature(s) for s in audio_streams)
    )


def _source_block_declaration(project_folder: str) -> tuple:
    """`(program_stream, measure_flag)` from the project's `source:` block.

    Read through `footage_identity` - the one module that owns
    footage properties - so a declaration works whether or not the key
    reached the run's broadcast `project_config`. An unreadable
    project.yaml is not a catalog failure: the declaration stays
    undeclared and selection proceeds to refusal as before.
    """
    if not project_folder:
        return None, False
    try:
        from library.tools.footage_identity import (
            declared_program_stream, measure_program_stream_flag)
    except ImportError:
        return None, False
    try:
        return (declared_program_stream(project_folder),
                measure_program_stream_flag(project_folder))
    except Exception as exc:
        print(f"WARNING: could not read source block for "
              f"{project_folder}: {exc}", file=sys.stderr)
        return None, False


def extract_metadata(filepath: str, program_stream=None,
                     measure_program_stream: bool = False) -> dict:
    """
    Extract technical metadata from a video file using ffprobe.
    Returns a dict of metadata fields, or a dict with an 'error' key if extraction fails.

    `program_stream` is the project's declaration of which audio stream
    is the program mix (1-based channel ordinal, e.g. 1 for CH1). A
    multi-stream source without one is RECORDED as refused, never
    defaulted: see `select_program_stream`. When `measure_program_stream`
    is true and nothing is declared, the footage itself is measured
    (uniquely loudest stream) and the levels recorded; an indecisive
    measurement refuses the same way.
    """
    try:
        result = subprocess.run(
            [
                "ffprobe",
                "-v", "quiet",
                "-print_format", "json",
                "-show_format",
                "-show_streams",
                filepath,
            ],
            capture_output=True,
            text=True, encoding="utf-8", errors="replace",
            timeout=30,
        )
    except FileNotFoundError:
        raise RuntimeError(
            "ffprobe not found. Install ffmpeg: brew install ffmpeg"
        )
    except subprocess.TimeoutExpired as e:
        err_msg = f"ffprobe timed out after {e.timeout}s"
        print(f"WARNING: {err_msg} for {filepath}", file=sys.stderr)
        return {"error": err_msg, "timeout_duration": e.timeout}

    if result.returncode != 0:
        err_msg = f"ffprobe failed (exit {result.returncode}): {result.stderr[:200]}"
        print(f"WARNING: {err_msg} for {filepath}", file=sys.stderr)
        return {"error": err_msg}

    try:
        probe = json.loads(result.stdout)
    except json.JSONDecodeError:
        err_msg = f"ffprobe returned invalid JSON"
        print(f"WARNING: {err_msg} for {filepath}", file=sys.stderr)
        return {"error": err_msg}

    # Find the video stream and EVERY audio stream. The catalog used to
    # take the first audio stream it found and discard the rest, so a
    # four-stream MXF arrived downstream as one stream and nothing ever
    # decided which of the four reaches the timeline - the root of the
    # non-program leak. Every stream is recorded now; the program-stream
    # selection below decides, or refuses.
    video_stream = None
    audio_streams = []
    for stream in probe.get("streams", []):
        if stream.get("codec_type") == "video" and video_stream is None:
            video_stream = stream
        elif stream.get("codec_type") == "audio":
            audio_streams.append(stream)

    if not video_stream:
        err_msg = "No video stream found"
        print(f"WARNING: {err_msg} in {filepath}", file=sys.stderr)
        return {"error": err_msg}

    fmt = probe.get("format", {})
    fmt_tags = fmt.get("tags", {})

    # Extract creation_time from format tags (multiple possible keys)
    creation_time = None
    for key in [
        "creation_time",
        "com.apple.quicktime.creationdate",
        "date",
    ]:
        val = fmt_tags.get(key) or fmt_tags.get(key.lower())
        if val:
            creation_time = val
            break

    # Also check video stream tags
    if not creation_time:
        stream_tags = video_stream.get("tags", {})
        for key in ["creation_time", "date"]:
            val = stream_tags.get(key)
            if val:
                creation_time = val
                break

    # Parse frame rate from video stream
    frame_rate = None
    fps_str = video_stream.get("r_frame_rate", "")
    if fps_str and "/" in fps_str:
        num, den = fps_str.split("/")
        if int(den) > 0:
            frame_rate = round(int(num) / int(den), 3)
    elif fps_str:
        try:
            frame_rate = float(fps_str)
        except ValueError:
            pass

    # Extract rotation from video stream side_data or tags
    rotation = 0
    for side_data in video_stream.get("side_data_list", []):
        if "rotation" in side_data:
            rotation = int(side_data["rotation"])
            break
    if rotation == 0:
        rot_tag = video_stream.get("tags", {}).get("rotate", "0")
        try:
            rotation = int(rot_tag)
        except ValueError:
            rotation = 0

    # The program-stream decision, recorded, never defaulted. A
    # refusal is data on the entry - not an exception - so one
    # undeclared source cannot fail the whole catalog; the entry says
    # which source and what ffprobe saw, and downstream must not place
    # its audio until the project declares. A declaration always wins;
    # measurement runs only when nothing is declared, and its own
    # refusal lands in the same field.
    described_streams = describe_audio_streams(probe)
    _program_selection = None
    _program_refusal = None
    _measured_selection = None
    if (program_stream is None and measure_program_stream
            and len(described_streams) > 1):
        try:
            _measured_selection = measure_program_selection(
                filepath, described_streams,
                source=os.path.basename(filepath))
        except ProgramStreamRefused as exc:
            _program_refusal = str(exc)
    if _program_refusal is None:
        try:
            _program_selection = select_program_stream(
                described_streams, declaration=program_stream,
                source=os.path.basename(filepath),
                measured_selection=_measured_selection)
        except ProgramStreamRefused as exc:
            _program_refusal = str(exc)

    return {
        "duration_seconds": round(float(fmt.get("duration", 0)), 3),
        "width": int(video_stream.get("width", 0)),
        "height": int(video_stream.get("height", 0)),
        "frame_rate": frame_rate,
        "video_codec": video_stream.get("codec_name", "unknown"),
        # Every stream the source carries, with whatever tells them
        # apart. Downstream places exactly one of them; the choice is
        # recorded in `program_stream`, or refused in its place.
        "audio_streams": describe_audio_streams(probe),
        "program_stream": _program_selection,
        "program_stream_refusal": _program_refusal,
        # Legacy singular fields describe the SELECTED program stream,
        # so existing readers keep working. Refused means unselected:
        # None, never stream 0 dressed as the mix.
        "audio_codec": _program_selection.get("codec") if _program_selection else None,
        "audio_channels": (
            _program_selection.get("channels")
            if _program_selection else None
        ),
        "audio_sample_rate": (
            _program_selection.get("sample_rate")
            if _program_selection else None
        ),
        "creation_time": creation_time,
        "rotation": rotation,
        "pixel_format": video_stream.get("pix_fmt", "unknown"),
        "has_audio": bool(described_streams),
    }


def parse_creation_time(ct_str: str | None) -> datetime | None:
    """Parse a creation_time string into a datetime for sorting."""
    if not ct_str:
        return None
    # Try common formats
    for fmt in [
        "%Y-%m-%dT%H:%M:%S.%fZ",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S.%f%z",
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S",
    ]:
        try:
            dt = datetime.strptime(ct_str, fmt)
            return dt.replace(tzinfo=None)
        except ValueError:
            continue
    return None


def catalog_footage(raw_footage_files: list, program_stream=None,
                    project_config: dict | None = None,
                    project_folder: str = "",
                    measure_program_stream: bool | None = None) -> dict:
    """
    Extract metadata for each file, sort chronologically, assign ordering.

    `program_stream` (or `project_config["audio"]["program_stream"]`,
    or the project's `source:` block) declares which audio stream is
    the program mix. See `select_program_stream`. When nothing declares
    one, `measure_program_stream` (or
    `source.measure_program_stream`) opts into measuring it off the
    footage; an indecisive measurement refuses like an undeclared one.
    """
    if program_stream is None and project_config:
        program_stream = (project_config.get("audio") or {}).get(
            "program_stream")
    if program_stream is None and project_config:
        program_stream = (project_config.get("source") or {}).get(
            "program_stream")
    if measure_program_stream is None and project_config:
        for block_name in ("audio", "source"):
            block = project_config.get(block_name) or {}
            if block.get("measure_program_stream"):
                measure_program_stream = True
                break
    if program_stream is None or measure_program_stream is None:
        file_declared, file_measure = _source_block_declaration(
            project_folder)
        if program_stream is None:
            program_stream = file_declared
        if measure_program_stream is None:
            measure_program_stream = file_measure
    if measure_program_stream is None:
        measure_program_stream = False
    entries = []
    skipped = []

    for file_info in raw_footage_files:
        filepath = file_info["path"]

        if not os.path.isfile(filepath):
            skipped.append({
                "path": filepath,
                "reason": "file not found"
            })
            print(
                f"WARNING: File not found, skipping: {filepath}",
                file=sys.stderr,
            )
            continue

        metadata = extract_metadata(
            filepath, program_stream=program_stream,
            measure_program_stream=measure_program_stream)
        if metadata is None or "error" in metadata:
            err = metadata.get("error", "metadata extraction failed") if metadata else "metadata extraction failed"
            skipped.append({
                "path": filepath,
                "reason": err,
                "timeout_duration": metadata.get("timeout_duration") if metadata else None
            })
            continue

        entries.append({
            "path": filepath,
            "source_file": filepath,  # Alias for downstream steps (3.1, 3.3)
            "filename": file_info["filename"],
            "file_size_bytes": file_info["size_bytes"],
            "clip_id": file_info.get("clip_id", ""),
            **metadata,
        })

    if not entries:
        raise ValueError(
            "No files yielded valid metadata. "
            "Cannot build catalog."
        )

    # --- Fall back to filesystem mtime when creation_time is absent ---
    # H5 fix: Some clips (e.g., screen recordings, re-encoded files) lack
    # creation_time metadata. Using mtime preserves chronological ordering
    # instead of crashing the pipeline.
    for entry in entries:
        if not entry.get("creation_time"):
            mtime = os.path.getmtime(entry["path"])
            entry["creation_time"] = datetime.fromtimestamp(mtime).strftime(
                "%Y-%m-%dT%H:%M:%S"
            )
            print(
                f"WARNING: No creation_time for {entry['filename']}, "
                f"falling back to filesystem mtime: {entry['creation_time']}",
                file=sys.stderr,
            )

    # --- Sort by creation_time ascending, filename as tiebreaker ---
    entries.sort(key=lambda e: (
        parse_creation_time(e["creation_time"]) or datetime.min,
        e["filename"],
    ))

    # --- Assign source_order ---
    for i, entry in enumerate(entries):
        entry["source_order"] = i + 1

    # --- Derive project fps and the SOURCE resolution ---
    # `source_resolution` DESCRIBES THE FOOTAGE. It is not the render
    # target and must never be used as one (captain's ruling,
    # 2026-08-19). It was called `project_resolution` and it was handed
    # straight to Resolve as the timeline size, which shipped project 001
    # as a 1920x1080 master with the vertical overlays banded down the
    # middle. The render target comes from
    # library/tools/delivery_format.py.
    fps_counts = {}
    res_counts = {}
    for entry in entries:
        fps = entry.get("frame_rate")
        if fps:
            fps_counts[fps] = fps_counts.get(fps, 0) + 1
        w = entry.get("width")
        h = entry.get("height")
        if w and h:
            res = (w, h)
            res_counts[res] = res_counts.get(res, 0) + 1
            
    project_fps = max(fps_counts, key=fps_counts.get) if fps_counts else 30.0
    source_res = list(max(res_counts, key=res_counts.get)) if res_counts else [1080, 1920]

    if len(fps_counts) > 1:
        print(f"REPORT: Mixed frame rates detected: {fps_counts}", file=sys.stderr)
    if len(res_counts) > 1:
        print(f"REPORT: Mixed resolutions detected: {res_counts}", file=sys.stderr)

    # --- Verification ---
    # No null values for critical fields
    for entry in entries:
        for field in ["duration_seconds", "width", "height", "frame_rate"]:
            if entry.get(field) is None:
                raise ValueError(
                    f"Refusal: Missing required field '{field}' in file '{entry['filename']}'"
                )


    # source_order values are unique and sequential
    orders = [e["source_order"] for e in entries]
    assert orders == list(range(1, len(entries) + 1)), \
        "source_order values are not sequential"

    # clip_id values are unique
    ids = [e["clip_id"] for e in entries]
    assert len(ids) == len(set(ids)), "Duplicate clip_id values"

    return {
        "clip_catalog": entries,
        "total_clips": len(entries),
        "skipped_files": skipped,
        "project_fps": project_fps,
        "source_resolution": source_res,
    }


def main():
    input_data = json.loads(sys.stdin.read())
    raw_footage_files = input_data.get("raw_footage_files")

    if not raw_footage_files:
        print(json.dumps({
            "error": "Missing required input: raw_footage_files",
            "step": "1.2_catalog_footage"
        }))
        sys.exit(1)

    try:
        result = catalog_footage(
            raw_footage_files,
            program_stream=input_data.get("program_stream"),
            project_config=input_data.get("project_config"),
            project_folder=input_data.get("project_folder", ""),
            measure_program_stream=input_data.get(
                "measure_program_stream"),
        )
    except (ValueError, RuntimeError) as e:
        print(json.dumps({
            "error": str(e),
            "step": "1.2_catalog_footage"
        }))
        sys.exit(1)

    json.dump(result, sys.stdout, indent=2)


if __name__ == "__main__":
    main()
