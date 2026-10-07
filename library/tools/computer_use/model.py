"""The decide half: turn an observation into the model's one action.

The loop is model-agnostic (`Model = Callable[[Observation], dict]`);
this module is the production callable. It builds the decide prompt
from the observation - the text render plus the point-size screenshot -
POSTs it to one OpenAI-compatible endpoint, and parses the reply into
the single action dict the loop validates.

One code path, two deployments (the architecture report's "config
switch, not a code path"): the endpoint, model name and prices come
from `ModelConfig`, which defaults to the resident local server - the
free default, the same default endpoint `vision_model` serves
(`GEMMA_SERVER_URL`). A grounding-strong hosted endpoint is the same
POST with a different URL; its caller passes that endpoint with its
prices. The local server is a sunk cost, so its prices default to
zero; the worked hosted example is Claude Sonnet 5 at $2 per MTok in
and $10 per MTok out (architecture report, `Model and cost per step`).

Cost is enforced here, not in the loop - the loop owns the step
budget, this module owns the dollar budget. Every POST's token usage
goes to `perf_ledger` under `computer_use_decide` (input/output
tokens, calls, backend, model), and `spent_usd` accumulates from the
config's prices. A session whose spend has reached `max_cost_usd`
returns the `budget_exhausted` terminal signal without calling - the
loop already knows how to stop on it, so no new contract is needed.

A reply that is not a valid action gets one repair call carrying the
validation error, then the session refuses (`ModelError`, a
`RenRefusal`) naming what the model sent. The repair carries no new
screenshot - it asks for a reformat of the same observation, not a new
decision. An HTTP 4xx propagates as a client bug (our payload is
malformed - `vision_model`'s rule); a connection failure, timeout or
5xx refuses naming the endpoint, never silently falling back.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from library.tools import perf_ledger
from library.tools.computer_use.actions import (
    ACTION_KINDS,
    MODIFIERS,
    REQUIRED_PARAMS,
    TERMINAL_KINDS,
    Action,
    ActionError,
)
from library.tools.computer_use.observe import Observation
from library.tools.ren_refusal import RenRefusal
from library.tools.vision_model import MODEL_ID as GEMMA_MODEL_ID
from library.tools.vision_model import SERVER_URL as GEMMA_SERVER_URL

#: The knobs that select the deployment, read from the environment by
#: `ModelConfig.from_env`. Prices are USD per million tokens.
ENV_URL = "COMPUTER_USE_MODEL_URL"
ENV_MODEL = "COMPUTER_USE_MODEL_NAME"
ENV_MAX_COST_USD = "COMPUTER_USE_MAX_COST_USD"
ENV_PRICE_IN_PER_MTOK = "COMPUTER_USE_PRICE_IN_PER_MTOK"
ENV_PRICE_OUT_PER_MTOK = "COMPUTER_USE_PRICE_OUT_PER_MTOK"

#: Cap on the model's reply. The architecture report measures ~150
#: output tokens per decide step (one JSON action); the cap sits well
#: above so a valid action is never truncated, and well below a
#: rambling reply spending the budget.
MAX_TOKENS = 512

#: The repair round is one call, not a conversation: the first reply
#: was not a valid action, the second chance carries the error.
MAX_REPAIR_CALLS = 1


class ModelError(RenRefusal):
    """The model could not be turned into an action - refused with the fix."""


@dataclass
class ModelConfig:
    """Where the decide call goes and what it may spend.

    `backend` is the ledger label (`vision_model` records
    `gemma_server`/`mlx_vlm` the same way): `local` for the resident
    server, `hosted` for a deployment that bills. Prices are USD per
    million tokens and default to zero - the local server is a sunk
    cost. `max_cost_usd` None means the step budget alone bounds the
    task.
    """

    endpoint: str = ""
    model: str = ""
    backend: str = "local"
    price_in_per_mtok: float = 0.0
    price_out_per_mtok: float = 0.0
    max_tokens: int = MAX_TOKENS
    max_cost_usd: float | None = None

    @classmethod
    def from_env(cls) -> ModelConfig:
        """Read the deployment from the environment.

        Unset URL falls back to the resident server's own default, so
        a machine with the Gemma server up needs no configuration;
        `COMPUTER_USE_MAX_COST_USD` unset means unbounded dollars.
        """
        raw_max = os.environ.get(ENV_MAX_COST_USD, "").strip()
        return cls(
            endpoint=os.environ.get(ENV_URL, GEMMA_SERVER_URL).rstrip("/"),
            model=os.environ.get(ENV_MODEL, GEMMA_MODEL_ID),
            price_in_per_mtok=float(os.environ.get(ENV_PRICE_IN_PER_MTOK, "0.0")),
            price_out_per_mtok=float(os.environ.get(ENV_PRICE_OUT_PER_MTOK, "0.0")),
            max_cost_usd=float(raw_max) if raw_max else None,
        )


def build_decide_messages(obs: Observation) -> list[dict]:
    """Build the chat messages for one decide call.

    The system message carries the action vocabulary - derived from
    `actions.py`, never restated - and the coordinate space: the image
    is `screenshot_points` window points, and every coordinate the
    model returns is in those points. The user message is the
    observation's text render plus the screenshot as a data URL, so
    the model decides from this observation, never a remembered
    coordinate.
    """
    actions = ", ".join(ACTION_KINDS)
    terminals = ", ".join(TERMINAL_KINDS)
    needs = "; ".join(
        f"{kind} needs {', '.join(REQUIRED_PARAMS[kind])}"
        for kind in ACTION_KINDS
        if REQUIRED_PARAMS[kind]
    )
    width, height = obs.screenshot_points
    app = "the Chrome window" if obs.target == "chrome" else "the DaVinci Resolve UI"
    system = (
        f"You drive {app}. Reply with exactly one JSON "
        "object: one action or one terminal signal, no prose.\n"
        f"Actions: {actions}. Terminal signals: {terminals}.\n"
        f"{needs}. `screenshot` needs nothing. "
        "Modifiers for `key` are: " + ", ".join(MODIFIERS) + ".\n"
        f"The screenshot is {width}x{height} window points; every "
        "coordinate (click, drag, scroll) is in those points. "
        "Prefer a menu `path` from the AX surface over coordinates - "
        "paths survive window moves, points do not.\n"
        "If history shows SKYLIGHT_UNAVAILABLE, do not repeat the same "
        "click, key or text action; choose an accessible AX or menu action "
        "from this observation when one can complete the task.\n"
        "Decide from THIS observation only: the history names past "
        "actions, never coordinates to reuse."
    )
    image_b64 = base64.b64encode(obs.screenshot).decode("ascii")
    return [
        {"role": "system", "content": system},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": obs.render()},
                {
                    "type": "image_url",
                    "image_url": {"url": f"data:image/png;base64,{image_b64}"},
                },
            ],
        },
    ]


def _post(config: ModelConfig, messages: list[dict]) -> tuple[str, dict]:
    """POST one chat/completions request; return `(text, usage)`.

    HTTP 4xx is our payload malformed and propagates as a client bug -
    retrying would mask it. A connection failure, timeout or 5xx
    refuses naming the endpoint.
    """
    url = f"{config.endpoint}/v1/chat/completions"
    body = json.dumps(
        {
            "model": config.model,
            "messages": messages,
            "max_tokens": config.max_tokens,
            "temperature": 0.1,
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url, data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=300) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        if 500 <= exc.code < 600:
            raise ModelError(
                f"the model endpoint answered HTTP {exc.code}",
                f"{config.endpoint} failed on a decide call",
                f"retry the run; if it repeats, check the endpoint at {config.endpoint}",
                code="MODEL_ENDPOINT_FAILED",
            ) from exc
        raise ModelError(
            f"the model endpoint refused the decide call (HTTP {exc.code})",
            "a 4xx means our payload is malformed, not the model",
            "read the endpoint's error above and fix the request this module builds",
            code="MODEL_BAD_REQUEST",
        ) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise ModelError(
            "the model endpoint could not be reached",
            f"{config.endpoint}: {exc}",
            f"start the local server or set ${ENV_URL} to the hosted endpoint",
            code="MODEL_ENDPOINT_UNREACHABLE",
        ) from exc
    try:
        text = payload["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ModelError(
            "the model endpoint answered in a shape this module does not read",
            f"no choices[0].message.content in {payload!r:.200}",
            f"check the endpoint at {config.endpoint} speaks chat/completions",
            code="MODEL_BAD_RESPONSE",
        ) from exc
    usage = payload.get("usage") if isinstance(payload, dict) else None
    return text, usage if isinstance(usage, dict) else {}


def _parse_action(text: str) -> dict:
    """Parse the model's reply into the validated action dict.

    Strips code fences, then parses the object; the loop's schema
    (`Action.from_dict`) is the validator, so an unknown kind or a
    missing param raises `ActionError` naming the vocabulary. Returns
    the parsed dict itself - it passed validation unchanged.
    """
    stripped = text.strip()
    for fence in ("```json", "```"):
        if stripped.startswith(fence):
            stripped = stripped[len(fence):]
            break
    if stripped.endswith("```"):
        stripped = stripped[: -len("```")]
    stripped = stripped.strip()
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        start, end = stripped.find("{"), stripped.rfind("}")
        if start < 0 or end <= start:
            raise ActionError(
                f"the model did not return a JSON object: {stripped[:200]!r}"
            )
        try:
            data = json.loads(stripped[start : end + 1])
        except json.JSONDecodeError:
            raise ActionError(
                f"the model did not return a JSON object: {stripped[:200]!r}"
            )
    Action.from_dict(data)
    return data


@dataclass
class ModelSession:
    """One task's decide calls: the loop's `Model` with a dollar budget.

    `spent_usd` accumulates from reported token usage at the config's
    prices. When it reaches `max_cost_usd` the session returns the
    `budget_exhausted` terminal signal without calling - the loop
    stops on it the way it stops on its step budget.
    """

    config: ModelConfig = field(default_factory=ModelConfig.from_env)
    spent_usd: float = 0.0
    calls: int = 0

    def __call__(self, obs: Observation) -> dict:
        """Decide one action for the observation."""
        if self._exhausted():
            return {"action": "budget_exhausted"}
        messages = build_decide_messages(obs)
        text, usage = _post(self.config, messages)
        self._record(usage)
        try:
            return _parse_action(text)
        except ValueError as first_err:
            if self._exhausted():
                return {"action": "budget_exhausted"}
            repair = messages + [
                {
                    "role": "user",
                    "content": (
                        "That reply was not a valid action: "
                        f"{first_err}. Reply with exactly one JSON action object."
                    ),
                }
            ]
            text, usage = _post(self.config, repair)
            self._record(usage)
            try:
                return _parse_action(text)
            except ValueError as second_err:
                raise ModelError(
                    "the model did not return a valid action after a repair",
                    f"first: {first_err}; then: {second_err}",
                    "inspect the observation the model saw - the AX surface "
                    "or screenshot may be empty - and retry the run",
                    code="MODEL_INVALID_ACTION",
                ) from second_err

    def _exhausted(self) -> bool:
        cap = self.config.max_cost_usd
        return cap is not None and self.spent_usd >= cap

    def _record(self, usage: dict) -> None:
        """Record one call's tokens in the ledger and the spend."""
        self.calls += 1
        in_tokens = usage.get("prompt_tokens") or 0
        out_tokens = usage.get("completion_tokens") or 0
        with perf_ledger.span(
            "computer_use_decide",
            backend=self.config.backend,
            model=self.config.model,
            calls=1,
        ) as cost:
            cost["input_tokens"] = in_tokens
            cost["output_tokens"] = out_tokens
        self.spent_usd += (
            in_tokens / 1_000_000 * self.config.price_in_per_mtok
            + out_tokens / 1_000_000 * self.config.price_out_per_mtok
        )
