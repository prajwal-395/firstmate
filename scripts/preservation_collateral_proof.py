"""PLAN-006: measure the collateral a request may cause, BEFORE and AFTER.

The punch list asks (PLAN-006) for preservation-aware edit planning and
names the measurement: unauthorized changes detected BEFORE execution,
before vs after, with the denominator.

So this script runs a corpus of REQUESTS against SCRATCH COPIES of a
timeline, and for each request it asks two engines the same question:

    what did the write actually move, and what did the engine's pre-write
    gate say about it BEFORE the write?

The scratch copy is the CANONICAL RESOLVE DOUBLE
(``tests/resolve_double.py``), whose behaviour is measured against real
Resolve rather than assumed, and every request runs in a fresh project
under a temporary directory.  Nothing here reads a captain's project,
touches the captain's media, starts a broker, or contacts Resolve: the
double is not Resolve, and the machine's one Resolve is somebody else's
business.

WHAT IS MEASURED

    unauthorized changes   an observed change to an item the request
                           does not name, on a row the request does not
                           name, and not a change to the LINK GROUP's
                           state (a group is one audio in two rows, so
                           the group's state is the group's).

    collateral             every observed change to an item the request
                           does not name - the unauthorized ones, the
                           group's, and the ones on the request's own row
                           that a ripple moved.

    stranded caption       a caption card that still covers an interval
                           the picture under it no longer covers.  No
                           item changed, so no diff row records it, and
                           it is the clearest example of a consequence
                           nobody was told about.

    approved work          a request that requires work unchanged, and
                           whose own CONSEQUENCES reach it.

The ground truth is the OBSERVED diff of the scratch copy, classified by
this harness's own reading of the request - not by the module under
test.  The harness and the module therefore agree by construction only
where the harness says so; where they disagree, the report says so too.

    python3 scripts/preservation_collateral_proof.py [--base <sha>] [--json]

``--base`` names the commit the BEFORE measurement is taken from (the
merge-base with origin/main by default).  The base tree is extracted to
a temporary directory and the SAME corpus is run against it, so the two
numbers are two engines, one corpus, one executor.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tarfile
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

#: The tree the probe runs against.  The BEFORE measurement points it at
#: the extracted base tree; the AFTER measurement leaves it at this
#: repository.
ENGINE_ROOT = REPO_ROOT


# ── the corpus ─────────────────────────────────────────────────────────
#
# One world, described once, and a set of requests against it.  The
# rows are: the picture row V1, the speech row A1, the overlay row V2
# and the caption row (a picture row that carries captions).  v1 and a1
# are one link group - the same audio, shown twice.

WORLD = {
    "picture_row": ["video", 1],
    "caption_row": ["video", 3],
    "rows": [
        {"type": "video", "index": 1, "name": "V1", "clips": [
            {"uid": "v1", "name": "v1.mov", "frames": 48},
            {"uid": "v2", "name": "v2.mov", "frames": 48}]},
        {"type": "audio", "index": 1, "name": "A1", "clips": [
            {"uid": "a1", "name": "a1.mov", "frames": 48},
            {"uid": "a2", "name": "a2.mov", "frames": 48}]},
        {"type": "video", "index": 2, "name": "V2", "clips": [
            {"uid": "o1", "name": "o1.mov", "frames": 48},
            {"uid": "o2", "name": "o2.mov", "frames": 48}]},
        {"type": "video", "index": 3, "name": "Captions", "clips": [
            {"uid": "c1", "name": "c1.png", "frames": 48},
            {"uid": "c2", "name": "c2.png", "frames": 48}]},
    ],
    "links": [["v1", "a1"]],
}

#: The requests.  ``execute`` says how the scratch copy carries the
#: request out:
#:
#: ``patch``             the engine's own patch layer (edit_patch);
#: ``native_ripple``     Resolve's NATIVE ripple delete - the primitive
#:                       ``clip.remove`` names and the engine refuses
#:                       unscoped, so the corpus asks what the request
#:                       WOULD move;
#: ``none``              the request is measured on its plan alone.
CORPUS = [
    {
        "id": "local-delete",
        "operations": [{"op": "clip.delete", "unique_id": "v1"}],
        "domains": ["timeline_structure"], "spans": [[0, 48]],
        "execute": "patch",
    },
    {
        "id": "native-ripple-delete",
        "operations": [{"op": "clip.remove", "unique_id": "v1"}],
        "domains": ["timeline_structure"], "spans": [[0, 192]],
        "execute": "native_ripple",
    },
    {
        "id": "native-ripple-with-a-named-speech-row",
        "operations": [
            {"op": "clip.remove", "unique_id": "v1"},
            {"op": "clip.set_enabled", "unique_id": "a2", "enabled": False}],
        "domains": ["timeline_structure"], "spans": [[0, 192]],
        "execute": "native_ripple",
    },
    {
        "id": "link-two-clips",
        "operations": [{"op": "clip.link", "unique_id": "v1",
                        "with_unique_ids": ["v2"]}],
        "domains": ["timeline_structure"], "spans": [[0, 96]],
        "execute": "patch",
    },
    {
        "id": "unlink-one-of-a-group",
        "operations": [{"op": "clip.unlink", "unique_id": "v1"}],
        "domains": ["timeline_structure"], "spans": [[0, 48]],
        "execute": "patch",
    },
    {
        "id": "delete-under-a-caption",
        "operations": [{"op": "clip.delete", "unique_id": "v1"}],
        "domains": ["timeline_structure"], "spans": [[0, 48]],
        "execute": "patch",
    },
    {
        "id": "set-a-property",
        "operations": [{"op": "clip.set_property", "unique_id": "v1",
                        "key": "ZoomX", "value": 1.5}],
        "domains": ["picture_transform"], "spans": [[0, 48]],
        "execute": "patch",
    },
    {
        "id": "disable-a-clip",
        "operations": [{"op": "clip.set_enabled", "unique_id": "v1",
                        "enabled": False}],
        "domains": ["timeline_structure"], "spans": [[0, 48]],
        "execute": "patch",
    },
    {
        "id": "add-a-timeline-marker",
        "operations": [{"op": "marker.add", "frame": 10, "color": "Blue",
                        "name": "approved"}],
        "domains": ["markers"], "spans": [[10, 11]],
        "execute": "patch",
    },
    {
        "id": "delete-an-approved-clip",
        "operations": [{"op": "clip.delete", "unique_id": "v1"}],
        "domains": ["timeline_structure"], "spans": [[0, 48]],
        "requirements": [{"kind": "clip_unchanged", "unique_id": "v1"}],
        "execute": "patch",
    },
    {
        "id": "ripple-into-approved-work",
        "operations": [{"op": "clip.ripple", "unique_id": "v1",
                        "delta_frames": -48}],
        "domains": ["timeline_structure"], "spans": [[0, 192]],
        "requirements": [{"kind": "clip_unchanged", "unique_id": "v2"}],
        "execute": "none",
    },
    {
        "id": "ripple-into-an-approved-row",
        "operations": [{"op": "clip.ripple", "unique_id": "v1",
                        "delta_frames": -48}],
        "domains": ["timeline_structure"], "spans": [[0, 192]],
        "requirements": [{"kind": "row_unchanged", "track_type": "video",
                          "track_index": 1}],
        "execute": "none",
    },
    {
        "id": "ripple-past-an-approved-marker",
        "operations": [{"op": "clip.ripple", "unique_id": "v1",
                        "delta_frames": -48}],
        "domains": ["timeline_structure"], "spans": [[0, 192]],
        "requirements": [{"kind": "marker_preserved", "frame": 60}],
        "execute": "none",
    },
]


# ── the scratch world ─────────────────────────────────────────────────


def _build_world(tmp: Path):
    """A fresh project, one timeline, and the world the corpus names."""
    sys.path.insert(0, str(ENGINE_ROOT))
    from library.tools import timeline_shadow as shadow
    from library.tools.resolve_lock import assume_sole_writer
    from tests.resolve_double import (
        FakeTimeline,
        make_pool_clip,
        make_project,
        place_clip,
    )

    project = make_project("Podcast")
    timeline = project.adopt(FakeTimeline("Reel 09", project=project))
    items: dict[str, object] = {}
    for row in WORLD["rows"]:
        for clip in row["clips"]:
            pool = make_pool_clip(clip["name"])
            item = place_clip(timeline, pool, 0, int(clip["frames"]) - 1,
                              kind=row["type"], track_index=int(row["index"]))
            item._uid = clip["uid"]
            items[clip["uid"]] = item
        # The row carries the NAME the corpus gives it: a caption row is
        # named for what it carries, and the name is how the engine's
        # collateral model finds it.
        timeline.SetTrackName(row["type"], int(row["index"]), row["name"])
    for first, second in WORLD["links"]:
        timeline.SetClipsLinked([items[first], items[second]], True)
    project.SetCurrentTimeline(timeline)
    store = shadow.ShadowStore(tmp / "shadow.db")
    with assume_sole_writer("the canonical Resolve double"):
        base = shadow.observe(project, timeline, store)
    return project, timeline, store, base, items


def _read_after(project, timeline, store):
    """The state the write left behind."""
    from library.tools import timeline_shadow as shadow
    from library.tools.resolve_lock import assume_sole_writer

    with assume_sole_writer("the canonical Resolve double"):
        try:
            return shadow.read_live(project, timeline)
        except Exception:  # noqa: BLE001 - an unreadable live state falls back to the recorded one
            head = store.head("Podcast", store.resolve_timeline("Podcast",
                                                                "Reel 09"))
            return store.snapshot(head) if head is not None else {}


# ── the harness's own reading of the request ──────────────────────────

#: The keys an operation names an item with.  Read here by the HARNESS,
#: independently of the module under test.
_NAME_KEYS = ("unique_id", "client_unique_id", "with_unique_ids",
              "other_unique_id")

#: The fields that carry a LINK GROUP's state.  A group is one audio in
#: two rows, so the state is the group's, and a request that changes it
#: changes both sides whether it names them or not.
_LINK_FIELDS = ("linked_unique_ids", "linked_clips")


def _named_ids(operations) -> set[str]:
    named: set[str] = set()
    for operation in operations or ():
        for key in _NAME_KEYS:
            value = operation.get(key)
            if value is None:
                continue
            if isinstance(value, (list, tuple)):
                named.update(str(entry) for entry in value if entry)
                continue
            named.add(str(value))
    return named


def _rows_of(snapshot) -> dict[str, tuple[str, int]]:
    rows: dict[str, tuple[str, int]] = {}
    for track in snapshot.get("tracks") or ():
        for clip in track.get("clips") or ():
            rows[str(clip.get("unique_id") or "")] = (
                str(track.get("type") or ""), int(track.get("index") or 0))
    return rows


def _named_rows(operations, snapshot) -> set[tuple[str, int]]:
    named = {row for uid, row in _rows_of(snapshot).items()
             if uid in _named_ids(operations)}
    for operation in operations or ():
        track_type = operation.get("track_type")
        track_index = operation.get("track_index")
        if track_type is not None and track_index is not None:
            named.add((str(track_type), int(track_index)))
    return named


def _classify(rows, operations, before) -> list[dict]:
    """The harness's own reading of what the write did.

    A change to an item the request names is REQUESTED.  A change to the
    LINK GROUP's state is NECESSARY.  A change to an item on a row the
    request names is REQUESTED - the request's own row.  Everything else
    is UNAUTHORIZED: the write touched work the request did not name,
    on a row the request did not name.
    """
    named = _named_ids(operations)
    named_rows = _named_rows(operations, before)
    out = []
    for row in rows:
        target = str(row.get("target") or "")
        if not target.startswith("clip:"):
            continue
        unique_id = target.split(":", 1)[1]
        field = str(row.get("field") or "")
        if unique_id in named:
            kind = "requested"
        elif field.split(".", 1)[0] in _LINK_FIELDS:
            kind = "necessary"
        elif _rows_of(before).get(unique_id) in named_rows:
            kind = "requested"
        else:
            kind = "unauthorized"
        out.append({"target": target, "field": field, "kind": kind,
                    "domain": str(row.get("domain") or "")})
    return out


def _stranded_captions(before, after) -> list[str]:
    """The caption cards left covering an interval with no picture."""
    caption_row = tuple(WORLD["caption_row"])
    picture_row = tuple(WORLD["picture_row"])

    def _spans(snapshot, row):
        out = []
        for track in snapshot.get("tracks") or ():
            if (str(track.get("type") or ""), int(track.get("index") or 0)) \
                    != row:
                continue
            for clip in track.get("clips") or ():
                out.append((str(clip.get("unique_id") or ""),
                            int(clip.get("record_in") or 0),
                            int(clip.get("record_out") or 0)))
        return out

    before_picture = _spans(before, picture_row)
    after_picture = _spans(after, picture_row)
    stranded = []
    for unique_id, start, end in _spans(after, caption_row):
        covered_before = any(start < b_end and b_start < end
                             for _uid, b_start, b_end in before_picture)
        covered_after = any(start < a_end and a_start < end
                            for _uid, a_start, a_end in after_picture)
        if covered_before and not covered_after:
            stranded.append(f"clip:{unique_id}")
    return sorted(stranded)


# ── one request against one engine ─────────────────────────────────────


def _probe_one(spec: dict, tmp: Path) -> dict:
    """Run one request against a scratch copy and judge it."""
    from library.tools import compound_transaction, edit_patch, preservation_invariant
    from library.tools.resolve_lock import assume_sole_writer
    from tests.resolve_double import FakeResolve

    project, timeline, store, base, items = _build_world(tmp)
    before = store.snapshot(base)
    operations = spec["operations"]

    gate = compound_transaction.preflight(
        project_folder="", transaction_id=f"p006-{spec['id']}",
        operations=operations, before=before,
        requirements=spec.get("requirements") or (),
        affected_spans=spec.get("spans") or ())

    executed = spec.get("execute", "none")
    status = "not executed"
    after = before
    if executed == "patch":
        patch = {
            "id": f"p006-{spec['id']}",
            "project": "Podcast", "timeline": "Reel 09",
            "base_generation": base.generation,
            "capability": "reel.touchup",
            "affected_spans": [list(span) for span in spec.get("spans") or ()],
            "conflict_domains": list(spec.get("domains") or ()),
            "operations": operations,
        }
        try:
            with assume_sole_writer("the canonical Resolve double"):
                receipt = edit_patch.apply_patch(
                    patch, resolve=FakeResolve(project), project=project,
                    timeline=timeline, store=store,
                    project_folder=str(tmp))
            status = str(receipt.get("status") or "committed")
        except Exception as exc:  # noqa: BLE001 - a refused write is a result
            status = f"refused: {type(exc).__name__}"
        after = _read_after(project, timeline, store)
    elif executed == "native_ripple":
        # The ripple's ANCHOR is the item the FIRST operation names: the
        # gap closes where that item was.
        target = None
        for operation in operations:
            unique_id = str(operation.get("unique_id") or "")
            if unique_id in items:
                target = items[unique_id]
                break
        if target is not None:
            with assume_sole_writer("the canonical Resolve double"):
                timeline.DeleteClips([target], True)
            status = "rippled"
        after = _read_after(project, timeline, store)

    rows = preservation_invariant.snapshot_delta(before, after)
    observed = _classify(rows, operations, before)
    stranded = _stranded_captions(before, after) if after is not None else []

    # The engine's OWN post-hoc read of the same write, for the record.
    scope = preservation_invariant.scope_for(
        operations, conflict_domains=spec.get("domains") or (),
        affected_spans=spec.get("spans") or ())
    invariant = preservation_invariant.invariant(
        scope=scope, before=before, operations=operations, after=after)
    post_hoc_unrelated = sorted({
        str(row.get("target")) for row in invariant.unrelated})

    report = gate.get("collateral") or (
        (gate.get("detail") or {}).get("collateral") or {})
    items_reported = report.get("items") or []
    reported_unauthorized = sorted({
        str(row.get("target")) for row in items_reported
        if row.get("kind") == "unauthorized"})
    reported_collateral = sorted({
        str(row.get("target")) for row in items_reported
        if not row.get("own", True)})
    reported_conflicts = sorted({
        str(row.get("target")) for row in (report.get("conflicts") or [])})

    observed_unauthorized = sorted({
        row["target"] for row in observed if row["kind"] == "unauthorized"})
    observed_collateral = sorted({
        row["target"] for row in observed if row["kind"] != "requested"})

    return {
        "id": spec["id"],
        "executed": executed,
        "status": status,
        "requirements": list(spec.get("requirements") or ()),
        "gate_refused": bool(gate.get("status") == "refused"),
        "gate_reason": str(gate.get("reason") or ""),
        "gate_category": str(gate.get("category") or ""),
        "observed_changes": observed,
        "observed_unauthorized": observed_unauthorized,
        "observed_collateral": observed_collateral,
        "stranded_captions": stranded,
        "post_hoc_unrelated": post_hoc_unrelated,
        "post_hoc_status": invariant.status,
        "reported_unauthorized": reported_unauthorized,
        "reported_collateral": reported_collateral,
        "reported_conflicts": reported_conflicts,
        "has_collateral_report": bool(report),
    }


def _probe(engine_root: Path) -> list[dict]:
    """Run the whole corpus against one engine."""
    results = []
    for spec in CORPUS:
        tmp = Path(tempfile.mkdtemp(prefix="p006-"))
        try:
            results.append(_probe_one(spec, tmp))
        except Exception as exc:  # noqa: BLE001 - a broken request is a result
            results.append({"id": spec["id"], "error": f"{type(exc).__name__}: {exc}"})
    return results


# ── the report ────────────────────────────────────────────────────────


def _measure(results: list[dict]) -> dict:
    """The numbers, with their denominators."""
    measured = [row for row in results if "error" not in row]
    executed = [row for row in measured if row["executed"] != "none"]
    unauthorized = [target for row in executed
                    for target in row["observed_unauthorized"]]
    collateral = [target for row in executed
                  for target in row["observed_collateral"]]
    stranded = [target for row in executed
                for target in row["stranded_captions"]]
    detected = [target for row in executed
                for target in row["observed_unauthorized"]
                if target in row["reported_unauthorized"]]
    named = [target for row in executed
             for target in row["observed_collateral"]
             if target in row["reported_collateral"]]
    stranded_named = [target for row in executed
                      for target in row["stranded_captions"]
                      if target in row["reported_collateral"]]
    false_conflicts = sorted({
        target for row in executed
        for target in row["reported_unauthorized"]
        if target not in row["observed_unauthorized"]})
    approved = [row for row in measured if row.get("requirements")]
    approved_refused = [row for row in approved if row["gate_refused"]]
    post_hoc_violations = [row for row in executed
                           if row["post_hoc_status"] == "violated"]
    return {
        "requests": len(measured),
        "executed_requests": len(executed),
        "unauthorized_changes": len(unauthorized),
        "unauthorized_detected": len(detected),
        "collateral_changes": len(collateral),
        "collateral_named": len(named),
        "stranded_captions": len(stranded),
        "stranded_captions_named": len(stranded_named),
        "false_unauthorized": len(false_conflicts),
        "false_unauthorized_targets": false_conflicts,
        "approved_work_requests": len(approved),
        "approved_work_refused": len(approved_refused),
        "post_hoc_violations": len(post_hoc_violations),
        "post_hoc_violation_ids": [row["id"] for row in post_hoc_violations],
        "detected_detail": detected,
    }


def _print_report(before: dict, after: dict, base: str, rows: list) -> None:
    print(f"BEFORE (base {base})")
    print(f"  requests measured                     {before['requests']}")
    print(f"  unauthorized changes observed         "
          f"{before['unauthorized_changes']}")
    print(f"  unauthorized changes detected BEFORE  "
          f"{before['unauthorized_detected']}/"
          f"{before['unauthorized_changes']}")
    print(f"  collateral changes observed           "
          f"{before['collateral_changes']}")
    print(f"  collateral named BEFORE               "
          f"{before['collateral_named']}/{before['collateral_changes']}")
    print(f"  stranded captions named BEFORE        "
          f"{before['stranded_captions_named']}/"
          f"{before['stranded_captions']}")
    print(f"  approved-work requests refused        "
          f"{before['approved_work_refused']}/"
          f"{before['approved_work_requests']}")
    print(f"  false unauthorized reported           "
          f"{before['false_unauthorized']}")
    print()
    print("AFTER (this branch)")
    print(f"  requests measured                     {after['requests']}")
    print(f"  unauthorized changes observed         "
          f"{after['unauthorized_changes']}")
    print(f"  unauthorized changes detected AFTER   "
          f"{after['unauthorized_detected']}/"
          f"{after['unauthorized_changes']}")
    print(f"  collateral changes observed           "
          f"{after['collateral_changes']}")
    print(f"  collateral named AFTER                "
          f"{after['collateral_named']}/"
          f"{after['collateral_changes']}")
    print(f"  stranded captions named AFTER         "
          f"{after['stranded_captions_named']}/"
          f"{after['stranded_captions']}")
    print(f"  approved-work requests refused        "
          f"{after['approved_work_refused']}/"
          f"{after['approved_work_requests']}")
    print(f"  false unauthorized reported           "
          f"{after['false_unauthorized']}")
    print(f"  post-hoc invariant violations         "
          f"{after['post_hoc_violations']}"
          f" {after['post_hoc_violation_ids']}")
    print()
    for row in rows:
        if "error" in row:
            print(f"  {row['id']}: ERROR {row['error']}")
            continue
        print(f"  {row['id']}: {row['status']} | gate "
              f"{'REFUSED' if row['gate_refused'] else 'ok'}"
              f" | observed unauthorized {row['observed_unauthorized']}"
              f" | reported {row['reported_unauthorized']}")


def _extract(base: str, destination: Path) -> None:
    """The base tree, extracted for the BEFORE measurement."""
    archive = subprocess.run(["git", "archive", base], cwd=str(REPO_ROOT),
                             capture_output=True, check=False)
    if archive.returncode != 0:
        raise SystemExit(f"git archive {base} failed: "
                         f"{archive.stderr.decode('utf-8', 'replace')}")
    destination.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=__import__("io").BytesIO(archive.stdout)) as tar:
        tar.extractall(destination)


def main(argv=None) -> int:
    global ENGINE_ROOT
    parser = argparse.ArgumentParser(
        prog="preservation_collateral_proof",
        description="Measure the collateral a request may cause, before and "
                    "after")
    parser.add_argument("--base", default=None,
                        help="the commit the BEFORE measurement is taken from")
    parser.add_argument("--json", action="store_true",
                        help="print the whole report as JSON")
    parser.add_argument("--probe", action="store_true",
                        help=argparse.SUPPRESS)
    parser.add_argument("--engine-root", default=None,
                        help=argparse.SUPPRESS)
    args = parser.parse_args(argv)

    ENGINE_ROOT = Path(args.engine_root).resolve() if args.engine_root \
        else REPO_ROOT
    if str(ENGINE_ROOT) not in sys.path:
        sys.path.insert(0, str(ENGINE_ROOT))
    if args.probe:
        print(json.dumps(_probe(ENGINE_ROOT)))
        return 0

    base = args.base
    if base is None:
        result = subprocess.run(["git", "merge-base", "HEAD", "origin/main"],
                                cwd=str(REPO_ROOT), capture_output=True,
                                text=True, encoding="utf-8", check=False)
        base = result.stdout.strip() or "HEAD"

    scratch = Path(tempfile.mkdtemp(prefix="p006-proof-"))
    before_root = scratch / "base"
    _extract(base, before_root)

    before_rows = _run_probe(before_root)
    after_rows = _run_probe(REPO_ROOT)
    report = {"base": base,
              "before": _measure(before_rows),
              "after": _measure(after_rows),
              "requests": after_rows}
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        _print_report(report["before"], report["after"], base, after_rows)
    return 0


def _run_probe(engine_root: Path) -> list[dict]:
    """Run the probe against one engine, in its own interpreter."""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(engine_root)
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--probe",
         "--engine-root", str(engine_root)],
        capture_output=True, text=True, encoding="utf-8", env=env,
        cwd=str(engine_root), check=False)
    if result.returncode != 0:
        raise SystemExit(f"the probe against {engine_root} failed:\n"
                         f"{result.stdout}\n{result.stderr}")
    return json.loads(result.stdout)


if __name__ == "__main__":
    raise SystemExit(main())
