"""Python's conformance to the generated protocol schemas.

The Rust types in `native/ren-protocol` are the source of truth; the
JSON Schemas in `native/ren-protocol/schemas/` are generated from those
same definitions. This module is Python's side of that contract: it
validates a payload against the schema at the boundary where the
payload is written or sent.

The mode
--------
`REN_PROTOCOL_MODE` chooses what a non-conforming payload does:

    refuse  raise `ProtocolConformanceError`. Tests and dev.
    log     print to stderr and continue. Production, where refusing
            would break a live path today.
    off     do not validate.

The default is `refuse` under pytest and `log` in production, so a
non-conforming payload is a finding in tests and a recorded line in
production. Each boundary states which it chose and why:

    `edit_patch.EditPatch.to_dict`   log in production. `to_dict` runs
                                     inside `apply_patch` before the
                                     patch is recorded; refusing there
                                     aborts a live render mid-apply.
                                     Refuses in tests/dev.
    `resolved/jobs.prepare`          the mode, as `to_dict`. Two broker
                                     regressions reached live rebuilds
                                     from test-double-only changes; a
                                     hard refusal on the job path needs
                                     live evidence first.
    `resolved/jobs.run`              the mode. A patch arriving from the
    (timeline.apply_patch)           socket is validated before Resolve is
                                     touched. A hard refusal on the live
                                     edit path needs live evidence first:
                                     any Python op change landing without
                                     its ren-protocol type update would
                                     otherwise make the broker refuse
                                     live rebuild edits.
    `operations.emit_skill`          refuse always. The capability
                                     registry export is a read-only
                                     projection; a non-conforming
                                     export is a bug to surface, never
                                     a live path to protect.

`from_dict` is in-process construction and does not validate:
compose/carry and test setup build patches through it, and validating
there cost the scheduler's critical path (measured 2.1 ms per compose
against a 1 ms requirement).

A checker that is missing (no schemas dir, no jsonschema) is never a
refusal in production: missing tooling is not a non-conforming payload.
It is logged and validation is skipped; under pytest it raises.

The schemas are generated (`cargo run --bin emit-schemas`) and committed;
`--check` fails when a type changes without its schema.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

REFUSE = "refuse"
LOG = "log"
OFF = "off"
MODES = (REFUSE, LOG, OFF)

REPO_ROOT = Path(__file__).resolve().parents[2]
SCHEMAS_DIR = REPO_ROOT / "native" / "ren-protocol" / "schemas"


class ProtocolConformanceError(RuntimeError):
    """A payload does not conform to the generated protocol schema.

    Carries the schema name and every validation error, so the finding
    names what failed and which schema it failed against.
    """

    def __init__(self, schema_name: str, detail: str) -> None:
        self.schema_name = schema_name
        self.detail = detail
        super().__init__(
            f"payload does not conform to {schema_name}.schema.json: {detail}")


def mode() -> str:
    """The validation mode: `REN_PROTOCOL_MODE`, else refuse under
    pytest, else log."""
    env = os.environ.get("REN_PROTOCOL_MODE", "").strip().lower()
    if env in MODES:
        return env
    if os.environ.get("PYTEST_VERSION"):
        return REFUSE
    return LOG


#: The loaded validators, keyed by schema name. `prepare` runs per job,
#: so the schemas are read and compiled once, not per submission.
_VALIDATORS: dict = {}


def _load_schema(schema_name: str):
    """The parsed schema, or None when the checker is unavailable.

    A missing schemas directory, a missing schema file, or jsonschema
    itself not being installed is the CHECKER missing, not a
    non-conforming payload. In production that is a log line and
    validation is skipped; under pytest it is a finding.
    """
    path = SCHEMAS_DIR / f"{schema_name}.schema.json"
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        if mode() == REFUSE:
            raise ProtocolConformanceError(
                schema_name,
                f"{path} is missing or unreadable - "
                "run `cargo run --bin emit-schemas` from native/")
        print(f"ren-protocol: {schema_name} schema unavailable, "
              f"skipping validation", file=sys.stderr)
        return None


def _validator(schema_name: str):
    """The jsonschema validator for one generated schema, cached.

    Returns None when the checker is unavailable in production (the
    missing tooling was already logged).
    """
    if schema_name in _VALIDATORS:
        return _VALIDATORS[schema_name]
    schema = _load_schema(schema_name)
    if schema is None:
        _VALIDATORS[schema_name] = None
        return None
    try:
        import jsonschema
    except ImportError:
        if mode() == REFUSE:
            raise ProtocolConformanceError(
                schema_name,
                "jsonschema is not installed - the protocol checker "
                "cannot run")
        print("ren-protocol: jsonschema unavailable, skipping validation",
              file=sys.stderr)
        _VALIDATORS[schema_name] = None
        return None
    validator = jsonschema.Draft202012Validator(schema)
    _VALIDATORS[schema_name] = validator
    return validator


def validate(instance, schema_name: str, *, force_refuse: bool = False) -> None:
    """Validate one payload against the named generated schema.

    `refuse` raises `ProtocolConformanceError`; `log` prints to stderr
    and continues; `off` does nothing. `force_refuse` raises regardless
    of the mode, for a boundary where refusing is always safe (reading a
    payload nothing has written yet).

    A checker that is missing (no schemas dir, no jsonschema) is never
    a refusal in production: it is logged and validation is skipped.
    Under pytest it raises.
    """
    current = mode()
    if current == OFF and not force_refuse:
        return
    validator = _validator(schema_name)
    if validator is None:
        return
    errors = sorted(validator.iter_errors(instance),
                    key=lambda error: list(error.path))
    if not errors:
        return
    detail = "; ".join(
        f"{'/'.join(str(part) for part in error.path) or '<root>'}: "
        f"{error.message}"
        for error in errors)
    if current == REFUSE or force_refuse:
        raise ProtocolConformanceError(schema_name, detail)
    print(f"ren-protocol: {schema_name} does not conform: {detail}",
          file=sys.stderr)
