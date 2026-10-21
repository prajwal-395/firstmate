"""What one model answer cost.

Ren never calls a model API: a host model (Claude Code, Codex, opencode)
answers through the file handshake (`library/tools/llm_handshake.py`), and
the host charges for the text it read and wrote. The performance ledger
already has a row shape for a model call - the `host_model` and
`gemma_inference` layers - and a reader that sums those rows' counts
(`library/tools/perf_report.py`). What was missing was the ROW itself: a
model answer taken OUTSIDE a pipeline step's own capability (a touch-up's
edit spec, a reel's visual ask) was never attached to the ledger, so
`ren profile`'s cost section read empty on every run that asked a model
anything.

This module is the ONE place that turns a model exchange into a ledger
row:

* the COUNT comes from `library/tools/token_measure.py` - the engine's
  one counting module, which names the tokenizer that produced it and
  leaves a count ABSENT where no tokenizer ran rather than estimating one
  under a real tokenizer's name. This module never counts anything
  itself;
* `record_answer` writes the row, ONCE per exchange.

An exchange is recorded ONCE. The same answer read twice - a proposal
previewed and then proposed, a revision re-read on resume - cost the host
once, and a cost recorded twice is a cost the captain did not pay. The
marker that says so is the exchange id and the answer's digest, so a
DIFFERENT answer to the same request is a different exchange and is
recorded again.

What is measured and what is not: the text the host read and wrote is
measured, from the request and the answer. The TIME the host took is NOT,
when the answer was already on disk when the engine read it - the engine
did not wait for it. The row then carries no wall and says so, rather
than inventing a wait from a file's timestamp.

Where the row lands: the ledger attached to the capability that is
working, when there is one (`perf_ledger.attached_ledger_path`), and the
project's own ledger otherwise. A run that asks a model something outside
a step therefore still has a cost, and `ren profile` can answer the
captain's planning-versus-execution question.

The rules this module owns
--------------------------
These are the engine's rules for this module, and they live here with
the code that obeys them.

- **A model answer's cost is a ledger row, and it is written once.**
  `library/tools/model_cost.py`.
- **A model answer taken outside a capability is still recorded.** The
  ledger attached to the capability that is working takes the row when
  there is one; the project's own ledger takes it when there is none.
- **A count is never published without the tokenizer that produced it.**
  `library/tools/token_measure.py` owns the count; this module owns the
  row.
- **A run with no model call has no measured model cost, and says so.**
  `library/tools/perf_report.py`.

[why - 3,622 ledger rows across 80 runs, and not one of them carried a
cost](docs/RULE_EVIDENCE.md#the-model-cost-that-was-never-recorded)
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from library.tools import perf_ledger, token_measure

#: The layer a host model's answer is recorded under. The local VLM
#: records under `gemma_inference`; both are model layers
#: (`perf_ledger.MODEL_LAYERS`).
HOST_LAYER = "host_model"


def exchange_marker(exchange_id: str, response_text: Any) -> str:
    """The identity of one exchange: its id and the answer it produced.

    Two readings of the same answer share a marker, so the cost is
    recorded once. A different answer to the same request has a
    different marker, so it is recorded again - the host was paid for
    it.
    """
    digest = hashlib.sha256(
        (response_text if isinstance(response_text, str)
         else str(response_text or "")).encode("utf-8")).hexdigest()[:12]
    return f"{exchange_id}@{digest}"


#: The markers a ledger already carries, cached per file so a project
#: with a long ledger is read once per change rather than once per
#: answer.
_marker_cache: dict[str, tuple[tuple[int, int], set[str]]] = {}


def _recorded_markers(path: Path) -> set[str]:
    """The exchange markers a ledger file already carries."""
    key = str(path)
    try:
        stat = path.stat()
    except OSError:
        return set()
    stamp = (stat.st_mtime_ns, stat.st_size)
    cached = _marker_cache.get(key)
    if cached is not None and cached[0] == stamp:
        return cached[1]
    markers: set[str] = set()
    try:
        with open(path, "r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                marker = row.get("exchange")
                if isinstance(marker, str):
                    markers.add(marker)
    except OSError:
        markers = set()
    _marker_cache[key] = (stamp, markers)
    return markers


def run_id_for(project_folder: str) -> str:
    """The run an answer belongs to when no capability claimed it.

    In order: the run this process was started under (`REN_RUN_ID`), the
    run the project is in (`pipeline_run.json`), and - when the project
    has no run at all - a fresh id, so the cost is never unattributable.
    """
    from library.tools import run_id as run_identity

    current = run_identity.current()
    if current:
        return current
    try:
        from library.tools import run_control
        status = run_control.read_run_status(project_folder) or {}
    except Exception:  # noqa: BLE001 - attribution never breaks an answer
        status = {}
    value = status.get("run_id")
    if value:
        return str(value)
    return run_identity.mint_run_id()


def record_answer(project_folder: str, *, exchange_id: str,
                  request_text: Any = "", response_text: Any = "",
                  layer: str = HOST_LAYER, backend: str = "agent",
                  capability: str = "", wall_s: float = 0.0,
                  input_measure: dict | None = None,
                  output_measure: dict | None = None) -> dict[str, Any] | None:
    """Record what one model exchange cost, once.

    `exchange_id` names the exchange (the handshake request id). The
    answer's own text decides whether this is a NEW exchange or one
    already paid for: an exchange already recorded is not recorded
    again. `None` means it was already recorded.

    `input_measure`/`output_measure` are `token_measure` measurements of
    the text the host read and wrote; a caller that already measured
    them passes them, and a caller that has only the text has it
    measured here, by the same module. `wall_s` is the time the engine
    WAITED for the answer, and is zero when the answer was already on
    disk - the engine did not wait for something that had already
    happened.
    """
    if not exchange_id:
        return None
    marker = exchange_marker(exchange_id, response_text)
    attached = perf_ledger.attached_ledger_path()
    targets = [path for path in (
        attached,
        _project_ledger_path(project_folder)) if path is not None]
    for target in targets:
        if marker in _recorded_markers(target):
            return None

    if input_measure is None:
        input_measure = token_measure.measure_text(
            request_text if isinstance(request_text, str) else "")
    if output_measure is None:
        output_measure = token_measure.measure_text(
            response_text if isinstance(response_text, str) else "")
    wall = max(0.0, float(wall_s or 0.0))
    # The count names the unit that produced it. Where no tokenizer ran,
    # the count published IS the byte count, and its name is
    # `utf8_bytes` - never None, and never a token count under a
    # tokenizer's name.
    tokenizer = input_measure.get("tokenizer") or token_measure.BYTES
    fields = {
        "backend": backend,
        "calls": 1,
        # A count from the named unit is MEASURED. Where only bytes were
        # counted, the token fields are absent rather than estimated.
        "tokens_estimated": False,
        "input_tokens": input_measure.get("tokens"),
        "output_tokens": output_measure.get("tokens"),
        "input_bytes": input_measure.get("bytes"),
        "output_bytes": output_measure.get("bytes"),
        "tokenizer": tokenizer,
        "exchange": marker,
        "exchange_id": str(exchange_id),
    }
    if attached is not None:
        # A capability is working: the row joins that capability's
        # ledger, and carries the run and capability the ledger knows.
        perf_ledger.record(layer, wall, **fields)
    else:
        row = {
            "kind": perf_ledger.SPAN,
            "layer": layer,
            "run_id": run_id_for(project_folder),
            "capability": capability or "model_answer",
            "started_at": round(time.time() - wall, 3),
            "wall_s": round(wall, 3),
            **fields,
        }
        try:
            perf_ledger.commit(project_folder, row)
        except Exception:  # noqa: BLE001 - a cost never breaks an answer
            return None
    return {"exchange": marker, "exchange_id": str(exchange_id),
            "input_tokens": input_measure.get("tokens"),
            "output_tokens": output_measure.get("tokens"),
            "input_bytes": input_measure.get("bytes"),
            "output_bytes": output_measure.get("bytes"),
            "tokenizer": tokenizer,
            "wall_s": round(wall, 3), "layer": layer}


def _project_ledger_path(project_folder: str) -> Path | None:
    """The project's own ledger, or None when it cannot be resolved."""
    try:
        return perf_ledger.ledger_path(project_folder)
    except Exception:  # noqa: BLE001 - a cost never breaks an answer
        return None
