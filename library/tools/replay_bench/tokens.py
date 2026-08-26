"""Token counts measured from the reconstructed string, tokenizer named.

**Never use the pipeline's own token figures.**  The one place it records
them - `present_llm_step`'s `llm_token_stats` and `llm_generation` events -
computes `len(s.split()) * 1.3`, which the LLM context audit measured to be
wrong by 0.3x to 9.9x in both directions on 001's own archive.  A count
that wrong is worse than none, because it reads as a measurement.

So every figure here is produced from the string this bench built, and
every figure carries the name of the tokenizer that produced it.

`utf8_bytes` is always available and is EXACT.  It is the honest default,
and it is what the context audit and the information-layer plan both
report, so their numbers and these are directly comparable.

`o200k_base` needs `tiktoken`, which is not in `requirements.txt`.  It is a
STATED PROXY: Claude's tokenizer is not public and Gemini's needs an API
key.  When `tiktoken` is absent the count is absent - the bench does not
substitute an estimate under a real tokenizer's name.
"""

from __future__ import annotations

BYTES = "utf8_bytes"
O200K = "o200k_base"


def available() -> list:
    names = [BYTES]
    try:
        import tiktoken  # noqa: F401
        names.append(O200K)
    except ImportError:
        pass
    return names


def count(text: str, tokenizer: str = BYTES) -> int:
    if tokenizer == BYTES:
        return len(text.encode("utf-8"))
    if tokenizer == O200K:
        try:
            import tiktoken
        except ImportError as exc:
            raise RuntimeError(
                "o200k_base needs tiktoken, which this environment does not "
                "have. Install it (pip install tiktoken) or ask for "
                "utf8_bytes. The bench will not estimate a count and label "
                "it with a tokenizer it did not run."
            ) from exc
        return len(tiktoken.get_encoding(O200K).encode(text))
    raise ValueError(f"unknown tokenizer {tokenizer!r}; have {available()}")


def measure(text: str) -> dict:
    """Every count this environment can honestly produce, keyed by tokenizer."""
    return {name: count(text, name) for name in available()}


def pipeline_heuristic(text: str) -> int:
    """What the pipeline would log for this string.  NOT a token count.

    `present_llm_step` records `len(s.split()) * 1.3` under the key
    `token_count`, and `pipeline_log.jsonl` is the only place the pipeline
    writes token figures at all.  This reproduces it so a report can show
    the two side by side rather than asserting the gap - it is deliberately
    NOT reachable through `count()` or `measure()`, because those name a
    tokenizer and this names none.
    """
    return int(len(text.split()) * 1.3)
