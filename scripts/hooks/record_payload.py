"""Append what a hook was handed, so an operator can see the real payload.

The first thing anyone writing a hook needs is the SHAPE of what arrives,
and the payload belongs to the firing site rather than to the
declaration - guessing it is how a hook ends up matching nothing and
looking like a hook layer that does not work.

So this records rather than interprets. It reads the envelope on stdin
and appends one JSON line to `<project>/pipeline_output/logs/hooks.log`,
which is where the run's other logs already are. It measures nothing,
decides nothing, and has no opinion about what it was handed.

    python3 -m library.tools.hooks --conditions   # what may fire it

Declare it like this, in `library/hooks.json` or a project's own:

    {"hooks": [{
      "name": "see_what_qa_sends",
      "describe": "Recording the payload before writing a real hook for it.",
      "when": "qa_finding_raised",
      "action": {"kind": "run", "script": "record_payload.py"}
    }]}
"""

from __future__ import annotations

import json
import os
import sys


LOG_NAME = "hooks.log"


def main() -> int:
    raw = sys.stdin.read()
    try:
        envelope = json.loads(raw)
    except ValueError as exc:
        # Refuse loudly rather than write a line nobody can parse: a log
        # of malformed records is worse than a hook that reported failing.
        print(f"stdin was not the JSON envelope hooks.py sends: {exc}",
              file=sys.stderr)
        return 2

    project_dir = envelope.get("project_dir") or ""
    if not project_dir or not os.path.isdir(project_dir):
        print(f"project_dir {project_dir!r} is not a directory", file=sys.stderr)
        return 2

    # Written straight to the logs area rather than through ProjectLayout,
    # because a hook script runs as its own process and must not need the
    # repository on its import path to do one append.
    logs = os.path.join(project_dir, "pipeline_output", "logs")
    os.makedirs(logs, exist_ok=True)
    with open(os.path.join(logs, LOG_NAME), "a", encoding="utf-8") as handle:
        handle.write(json.dumps(envelope, sort_keys=True) + "\n")

    print(f"recorded {envelope.get('condition', '?')} for hook "
          f"{envelope.get('hook', '?')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
