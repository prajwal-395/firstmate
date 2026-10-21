"""A count that NAMES the tokenizer that produced it.  One implementation.

The engine prices a request with it (`library/tools/model_task.py`) and the
step-replay bench measures the same strings with it
(`library/tools/replay_bench/tokens.py`), so the two can be compared
instead of merely coexisting.

**Why this module exists.**  The pipeline used to log `len(s.split()) * 1.3`
under a key called `token_count`, and to price a request with the
UNPROJECTED input dictionary PLUS the prompt.  The LLM context audit
measured that figure to be wrong by 0.3x to 9.9x in both directions on
project 001's own archive, and for `speech_sequence` it over-stated the
request by 16x.  The two errors point in opposite directions and do not
cancel, so no two planning variants could be ranked.  A count that wrong is
worse than none, because it reads as a measurement.

**What is counted here.**  `utf8_bytes` is always available and is EXACT -
it is BYTES, not tokens, and it says so.  `o200k_base` is a STATED PROXY
for a model whose own tokenizer is not public (Claude's is not; Gemini's
needs an API key).  It needs `tiktoken`, which is NOT in
`requirements.txt`, so where it is absent the token count is ABSENT
rather than estimated under a real tokenizer's name.

Two rules follow, and both are the point of the module:

- a count is never published without the name of the tokenizer that made
  it;
- a count is never published for text that was not the text that was
  filed.
"""

from __future__ import annotations

BYTES = "utf8_bytes"
O200K = "o200k_base"


def available() -> list:
    """Every tokenizer this environment can honestly run."""
    names = [BYTES]
    try:
        import tiktoken  # noqa: F401
        names.append(O200K)
    except ImportError:
        pass
    return names


def count(text: str, tokenizer: str = BYTES) -> int:
    """Count `text` with `tokenizer`, exactly as that tokenizer counts."""
    if tokenizer == BYTES:
        return len(text.encode("utf-8"))
    if tokenizer == O200K:
        try:
            import tiktoken
        except ImportError as exc:
            raise RuntimeError(
                "o200k_base needs tiktoken, which this environment does "
                "not have. Install it (pip install tiktoken) or ask for "
                "utf8_bytes. Nothing here will estimate a count and label "
                "it with a tokenizer it did not run."
            ) from exc
        return len(tiktoken.get_encoding(O200K).encode(text))
    raise ValueError(f"unknown tokenizer {tokenizer!r}; have {available()}")


def measure(text: str) -> dict:
    """Every count this environment can honestly produce, keyed by name."""
    return {name: count(text, name) for name in available()}


def tokenizer() -> str | None:
    """The tokenizer this environment can run, or None.

    None means no TOKENIZER - `utf8_bytes` is always available, but a
    byte count is not a token count and is never reported as one.
    """
    return O200K if O200K in available() else None


def measure_text(text: str) -> dict:
    """What one string costs, in every unit this environment can measure.

    `{"tokenizer": name | None, "tokens": int | None, "bytes": int}`.

    `bytes` is always exact.  `tokens` is present only when a tokenizer
    ran, and `tokenizer` then names it.
    """
    name = tokenizer()
    return {
        "tokenizer": name,
        "tokens": count(text, name) if name else None,
        "bytes": len(text.encode("utf-8")),
    }


def comparable(measurement: dict) -> int:
    """The number to compare with, in the unit that was really measured.

    A token count when a tokenizer ran, the exact byte count when none
    did.  Two figures are only comparable when they are in the SAME unit,
    so this is the one way to put them side by side.
    """
    if measurement.get("tokens") is not None:
        return int(measurement["tokens"])
    return int(measurement["bytes"])


def pipeline_heuristic(text: str) -> int:
    """The figure the pipeline logged BEFORE this module existed.  NOT a count.

    It is `len(s.split()) * 1.3`, kept so a report can show the gap that
    was closed rather than assert it.  It names no tokenizer, so it is
    deliberately NOT reachable through `count()` or `measure()`.
    """
    return int(len(text.split()) * 1.3)
