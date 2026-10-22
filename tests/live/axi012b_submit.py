"""Submit the AXI-012b live measurement through the SERVING broker.

The measurement itself is ``tests/live/axi012b_audio_gain_measurement.py``;
it runs INSIDE Resolve, on the broker's own scripting connection, so it
cannot be run from here.  This script is the half that can:

* render the measurement with the engine root substituted for the
  module path, so the numbers come from the SHIPPED measurement module
  and not from a copy of it;
* submit it as a ``resolve.qualification_session`` job through the
  broker that is already serving - never starting one, never reaching
  for a quiet window (the run queues behind whatever else the machine
  is doing, and the broker's lease is the coordination);
* copy the report the run produced back into the repository.

The broker runs the engine from ITS OWN checkout, so the measurement
loads the module by path rather than importing it: that is what makes
the run measure THIS tree's code.

Usage::

    python3 tests/live/axi012b_submit.py [--wait 900] [--dry-run]
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from pathlib import Path

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))))
TEMPLATE = os.path.join(ROOT, "tests", "live",
                         "axi012b_audio_gain_measurement.py")
MODULE = os.path.join(ROOT, "library", "tools", "audio_render_evidence.py")
DIMENSIONS = os.path.join(ROOT, "library", "tools",
                           "axi_qualification_dimensions.py")
RENDERED = "/tmp/axi012b_audio_gain_measurement.py"
REPORT_TMP = "/tmp/axi012b-audio-gain.json"
REPORT_REPO = os.path.join(ROOT, "tests", "live", "reports",
                           "axi012b-audio-gain.json")
PROJECT = "Ren Qualification"

#: The Ren project the recorded run files its .drt backup under.  A
#: recorded WRITING script needs one: the run's own backup belongs to a
#: project, not to a temporary directory.
PROJECT_ROOT = Path("/tmp/axi012b-projects")
PROJECT_DIR = PROJECT_ROOT / "axi012b-measurement"


def project_dir() -> str:
    """The Ren project the run files its backup under, created if needed."""
    if not (PROJECT_DIR / "project.yaml").is_file():
        from library.tools import project_registry

        PROJECT_ROOT.mkdir(parents=True, exist_ok=True)
        project_registry.create_project(
            slug=PROJECT_DIR.name, name="AXI-012b measurement",
            root=PROJECT_ROOT, resolve_project_name=PROJECT)
    return str(PROJECT_DIR)


def render_script() -> str:
    """The measurement, with the engine root substituted in."""
    with open(TEMPLATE, encoding="utf-8") as handle:
        script = handle.read()
    if "__AXI012B_MODULE_PATH__" not in script:
        raise SystemExit(
            f"{TEMPLATE} no longer carries the module placeholder; the "
            f"submitter and the measurement have drifted apart")
    # The placeholders sit INSIDE the assignment's own quotes, so the
    # replacement is the VALUE and not a quoted literal: replacing the
    # placeholder with ``repr(path)`` would leave the path wearing a
    # second pair of quotes, and the module would be looked up under a
    # name that ends in a quote character.
    script = script.replace("__AXI012B_MODULE_PATH__", MODULE)
    script = script.replace("__AXI012B_DIMENSIONS_PATH__", DIMENSIONS)
    script = script.replace("__AXI012B_REPORT_PATH__", REPORT_REPO)
    with open(RENDERED, "w", encoding="utf-8") as handle:
        handle.write(script)
    return RENDERED


def submit(wait: float) -> dict:
    """Queue the measurement on the serving broker and wait for it."""
    from library.tools.resolved import client

    job = client.submit(
        "resolve.qualification_session",
        {
            "project": PROJECT,
            "steps": [{
                "resolve_axi": [
                    "run", "--file", RENDERED, "--unsafe", "--json",
                    "--purpose",
                    "AXI-012b: measure the native audio gain and pan "
                    "routes in the rendered audio",
                    "--scope", "project",
                    "--project-dir", project_dir(),
                ],
            }],
        },
        qualification=True,
    )
    return client.result(job["id"], wait=wait, until_done=True,
                         deadline=wait + 120.0)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--wait", type=float, default=900.0,
                        help="seconds to wait for the measurement")
    parser.add_argument("--dry-run", action="store_true",
                        help="render the script and print the job, but "
                             "do not submit it")
    args = parser.parse_args(argv)
    if not os.path.isfile(MODULE):
        raise SystemExit(f"the measurement module is missing: {MODULE}")
    if not os.path.isfile(DIMENSIONS):
        raise SystemExit(f"the dimensions module is missing: {DIMENSIONS}")
    script = render_script()
    if args.dry_run:
        print(json.dumps({"script": script, "module": MODULE}, indent=2))
        return 0
    receipt = submit(args.wait)
    print(json.dumps({"receipt": receipt}, indent=2, default=str)[:8000])
    if os.path.isfile(REPORT_TMP):
        os.makedirs(os.path.dirname(REPORT_REPO), exist_ok=True)
        shutil.copyfile(REPORT_TMP, REPORT_REPO)
        with open(REPORT_REPO, encoding="utf-8") as handle:
            report = json.load(handle)
        print(json.dumps({
            "report": REPORT_REPO,
            "gain_verified": (report.get("gain") or {}).get("verified"),
            "pan_audible": (report.get("pan") or {}).get("audible"),
            "errors": report.get("errors"),
        }, indent=2))
        return 0 if not report.get("errors") else 1
    print("the run produced no report", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
