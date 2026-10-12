"""A request for reels: how many, and about what, said once.

"Make 3 reels about pricing from this podcast" carries two facts that
change what step 3.04 (`select_reels`) chooses: the COUNT and the TOPIC.
Until T14 the only way to say either was to write it into brief.md, so a
person with only a sentence had to learn the brief's conventions first.

The host reads the person's sentence and passes the two facts as flags -
`ren edit <project> --reels 3 --about "pricing"` - and the engine parses
no prose. What the flags carry reaches the steps that read the creative
brief as BRIEFING ANSWERS (`briefing_chat.answers_for`), the same channel
the chat interview's answers take, so 3.04 reads "how many / about what"
the way it reads anything the commissioner said. Nothing here decides
which moments match the topic: that is the model's judgement over the
transcript (AGENTS.md 10.5).

A reels request also SCOPES the run: with no other scope flag it runs
`select_reels` and `judge_reels` and whatever they cannot run without,
not the whole edit - the transcript they read is heard from the footage
when no Resolve cut exists (`library/tools/source_transcript.py`).

The request is recorded on `state["reel_request"]` and stays until a new
one replaces it, the way interview answers stay.

`tests/unit/reels/test_source_transcript.py`.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass

STATE_KEY = "reel_request"

REELS_STEPS = ("select_reels", "judge_reels")
"""What a reels request runs when it names no other scope."""

COUNT_QUESTION = "How many reels does this request want?"
TOPIC_QUESTION = "What should the reels be about?"


@dataclass(frozen=True)
class ReelRequest:
    count: int | None = None
    about: str = ""

    def __bool__(self) -> bool:
        return self.count is not None or bool(self.about)

    def as_dict(self) -> dict:
        return {"count": self.count, "about": self.about}


def _count(raw: str) -> int:
    try:
        value = int(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"--reels takes a whole number of reels, not {raw!r}") from exc
    if value < 1:
        raise argparse.ArgumentTypeError(
            f"--reels takes at least 1, not {value}")
    return value


def add_request_arguments(parser: argparse.ArgumentParser) -> None:
    """The two flags, registered once for both CLIs (`ren edit` and the
    runner it execs), so the words cannot differ."""
    parser.add_argument(
        "--reels", type=_count, metavar="N", default=None,
        help="A reels request: how many reels to propose. Reaches reel "
             "selection as a briefing answer; with no other scope flag "
             "the run selects and judges reels only, hearing the "
             "transcript from the footage when no Resolve cut exists")
    parser.add_argument(
        "--about", metavar="TOPIC", default="",
        help="A reels request: what the reels should be about, in the "
             "person's words. Reaches reel selection as a briefing answer")


def from_args(args) -> ReelRequest:
    return ReelRequest(count=getattr(args, "reels", None),
                       about=(getattr(args, "about", "") or "").strip())


def forward(request: ReelRequest) -> list:
    """The flags again, for a CLI that execs the runner."""
    argv = []
    if request.count is not None:
        argv += ["--reels", str(request.count)]
    if request.about:
        argv += ["--about", request.about]
    return argv


def scope(request: ReelRequest, *, target=None, only=None, single_step=None,
          from_step=None) -> list:
    """The `--only` list a run takes for this request.

    A request with no scope of its own runs the reels steps; any scope
    the person named wins and is left exactly as given.
    """
    named = list(only or ())
    if not request or target or named or single_step or from_step:
        return named
    return list(REELS_STEPS)


def record(state: dict, request: ReelRequest) -> dict:
    """Write the request onto the run's state; a new one replaces the old."""
    if request:
        state[STATE_KEY] = request.as_dict()
    return state


def briefing_answers(state: dict) -> list:
    """The recorded request as `{question, answer}` briefing answers."""
    recorded = (state or {}).get(STATE_KEY) or {}
    answers = []
    if recorded.get("count") is not None:
        answers.append({"question": COUNT_QUESTION,
                        "answer": str(recorded["count"])})
    if recorded.get("about"):
        answers.append({"question": TOPIC_QUESTION,
                        "answer": str(recorded["about"])})
    return answers
