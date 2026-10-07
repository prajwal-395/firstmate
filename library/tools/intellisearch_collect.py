"""Resolve IntelliSearch as extra evidence: collect the matching clips.

Ren search stays primary: these rows merge beside its answer through
`footage_query.merge_external_evidence` (slice 1), never into it. The
probe (slice 2) measured no scripting query verb, so rows are collected
through the UI - and the menu/AX inventory (slice 3) measured zero
IntelliSearch surface outside Qt. The collection therefore takes the
Media page through its menu path (background AX) and the Qt search box
through the announced foreground take (slice 3's `foreground` spec), in
one qualification session on the serving broker.

Two honesty rules shape this module, both measured:

- A pool clip name is claimed only by EXACT match against the project
  catalog - a substring match attributes Resolve's guess to the wrong
  source ("take1" is not "take10"). The catalog is the project's own
  `clip_catalog`, so no Resolve read-back is needed to map a name to a
  `clip_id`, and a name with no catalog entry is never a row.
- Rows carry no score and no span: the collector reads NAMES off the
  pixels, and a span or a confidence would be invented. The merge
  prints such rows as whole clips, which is what they are.

The search box has no address until a live observation measures one:
`build_collection_spec` refuses without `box` rather than aiming at
an unmeasured point. `survey` is the background-safe half that runs
anytime - the menu/AX scan whose findings the box observation builds
on.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from library.tools.ren_refusal import RenRefusal
from library.tools.resolved.jobs import JobRefused

#: Menu route to the Media page. Measured live with `list_commands`,
#: 2026-10-07: the seven Switch-to-Page entries, Media among them.
MEDIA_PAGE_PATH = ["Workspace", "Switch to Page", "Media"]

#: What the broker calls the one Resolve project a live job may touch.
QUALIFICATION_PROJECT = "Ren Qualification"

#: Settle delay after the page switch before the box click, in seconds.
#: A poll timeout, not a measurement: the page menu press returns before
#: Resolve redraws, and the click must land on the drawn box.
SETTLE_SECONDS = 2.0

#: How long the collector waits for results to populate before reading,
#: in seconds. Same standing as the settle delay: a poll timeout the
#: live run will confirm or correct, recorded here either way.
RESULTS_WAIT_SECONDS = 3.0


def _catalog_names(project_folder: str) -> dict:
    """Pool-visible file names to `clip_id`, from the project catalog.

    Both the full basename (`IMG_0001.MOV`) and the stem (`IMG_0001`)
    answer: the pool grid may render either, and both are exact forms of
    the same measured name - not fuzzy matching.
    """
    from library.tools.analysis.footage_segments import load_catalog

    names = {}
    for clip in load_catalog(project_folder) or []:
        clip_id = clip.get("clip_id")
        if not clip_id:
            continue
        for field in ("source_file", "path", "filename"):
            value = clip.get(field)
            if not value:
                continue
            base = os.path.basename(str(value))
            names.setdefault(base, clip_id)
            stem, _ = os.path.splitext(base)
            names.setdefault(stem, clip_id)
    return names


def build_collection_spec(query: str, box: tuple, page_path=None,
                          budget_seconds: float | None = None,
                          timeline: str = "") -> dict:
    """The `resolve.ui_transaction` spec that collects one query.

    `query` is what IntelliSearch is asked; `box` is the search field's
    measured `(x, y)` center in window points - runtime input, never
    defaulted. A blank query is refused (a call with nothing to ask is
    not made); a missing box is refused (a step says what it could not
    determine). Returns the spec validated through
    `ui_transaction.validate_spec`, so a malformed collection never
    reaches the broker.
    """
    from library.tools.resolved import ui_transaction

    if not str(query or "").strip():
        raise JobRefused(
            "an IntelliSearch collection needs 'query', what to look for")
    if (not isinstance(box, (list, tuple)) or len(box) != 2
            or not all(isinstance(v, (int, float)) for v in box)):
        raise JobRefused(
            "an IntelliSearch collection needs 'box', the search field's "
            "measured (x, y) center in window points - nothing here aims "
            "at an unmeasured point")
    x, y = float(box[0]), float(box[1])
    text = str(query).strip()
    page = list(page_path or MEDIA_PAGE_PATH)
    if len(page) < 2:
        raise JobRefused(
            "the page route needs a menu path above the page, not just "
            "its title - the driver resolves 'path' against the element's "
            "ancestors")
    # The driver matches 'path' against ancestors EXCLUDING the element
    # itself: measured live 2026-10-07, title Media with the inclusive
    # path matched nothing (the take's try 1 refused), with the
    # ancestors-only path exactly one element (the retry committed).
    # This is also what ui_transaction's own docstring shows.
    foreground: dict = {
        "reason": f"IntelliSearch search for {text!r} - the box is Qt-drawn",
    }
    if budget_seconds is not None:
        foreground["budget_seconds"] = budget_seconds
    spec = {
        "project": QUALIFICATION_PROJECT,
        "timeline": timeline,
        "target": {"clip_id": "intellisearch-pool"},
        "preconditions": {},
        "foreground": foreground,
        "actions": [
            {"action": "press", "target": {
                "kind": "accessibility_element",
                "title": page[-1],
                "path": page[:-1]}},
            {"action": "wait", "for": {"seconds": SETTLE_SECONDS}},
            {"action": "click", "target": {
                "kind": "window_relative_coordinate", "x": x, "y": y}},
            {"action": "type", "target": {
                "kind": "window_relative_coordinate", "x": x, "y": y,
                "text": text}},
            {"action": "key", "target": {
                "kind": "window_relative_coordinate", "x": x, "y": y,
                "key": "return", "modifiers": []}},
            {"action": "wait", "for": {"seconds": RESULTS_WAIT_SECONDS}},
        ],
        "verification": {"kind": "read_text", "include_elements": True},
        "restoration": {"page": True, "timeline": True, "playhead": True},
    }
    return ui_transaction.validate_spec(spec)


def extract_rows(ocr_elements: list, catalog_names: dict, query: str,
                 collected_by: str = "resolve_intellisearch_ui") -> dict:
    """Pool clip names off the pixels, intersected with the catalog.

    `ocr_elements` are `read_text` elements (`text` + `frame`);
    `catalog_names` maps exact pool-visible names to `clip_id`
    (`_catalog_names`). One row per clip, first occurrence wins; an OCR
    string that names no catalog entry is counted, never claimed. Rows
    are shaped for `--external-hits`: `clip_id`, the matched name as
    `text`, the query, and the provenance the merge stamps.
    """
    rows = []
    seen_clips = set()
    seen = 0
    for element in ocr_elements or []:
        if not isinstance(element, dict):
            continue
        name = str(element.get("text") or "").strip()
        if not name:
            continue
        seen += 1
        clip_id = catalog_names.get(name)
        if clip_id is None or clip_id in seen_clips:
            continue
        seen_clips.add(clip_id)
        rows.append({"clip_id": clip_id, "text": name,
                     "query": query, "collected_by": collected_by})
    return {"rows": rows, "matched_clips": len(rows),
            "seen_strings": seen}


def default_hits_path(project_folder: str) -> Path:
    """Where the collector files the rows its run produced."""
    return (Path(project_folder) / "pipeline_output" / "scratch"
            / "intellisearch_hits.json")


def collect(project_folder: str, query: str, box: tuple,
            out=None, timeline: str = "",
            budget_seconds: float | None = None) -> dict:
    """Collect one IntelliSearch query into an `--external-hits` file.

    Builds the spec, runs it as one `resolve.qualification_session` job
    on the serving broker, reads the query and result names from that
    transaction's in-receipt OCR, intersects them with the project
    catalog, and files the rows. The session borrows Resolve, reads back
    `Ren Qualification` by name, and restores the open project before
    releasing its exclusive lease. Every boundary refuses plainly: a
    blank query, a missing box, an empty catalog, no serving broker, a
    failed job, a locked screen (the driver's own refusal propagates -
    the take backs off, it is never retried here). An honest empty -
    names on screen, none in the catalog - files zero rows and says what
    was seen.
    """
    from library.tools.resolved import client

    if not str(query or "").strip():
        raise RenRefusal(
            "an IntelliSearch collection needs a query",
            "nothing to look for was given",
            "hand over the text IntelliSearch should be asked")
    names = _catalog_names(project_folder)
    if not names:
        raise RenRefusal(
            f"no catalog clip names in {project_folder}",
            "names are intersected with the project catalog, and it "
            "holds nothing to intersect with",
            f"build it first: ren search-index '{project_folder}'")
    if not client.serving():
        raise RenRefusal(
            "no ren-resolved broker is serving",
            "the collection runs as a qualification-session broker job, and "
            "Ren never starts a broker itself",
            "start one with `ren resolved serve`")
    spec = build_collection_spec(str(query).strip(), box,
                                 budget_seconds=budget_seconds,
                                 timeline=timeline)
    # Keep the borrowed wrapper: a plain ui_transaction refuses while
    # the captain's project is open, and a qualification-marked job only
    # runs when Ren Qualification was already open. This session saves,
    # opens and reads Ren Qualification back by name, then restores.
    submitted = client.submit("resolve.qualification_session", {
        "project": QUALIFICATION_PROJECT,
        "steps": [{"ui_transaction": spec}],
    })
    job = client.result(submitted["id"], wait=0.0)
    while job["state"] in ("queued", "running"):
        job = client.result(submitted["id"], wait=60.0)
    if job["state"] != "done":
        raise RenRefusal(
            f"the collection job ended {job['state']}",
            f"{job.get('error') or 'no error recorded'}",
            "read the receipt in the job result and re-run")
    session = job["result"]
    transaction = session["steps"][0]["receipt"]
    if transaction["observed_before"]["project"] != QUALIFICATION_PROJECT:
        raise RenRefusal(
            "the transaction did not read back the qualification project",
            f"the transaction opened {transaction['observed_before']['project']!r}",
            "re-run only through the qualification session broker job")
    verification = transaction["verification"]
    if verification["kind"] != "read_text":
        raise RenRefusal(
            "the transaction returned no in-lease screen observation",
            f"verification kind was {verification['kind']!r}",
            "re-run with the read_text transaction verification")
    screen_text = verification["text"]
    text = str(query).strip()
    if text.casefold() not in screen_text.casefold():
        raise RenRefusal(
            f"IntelliSearch did not render {text!r} on screen",
            f"the in-lease read_text receipt said: {screen_text!r}",
            "inspect the receipt before making another foreground take")
    elements = verification["elements"]
    report = extract_rows(elements, names, text)
    out_path = Path(out) if out else default_hits_path(project_folder)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(report["rows"], handle, indent=2)
    return {"rows": len(report["rows"]),
            "matched_clips": report["matched_clips"],
            "seen_strings": report["seen_strings"],
            "screen_text": screen_text,
            "matching_clip_names": [row["text"] for row in report["rows"]],
            "out": str(out_path), "job_id": submitted["id"]}


def survey() -> dict:
    """The background-safe half: what the menu/AX routes reach today.

    Runs anytime, screen locked or not - list_commands and the AX tree
    are reads. Reports the IntelliSearch menu entries (none, measured),
    the AX text fields (the Help search only), and the Media page path,
    which is what the box observation builds on.
    """
    from library.tools.computer_use.client import DriverClient

    driver = DriverClient()
    try:
        commands = driver.call("list_commands", {}).get("commands") or []
        tree = driver.call("get_accessibility_tree", {}).get("tree") or {}
    finally:
        driver.close()
    intelli_menu = [
        entry.get("path") for entry in commands
        if any("ntelli" in str(bit).lower()
               for bit in (entry.get("path") or []))]
    fields = []
    stack = [tree]
    while stack:
        node = stack.pop()
        if node.get("role") == "AXTextField":
            fields.append({"title": node.get("title"),
                           "label": node.get("label")})
        stack.extend(node.get("children") or [])
    media_paths = [
        entry.get("path") for entry in commands
        if (entry.get("path") or [])[-1:] == ["Media"]]
    return {"menu_commands": len(commands),
            "intellisearch_menu": intelli_menu,
            "ax_text_fields": fields,
            "media_page_paths": media_paths}
