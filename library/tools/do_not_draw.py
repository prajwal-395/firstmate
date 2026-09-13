"""A planned graphic the captain deleted, and the instruction to keep it gone.

The defect this closes
----------------------
2026-09-13, wipe and rebuild: the captain had deleted the planned
graphic `mg_geo-podcast_a072b160.mov` from Reel 01's timeline by hand,
and the fresh build placed it again - nothing recorded the act at all.
`captain_edits.transform_override` held 10 of 10 picture moves through
the same rebuild because each one is a declaration the build re-applies;
a deletion with no store is not a weaker hold, it is no hold.

What a declaration says
-----------------------
`<project>/external/do_not_draw.json`, checked and never asserted (the
`overlay_intent.json` / `reel_ending.json` precedent)::

    {"version": 1,
     "suppressions": [
       {"reel": "Reel 01 - the-cta",
        "placement_label": "vox_reel_01_the_cta_00",
        "segment_id": "mg_geo-podcast_a072b160",
        "elements": ["stat_callout"],
        "reason": "captain 2026-09-13: deleted by hand in Resolve"}]}

`reel` is matched against the reel's timeline name by prefix - the
`reel_ending.declared_ending` convention - so a staging suffix names
the same reel as the promoted timeline.

The graphic is named by `placement_label` first: the per-reel render
name (`vox_<reelslug>_<index>`) that survives a re-render while the
content-keyed filename (`mg_<project>_<digest>`) does not - the same
reason `overlay_intent` pins died on the hash and were re-keyed onto
the stable prefix. `segment_id` is the filename stem the captain reads
off the timeline, kept so the declaration says what was actually seen.
At least one of the two is required.

`elements` is the cross-check, and the reason a label is safe to name:
a plan index shifts when an earlier graphic is added, so a label
re-pointed at a different graphic must not silently delete that one.
Where both the rule and the segment carry element names and the two
sets are disjoint, the suppression REFUSES and says to re-transcribe -
never the wrong graphic gone quietly. Where either side carries none,
there is nothing to check against and the label decides alone.

Suppress the placement, not the plan
------------------------------------
The plan is the record of what was intended - model-authored, reviewed,
and re-authored on the reel path every build - so deleting from it
destroys the evidence and the next re-plan re-adds the graphic anyway.
The suppression is enforced at placement (`reel_build.
place_overlay_segments` and the caption loop): the segment is not
imported, not placed, and the build SAYS which rule held it back, on
stderr and on the build record. A rule for this reel that matched no
segment is reported the same way `overlay_intent.report_unmatched`
reports it - the graphic left the plan, so retire the rule or
re-transcribe it. Silence in either direction is the defect this
module exists to stop.

`edit_depth.py` `mg_content` is the owning row; this module is its
declared half, the way `overlay_intent` is `overlay_position`'s.
"""

from __future__ import annotations

import json
import os
import sys
from typing import List, Optional, Tuple

#: The file basename, under the project's external-inputs area.
RULES_FILENAME = "do_not_draw.json"

#: Schema version this reader honours.
RULES_VERSION = 1


class DoNotDrawError(ValueError):
    """A declared suppression cannot be honoured as written."""


def _non_empty_str(value, label: str, index: int) -> str:
    if not isinstance(value, str) or not value.strip():
        raise DoNotDrawError(
            f"do_not_draw.suppressions[{index}].{label} names no "
            f"{label}: it must be a non-empty string.")
    return value


def validate_rules(value) -> list:
    """Declared suppressions, checked, or a refusal saying why not."""
    if not isinstance(value, list):
        raise DoNotDrawError(
            "do_not_draw.suppressions must be a list of suppressions, "
            f"not {type(value).__name__}.")
    seen = set()
    checked = []
    for index, rule in enumerate(value):
        label = f"do_not_draw.suppressions[{index}]"
        if not isinstance(rule, dict):
            raise DoNotDrawError(f"{label} is not an object")
        reel = _non_empty_str(rule.get("reel"), "reel", index)
        reason = rule.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise DoNotDrawError(
                f"{label} carries no `reason`. A suppression nobody "
                f"justified is a guess about what the captain wanted - "
                f"say whose decision this records.")
        placement_label = rule.get("placement_label")
        segment_id = rule.get("segment_id")
        for key, candidate in (("placement_label", placement_label),
                               ("segment_id", segment_id)):
            if candidate is not None and (
                    not isinstance(candidate, str)
                    or not candidate.strip()):
                raise DoNotDrawError(
                    f"{label}.{key} is {candidate!r}: name the graphic "
                    f"or leave the key out.")
        if not (isinstance(placement_label, str)
                and placement_label.strip()) and not (
                isinstance(segment_id, str) and segment_id.strip()):
            raise DoNotDrawError(
                f"{label} names no graphic: at least one of "
                f"`placement_label` (the per-reel render name, stable "
                f"across a re-render) or `segment_id` (the filename "
                f"stem off the timeline) is required. A suppression "
                f"that names nothing suppresses nothing.")
        elements = rule.get("elements", [])
        if not isinstance(elements, list) or any(
                not isinstance(e, str) or not e.strip() for e in elements):
            raise DoNotDrawError(
                f"{label}.elements is {rule.get('elements')!r}: it must "
                f"be a list of element names, or left out where there "
                f"is nothing to cross-check the placement label "
                f"against.")
        key = (reel,
               placement_label.strip()
               if isinstance(placement_label, str) else "",
               segment_id.strip() if isinstance(segment_id, str) else "")
        if key in seen:
            raise DoNotDrawError(
                f"{label} repeats an earlier suppression for the same "
                f"reel and graphic: one declaration per deletion, so "
                f"the record says once what is gone.")
        seen.add(key)
        checked.append({
            "reel": reel,
            "placement_label": key[1] or None,
            "segment_id": key[2] or None,
            "elements": [e for e in elements],
            "reason": reason.strip(),
        })
    return checked


def parse_rules(body: dict, source: str = RULES_FILENAME) -> list:
    """Validated suppressions from a decoded rules file, or a refusal."""
    if not isinstance(body, dict):
        raise DoNotDrawError(
            f"{source} must be a JSON object, not "
            f"{type(body).__name__}.")
    version = body.get("version")
    if version != RULES_VERSION:
        raise DoNotDrawError(
            f"{source} declares version {version!r}: this reader "
            f"honours version {RULES_VERSION}.")
    suppressions = body.get("suppressions", [])
    try:
        return validate_rules(suppressions)
    except DoNotDrawError as exc:
        raise DoNotDrawError(f"{source}: {exc}") from None


def rules_path(project_folder) -> str:
    """The file a project's suppressions live in, whether present or not."""
    from library.tools.external_inputs import external_dir

    return os.path.join(str(external_dir(project_folder)), RULES_FILENAME)


def load_rules(project_folder=None) -> list:
    """Declared suppressions for a project, or `[]` where none are declared.

    A present-but-malformed file raises `DoNotDrawError`: a deletion
    the build cannot read must refuse, never build silently past it -
    which is how the graphic came back in the first place.
    """
    if not project_folder:
        return []
    path = rules_path(project_folder)
    if not os.path.isfile(path):
        return []
    with open(path, encoding="utf-8") as handle:
        try:
            body = json.load(handle)
        except json.JSONDecodeError as exc:
            raise DoNotDrawError(
                f"{path} is not JSON: {exc}") from exc
    return parse_rules(body, source=path)


def _reel_match(rule_reel: str, reel_name: str) -> bool:
    """The `reel_ending` prefix convention: exact or staging-suffixed."""
    return bool(reel_name) and (
        reel_name == rule_reel or reel_name.startswith(rule_reel))


def _segment_keys(segment: dict) -> Tuple[Optional[str], Optional[str]]:
    """The two names a placed segment answers to, or None each."""
    label = (segment or {}).get("placement_label")
    label = str(label) if isinstance(label, str) and label.strip() else None
    declared = (segment or {}).get("segment_id")
    declared = (str(declared) if isinstance(declared, str)
                and declared.strip() else None)
    if declared is None:
        import os as _os

        path = (segment or {}).get("overlay_path") or ""
        stem = _os.path.splitext(_os.path.basename(path))[0]
        declared = stem or None
    return label, declared


def should_suppress(rules, reel_name: str,
                    segment: dict) -> Tuple[bool, str]:
    """Whether this reel's build must not draw this segment, and why.

    Returns `(True, reason)` where a suppression names this reel (by
    prefix) and this graphic (by placement label, by segment id, or -
    for re-rendered captions - by provenance prefix), and the element
    cross-check passes. Returns `(False, "")` otherwise, including for
    rules scoped to other reels: a Reel 02 deletion on a Reel 01 build
    is expected, not news.

    A label hit whose recorded elements are disjoint from the
    segment's REFUSES the suppression and says so in the reason: the
    plan shifted under the label and some other graphic is wearing it
    now. The caller reports that reason LOUDLY rather than placing
    past it, and treats it as not-suppressed - the graphic plays until
    the declaration is re-transcribed, because a wrong deletion is
    worse than a present one.
    """
    from library.tools.subtitle_segment_id import stable_prefix

    label, declared = _segment_keys(segment or {})
    prefixes = {p for p in (
        stable_prefix(label) if label else None,
        stable_prefix(declared) if declared else None) if p}
    segment_elements = set(
        e for e in ((segment or {}).get("elements") or [])
        if isinstance(e, str) and e.strip())
    for rule in rules or []:
        if not _reel_match(rule["reel"], reel_name or ""):
            continue
        hit = (
            (rule["placement_label"] is not None
             and rule["placement_label"] == label)
            or (rule["segment_id"] is not None
                and (rule["segment_id"] == declared
                     or rule["segment_id"] == label
                     or stable_prefix(rule["segment_id"]) in prefixes)))
        if not hit:
            continue
        rule_elements = set(rule.get("elements") or [])
        if (rule_elements and segment_elements
                and rule_elements.isdisjoint(segment_elements)):
            return (False,
                    f"do_not_draw for reel {rule['reel']!r} names "
                    f"{rule['placement_label'] or rule['segment_id']!r} "
                    f"({', '.join(sorted(rule_elements))}) but this "
                    f"build's segment wears that name carrying "
                    f"{', '.join(sorted(segment_elements))} - the plan "
                    f"shifted under the label. NOT suppressed: "
                    f"re-transcribe the declaration. "
                    f"({rule['reason']})")
        which = (f"placement_label {label!r}" if label == (
            rule["placement_label"] or rule["segment_id"])
            else f"segment {declared!r}")
        return (True,
                f"do_not_draw: {which} on {reel_name} stays undrawn "
                f"({rule['reason']})")
    return False, ""


def unmatched(rules, reel_name: str, seen) -> list:
    """This reel's suppressions that matched NO segment on the build.

    A suppression whose graphic left the plan is not honoured and not
    refused - it simply does nothing, silently. What is reported here
    is genuinely unbound on this reel: retire it, or re-transcribe it
    against the plan's current names. Rules scoped to other reels are
    not this build's business.
    """
    seen_labels: set = set()
    seen_prefixes: set = set()
    from library.tools.subtitle_segment_id import stable_prefix

    for entry in seen or ():
        if isinstance(entry, dict):
            label, declared = _segment_keys(entry)
        else:
            label, declared = None, (
                str(entry) if isinstance(entry, str) and entry else None)
        for value in (label, declared):
            if value:
                seen_labels.add(value)
                seen_prefixes.add(stable_prefix(value))
    missed = []
    for rule in rules or []:
        if not _reel_match(rule["reel"], reel_name or ""):
            continue
        names = [n for n in (rule["placement_label"], rule["segment_id"])
                 if n]
        if any(n in seen_labels or stable_prefix(n) in seen_prefixes
               for n in names):
            continue
        missed.append(rule)
    return missed


def report_unmatched(rules, reel_name: str, seen,
                     source: str = RULES_FILENAME) -> list:
    """This reel's suppressions that matched nothing, said aloud."""
    missed = unmatched(rules, reel_name, seen)
    if missed:
        import sys

        names = [r["placement_label"] or r["segment_id"] for r in missed]
        print(f"  {source} ({reel_name}): {len(missed)} suppression(s) "
              f"matched no segment on this build - "
              f"{', '.join(names)}. A suppression that matches nothing "
              f"holds nothing back: the graphic left the plan, so "
              f"retire the rule or re-transcribe it.",
              file=sys.stderr, flush=True)
    return missed


def describe_rules(rules) -> List[str]:
    """One plain-language line per suppression, for listings."""
    lines = []
    for rule in rules or []:
        lines.append(
            f"reel {rule['reel']!r} never draws "
            f"{rule['placement_label'] or rule['segment_id']!r} "
            f"({rule['reason']})")
    return lines


def main(argv=None) -> int:
    """`python3 -m library.tools.do_not_draw <project> list`."""
    import argparse

    parser = argparse.ArgumentParser(
        prog="library.tools.do_not_draw",
        description="A planned graphic the captain deleted, kept gone.")
    parser.add_argument("project_folder")
    parser.add_argument("verb", nargs="?", default="list",
                        choices=["list"])
    args = parser.parse_args(list(sys.argv[1:] if argv is None else argv))
    try:
        rules = load_rules(args.project_folder)
    except DoNotDrawError as exc:
        print(f"REFUSED\n\n{exc}\n")
        return 1
    if args.verb == "list":
        for line in describe_rules(rules) or ["no suppressions declared"]:
            print(line)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
