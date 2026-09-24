"""Conformance verifier: read a BUILT timeline back against the SOP.

Given a timeline, report every SOP violation found in it: an empty
track, an unnamed track, an a-roll picture with no linked audio, a
caption inside a speech span with no link, two rows carrying what
should be one row's role, a placed stream that is not the recorded
program stream.

One picture item is deliberately outside the link check: a rendered
HOLD (`reel_ending.is_freeze_path`). It has no audio anywhere on the
timeline, so it can never be linked and is not evidence of anything.

Deterministic, and it can fail, so it is a real gate (AGENTS.md 10.4).
Link and stream checks need the track plan (`library.tools.
timeline_layout.TrackPlan`) - roles are what say which rows those
checks apply to. Without a plan only the structural checks run, and the
rest are reported as SKIPPED, never as passing.

Ship shape: `verify_timeline` is the callable surface, wrapped as the
`verify_timeline` gating pipeline skill in
`library/skills/verify_timeline/`; the CLI below points it at a
timeline by name and prints the structured report as JSON. Status logs
go to stderr; stdout carries JSON only.
"""

from library.tools.resolve_lock import under_lease


import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

from library.tools.timeline_layout import DEFAULT_NAMES

#: Every check the verifier can run, in a fixed order.
CHECKS = (
    "no_empty_tracks",
    "named_tracks",
    "singleton_roles",
    "aroll_linked",
    "captions_linked",
    "program_stream",
)

#: Checks that need the track plan's roles. Without a plan they are
#: skipped openly (see module docstring).
PLAN_CHECKS = frozenset({"aroll_linked", "captions_linked",
                         "program_stream"})


def _default_name(name: str, media_type: str, index: int) -> bool:
    """Whether a track name is Resolve's own default - never organised."""
    if not name:
        return True
    stem = (name or "").strip()
    if stem in DEFAULT_NAMES:
        return True
    return stem == f"{media_type.capitalize()} {index}"


def _span(item):
    try:
        return (item.GetStart(), item.GetEnd())
    except Exception:
        return None


def _uid(item):
    try:
        return item.GetUniqueId()
    except Exception:
        return None


def _linked_ids(item):
    try:
        return {_uid(i) for i in (item.GetLinkedItems() or [])} - {None}
    except Exception:
        return set()


def _channel_of(item):
    """The source channel an audio item carries, or None if unreadable."""
    try:
        mapping = json.loads(item.GetSourceAudioChannelMapping())
        channels = (mapping.get("track_mapping", {})
                    .get("1", {}).get("channel_idx", []))
    except Exception:
        return None
    channels = list(channels or [])
    return channels[0] if len(channels) == 1 else None


def _items(timeline, media_type: str, index: int):
    try:
        return timeline.GetItemListInTrack(media_type, index) or []
    except Exception:
        return []


def _is_held_frame(item) -> bool:
    """Whether this picture item is a rendered HOLD, not footage.

    `reel_ending` owns both the artefact and the question; this asks it
    with the path Resolve gives rather than restating the naming
    convention. An item Resolve will not answer for is NOT called a
    hold - the caller's default is to treat it as picture, which is the
    safe way to be wrong.
    """
    from library.tools.reel_ending import is_freeze_path
    try:
        pool_item = item.GetMediaPoolItem()
    except Exception:  # noqa: BLE001 - an unreadable item is not a hold
        return False
    if not pool_item:
        return False
    try:
        return is_freeze_path(pool_item.GetClipProperty("File Path"))
    except Exception:  # noqa: BLE001 - same reading
        return False


@under_lease("verify conformance", exclusive=False)
def verify_timeline(timeline, plan=None) -> dict:
    """Read `timeline` back and report SOP violations.

    Returns {"passed", "violations": [{check, detail, ...}],
    "checks_run": [...], "checks_skipped": [...], "track_count": {...}}.
    """
    violations = []
    checks_run = []
    checks_skipped = []

    counts = {}
    names = {}
    for media_type in ("video", "audio", "subtitle"):
        try:
            count = timeline.GetTrackCount(media_type) or 0
        except Exception:
            continue
        counts[media_type] = count
        for index in range(1, count + 1):
            try:
                names[(media_type, index)] = timeline.GetTrackName(
                    media_type, index)
            except Exception:
                names[(media_type, index)] = ""

    # ── no_empty_tracks: an empty row is never kept ──
    # Except a PLANNED-empty V1: a voiceover-led cut mints the legacy
    # row with nothing to place (the picture rides V2 by design), and
    # the build keeps it on the same flag rather than shifting every
    # row above it down. The flag travels on the track plan's material,
    # so a run without it still fails an empty V1 like before.
    _material = {}
    try:
        _material = (plan.material or {}) if plan is not None else {}
    except AttributeError:
        _material = {}
    _v1_may_be_empty = bool(
        isinstance(_material, dict)
        and _material.get("v1_intentionally_empty"))
    _speech_may_be_empty = bool(
        isinstance(_material, dict)
        and _material.get("speech_row_intentionally_empty"))
    _speech_rows = set()
    try:
        _plan_tracks = (getattr(plan, "audio_tracks", None)
                        if plan is not None else None) or []
        _speech_rows = {
            ("audio", t.index) for t in _plan_tracks
            if getattr(t, "role", "") == "speech"}
    except (AttributeError, TypeError):
        _speech_rows = set()
    for (media_type, index), name in names.items():
        if not _items(timeline, media_type, index):
            if (_v1_may_be_empty and media_type == "video"
                    and index == 1):
                continue
            if (_speech_may_be_empty
                    and (media_type, index) in _speech_rows):
                continue
            violations.append({
                "check": "empty_track",
                "track": f"{media_type}:{index}",
                "name": name,
                "detail": (f"Empty row {media_type} {index} "
                           f"({name or 'unnamed'}): a row exists because "
                           f"something goes on it."),
            })
    checks_run.append("no_empty_tracks")

    # ── named_tracks: no default names survive ──
    for (media_type, index), name in names.items():
        if _default_name(name, media_type, index):
            violations.append({
                "check": "unnamed_track",
                "track": f"{media_type}:{index}",
                "name": name,
                "detail": (f"Row {media_type} {index} carries Resolve's "
                           f"default name ({name!r}): nothing on the "
                           f"timeline is unorganised."),
            })
    checks_run.append("named_tracks")

    # ── singleton_roles: two rows must not share one role's name ──
    seen = {}
    for (media_type, index), name in sorted(names.items()):
        if not name or _default_name(name, media_type, index):
            continue
        if name in seen:
            violations.append({
                "check": "duplicate_role",
                "track": f"{media_type}:{index}",
                "name": name,
                "detail": (f"Row {media_type} {index} repeats the name of "
                           f"{seen[name]}: two rows carrying what should "
                           f"be one row's role."),
            })
        else:
            seen[name] = f"{media_type} {index}"
    checks_run.append("singleton_roles")

    if plan is None:
        checks_skipped.extend(sorted(PLAN_CHECKS))
        return {
            "passed": not violations,
            "violations": violations,
            "checks_run": checks_run,
            "checks_skipped": checks_skipped,
            "track_count": counts,
        }

    # ── aroll_linked: picture travels with its speech ──
    speech_index = []  # (start, end, item, row)
    for row in plan.speech_rows():
        for item in _items(timeline, "audio", row.index):
            span = _span(item)
            if span is None:
                continue
            speech_index.append((span[0], span[1], item, row.index))
            if not _linked_ids(item):
                violations.append({
                    "check": "aroll_unlinked",
                    "track": f"audio:{row.index}",
                    "name": row.name,
                    "detail": (f"Speech item at {span[0]}-{span[1]} on "
                               f"{row.name} links to nothing: a-roll "
                               f"picture and its speech travel together."),
                })

    def _in_picture_led_span(item) -> bool:
        """Whether this picture item plays a picture-led moment.

        Timeline frames against the build's own spans off the track
        plan's material. An item Resolve will not answer for is NOT
        called picture-led - the caller's default is to treat it as
        picture with speech, which is the safe way to be wrong.
        """
        try:
            material = (plan.material or {}) if plan is not None else {}
        except AttributeError:
            return False
        spans = (material.get("picture_led_spans") or []
                 if isinstance(material, dict) else [])
        if not spans:
            return False
        span = _span(item)
        if span is None:
            return False
        for bounds in spans:
            try:
                start, end = bounds
            except (TypeError, ValueError):
                continue
            if (isinstance(start, (int, float))
                    and isinstance(end, (int, float))
                    and start <= span[0] and span[1] <= end):
                return True
        return False
    for row in plan.aroll_rows():
        for item in _items(timeline, "video", row.index):
            if _is_held_frame(item):
                # A HELD FRAME is a copy of a frame the reel already
                # plays, laid on the ending shot's own row so it
                # inherits that shot's framing and grade. It carries no
                # audio anywhere on the timeline, so there is nothing
                # here for it to link TO - "could not be linked" is not
                # "was left unlinked", and a gate that cannot tell them
                # apart refuses correct output (AGENTS.md 10.4).
                # Measured 2026-09-12 rebuilding Reel 09 through the
                # variant path: the reel placed correctly and this
                # check removed it, naming the freeze at 1650.
                continue
            if _in_picture_led_span(item):
                # A picture-led moment says nothing: no words, no
                # voiceover, no audio anywhere for it to link to. The
                # spans ride the track plan's material off the build
                # that placed them.
                continue
            if not _linked_ids(item):
                span = _span(item)
                violations.append({
                    "check": "aroll_unlinked",
                    "track": f"video:{row.index}",
                    "name": row.name,
                    "detail": (f"Picture item at "
                               f"{span[0] if span else '?'} on {row.name} "
                               f"links to nothing: a-roll picture and its "
                               f"speech travel together."),
                })
    checks_run.append("aroll_linked")

    # ── captions_linked: a caption inside a speech span joins it ──
    caption_row = plan.caption_row()
    if caption_row is not None:
        for item in _items(timeline, "video", caption_row.index):
            span = _span(item)
            if span is None:
                continue
            hosts = [(s, e, host) for (s, e, host, _) in speech_index
                     if s <= span[0] and span[1] <= e]
            if not hosts:
                continue
            _, _, host = hosts[0]
            host_ids = _linked_ids(host) | {_uid(host)}
            if not (_linked_ids(item) & host_ids):
                violations.append({
                    "check": "caption_unlinked",
                    "track": f"video:{caption_row.index}",
                    "name": caption_row.name,
                    "detail": (f"Caption at {span[0]}-{span[1]} falls "
                               f"inside a speech span but shares no link "
                               f"with it."),
                })
    checks_run.append("captions_linked")

    # ── program_stream: the placed stream is the recorded one ──
    expected = (plan.program_channels() if hasattr(plan, "program_channels")
                else {})
    for row in plan.speech_rows():
        want = expected.get(row.index)
        if want is None:
            continue
        for item in _items(timeline, "audio", row.index):
            got = _channel_of(item)
            span = _span(item)
            if got is None:
                violations.append({
                    "check": "program_stream",
                    "track": f"audio:{row.index}",
                    "name": row.name,
                    "detail": (f"Speech item at "
                               f"{span[0] if span else '?'} on {row.name}: "
                               f"channel mapping unreadable, program "
                               f"CH{want} unverified."),
                })
            elif got != want:
                violations.append({
                    "check": "program_stream",
                    "track": f"audio:{row.index}",
                    "name": row.name,
                    "detail": (f"Speech item at "
                               f"{span[0] if span else '?'} on {row.name} "
                               f"carries CH{got}; the recorded program "
                               f"stream is CH{want}."),
                })
    checks_run.append("program_stream")

    return {
        "passed": not violations,
        "violations": violations,
        "checks_run": checks_run,
        "checks_skipped": checks_skipped,
        "track_count": counts,
    }


def main(argv=None) -> int:
    """CLI: point the verifier at a timeline by name. Prints the
    structured report as JSON on stdout; logs on stderr."""
    import argparse

    parser = argparse.ArgumentParser(
        description="Verify a built Resolve timeline against the SOP.")
    parser.add_argument("--project", required=True,
                        help="Exact Resolve project name")
    parser.add_argument("--timeline", required=True,
                        help="Exact timeline name")
    parser.add_argument("--plan-json", default="",
                        help="TrackPlan serializable (from a build result's "
                             "track_plan); without it, link and stream "
                             "checks are skipped openly")
    args = parser.parse_args(argv)

    sys.path.insert(
        0, "/Library/Application Support/Blackmagic Design/"
           "DaVinci Resolve/Developer/Scripting/Modules")
    import DaVinciResolveScript as dvr
    from library.tools.resolve_locale import scriptapp_preserving_locale

    resolve = scriptapp_preserving_locale(dvr)
    if resolve is None:
        print(json.dumps({"passed": False,
                          "error": "Resolve is not running"}),
              file=sys.stderr)
        return 2
    project = resolve.GetProjectManager().GetCurrentProject()
    if project is None or project.GetName() != args.project:
        print(json.dumps({"passed": False,
                          "error": f"open project is not exactly "
                                   f"{args.project!r}"}),
              file=sys.stderr)
        return 2
    timeline = None
    for i in range(1, (project.GetTimelineCount() or 0) + 1):
        candidate = project.GetTimelineByIndex(i)
        if candidate is not None and candidate.GetName() == args.timeline:
            timeline = candidate
            break
    if timeline is None:
        print(json.dumps({"passed": False,
                          "error": f"no timeline exactly {args.timeline!r}"}),
              file=sys.stderr)
        return 2

    plan = None
    if args.plan_json:
        from library.tools.timeline_layout import TrackPlan, TrackSpec
        raw = json.loads(args.plan_json)
        plan = TrackPlan(
            video_tracks=[TrackSpec(**t) for t in raw["video_tracks"]],
            audio_tracks=[TrackSpec(**t) for t in raw["audio_tracks"]],
            material=raw.get("material", {}))

    report = verify_timeline(timeline, plan=plan)
    json.dump(report, sys.stdout, indent=1, default=str)
    sys.stdout.write("\n")
    return 0 if report["passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
