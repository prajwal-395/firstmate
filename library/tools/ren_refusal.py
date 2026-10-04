"""One refusal shape for every `ren` verb.

A REFUSAL is not a failure: nothing ran, nothing is half-written, and
the next step is known. Every refusal a user or host LLM meets on a
`ren` verb's path carries the same three lines - what happened, why,
and the fix (the exact next command or edit):

    ren: refused - <what happened>
      why: <why it refused>
      fix: <the exact next command or edit>

`RenRefusal` is the type; the `ren` CLI boundary (each process a verb
can land in: `ren` itself, `manage_project.py`, `run_pipeline.py`,
`footage_query`) catches it in ONE place per process, prints
`render()`, and exits `REFUSAL_EXIT_CODE`. An unexpected error - a bug,
a missing file the code did not anticipate - is NOT a refusal and
keeps its traceback and exit 1, because a traceback with a fix
invented for it would be a lie.

A refusal class for one area (touchups, undos, hooks, ...) subclasses
`RenRefusal` alongside its historical base (`class TouchupRefused
(RenRefusal, RuntimeError)`) so every existing `except` still catches
it, and every raise site passes all three fields - the constructor
refuses an empty one, so a refusal without a fix fails loudly at the
raise site rather than reaching a user.

Every refusal also carries a stable machine-readable CODE
(`RenRefusal.code`): the support bundle, the structured log and any
machine reader key off it instead of parsing the prose. The default is
derived from the class name (`SpliceRefused` -> `REN_SPLICE_REFUSED`)
so every existing subclass is coded without touching its file; a
subclass that needs a code that survives a rename sets `CODE`
explicitly, and a raise site that needs one for this instance passes
`code=`. The human shape `render()` never carries the code - it stays
the pinned three lines - and `to_dict()` carries all four fields.
"""

from __future__ import annotations

import re


REFUSAL_EXIT_CODE = 4
"""Exit code for a refusal with a fix. Part of the contract below."""


EXIT_CONTRACT = """\
ren exit codes:
  0  success (a verb that only reports still exits 0 once it printed).
  1  failed - an unexpected error. Traceback on stderr, no fix recorded;
     this is a bug or an environment the code did not anticipate, not a refusal.
  2  usage - an unknown verb, bad arguments, or a run selection that never
     started (a REFUSED run summary keeps this code; its reason prints in
     the refusal shape).
  3  no engine checkout (ren only: no manage_project.py beside the install).
  4  refused - nothing ran. The fix printed above is the next step; run it
     and retry the verb."""
"""The exit code contract, stated once. `ren --help` does not print it;
`ren doctor` points here when a verb exits 4."""


class RenRefusal(RuntimeError, ValueError):
    """What happened, why, and the fix. All three are required.

    Subclasses both `RuntimeError` and `ValueError` so every existing
    `except` clause keeps catching an adopted refusal - adopting a
    class changes what its message carries, never who catches it."""

    CODE = ""
    """Explicit machine-readable code for this refusal family. When
    empty the instance falls back to the class-name derivation
    (`SpliceRefused` -> `REN_SPLICE_REFUSED`); set this when the code
    must survive a class rename."""

    def __init__(self, what: str, why: str, fix: str, code: str = "") -> None:
        missing = [name for name, value in
                   (("what", what), ("why", why), ("fix", fix))
                   if not (isinstance(value, str) and value.strip())]
        if missing:
            raise ValueError(
                "a refusal must carry what, why and fix; missing: "
                + ", ".join(missing) + ". A refusal without a fix is "
                "the defect this module exists to remove - state the "
                "exact next command or edit.")
        self.what = what.strip()
        self.why = why.strip()
        self.fix = fix.strip()
        self.code = (code.strip() or type(self).CODE.strip()
                     or _default_code(type(self).__name__))
        super().__init__(self.render())

    def render(self) -> str:
        """The one shape, as the `ren` boundary prints it."""
        return (f"ren: refused - {self.what}\n"
                f"  why: {self.why}\n"
                f"  fix: {self.fix}")

    def to_dict(self) -> dict:
        """All four fields, for structured logs and the support bundle."""
        return {"code": self.code, "what": self.what,
                "why": self.why, "fix": self.fix}


def _default_code(class_name: str) -> str:
    """`SpliceRefused` -> `REN_SPLICE_REFUSED`. Derived, so every
    existing subclass is coded without touching its file; a rename
    changes the code, which is why a family that must not move sets
    `CODE` explicitly."""
    if class_name == "RenRefusal":
        return "REN_REFUSAL"
    snake = re.sub(r"(?<!^)(?=[A-Z])", "_", class_name).upper()
    return snake if snake.startswith("REN_") else f"REN_{snake}"
