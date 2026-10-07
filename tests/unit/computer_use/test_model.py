"""The decide half's contract, pinned against a stubbed endpoint.

Each test names the defect it catches. The expensive one is the cost
budget: PR 1864's review caught `max_cost_usd` accepted and never
enforced, and this module is where that enforcement lives - a session
past its budget must return `budget_exhausted` without calling, the
way the loop stops on its step budget.
"""

from __future__ import annotations

import base64
import json
from contextlib import contextmanager

import pytest

from library.tools.computer_use import model as model_module
from library.tools.computer_use.actions import ACTION_KINDS, TERMINAL_KINDS
from library.tools.computer_use.model import (
    ModelConfig,
    ModelError,
    ModelSession,
    build_decide_messages,
)
from library.tools.computer_use.observe import Observation


def _obs() -> Observation:
    return Observation(
        screenshot=b"\x89PNG-fake",
        screenshot_points=(100, 200),
        ax_surface=[
            {
                "role": "AXMenuItem",
                "title": "Media",
                "path": ["Workspace", "Switch to Page", "Media"],
                "actions": ["press"],
            }
        ],
        ocr_labels=[{"text": "Master", "frame": {"x": 10, "y": 20, "width": 30, "height": 8}}],
        window_frame={"x": 0, "y": 0, "width": 100, "height": 200},
        goal="switch to the Media page",
        history=[{"action": "press", "outcome": "ok"}],
        step=1,
    )


class _FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode("utf-8")

    def read(self):
        return self._payload

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _chat_payload(text, usage=None):
    payload = {"choices": [{"message": {"content": text}}]}
    if usage is not None:
        payload["usage"] = usage
    return payload


def _stub_endpoint(monkeypatch, replies):
    """Answer successive POSTs with `replies`, recording request bodies."""
    bodies: list = []
    queue = list(replies)

    def fake_urlopen(req, timeout=None):
        bodies.append(json.loads(req.data.decode("utf-8")))
        return _FakeResponse(queue.pop(0))

    monkeypatch.setattr(model_module.urllib.request, "urlopen", fake_urlopen)
    return bodies


def _stub_ledger(monkeypatch):
    """Capture perf_ledger spans without a ledger file."""
    spans: list = []

    @contextmanager
    def fake_span(layer, **fields):
        row = {"layer": layer, **fields}
        spans.append(row)
        yield row

    monkeypatch.setattr(model_module.perf_ledger, "span", fake_span)
    return spans


def _config(**overrides) -> ModelConfig:
    base = {
        "endpoint": "http://stub-model:8080",
        "model": "stub-model",
        "backend": "hosted",
        "price_in_per_mtok": 2.0,
        "price_out_per_mtok": 10.0,
    }
    base.update(overrides)
    return ModelConfig(**base)


def test_an_invalid_action_is_repaired_with_the_error_named(monkeypatch):
    """A reply missing the click coordinates gets one repair call
    carrying the validation error - not a silent drop, and not an
    unbounded argument. The second request names what was wrong."""
    bodies = _stub_endpoint(
        monkeypatch,
        [
            _chat_payload('{"action": "click"}'),
            _chat_payload('{"action": "click", "x": 10, "y": 20}'),
        ],
    )
    _stub_ledger(monkeypatch)
    session = ModelSession(config=_config())

    result = session(_obs())

    assert result == {"action": "click", "x": 10, "y": 20}
    assert len(bodies) == 2
    repair_text = bodies[1]["messages"][-1]["content"]
    assert "missing" in repair_text and "x" in repair_text


def test_persistent_garbage_refuses_after_one_repair(monkeypatch):
    """Two non-JSON replies refuse naming what the model sent - the
    session does not retry forever, and the refusal carries the reply
    so the next step (inspect the observation) is known."""
    _stub_endpoint(monkeypatch, [_chat_payload("hello there"), _chat_payload("hello again")])
    _stub_ledger(monkeypatch)
    session = ModelSession(config=_config())

    with pytest.raises(ModelError) as excinfo:
        session(_obs())

    assert "hello" in excinfo.value.why
    assert excinfo.value.code == "MODEL_INVALID_ACTION"


def test_the_cost_budget_returns_budget_exhausted_without_calling(monkeypatch):
    """A session whose spend reached `max_cost_usd` returns the
    `budget_exhausted` terminal without POSTing - the dollar budget
    stops calls the way the step budget stops the loop."""
    def _boom(req, timeout=None):
        raise AssertionError("no HTTP call may be made past the budget")

    monkeypatch.setattr(model_module.urllib.request, "urlopen", _boom)
    session = ModelSession(config=_config(max_cost_usd=1.0), spent_usd=1.0)

    assert session(_obs()) == {"action": "budget_exhausted"}


def test_token_usage_reaches_the_ledger_and_accumulates_cost(monkeypatch):
    """One valid reply POSTs once, records its tokens under
    `computer_use_decide`, and accumulates the priced spend: 5000 in +
    150 out at $2/$10 per MTok is $0.0115. A wrapper that POSTs twice
    per decide (or prices nothing) fails here."""
    bodies = _stub_endpoint(
        monkeypatch,
        [_chat_payload('{"action": "done"}', {"prompt_tokens": 5000, "completion_tokens": 150})],
    )
    spans = _stub_ledger(monkeypatch)
    session = ModelSession(config=_config())

    assert session(_obs()) == {"action": "done"}

    assert len(bodies) == 1
    assert spans == [
        {
            "layer": "computer_use_decide",
            "backend": "hosted",
            "model": "stub-model",
            "calls": 1,
            "input_tokens": 5000,
            "output_tokens": 150,
        }
    ]
    assert session.spent_usd == pytest.approx(0.0115)
    assert session.calls == 1


def test_the_prompt_carries_the_vocabulary_points_and_screenshot():
    """The decide prompt names every action and terminal signal, states
    the point-size coordinate space, and embeds the screenshot - a
    model that never saw the vocabulary invents actions, and one that
    never saw the image guesses coordinates in the wrong pixels."""
    messages = build_decide_messages(_obs())

    system = messages[0]["content"]
    for kind in ACTION_KINDS + TERMINAL_KINDS:
        assert kind in system
    assert "100x200" in system
    user_text, user_image = messages[1]["content"]
    assert "switch to the Media page" in user_text["text"]
    url = user_image["image_url"]["url"]
    assert url.startswith("data:image/png;base64,")
    assert base64.b64decode(url.split(",", 1)[1]) == b"\x89PNG-fake"


def test_the_prompt_names_the_target_app():
    """The decide prompt says which app the model drives - a Chrome
    observation under a Resolve prompt would aim Resolve vocabulary at
    a web app."""
    from library.tools.computer_use.observe import Observation

    chrome_obs = Observation(
        screenshot=b"\x89PNG-fake",
        screenshot_points=(100, 200),
        ax_surface=[],
        ocr_labels=[],
        window_frame={},
        target="chrome",
    )
    system = build_decide_messages(chrome_obs)[0]["content"]
    assert "Chrome" in system
    assert "DaVinci Resolve" not in system
