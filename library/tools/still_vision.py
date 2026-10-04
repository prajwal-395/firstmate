"""Still-frame inspection uses Codex directly, with gemma as fallback.

The captain's ruling, 2026-09-24: still-frame inspection uses the
driving model's vision, with gemma4 as fallback when no capable route
is available. For Codex, `codex exec` makes each call through the
subscription CLI. Whole-video understanding stays with gemma:
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

1. No host, or no Codex CLI: gemma4, exactly as before.
2. An image-capable `agent` host with Codex installed: one isolated
   `codex exec` per still request. Each call receives its own prompt and
   image arguments, and the site's parser reads its final answer.
3. A host declared BLIND: gemma4. Unknown harnesses still refuse rather
   than being guessed about.

What the router never does: change which stills are extracted, how
they are sampled, or what the audio half of an action window hears -
the semantic scheduler and this module share one bounded concurrency
setting. A failed or empty Codex response raises so the caller's
existing retry and refusal path handles it; Gemma is used when Codex is
unavailable, not when a model call fails.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from pathlib import Path

#: Which harnesses the host answers through can be shown a picture.
# A COMPLETE enumeration, the same shape as
# `window_frames.HARNESS_SHOWS_FRAMES` and
# `brief_reference.HARNESS_READS_FILES`: an unknown harness raises
# rather than being guessed about, because fallback-to-gemma is a
# DECLARED property of the host, not an inference. A future harness is
# added here explicitly once its vision is established - until then a
# run under it refuses loudly instead of answering from filenames.
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

# `agent` is the runner's handoff mechanism, not a provider name. Codex's
# installed CLI is the direct subscription route for Codex-driven runs.
# Claude and opencode are absent because the current host declaration does
# not identify either as the driver for a particular run.
DIRECT_STILL_CLI = {"agent": "codex"}

MAX_CONCURRENCY_ENV_VAR = "PIPELINE_STILL_VISION_MAX_CONCURRENCY"
DEFAULT_MAX_CONCURRENCY = 10
MAX_ALLOWED_CONCURRENCY = 32


class _ConcurrencyGate:
    """Process-wide limit shared by every still-vision call site."""

    def __init__(self):
        self.condition = threading.Condition()
        self.active = 0

    def __enter__(self):
        limit = max_concurrent_calls()
        with self.condition:
            while self.active >= limit:
                self.condition.wait()
            self.active += 1
        return self

    def __exit__(self, *_exc):
        with self.condition:
            self.active -= 1
            self.condition.notify_all()
        return False


_CONCURRENCY_GATE = _ConcurrencyGate()

#: Environment carrying the driving harness into code that never sees
#: the runner's argv: the vision subprocess, the skills, the QA
#: paths. Set by `run_pipeline` from `--full-auto`; unset or empty
#: means no host drives, and stills go to gemma. Explicit `harness=`
#: arguments always win over this.
HARNESS_ENV_VAR = "PIPELINE_HOST_HARNESS"


class UnknownHost(ValueError):
    """A harness whose vision nobody has established."""


class StillVisionCallError(RuntimeError):
    """A direct still-vision call failed and must be retried or refused."""


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


def max_concurrent_calls() -> int:
    """Configured upper bound for direct still calls and the cloud lane."""
    raw = os.environ.get(MAX_CONCURRENCY_ENV_VAR,
                         str(DEFAULT_MAX_CONCURRENCY))
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(
            f"{MAX_CONCURRENCY_ENV_VAR} must be an integer from 1 to "
            f"{MAX_ALLOWED_CONCURRENCY}, got {raw!r}") from exc
    if not 1 <= value <= MAX_ALLOWED_CONCURRENCY:
        raise ValueError(
            f"{MAX_CONCURRENCY_ENV_VAR} must be from 1 to "
            f"{MAX_ALLOWED_CONCURRENCY}, got {value}")
    return value


def direct_cli_for_harness(harness: str) -> str | None:
    """Return the declared direct CLI when installed, otherwise None."""
    cli_name = DIRECT_STILL_CLI.get(harness)
    return shutil.which(cli_name) if cli_name else None


def _read_usage(stdout: str) -> tuple[int | None, int | None, str | None]:
    """Extract Codex's final per-call usage event without logging its text."""
    input_tokens = output_tokens = model = None
    for line in stdout.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        usage = event.get("usage")
        if isinstance(usage, dict):
            input_tokens = usage.get("input_tokens", input_tokens)
            output_tokens = usage.get("output_tokens", output_tokens)
        model = event.get("model", model)
    return input_tokens, output_tokens, model


def request_direct_answer(prompt: str, image_paths: list,
                          step_id: str,
                          label: str = "stills",
                          max_tokens: int = 800,
                          timeout_seconds: float = 300,
                          executable: str | None = None,
                          route_metadata: dict | None = None) -> str:
    """Ask one Codex CLI call to inspect exactly these images.

    Every request gets a separate process, stdin prompt, image argument list,
    and output file. That keeps a response tied to the request that supplied
    its images even when several calls finish in a different order.
    """
    executable = executable or direct_cli_for_harness("agent")
    if not executable:
        raise StillVisionCallError("Codex CLI is unavailable")
    checked_images = []
    for image in image_paths:
        path = Path(image)
        if not path.is_absolute():
            raise ValueError(f"still image path is not absolute: {image}")
        if not path.is_file():
            raise ValueError(f"still image path is not a file: {image}")
        checked_images.append(str(path))
    if not checked_images:
        raise ValueError("still-vision request needs at least one image")

    request_id = f"{step_id}:{uuid.uuid4().hex[:12]}"
    context = (f"Look at the {len(checked_images)} still image(s) with your "
               f"own vision and answer the prompt. Max ~{max_tokens} tokens "
               "of answer.")
    direct_prompt = f"{prompt}\n\n{context}"
    started = time.perf_counter()
    started_at = time.time()
    command = [
        executable, "exec", "--ephemeral", "--json", "--sandbox",
        "read-only", "--skip-git-repo-check",
    ]
    with tempfile.TemporaryDirectory(prefix="ren-still-codex-") as scratch:
        answer_path = Path(scratch) / "answer.txt"
        command.extend(["--output-last-message", str(answer_path)])
        for image in checked_images:
            command.extend(["--image", image])
        command.append("-")
        print(f"  [still-vision] {label}: Codex inspecting "
              f"{len(checked_images)} still(s) ({request_id}).",
              file=sys.stderr, flush=True)
        try:
            with _CONCURRENCY_GATE:
                result = subprocess.run(
                    command, input=direct_prompt, capture_output=True,
                    text=True, encoding="utf-8", errors="replace",
                    timeout=timeout_seconds, cwd=scratch, check=False)
        except subprocess.TimeoutExpired as exc:
            raise StillVisionCallError(
                f"Codex still request {request_id} timed out after "
                f"{timeout_seconds:g}s") from exc
        except OSError as exc:
            raise StillVisionCallError(
                f"Codex still request {request_id} could not start: "
                f"{exc.strerror or type(exc).__name__}") from exc
        elapsed = time.perf_counter() - started
        if result.returncode != 0:
            raise StillVisionCallError(
                f"Codex still request {request_id} exited with "
                f"status {result.returncode}")
        try:
            answer = answer_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise StillVisionCallError(
                f"Codex still request {request_id} produced no answer file") \
                from exc
        if not answer.strip():
            raise StillVisionCallError(
                f"Codex still request {request_id} returned an empty answer")
        input_tokens, output_tokens, model = _read_usage(result.stdout)
        if route_metadata is not None:
            route_metadata["model"] = model or "codex-cli-default"
            route_metadata["input_tokens"] = input_tokens
            route_metadata["output_tokens"] = output_tokens
        from library.tools import perf_ledger
        perf_ledger.record(
            "host_model", elapsed, started_at=started_at,
            backend="codex_exec", model=model or "codex-cli-default",
            calls=1, tokens_estimated=False, input_tokens=input_tokens,
            output_tokens=output_tokens, request_id=request_id)
        return answer


def answer_via_gemma(prompt: str, image_paths: list,
                     max_tokens: int = 800,
                     route_metadata: dict | None = None,
                     inference_admission=None) -> str:
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
    route_kwargs = {}
    if route_metadata is not None:
        route_kwargs["_route_metadata"] = route_metadata
    if inference_admission is not None:
        route_kwargs["inference_admission"] = inference_admission
    if len(image_paths) == 1:
        return model.analyze_image(
            image_paths[0], prompt, max_tokens=max_tokens,
            **route_kwargs)
    return model.analyze_images(
        list(image_paths), prompt, max_tokens=max_tokens,
        **route_kwargs)


def inspect_stills(prompt: str, image_paths: list, *,
                   harness: str | None = None,
                   project_folder: str | None = None,
                   step_id: str = "stills",
                   label: str = "stills",
                   max_tokens: int = 800,
                   timeout_seconds: float = 300,
                   route_metadata: dict | None = None,
                   inference_admission=None) -> str:
    """Look at stills. Returns the model's answer as raw text.

    Codex is used when the declared image-capable host has its direct CLI;
    gemma4 handles runs without that CLI. Either way the return is raw text,
    so every caller's parsing is byte-identical to the gemma-only days.
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
        if route_metadata is not None:
            route_metadata.setdefault("fallback_causes", []).append(
                {"stage": "still_route", "cause": "no_host_configured"})
        return answer_via_gemma(prompt, list(image_paths),
                                max_tokens=max_tokens,
                                route_metadata=route_metadata,
                                **({"inference_admission": inference_admission}
                                   if inference_admission is not None else {}))
    if host_sees_images(driving):
        executable = direct_cli_for_harness(driving)
        if executable:
            if route_metadata is not None:
                route_metadata.update({
                    "backend": "codex_exec",
                    "model": "codex-cli-default",
                    "model_version": "cli-default",
                    "fallback_causes": [],
                })
            return request_direct_answer(
                prompt, list(image_paths), step_id,
                label=label, max_tokens=max_tokens,
                timeout_seconds=timeout_seconds, executable=executable,
                route_metadata=route_metadata)
        print(f"  [still-vision] {label}: no direct image-capable CLI "
              f"for host {driving!r} - gemma4 fallback.", file=sys.stderr)
        if route_metadata is not None:
            route_metadata.setdefault("fallback_causes", []).append(
                {"stage": "still_route", "cause": "direct_cli_unavailable"})
        return answer_via_gemma(
            prompt, list(image_paths), max_tokens=max_tokens,
            route_metadata=route_metadata,
            **({"inference_admission": inference_admission}
               if inference_admission is not None else {}))
    print(f"  [still-vision] {label}: host harness {driving!r} "
          f"declares blind - gemma4 fallback.", file=sys.stderr)
    if route_metadata is not None:
        route_metadata.setdefault("fallback_causes", []).append(
            {"stage": "still_route", "cause": f"host_declared_blind:{driving}"})
    return answer_via_gemma(prompt, list(image_paths),
                            max_tokens=max_tokens,
                            route_metadata=route_metadata,
                            **({"inference_admission": inference_admission}
                               if inference_admission is not None else {}))
