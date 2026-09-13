"""Edit-step input digests: record what an expensive step ran on.

The problem this exists to remove
---------------------------------
``pipeline_run.json``'s ``run_history`` on ``lucie/geo-podcast`` records
``select_reels`` re-run three times on 2026-09-06 (47.8, 29.6 and
63.3 minutes), while deterministic context assembly for that same step
measures 2.78s wall. Essentially all of that two hours and twenty
minutes is model latency, repaid in full on every manual re-run because
nothing can tell whether the inputs changed.

``library/tools/step_ledger.py`` splits steps into preflight (gated by
footage identity plus code hashes) and edit ("cheap to redo and redone
often", gated by nothing). That declaration is false for the
model-driven edit steps. This module extends the proven pattern's
RECORD half to them: each participating step declares its inputs, every
run stamps the digest into the run record, and a re-run REPORTS whether
the inputs match the last completed run.

What this is NOT
---------------
This records; it does not skip. A gate that skips on a digest that
misses an input serves a stale result SILENTLY, which is strictly worse
than being slow. The preflight gate earns its skip because footage
identity is a closed, physical thing; a model step's inputs are not
obviously closed, and nobody has yet proved they can be enumerated
correctly. The stamp is the instrument that will answer whether skipping
is worth building at all - it must never become the skip itself without
a separate, explicit decision.

What is hashed
--------------
Per step, declared in its own manifest under
``classification.input_digest`` - the way ``classification.stage`` is
declared and enforced by ``stage_of`` - never inferred by walking the
filesystem:

* ``inputs``: keys of the step's gathered pre-bridge inputs that the
  step actually reads, through its bridge or its prompt. Every name must
  already be declared in the manifest's ``interface.inputs`` - a digest
  entry may only narrow what the manifest declares, never invent a new
  source. An input that is absent on a run (an optional the project did
  not supply) is recorded as ABSENT, not skipped: absence versus
  presence is a real difference in what the step ran on.
* ``context``: deterministic bridge-output tables that reach the prompt
  (``reel_candidates``, ``reels_to_read``, ...). The bridge is
  deterministic code, so these are functions of the inputs and the code
  below - hashing them as well is what catches a change to SHARED
  library code the step executes (``reel_exchange``,
  ``motion_graphics_vocabulary``, ...) without maintaining a second
  dependency map. The model's own answer keys are NEVER named here: an
  answer is not an input, and hashing one would report "changed" on
  every re-run by construction.
* ``include_code``: fold in the step's own code and prompt. This is the
  step directory's ``.py``/``.json`` (as ``code_identity`` hashes them)
  PLUS ``handoff.md``. ``code_identity`` deliberately excludes ``.md``
  as prose - but for a MODEL step the handoff IS the executable prompt,
  so excluding it would watch everything except the question asked.

A step with no ``input_digest`` block participates in nothing: ``digest_spec``
returns None, the runner stamps nothing and prints nothing for it. A
step whose inputs genuinely cannot be enumerated carries no block and a
stated reason wherever that decision was taken - a step excluded with a
reason is a good outcome, and a digest nobody trusts is worse than none.

Where the stamp lives
---------------------
``pipeline_run.json``, beside what is already recorded - ``NOT``
``pipeline_data.json``, which on a real project is gigabytes and cannot
be loaded for inspection in reasonable time. ``begin_run_status``
carries the previous run's ``step_input_digests`` forward as
``previous_step_input_digests`` (one entry per step, bounded by the step
count, not by history length), and each completed step merges one stamp
into ``step_input_digests``. The comparison is printed to stderr and the
step runs every time regardless of what it says.

One enumeration: ``library/tools/edit_input_digest.py``.
"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

# Where the declaration lives in a step manifest. Under
# `classification`, beside `stage`: the field is a claim about the step,
# enforced by `digest_spec` the way `stage` is enforced by `stage_of`.
INPUT_DIGEST_KEY = "input_digest"

# The prompt file: executable for a model step, prose for a preflight
# one. Hashed alongside the code_identity sources (see module docstring).
PROMPT_FILENAME = "handoff.md"

# Recorded for a declared input that is absent on a run. ABSENCE, not a
# skip: an optional the project did not supply is a different thing the
# step ran on than one it did, and a digest that silently dropped it
# would call those two runs identical.
ABSENT = {"__input_digest__": "absent"}

# NaN/Infinity are not JSON and must never reach a digest quietly.
_JSON_DUMPS_KWARGS: dict[str, Any] = {
    "sort_keys": True,
    "separators": (",", ":"),
    "ensure_ascii": True,
    "allow_nan": False,
}


class DigestError(ValueError):
    """A step whose input digest cannot be declared or computed."""


# ── The declared field ──────────────────────────────────────────────

def _interface_input_names(manifest: dict) -> list[str]:
    interface = (manifest or {}).get("interface") or {}
    names = []
    for entry in interface.get("inputs") or []:
        if isinstance(entry, dict) and entry.get("name"):
            names.append(entry["name"])
    return names


def digest_spec(manifest: dict | None,
                node_id: str = "") -> dict | None:
    """The input-digest declaration a step manifest makes, or None.

    None means the step does not participate: no declaration, no digest,
    no stamp, no comparison line. Anything malformed RAISES rather than
    defaulting - a digest over the wrong inputs is the silent-staleness
    failure this module exists not to produce:

    * the block must be a dict under ``classification``;
    * ``inputs`` must be a non-empty list of strings, each one already
      declared in the manifest's own ``interface.inputs``;
    * ``context`` (when present) must be a list of strings naming
      bridge-output keys - it may NOT name an input, and an input may
      not be repeated there;
    * ``include_code`` (when present) must be a bool.
    """
    classification = (manifest or {}).get("classification") or {}
    if INPUT_DIGEST_KEY not in classification:
        return None
    who = node_id or (manifest or {}).get("id", "?")
    block = classification[INPUT_DIGEST_KEY]
    if not isinstance(block, dict):
        raise DigestError(
            f"Step '{who}' declares classification.{INPUT_DIGEST_KEY} "
            f"that is not an object."
        )
    unknown_keys = set(block) - {"inputs", "context", "include_code"}
    if unknown_keys:
        raise DigestError(
            f"Step '{who}' declares classification.{INPUT_DIGEST_KEY} "
            f"with keys {sorted(unknown_keys)} nothing reads. A "
            f"declaration nothing reads is refused."
        )
    inputs = block.get("inputs")
    if (not isinstance(inputs, list) or not inputs
            or not all(isinstance(name, str) and name for name in inputs)):
        raise DigestError(
            f"Step '{who}' declares classification.{INPUT_DIGEST_KEY} "
            f"with no non-empty `inputs` list of input names."
        )
    context = block.get("context", [])
    if (not isinstance(context, list)
            or not all(isinstance(name, str) and name for name in context)):
        raise DigestError(
            f"Step '{who}' declares classification.{INPUT_DIGEST_KEY} "
            f"with a `context` that is not a list of bridge-output names."
        )
    include_code = block.get("include_code", True)
    if not isinstance(include_code, bool):
        raise DigestError(
            f"Step '{who}' declares classification.{INPUT_DIGEST_KEY} "
            f"with `include_code` that is not a bool."
        )
    declared = set(_interface_input_names(manifest))
    unknown = [name for name in inputs if name not in declared]
    if unknown:
        raise DigestError(
            f"Step '{who}' declares {INPUT_DIGEST_KEY} inputs "
            f"{unknown} that its own interface.inputs does not declare. "
            f"A digest entry may only narrow what the manifest declares, "
            f"never invent a new source."
        )
    overlap = [name for name in context if name in set(inputs)]
    if overlap:
        raise DigestError(
            f"Step '{who}' declares {INPUT_DIGEST_KEY} `context` names "
            f"{overlap} that are also `inputs`. A value is hashed once, "
            f"on one side."
        )
    if len(set(inputs)) != len(inputs) or len(set(context)) != len(context):
        raise DigestError(
            f"Step '{who}' declares {INPUT_DIGEST_KEY} with a repeated "
            f"name. A value is hashed once."
        )
    return {
        "inputs": list(inputs),
        "context": list(context),
        "include_code": include_code,
    }


# ── The digest ──────────────────────────────────────────────────────

def step_code_digest(step_dir: Any) -> str | None:
    """The step's own code plus its prompt, as one hex digest.

    ``.py``/``.json`` hash exactly as ``code_identity`` hashes them; the
    handoff prompt is folded in beside them because for a model step the
    prompt is executable (see module docstring). Returns None when the
    directory holds nothing hashable - the stamp records the absence
    rather than hashing around it.
    """
    from library.tools import code_identity

    path = Path(step_dir)
    base = code_identity.step_code_hash(str(path))
    prompt_file = path / PROMPT_FILENAME
    try:
        prompt_bytes = prompt_file.read_bytes()
    except OSError:
        prompt_bytes = None
    if base is None and prompt_bytes is None:
        return None
    digest = hashlib.sha256()
    digest.update(b"code:")
    digest.update((base or "none").encode("utf-8"))
    digest.update(b"\x00prompt:")
    digest.update(prompt_bytes or b"none")
    return digest.hexdigest()


def _freeze(value: Any) -> Any:
    """The digest of a declared input value, or ABSENT when it is missing.

    Values arrive out of JSON state and JSON files, so the stable dump
    below handles them directly. Anything else (a set smuggled through
    an injected global, a float NaN) RAISES rather than being stringified
    around: a digest that guessed at a value is the failure mode.
    """
    try:
        return json.loads(
            json.dumps(value, **_JSON_DUMPS_KWARGS))
    except (TypeError, ValueError) as exc:
        raise DigestError(
            f"unhashable input value ({type(value).__name__}): {exc}"
        ) from None


_MISSING = object()


def compute_digest(spec: dict, inputs: dict[str, Any],
                   output: dict[str, Any],
                   step_dir: Any) -> dict:
    """Hash one step's declared inputs plus its code into a stamp record.

    ``inputs`` is the step's gathered pre-bridge inputs; ``output`` is
    the step's merged result (bridge tables plus the model's answer -
    only the declared ``context`` tables are read out of it). Returns a
    record with ``digest`` (or None plus ``error`` when a value cannot be
    hashed - recorded honestly, never guessed at), the ``inputs_digest``
    and ``code_digest`` halves so a mismatch can say which half moved,
    and ``recorded_at``.
    """
    frozen_inputs: dict[str, Any] = {}
    for name in spec["inputs"]:
        value = (inputs or {}).get(name, _MISSING)
        if value is _MISSING:
            frozen_inputs[name] = dict(ABSENT)
            continue
        try:
            frozen_inputs[name] = _freeze(value)
        except DigestError as exc:
            return _error_record(f"input {name!r}: {exc}")
    frozen_context: dict[str, Any] = {}
    for name in spec["context"]:
        value = (output or {}).get(name, _MISSING)
        if value is _MISSING:
            frozen_context[name] = dict(ABSENT)
            continue
        try:
            frozen_context[name] = _freeze(value)
        except DigestError as exc:
            return _error_record(f"bridge table {name!r}: {exc}")
    code_digest: str | None = None
    if spec.get("include_code", True):
        try:
            code_digest = step_code_digest(step_dir)
        except OSError as exc:
            return _error_record(f"code hash: {exc}")
    try:
        inputs_digest = hashlib.sha256(
            json.dumps({"inputs": frozen_inputs,
                        "context": frozen_context},
                       **_JSON_DUMPS_KWARGS).encode("utf-8")).hexdigest()
    except (TypeError, ValueError) as exc:  # pragma: no cover - frozen
        return _error_record(f"digest assembly: {exc}")
    if code_digest is None:
        combined = inputs_digest
    else:
        combined = hashlib.sha256(
            f"{inputs_digest}\x00{code_digest}".encode()).hexdigest()
    return {
        "digest": combined,
        "inputs_digest": inputs_digest,
        "code_digest": code_digest,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


def _error_record(reason: str) -> dict:
    return {
        "digest": None,
        "inputs_digest": None,
        "code_digest": None,
        "error": reason,
        "recorded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    }


# ── The comparison: report, never act ───────────────────────────────

def comparison_lines(node_id: str, current: dict,
                     previous: dict | None) -> list[str]:
    """What a re-run prints about one step's inputs. Reports; decides nothing.

    Four outcomes, and each one is said aloud: identical, changed (naming
    which half moved - the upstream values or the step's own code), or
    unknown because no prior stamp exists or neither run could hash.
    """
    current_digest = (current or {}).get("digest")
    if previous is None:
        if current_digest is None:
            return [
                ("  Input digest [{}]: unknown - this run could "
                 "not hash its inputs "
                 "({}), "
                 "so there is nothing to compare.".format(
                     node_id,
                     (current or {}).get('error') or 'no digest')),
            ]
        return [
            f"  Input digest [{node_id}]: {current_digest}",
            ("           unknown - no prior stamp for this step, so this "
             "run becomes the baseline. The step ran normally."),
        ]
    previous_digest = previous.get("digest")
    if current_digest is None or previous_digest is None:
        return [
            ("  Input digest [{}]: unknown - "
             "{} "
             "could not hash its inputs "
             "({}). "
             "The step ran normally.".format(
                 node_id,
                 'this run' if current_digest is None else 'the last run',
                 (current if current_digest is None else previous).get(
                     'error') or 'no digest')),
        ]
    if current_digest == previous_digest:
        return [
            f"  Input digest [{node_id}]: {current_digest}",
            ("           identical inputs to the last completed run. "
             "The step ran normally - this line reports, it does not skip."),
        ]
    halves = []
    if current.get("inputs_digest") != previous.get("inputs_digest"):
        halves.append("upstream inputs")
    if current.get("code_digest") != previous.get("code_digest"):
        halves.append("step code/prompt")
    moved = " and ".join(halves) if halves else "inputs"
    return [
        f"  Input digest [{node_id}]: {current_digest}",
        ("           CHANGED inputs vs the last completed run "
         f"({moved} moved; was {previous_digest}). "
         "The step ran normally - this line reports, it does not skip."),
    ]
