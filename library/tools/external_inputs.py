"""State the pipeline did not produce, offered to a step, and CHECKED.

The gap this closes
-------------------
A step's prerequisites could be met two ways: a step in this run
produces the value, or a previous run recorded it.  Both are statements
about LINEAGE - which step made it.  The captain's model (#260,
2026-08-28) is a statement about STATE:

    "it should be possible to have as much or as little in the number of
    steps in the pipeline (given that all necessary prerequisties have
    been fulfilled -- like for example you should not be able to add
    transitions or effects when there exists no roughcut either already
    on the timeline manually or automated by the LLM during the process)"

"already on the timeline manually" is the third way, and it did not
exist.  This module is that third way, and the whole of it.

Why this is not a flag
----------------------
An unchecked "trust me, it exists" flag would dissolve exactly the
contract enforcement the captain asked to keep, so nothing here is
asserted.  A prerequisite is satisfied from outside by SUPPLYING THE
VALUE, in a file, which is then verified against a check registered for
that state key - and the same verified value is what
`gather_step_inputs` hands the step.  So the thing that satisfies the
resolver is the thing the step receives; there is no state of the world
where the resolver believed something the run then could not use.

That is the same standard the recorded-output path meets, where a ledger
entry alone is not enough and the `step_outputs` value has to be there
too.

What can be asserted, and what cannot
-------------------------------------
`CHECKS` is the enumeration.  A key that is not in it CANNOT be
supplied - the file is refused by name, saying so - because a check that
does not exist is not a check that passes.  `WITHDRAWN` records the
claims somebody would reasonably try and why they are not assertable;
the first of them is the captain's own words, and the answer to it is
that a hand-built Resolve timeline is not refusable at resolve time,
while the artifact describing it is.

Where it lives
--------------
`<project>/external/<state_key>.json`, an `Area.EXTERNAL_STATE` input
area: `write_dir` raises for it, `ensure()` does not create it, and no
step may write there.  Each file is:

    {
      "key": "assembly_manifest",
      "source": "assembled by hand in Resolve and exported, 2026-08-28",
      "value": { ... }
    }

`source` is RECORDED, never trusted - it is what a reader of
`RUN-TRACEBACK.md` needs in order to know the value did not come from a
step.  The verdict comes from the check, not from the sentence.

    python3 -m library.tools.external_inputs <project_folder>

`tests/test_external_inputs.py`.


Rules relocated from AGENTS.md 3
--------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 3 keeps the headline
and points here.

**A prerequisite may be satisfied from outside the pipeline, and it is CHECKED, never asserted.**
One enumeration, `library/tools/external_inputs.py`.
- The value is SUPPLIED, in `<project>/external/<state_key>.json` carrying `key`, `source` and `value` - not claimed by a flag. The same verified value is what `gather_step_inputs` hands the step, so the resolver can never believe something the run cannot use.
- **The file is named for the STATE key, which is the PRODUCER's name for it.** Step 6.01 records `render_output`; step 6.02 calls the same value `rendered_output`. Offering the consumer's name is refused, naming the producer's.
- **`CHECKS` is the whole of what can be supplied. A key that is not in it is refused by name**, because a check that does not exist is not a check that passes.  **A Resolve timeline built by hand is not one of them**: it is not refusable at resolve time, so supply the artifact that describes it instead.   It is not skipped.
- `tests/test_external_inputs.py`.
"""

from __future__ import annotations

import json
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Dict, List, Mapping, Optional

from library.tools.project_layout import Area, ProjectLayout


class ExternalStateError(ValueError):
    """A supplied value that does not check out, refused before the run."""


SUFFIX = ".json"
_MIN_RENDER_BYTES = 100_000
"""A master under this is not a delivered video. `render_qa` reads a
single FRAME as broken under 2KB; a whole render is three orders bigger,
and the point of the floor is to catch an empty or truncated file, not
to judge the picture."""


@dataclass(frozen=True)
class Supplied:
    """One verified external value."""

    key: str
    """The state key it supplies - what `step_outputs[producer][key]`
    would have held."""

    value: object
    source: str
    path: Path
    checked: str
    """One sentence naming what was actually verified, for the run log."""


@dataclass(frozen=True)
class Context:
    """What a check may consult besides the value itself."""

    project_folder: Path
    state: Mapping
    """The project's `pipeline_data.json`, for cross-checks that are free
    when the pipeline has already measured something - the catalog's
    per-clip durations, for instance. A check must still be able to
    reach a verdict without it."""


# ── The checks ───────────────────────────────────────────────────────

def _number(value, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ExternalStateError(f"{label} is not a number: {value!r}")
    return float(value)


def _existing_file(path, label: str) -> Path:
    if not isinstance(path, str) or not path:
        raise ExternalStateError(f"{label} names no file")
    resolved = Path(path)
    if not resolved.is_absolute():
        raise ExternalStateError(
            f"{label} is {path!r}, a relative path. Media is addressed "
            f"absolutely everywhere else in this pipeline, and a relative "
            f"one resolves against whichever directory a step happens to "
            f"run in.")
    if not resolved.is_file():
        raise ExternalStateError(f"{label} names {path}, which is not a file")
    return resolved


def _catalog_durations(context: Context) -> Dict[str, float]:
    catalog = ((context.state.get("step_outputs") or {})
               .get("catalog") or {}).get("clip_catalog") or []
    out = {}
    for clip in catalog:
        if isinstance(clip, dict) and clip.get("clip_id"):
            duration = clip.get("duration_seconds")
            if isinstance(duration, (int, float)):
                out[clip["clip_id"]] = float(duration)
    return out


def _check_a_roll_assignments(value, context: Context) -> str:
    """A cut somebody made elsewhere: which clip plays, from where, when.

    Checkable because every claim it makes is about a file on disk and a
    range inside it. What is NOT checked is whether the cut is any good;
    that was never the pipeline's job.
    """
    if not isinstance(value, list) or not value:
        raise ExternalStateError(
            "a_roll_assignments must be a non-empty list of assignments. "
            "An empty one is the absence of a rough cut, not a rough cut "
            "supplied from outside.")
    durations = _catalog_durations(context)
    checked_against_catalog = 0
    for index, entry in enumerate(value):
        label = f"a_roll_assignments[{index}]"
        if not isinstance(entry, dict):
            raise ExternalStateError(f"{label} is not an object")
        source = _existing_file(entry.get("source_file"),
                                f"{label}.source_file")
        video_in = _number(entry.get("video_in"), f"{label}.video_in")
        video_out = _number(entry.get("video_out"), f"{label}.video_out")
        start = _number(entry.get("timeline_start"),
                        f"{label}.timeline_start")
        end = _number(entry.get("timeline_end"), f"{label}.timeline_end")
        if video_out <= video_in:
            raise ExternalStateError(
                f"{label} plays {source.name} from {video_in} to "
                f"{video_out}, which is not a range")
        if end <= start:
            raise ExternalStateError(
                f"{label} occupies {start} to {end} on the timeline, "
                f"which is not a range")
        clip_id = entry.get("clip_id") or entry.get("source_clip_id")
        if clip_id in durations:
            checked_against_catalog += 1
            if video_out > durations[clip_id] + 0.001:
                raise ExternalStateError(
                    f"{label} plays {clip_id} to {video_out}s and the "
                    f"catalog measured that clip at "
                    f"{durations[clip_id]}s")
    against = (f", {checked_against_catalog} of them against the "
               f"catalog's measured durations" if checked_against_catalog
               else ", and the catalog is not on file so no duration "
                    "could be cross-checked")
    return (f"{len(value)} assignments, every source file present on disk "
            f"and every range non-empty{against}")


def _check_audio_spine(value, context: Context) -> str:
    """The cut's structure, checked with the pipeline's own contract."""
    from library.tools import spine_contract

    if not isinstance(value, dict):
        raise ExternalStateError("audio_spine must be an object")
    blocks = value.get("structure")
    if not isinstance(blocks, list) or not blocks:
        raise ExternalStateError(
            "audio_spine.structure must be a non-empty list of blocks")
    try:
        spine_contract.validate_spine_blocks(blocks)
    except spine_contract.SpineContractError as exc:
        raise ExternalStateError(
            f"audio_spine fails the spine contract every step downstream "
            f"reads it through (library/tools/spine_contract.py): "
            f"{exc}") from exc
    return (f"{len(blocks)} blocks, passing "
            f"spine_contract.validate_spine_blocks")


def _check_assembly_manifest(value, context: Context) -> str:
    """A manifest assembled elsewhere, checked as the compiler checks its
    own: structure, then the semantic half, then the media."""
    from library.tools import manifest_validator

    if not isinstance(value, dict):
        raise ExternalStateError("assembly_manifest must be an object")
    errors = list(manifest_validator.validate_manifest(value))
    errors += list(manifest_validator.validate_manifest_semantics(value))
    if errors:
        raise ExternalStateError(
            "assembly_manifest fails the validator step 5.04 runs on its "
            "own output:\n  - " + "\n  - ".join(str(e) for e in errors))
    files = 0
    for track, data in (value.get("tracks") or {}).items():
        for index, clip in enumerate(data.get("clips") or []):
            _existing_file(clip.get("source_file"),
                           f"tracks.{track}.clips[{index}].source_file")
            files += 1
    if not files:
        raise ExternalStateError(
            "assembly_manifest places no clip on any track")
    return (f"passes manifest_validator's structural and semantic halves, "
            f"and all {files} placed clips resolve to a file on disk")


def _check_render_output(value, context: Context) -> str:
    """A master rendered outside the pipeline.

    Named `render_output`, which is what step 6.01 records it as -
    `validate` calls the same value `rendered_output` on its own side of
    the edge. A file here is named for the STATE, so it is the
    producer's name that counts (AGENTS.md section 10.1 on key-name
    mismatches); `_alias_hint` says so when somebody uses the other one.

    The one check here that is not about JSON: ffprobe has to find a
    video stream in the named file. A path that exists is not a video,
    and step 6.02 would otherwise measure the letterbox bars of a text
    file.
    """
    if not isinstance(value, dict):
        raise ExternalStateError("render_output must be an object")
    path = _existing_file(value.get("output_path"),
                          "render_output.output_path")
    size = path.stat().st_size
    if size < _MIN_RENDER_BYTES:
        raise ExternalStateError(
            f"render_output names {path.name}, which is {size} bytes. "
            f"A delivered master is not that small.")
    streams = _probe_streams(path)
    if streams is None:
        raise ExternalStateError(
            f"ffprobe could not read {path.name}. A file that is not "
            f"decodable is not a render.")
    if "video" not in streams:
        raise ExternalStateError(
            f"ffprobe found no video stream in {path.name} "
            f"(streams: {sorted(streams) or 'none'})")
    return (f"{path.name}, {size // 1024} KiB, ffprobe reports "
            f"{'+'.join(sorted(streams))}")


def _probe_streams(path: Path) -> Optional[set]:
    try:
        proc = subprocess.run(
            ["ffprobe", "-v", "error", "-print_format", "json",
             "-show_streams", str(path)],
            capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=60, check=False)
        if proc.returncode != 0:
            return None
        data = json.loads(proc.stdout)
    except (OSError, ValueError, subprocess.SubprocessError):
        return None
    return {stream.get("codec_type") for stream in data.get("streams", [])
            if stream.get("codec_type")}


Check = Callable[[object, Context], str]

CHECKS: Dict[str, Check] = {
    "a_roll_assignments": _check_a_roll_assignments,
    "audio_spine": _check_audio_spine,
    "assembly_manifest": _check_assembly_manifest,
    "render_output": _check_render_output,
}


# ── What cannot be asserted ──────────────────────────────────────────
#
# Recorded rather than left to a puzzled reader, because "there is no
# check for that" is a real answer and the alternative - a flag taken on
# faith - is the thing this module exists to avoid.

WITHDRAWN: Dict[str, str] = {
    "a Resolve timeline built by hand":
        "The captain's own words are 'already on the timeline manually'. "
        "A timeline is not refusable at resolve time: it lives in "
        "Resolve's project database, reading it means copying that "
        "database and opening it as SQLite (AGENTS.md section 5), and "
        "nothing maps its clips back onto a typed pipeline key. The "
        "artifact DESCRIBING the cut is checkable and is what to supply "
        "instead - `audio_spine` for the structure, "
        "`assembly_manifest` for a whole assembly, `a_roll_assignments` "
        "for which clip plays when.",
    "transition_spec / enhancement_spec / sfx_spec / color_grade_spec":
        "Nothing to assert. Since #260 these are OPTIONAL inputs of "
        "`compile_manifest`, so a run that does not want them simply "
        "leaves their planners out and the manifest compiles with hard "
        "cuts, no effects, no sound design and no grade.",
    "creative_direction / speech_sequence / the planning outputs":
        "No check exists that could tell a real creative decision from a "
        "plausible-looking one, and a shape check would pass anything "
        "shaped right. Supplying taste from outside is a different "
        "feature from satisfying a prerequisite, and it would need a "
        "reviewer rather than a validator.",
}


# ── Reading a project's external state ───────────────────────────────

def external_dir(project_folder) -> Path:
    """Where a project's supplied values live. Never created here: an
    INPUT area exists because the captain made it."""
    return Path(ProjectLayout(str(project_folder)).read_dir(
        Area.EXTERNAL_STATE))


def _alias_hint(key: str) -> str:
    """Say so when the offered name is a CONSUMER'S name for something
    that IS checkable under the producer's name.

    `validate` declares `rendered_output`; step 6.01 records
    `render_output`. A file supplies STATE, so the producer's name is
    the one that counts - and a captain who read the consumer's manifest
    has no way to know that without being told.
    """
    from library.tools import run_scope

    for edge in run_scope.load_dag().get("edges", []):
        for source_key, destination in (edge.get("data_mapping") or {}).items():
            if destination == key and source_key in CHECKS:
                return (f"  {key!r} is what {edge['to']} calls it. The "
                        f"STATE key is {source_key!r} - {edge['from']} "
                        f"records it under that name - so the file is "
                        f"{source_key}{SUFFIX}.\n")
    return ""


def verify(path: Path, context: Context) -> Supplied:
    """One file, checked. Raises `ExternalStateError` with the reason."""
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ExternalStateError(f"{path} cannot be read: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ExternalStateError(f"{path} is not valid JSON: {exc}") from exc

    if not isinstance(document, dict):
        raise ExternalStateError(
            f"{path.name} must be an object with 'key', 'source' and "
            f"'value'.")

    key = document.get("key")
    if key != path.stem:
        raise ExternalStateError(
            f"{path.name} declares key {key!r}. The file name IS the "
            f"state key it supplies, so name the file {key}{SUFFIX} or "
            f"fix the key.")
    if key not in CHECKS:
        raise ExternalStateError(
            f"{key!r} cannot be supplied from outside the pipeline: "
            f"nothing here can check it. A check that does not exist is "
            f"not a check that passes.\n"
            f"  Checkable: {', '.join(sorted(CHECKS))}\n"
            f"{_alias_hint(key)}"
            f"  See library/tools/external_inputs.WITHDRAWN for what is "
            f"not, and why.")

    source = document.get("source")
    if not isinstance(source, str) or not source.strip():
        raise ExternalStateError(
            f"{path.name} declares no 'source'. It is recorded, not "
            f"trusted - a run has to be able to say where a value that "
            f"no step produced came from.")

    if "value" not in document:
        raise ExternalStateError(f"{path.name} carries no 'value'")
    value = document["value"]
    if value is None or (isinstance(value, (list, dict, str))
                         and len(value) == 0):
        raise ExternalStateError(
            f"{path.name} supplies an empty {type(value).__name__}. "
            f"Nothing is not a value; leave the file out instead.")

    checked = CHECKS[key](value, context)
    return Supplied(key=key, value=value, source=source.strip(), path=path,
                    checked=checked)


def load(project_folder, state: Optional[Mapping] = None
         ) -> Dict[str, Supplied]:
    """Every verified external value for a project, `{state key: value}`.

    A file that does not check out RAISES. It is a claim the captain
    made about their own project, and skipping it would leave the run to
    fail later for a reason that names a step instead of the file.
    """
    if not project_folder:
        return {}
    try:
        directory = external_dir(project_folder)
    except (KeyError, ValueError):
        return {}
    if not directory.is_dir():
        return {}

    context = Context(project_folder=Path(project_folder),
                      state=state or {})
    supplied: Dict[str, Supplied] = {}
    for path in sorted(directory.glob(f"*{SUFFIX}")):
        entry = verify(path, context)
        supplied[entry.key] = entry
    return supplied


def describe(supplied: Mapping[str, Supplied]) -> List[str]:
    """The lines a run prints about state it did not produce."""
    lines = []
    for key in sorted(supplied):
        entry = supplied[key]
        lines.append(f"  Supplied from outside: {key} - {entry.checked}")
        lines.append(f"      source (recorded, not checked): {entry.source}")
    return lines


def main(argv=None) -> int:
    import sys

    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: python3 -m library.tools.external_inputs "
              "<project_folder>", file=sys.stderr)
        return 2
    project_folder = argv[0]
    state_path = ProjectLayout(project_folder).pipeline_data_path
    state = {}
    if os.path.exists(state_path):
        try:
            state = json.loads(Path(state_path).read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
    try:
        supplied = load(project_folder, state)
    except ExternalStateError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    if not supplied:
        print(f"No external state in "
              f"{external_dir(project_folder)}.\n"
              f"Checkable keys: {', '.join(sorted(CHECKS))}")
        return 0
    print("\n".join(describe(supplied)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
