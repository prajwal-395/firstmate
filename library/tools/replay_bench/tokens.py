"""Token counts measured from the reconstructed string, tokenizer named.

**Never use a count that does not name its tokenizer.**  The pipeline used
to log `len(s.split()) * 1.3` under the key `token_count`, and to price a
request with the UNPROJECTED input dictionary plus the prompt - a figure
the LLM context audit measured to be wrong by 0.3x to 9.9x in both
directions on 001's own archive, and to over-state one request by 16x.
That figure is gone: the engine now counts the text it FILES, with a
named tokenizer, through `library/tools/token_measure.py`.

So every figure this bench produces comes from the string this bench
built, and every figure carries the name of the tokenizer that produced
it.

`utf8_bytes` is always available and is EXACT.  It is the honest default,
and it is what the context audit and the information-layer plan both
report, so their numbers and these are directly comparable.

`o200k_base` needs `tiktoken`, which is not in `requirements.txt`.  It is a
STATED PROXY: Claude's tokenizer is not public and Gemini's needs an API
key.  When `tiktoken` is absent the count is absent - the bench does not
substitute an estimate under a real tokenizer's name.

The counting itself lives in `library/tools/token_measure.py`, so the
engine and the bench measure with the SAME code and their numbers can be
compared.  This module is the bench's own name for it.
"""

from __future__ import annotations

from library.tools.token_measure import (
    BYTES,
    O200K,
    available,
    count,
    measure,
    measure_text,
    pipeline_heuristic,
    tokenizer,
)

__all__ = [
    "BYTES",
    "O200K",
    "available",
    "count",
    "measure",
    "measure_text",
    "pipeline_heuristic",
    "tokenizer",
]
