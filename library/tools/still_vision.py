"""Still-frame inspection goes to the DRIVER first, gemma only as fallback.

The captain's ruling, 2026-09-24: still-frame inspection goes to the
driving LLM's own vision first - "claude code can analyze images
directly" - with gemma4 only as the fallback when the driving LLM
cannot see images. Whole-video understanding stays with gemma:
"most LLMs don't process video natively".

This module is the ONE route every still-frame site calls:

* `library/tools/analysis/vision_pipeline_v3.py` - the object passes
  (`analyze_objects_coarse`, `analyze_objects_detail`) pack extracted
  JPEG stills; the scene, camera, action-window and assessment passes
  send VIDEO and never reach here.
* `library/skills/ask_the_footage/skill.py` - `ask_vision` (also the
  Gemma pass `verify_treatment` reuses).
* `library/tools/visual_qa_router.py` - `analyze_frame_locally` and
  `run_perceptual_observation`.
* `library/tools/qa/subtitle_qa.py` - `_vision_observation`.

NOT routed here, deliberately: `frame_ranker.py` (a local CLIP
measurement, not gemma), `render_watch.py` (no model call - strips
the host reads from context), `video_segment_analyzer.py`
(whole-video `analyze_video`, stays on gemma), and anything that
sends VIDEO to gemma.

The route, in order:

1. No host (no harness declared - bare CLI runs, non-agent
   full-auto, tests): gemma4, exactly as today.
2. A host whose declared capability says it sees images: the file
   handshake (`llm_handshake.py`) with the stills as the request's
   `images` list. The host opens each file, looks with its own
   vision, and answers `{"text": ...}`; the site's own parser then
   reads that text byte-for-byte the way it read gemma's.
3. A host declared BLIND: gemma4. Today no harness declares blind;
   the branch exists so a future harness lands there rather than
   being guessed about.

What the router never does: change which stills are extracted, how
they are sampled, or what the audio half of an action window hears -
those are later work. And it never substitutes gemma quietly: a
timeout or a malformed host answer RAISES (fail-closed, with the
request path and the resume), because a silent fallback would undo
the ruling invisibly.
"""

from __future__ import annotations

import json
import os
import sys
import time

#: Which harnesses the host answers through can be shown a picture.
# A COMPLETE enumeration, the same shape as
# `window_frames.HARNESS_SHOWS_FRAMES` and
# `brief_reference.HARNESS_READS_FILES`: an unknown harness raises
# rather than being guessed about, because fallback-to-gemma is a
# DECLARED property of the host, not an inference. A future harness
# (opencode, codex, ...) is added here explicitly once its vision is
# established - until then a run under it refuses loudly instead of
# answering from filenames.
#
# - `agent`: the host IS an agent with a shell and file tools
#   ("the agent IS the LLM"), so it opens each `images` path and
#   looks with its own vision.
# - `mock`: replays a recorded answer. No model runs, so nothing is
#   shown to anybody; listed as seeing because a recorded answer is
#   unaffected either way.
HOST_SEES_IMAGES = {
    "agent": True,
    "mock": True,
}

#: Environment carrying the driving harness into code that never sees
#: the runner's argv: the vision subprocess, the skills, the QA
#: paths. Set by `run_pipeline` from `--full-auto`; unset or empty
#: means no host drives, and stills go to gemma. Explicit `harness=`
#: arguments always win over this.
HARNESS_ENV_VAR = "PIPELINE_HOST_HARNESS"


class UnknownHost(ValueError):
    """A harness whose vision nobody has established."""


class StillVisionTimeout(RuntimeError):
    """The host never answered a still-vision request.

    Carries the request path (what was asked), the response path
    (where the answer goes), and how the run resumes. Fail-closed:
    a host that does not answer is NOT a host that cannot see, so
    this never falls back to gemma.
    """

    def __init__(self, step_id: str, request_path: str,
                 response_path: str, resume: str,
                 timeout_seconds: float) -> None:
        self.step_id = step_id
        self.request_path = request_path
        self.response_path = response_path
        self.resume = resume
        super().__init__(
            f"still-vision request for step {step_id} unanswered after "
            f"{timeout_seconds:g}s: the request is filed at "
            f"{request_path} - write {{\"text\": \"...\"}} (your answer "
            f"after looking at the request's `images` with your own "
            f"vision) to {response_path}, then {resume}")


def host_sees_images(harness: str) -> bool:
    """True when a host answering through `harness` sees images."""
    if harness not in HOST_SEES_IMAGES:
        raise UnknownHost(
            f"harness {harness!r} is not in HOST_SEES_IMAGES "
            f"({sorted(HOST_SEES_IMAGES)}). Establish whether a host "
            f"answering through it can look at image files, and record "
            f"the answer there - a harness whose vision nobody has "
            f"established is not a harness known to be blind.")
    return HOST_SEES_IMAGES[harness]


def resolve_harness(explicit: str | None = None) -> str | None:
    """The driving harness, or None when no host drives.

    An explicit argument wins; otherwise the runner's environment
    (`PIPELINE_HOST_HARNESS`); empty or unset means no host, and
    stills go to gemma - exactly the "runs with no host, such as
    non-agent full-auto, use gemma" half of the ruling.
    """
    if explicit:
        return explicit
    value = os.environ.get(HARNESS_ENV_VAR, "").strip()
    return value or None


def request_step_id(step_id: str) -> str:
    """The handshake file stem for a still-vision request.

    Suffixed so a nested still question never shares a file with its
    step's own handshake: the runner deletes a stale response when it
    files a request, so sharing a stem would eat the step's answer.
    """
    return f"{step_id}__stills"


def still_response_schema() -> str:
    """The `expected_schema` every still-vision request carries.

    One shape for every site: the host's answer as plain text under
    `text`. The site's own parser reads that text afterwards, so
    validation here is exactly the handshake's (an object, or a
    refusal) plus `require_text_answer`.
    """
    return json.dumps([
        {"name": "text", "type": "string", "required": True,
         "description": "Your answer to the prompt, as plain text, "
                        "written AFTER looking at every image in the "
                        "request's `images` list with your own vision."},
    ])


def request_host_answer(prompt: str, image_paths: list,
                        project_folder: str, step_id: str,
                        label: str = "stills",
                        max_tokens: int = 800,
                        timeout_seconds: float = 300) -> str:
    """Ask the driving host to look at stills. Returns its answer text.

    Files the handshake request (prompt, the stills as `images`),
    prints `LLM_REQUEST_READY` on stderr (never stdout - see below),
    and polls for the response. Stderr, not stdout: this is called
    from inside pre-bridges whose stdout the runner parses as JSON,
    and a marker line there reads as a broken bridge (measured
    2026-09-24: every host-answered stills request failed its step).
    The request file is what the host watches; the line is only the
    nudge. The
    response is validated exactly as today
    (`validate_response` + `require_text_answer`): malformed answers
    refuse with the fix, and an unanswered request raises
    `StillVisionTimeout` - never a quiet gemma substitution.
    """
    from library.tools import llm_handshake as _handshake

    if not project_folder:
        raise ValueError(
            f"still-vision {label}: the host declares vision but no "
            f"project_folder was given to file the handshake under - "
            f"pass project_folder and step_id so the request has "
            f"somewhere to live")
    request_id = request_step_id(step_id)
    req_path = _handshake.request_path(project_folder, request_id)
    res_path = _handshake.response_path(project_folder, request_id)
    os.makedirs(os.path.dirname(req_path), exist_ok=True)
    os.makedirs(os.path.dirname(res_path), exist_ok=True)

    import datetime as _dt
    payload = _handshake.build_request(
        request_id, prompt, constraints="",
        context=(f"Look at the {len(image_paths)} still image(s) in "
                 f"`images` with your own vision and answer the prompt. "
                 f"Max ~{max_tokens} tokens of answer."),
        expected_schema=still_response_schema(),
        project_folder=project_folder,
        timestamp=_dt.datetime.now(_dt.UTC).isoformat(),
        images=list(image_paths),
    )
    # Atomic, and the stale response cleared first: the host polls this
    # file and can otherwise read it half-written and die on the parse.
    _handshake.publish_request(req_path, res_path, payload)
    # The marker rides STDERR, never stdout: this function is called from
    # inside bridge and step subprocesses whose stdout IS the JSON result
    # the runner parses (`run_subprocess` refuses anything else as
    # `PreBridgeError: bridge.py produced invalid JSON`), and a vision
    # child inherits its step's stdout too. The runner streams bridge
    # stderr as the step's log, so the driver watches the marker there.
    # See tests/test_still_vision.py (finding 1: every agent-mode run
    # failed at colour grading on this line).
    print(f"{_handshake.READY_MARKER}: {req_path}", file=sys.stderr,
          flush=True)
    print(f"  [still-vision] {label}: host looking at "
          f"{len(image_paths)} still(s) (timeout {timeout_seconds:g}s)...",
          file=sys.stderr)

    deadline = time.time() + timeout_seconds
    while time.time() < deadline:
        if os.path.exists(res_path):
            time.sleep(0.5)
            with open(res_path, encoding="utf-8") as handle:
                raw = handle.read()
            parsed = _handshake.validate_response(
                request_id, raw, project_folder)
            return _handshake.require_text_answer(
                parsed, request_id, project_folder)
        time.sleep(2)
    raise StillVisionTimeout(
        request_id, req_path, res_path,
        _handshake.resume_command(project_folder), timeout_seconds)


def answer_via_gemma(prompt: str, image_paths: list,
                     max_tokens: int = 800) -> str:
    """The fallback: local gemma4, exactly as every site did before.

    One image goes through `analyze_image`, several through
    `analyze_images` - the same calls, so the same text shape every
    parser already reads. A line on stderr says this ran AND why,
    so a gemma answer is never mistaken for the driver's.
    """
    from library.tools.vision_model import VisionModel

    print(f"  [still-vision] gemma4 fallback answering "
          f"{len(image_paths)} still(s) locally.", file=sys.stderr)
    model = VisionModel()
    if len(image_paths) == 1:
        return model.analyze_image(
            image_paths[0], prompt, max_tokens=max_tokens)
    return model.analyze_images(
        list(image_paths), prompt, max_tokens=max_tokens)


def inspect_stills(prompt: str, image_paths: list, *,
                   harness: str | None = None,
                   project_folder: str | None = None,
                   step_id: str = "stills",
                   label: str = "stills",
                   max_tokens: int = 800,
                   timeout_seconds: float = 300) -> str:
    """Look at stills. Returns the model's answer as raw text.

    The driver first when one drives and sees images; gemma4
    otherwise. Either way the return is raw text, so every caller's
    parsing is byte-identical to the gemma-only days.
    """
    if not image_paths:
        raise ValueError(
            f"still-vision {label}: no stills to inspect - the caller "
            f"extracts stills first, and an empty list here would ask "
            f"the model about nothing")
    driving = resolve_harness(harness)
    if driving is None:
        print(f"  [still-vision] {label}: no host drives this run - "
              f"gemma4 fallback.", file=sys.stderr)
        return answer_via_gemma(prompt, list(image_paths),
                                max_tokens=max_tokens)
    if host_sees_images(driving):
        return request_host_answer(
            prompt, list(image_paths), project_folder or "", step_id,
            label=label, max_tokens=max_tokens,
            timeout_seconds=timeout_seconds)
    print(f"  [still-vision] {label}: host harness {driving!r} "
          f"declares blind - gemma4 fallback.", file=sys.stderr)
    return answer_via_gemma(prompt, list(image_paths),
                            max_tokens=max_tokens)
