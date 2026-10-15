"""The file handshake: the ONE contract between Ren and a host LLM.

Ren never calls an LLM API in the co-editor path (P5: OAuth harnesses
only - Claude Code, Codex, opencode). Instead the runner and the host
pass files back and forth inside the project:

* Request:  `<project>/pipeline_output/llm_requests/<step>.json`
* Response: `<project>/pipeline_output/llm_responses/<step>.json`

The runner writes the request, prints `LLM_REQUEST_READY: <path>` on
stdout, and polls for the response file. The host (following the
`ren-co-editor` skill) reads the request, does the work, writes the
response, and the runner continues. If the runner is no longer waiting
(it timed out or was stopped), re-running the same `ren edit` command
resumes from the pending step.

This module owns that contract: the request schema, what counts as a
valid response, where both files live, and how a malformed response
refuses. The skill (`.agents/skills/ren-co-editor/SKILL.md`) teaches
the handshake; this module enforces it. Nothing else restates the
schema - `run_pipeline.py`'s agent backend and
`docs/RUN_001_END_TO_END.md` point here.

A request file carries:

* `step_id`: the DAG node id (e.g. `creative_direction`).
* `prompt_ref`: `{sha256, path}` of the prompt text, written ONCE as a
  content-addressed `llm_requests/prompts/<sha256>.md` that every
  request asking the same thing shares (see "Shared text" below).
* `constraints`: the brand-constraint text. The prompt the model reads
  is the `prompt_ref` text FOLLOWED BY this, so the brand's words sit
  between the handoff and the context, and are on disk once.
* `context`: the rendered context the model decides from - the
  per-item part, inline.
* `context_refs` (optional): `[{sha256, path, offset}]`, constant
  context tables (the motion roster, the axes) cut out of `context`
  and shared the same way; each is spliced back in at its `offset`.
* `expected_schema`: the JSON schema text the answer must satisfy.
* `project_folder`: absolute path of the project.
* `timestamp`: UTC ISO-8601 creation time.
* `kind` (optional): `llm_step` (default) or `briefing_interview` -
  the chat interview the host conducts with the user
  (`library/tools/briefing_chat.py`).
* `images` (optional): absolute paths of still images a file-handoff
  caller asks the host to inspect. `still_vision.py` now uses a direct
  Codex CLI call, so its requests no longer use this field. LLM-step
  requests carry no `images` key: their pictures travel as frame strips
  referenced inside `context`, which the host opens with its own file
  tools per the `ren-co-editor` skill.

A valid response file is a UTF-8 JSON object (`{...}`) written at the
response path above, satisfying the request's `expected_schema`. Arrays,
strings, empty files and unparseable JSON are malformed and REFUSE with
the fix (paths, what was wrong, the resume command) - never silently
accepted, never retried without the model being told.

Shared text (LE-11)
-------------------
**A request is a WIRE form; its MATERIALIZED form is the request the
model answers, and only the second is ever read.** A batch of reels
once sent the same 22.6 KB prompt and 20 KB roster in every one of 31
requests (2.38 MB, 20% unique). `share_request` turns a full request
into its wire form - the prompt and declared constant context sections
written once under `llm_requests/prompts/`, by content hash - and
`materialize` / `read_request` rebuild the full request from it, byte
for byte: the same keys in the same order, `prompt` as prompt plus
constraints, `context` with each shared section spliced back. So:

* Every reader of a request file goes through `read_request`, never
  `json.load` - a raw read sees `prompt_ref`, not the prompt.
* A shared text whose bytes do not hash to its name REFUSES
  (`SharedTextMissing`), as does a missing one: a request the model
  cannot rebuild is a question it must not answer blind.
* Shared files are never deleted or rewritten: a content address is
  stable, so an archived request still resolves through its `path`.
  Archivers copy `prompts/` beside the requests they copy.
* What a step decides from is still its OWN materialized context
  (AGENTS.md 10.1); the replay bench materializes the archived request
  before it compares, so a reconstruction is checked against exactly
  what the model was shown.
* `ren asks <project>` (`library/tools/pending_asks.py`) is the reading
  surface: it lists what is owed and prints each shared text once.

`tests/unit/context/test_llm_handshake.py` pins the refusal;
`tests/unit/context/test_shared_prompts.py` pins the round trip.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path

#: Subdirectory names under `<project>/pipeline_output/`. Mirrors
#: `library/tools/project_layout.Area.LLM_REQUESTS/LLM_RESPONSES` - the
#: layout owns the directories, this module owns the contract spoken
#: over them. A second spelling of either name here is a rename that
#: fails loudly at import time via `_assert_layout_agrees` below.
REQUESTS_SUBDIR = os.path.join("pipeline_output", "llm_requests")
RESPONSES_SUBDIR = os.path.join("pipeline_output", "llm_responses")

#: Where shared prompt and context text lives, under the requests dir.
SHARED_SUBDIR = "prompts"

#: Marker printed on stdout when a request is ready. One spelling, here.
READY_MARKER = "LLM_REQUEST_READY"

#: Request kinds. `llm_step` is a pipeline step's prompt; briefing,
#: edit translation and intent review may ask the USER through the host.
KINDS = ("llm_step", "briefing_interview", "edit_spec",
         "edit_spec_intent")

#: Keys every MATERIALIZED request carries (`read_request`). `kind` is
#: optional (default `llm_step`); the rest are required. On disk
#: `prompt` is `prompt_ref` (see "Shared text").
REQUEST_KEYS = (
    "step_id", "prompt", "constraints", "context",
    "expected_schema", "project_folder", "timestamp",
)

# Host instructions are deliberately independent of project, step and prompt.
# The schema follows them in the byte-stable prefix; task material follows in
# named sections so hosts can reuse this front matter across projects.
STABLE_HOST_INSTRUCTIONS = (
    "Follow the task instructions and use only the supplied task context and "
    "evidence. Return one JSON object matching the schema below.\n"
)


def request_layout(request: dict) -> dict:
    """Return the deterministic model-facing prefix and named variable parts."""
    schema = request.get("expected_schema", "")
    prefix = STABLE_HOST_INSTRUCTIONS + "\nJSON schema:\n" + str(schema)
    # Section references avoid copying prompt/context text into the wire
    # form (LE-11 stores each shared prompt body once).
    sections = [
        {"name": "task_instructions", "field": "prompt"},
        {"name": "constraints", "field": "constraints"},
        {"name": "context", "field": "context"},
    ]
    if "questions" in request:
        sections.extend((
            {"name": "task_context", "fields": [
                "questions", "intent_view", "assumptions"]},
            {"name": "evidence", "field": "footage_inventory"},
        ))
    if "images" in request:
        sections.append({"name": "evidence_images", "field": "images"})
    encoded = prefix.encode("utf-8")
    return {
        "stable_prefix": prefix,
        "stable_prefix_bytes": len(encoded),
        "stable_prefix_sha256": hashlib.sha256(encoded).hexdigest(),
        "sections": sections,
    }


class SharedTextMissing(ValueError):
    """A request names shared text that is absent or not what it hashed."""


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def shared_dir(requests_dir) -> Path:
    """Where a requests directory's shared texts live."""
    return Path(requests_dir) / SHARED_SUBDIR


def _atomic_write(path: Path, text: str) -> None:
    """Replace `path` with `text` in one step, at the umask's file mode.

    A temporary file is created 0600; the request a host reads with its
    own tools gets the mode a plain `open(path, "w")` would have given.
    """
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=path.parent,
                prefix=f".{path.name}.", suffix=".tmp",
                delete=False) as handle:
            temp_path = Path(handle.name)
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        umask = os.umask(0)
        os.umask(umask)
        os.chmod(temp_path, 0o666 & ~umask)
        os.replace(temp_path, path)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def store_shared(requests_dir, text: str, *, write: bool = True) -> dict:
    """Write `text` once, by content, and return its `{sha256, path}`.

    An existing file whose bytes already hash to its name is left
    alone; one that does not (a torn or edited write) is replaced.
    `write=False` only names it (a reader checking a wire form).
    """
    digest = _sha256(text)
    path = shared_dir(requests_dir) / f"{digest}.md"
    if not write:
        return {"sha256": digest, "path": str(path)}
    try:
        current = path.read_bytes()
    except OSError:
        current = None
    if current is None or hashlib.sha256(current).hexdigest() != digest:
        path.parent.mkdir(parents=True, exist_ok=True)
        _atomic_write(path, text)
    return {"sha256": digest, "path": str(path)}


def read_shared(ref: dict, requests_dir) -> str:
    """The text a `{sha256, path}` names, or refuse.

    Looked up beside the request first (`<requests_dir>/prompts/`), so
    a copied or archived request resolves inside its own copy; then at
    the recorded `path`. The bytes must hash to `sha256`.
    """
    try:
        digest = str(ref["sha256"])
    except (KeyError, TypeError) as exc:
        raise SharedTextMissing(
            f"shared-text reference {ref!r} names no sha256") from exc
    candidates = [shared_dir(requests_dir) / f"{digest}.md"]
    if isinstance(ref, dict) and ref.get("path"):
        candidates.append(Path(str(ref["path"])))
    for candidate in candidates:
        try:
            raw = candidate.read_bytes()
        except OSError:
            continue
        if hashlib.sha256(raw).hexdigest() == digest:
            return raw.decode("utf-8")
    raise SharedTextMissing(
        f"shared text {digest[:12]} is missing or altered: looked in "
        f"{', '.join(str(c) for c in candidates)}. The request cannot be "
        f"rebuilt - re-run the step (or the reel build) that wrote it, "
        f"which writes the shared text again")


def share_request(request: dict, requests_dir, *,
                  context_shared=(), write: bool = True) -> dict:
    """The wire form of a full request: shared text written once.

    `request` is the request as the model must read it (`prompt` the
    full text, constraints included). Its `prompt` becomes `prompt_ref`
    (the prompt WITHOUT the trailing constraints, which stay inline in
    `constraints` - on disk once). Each string in `context_shared` must
    occur in a string `context`, in order; it is cut out and recorded
    in `context_refs` with the offset it is spliced back at.

    A request with no string `prompt` (an assignment envelope) is
    returned unchanged. The result is checked to materialize back to
    `request` exactly, so a wire form that loses a byte never lands.
    `write=False` writes nothing and skips that check (it needs the
    shared files on disk): the replay bench re-derives a wire form to
    compare with the one a writer left.
    """
    if not isinstance(request.get("prompt"), str):
        return dict(request)
    prompt = request["prompt"]
    constraints = request.get("constraints")
    body = prompt
    if isinstance(constraints, str) and constraints:
        if not prompt.endswith(constraints):
            raise ValueError(
                f"request for {request.get('step_id')!r}: `prompt` does not "
                f"end with its `constraints`, so the two cannot be stored "
                f"once each - build the prompt as handoff + constraints")
        body = prompt[:len(prompt) - len(constraints)]
    refs = []
    remainder = request.get("context")
    if context_shared:
        if not isinstance(remainder, str):
            raise ValueError(
                f"request for {request.get('step_id')!r}: shared context "
                f"sections need a text context")
        full, pieces, cursor = remainder, [], 0
        for section in context_shared:
            at = full.find(section, cursor) if section else -1
            if at < 0:
                raise ValueError(
                    f"request for {request.get('step_id')!r}: a shared "
                    f"context section ({section[:40]!r}...) is not in the "
                    f"context after offset {cursor}")
            pieces.append(full[cursor:at])
            refs.append(dict(store_shared(requests_dir, section,
                                          write=write), offset=at))
            cursor = at + len(section)
        pieces.append(full[cursor:])
        remainder = "".join(pieces)
    wire = {}
    for key, value in request.items():
        if key == "prompt":
            wire["prompt_ref"] = store_shared(requests_dir, body, write=write)
        elif key == "context" and refs:
            wire["context_refs"] = refs
            wire["context"] = remainder
        else:
            wire[key] = value
    if write and materialize(wire, requests_dir) != request:
        raise ValueError(
            f"request for {request.get('step_id')!r}: its shared form does "
            f"not rebuild the request it came from")
    return wire


def materialize(request: dict, requests_dir) -> dict:
    """The full request a wire form names, keys in their original order.

    A request already carrying `prompt` inline (written before shared
    text existed) is returned as it is.
    """
    if "prompt_ref" not in request and "context_refs" not in request:
        return dict(request)
    out = {}
    for key, value in request.items():
        if key == "prompt_ref":
            constraints = request.get("constraints")
            out["prompt"] = read_shared(value, requests_dir) + (
                constraints if isinstance(constraints, str) else "")
        elif key == "context_refs":
            continue
        elif key == "context" and request.get("context_refs"):
            text, cursor = "", 0
            for ref in request["context_refs"]:
                take = int(ref["offset"]) - len(text)
                if take < 0 or cursor + take > len(value):
                    raise SharedTextMissing(
                        f"context_refs offset {ref['offset']} does not fit "
                        f"the inline context of {request.get('step_id')!r}")
                text += value[cursor:cursor + take]
                cursor += take
                text += read_shared(ref, requests_dir)
            out["context"] = text + value[cursor:]
        else:
            out[key] = value
    return out


def read_request(path) -> dict:
    """A request file, materialized: what the model is asked.

    The ONE way to read a request file. Raises `OSError`/`ValueError`
    like `json.load` for a missing or unparseable file, and
    `SharedTextMissing` (a `ValueError`) when its shared text is gone.
    """
    path = Path(path)
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"{path} is not a JSON object")  # noqa: TRY004 - malformed handshake is a value error
    return materialize(payload, path.parent)


def write_request_file(path, request: dict, *, context_shared=(),
                       ensure_ascii: bool = True,
                       trailing_newline: bool = False) -> dict:
    """Write a request's wire form atomically; return the wire form.

    For writers whose answer slot outlives the request (the reel asks
    are rewritten on every build while their answers stand), so it
    does not touch the response the way `publish_request` does.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    wire = share_request(request, path.parent, context_shared=context_shared)
    text = json.dumps(wire, indent=2, ensure_ascii=ensure_ascii)
    _atomic_write(path, text + ("\n" if trailing_newline else ""))
    return wire


def copy_shared(src_requests_dir, dst_requests_dir) -> list:
    """Copy a requests dir's shared texts beside a copy of its requests.

    Archivers copy request files flat; this carries the `prompts/` they
    name, so the copy rebuilds without the original. Returns the
    destination paths written.
    """
    import shutil
    src = shared_dir(src_requests_dir)
    if not src.is_dir():
        return []
    dst = shared_dir(dst_requests_dir)
    dst.mkdir(parents=True, exist_ok=True)
    written = []
    for item in sorted(src.iterdir()):
        if item.is_file() and item.suffix == ".md":
            shutil.copy2(item, dst / item.name)
            written.append(dst / item.name)
    return written


class HandshakeRefusal(RuntimeError):
    """A malformed handshake response, carrying its own fix.

    A `RenRefusal` (what happened, why, the fix - see
    `library/tools/ren_refusal.py`), built from the step, the reason,
    the response path and the resume. The rendered message always
    names the step, the exact response path to repair, and how to
    resume. Constructed with the four handshake fields rather than
    raw what/why/fix so a raise site cannot forget what the fix must
    contain.
    """

    def __init__(self, step_id: str, reason: str, response_path: str,
                 resume: str) -> None:
        from library.tools.ren_refusal import RenRefusal
        self.step_id = step_id
        self.reason = reason
        self.response_path = response_path
        self.resume = resume
        self._ren = RenRefusal(
            f"malformed handshake response for step {step_id}: {reason}",
            f"the file at {response_path} is not the JSON object the "
            f"request's expected_schema asked for",
            f"write a UTF-8 JSON object satisfying the request's "
            f"expected_schema to {response_path}, then {resume}")
        super().__init__(str(self._ren.render()))


def request_path(project_folder: str, step_id: str) -> str:
    """Absolute path of the request file for a step."""
    return os.path.join(project_folder, REQUESTS_SUBDIR, f"{step_id}.json")


def response_path(project_folder: str, step_id: str) -> str:
    """Absolute path of the response file for a step."""
    return os.path.join(project_folder, RESPONSES_SUBDIR, f"{step_id}.json")


def publish_request(request: Path, response: Path, payload: dict) -> None:
    """Publish a fresh request without exposing the preceding turn.

    The payload is the FULL request; it lands in its wire form
    (`share_request`), so the prompt is shared text on disk.
    The request filename is stable across bounded retries. Remove its old
    contents before clearing the old response, then atomically replace it
    with the new payload. A polling host can therefore never pair the
    newly-cleared response slot with the previous attempt's request, or
    read a partially written JSON document.
    """
    request = Path(request)
    response = Path(response)
    request.unlink(missing_ok=True)
    response.unlink(missing_ok=True)
    request.parent.mkdir(parents=True, exist_ok=True)
    payload = share_request(payload, request.parent)
    _atomic_write(request, json.dumps(payload, indent=2))


def publish_operation_request(request: Path, response: Path,
                              envelope: dict) -> None:
    """Publish a host-neutral assignment operation through the same
    atomic request/response slots used by the legacy host handshake.

    Existing LLM step request payloads are unchanged; this is an additive
    compatibility path for structured operation envelopes.
    """
    publish_request(request, response,
                    {"kind": "assignment_operation", "envelope": envelope})


def publish_operation_response(request: Path, response: Path,
                               payload: dict) -> None:
    """Atomically checkpoint an operation envelope as a handshake response."""
    response = Path(response)
    response.parent.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", dir=response.parent,
                prefix=f".{response.name}.", suffix=".tmp",
                delete=False) as handle:
            temp_path = Path(handle.name)
            json.dump(payload, handle, indent=2, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_path, response)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)


def resume_command(project_folder: str) -> str:
    """How to resume after repairing a response.

    The runner polls while it waits, so when it is still alive nothing
    needs restarting - writing the file is enough. When it already
    timed out or was stopped, re-running the same edit command resumes
    from the pending step.
    """
    return (
        f"if the run is still waiting, just write the file; otherwise "
        f"`ren edit {project_folder} --full-auto agent` resumes it"
    )


def build_request(step_id: str, prompt: str, constraints: str,
                   context: str, expected_schema: str,
                   project_folder: str, timestamp: str,
                   kind: str = "llm_step",
                   images: list | None = None) -> dict:
    """Build the request payload the runner writes to disk.

    This is the MATERIALIZED request: `prompt` is the prompt followed by
    `constraints`, and `constraints` the brand's text on its own. On
    disk the two are not duplicated - `write_request_file` /
    `publish_request` store the prompt (without the constraints) once
    as shared text and keep `constraints` inline.

    `images` is the still-vision contract: absolute paths of stills
    the host must look at with its own vision (`still_vision.py`).
    Omitted entirely when None, so every existing request is
    byte-identical. When given, each entry must be an absolute path
    to a file on disk - a relative path or a missing file refuses
    here, at build time, rather than stranding a host with a
    picture it cannot open.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown handshake kind {kind!r}; want one of {KINDS}")
    request = {
        "step_id": step_id,
        "prompt": prompt + constraints,
        "constraints": constraints,
        "context": context,
        "expected_schema": expected_schema,
        "project_folder": project_folder,
        "timestamp": timestamp,
        "kind": kind,
    }
    if images is not None:
        request["images"] = _checked_images(images, step_id)
    request["request_layout"] = request_layout(request)
    return request


def _checked_images(images: list, step_id: str) -> list:
    """Validate a still-vision `images` list, or refuse with the fix."""
    if not isinstance(images, list) or not images:
        raise ValueError(
            f"still-vision request for step {step_id}: `images` must be "
            f"a non-empty list of absolute still paths, got {images!r}")
    checked = []
    for entry in images:
        if not isinstance(entry, str) or not os.path.isabs(entry):
            raise ValueError(
                f"still-vision request for step {step_id}: image "
                f"{entry!r} is not an absolute path - the host opens "
                f"these with its own file tools, so a relative path "
                f"resolves nowhere")
        if not os.path.isfile(entry):
            raise ValueError(
                f"still-vision request for step {step_id}: image "
                f"{entry!r} is not a file on disk - extract the still "
                f"first, then file the request")
        checked.append(entry)
    return checked


#: The key a still-vision response carries the model's answer under.
#: The handshake still validates exactly as today (a JSON object, or
#: a refusal with the fix); the site's own parser then reads this
#: text, byte-for-byte what the gemma path would have returned.
STILL_VISION_TEXT_KEY = "text"


def require_text_answer(parsed: dict, step_id: str,
                        project_folder: str) -> str:
    """Split the model's answer text out of a still-vision response.

    Returns the `text` string. Anything else refuses in the
    handshake's own shape (step, reason, path, resume) - a host that
    answers without looking, or in the wrong shape, is a malformed
    response like any other, never a quiet fallback.
    """
    res = response_path(project_folder, step_id)
    resume = resume_command(project_folder)
    text = parsed.get(STILL_VISION_TEXT_KEY, None) if isinstance(
        parsed, dict) else None
    if not isinstance(text, str) or not text.strip():
        raise HandshakeRefusal(
            step_id,
            f"the still-vision response has no usable "
            f"`{STILL_VISION_TEXT_KEY}` string - look at the request's "
            f"`images` with your own vision and write your answer as "
            f"plain text under `{STILL_VISION_TEXT_KEY}`.",
            res, resume)
    return text


def validate_response(step_id: str, raw_text: str, project_folder: str):
    """Parse and accept a response file's text, or refuse with the fix.

    Returns the parsed JSON object. Raises `HandshakeRefusal` (a
    ValueError-shaped Exception carrying step, reason, path and resume)
    for: empty files, unparseable JSON, and JSON that is not an object.
    Schema-field checking stays with the step's own validator - this
    refuses only what no step could read.
    """
    res = response_path(project_folder, step_id)
    resume = resume_command(project_folder)
    if not raw_text.strip():
        raise HandshakeRefusal(step_id, "the response file is empty.",
                               res, resume)
    try:
        parsed = json.loads(raw_text)
    except (ValueError, json.JSONDecodeError) as exc:
        raise HandshakeRefusal(
            step_id, f"the response file is not JSON ({exc}).",
            res, resume) from exc
    if not isinstance(parsed, dict):
        raise HandshakeRefusal(
            step_id,
            f"the response is a JSON {type(parsed).__name__}, not an object - "
            f"the step expects a {{...}} matching its expected_schema.",
            res, resume)
    return parsed


def _assert_layout_agrees() -> None:
    """The layout owns the directory names; this module must agree."""
    try:
        from library.tools.project_layout import _AREAS, Area
        assert _AREAS[Area.LLM_REQUESTS].reldir.endswith("llm_requests")
        assert _AREAS[Area.LLM_RESPONSES].reldir.endswith("llm_responses")
    except (ImportError, AttributeError, KeyError):
        return  # layout refactors rename here first; tests catch drift


_assert_layout_agrees()
