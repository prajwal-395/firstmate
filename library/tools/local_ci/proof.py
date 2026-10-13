"""Machine-readable live-proof obligations carried by a PR description."""

from __future__ import annotations

import re

_DECLARATION = re.compile(r"^\s*Ren-CI-Proof-Required:\s*([a-z0-9][a-z0-9._-]*)\s*$", re.IGNORECASE)


def requirements(body: str) -> list[str]:
    """Read repeated `Ren-CI-Proof-Required: <name>` declaration lines."""
    return sorted({match.group(1).lower()
                   for line in (body or "").splitlines()
                   if (match := _DECLARATION.match(line))})


def missing(pr: int, head: str, required: list[str], state_dir: str) -> list[str]:
    from library.tools.local_ci import state
    return [name for name in required
            if not state.has_accepted_proof(pr, head, name, state_dir)]
