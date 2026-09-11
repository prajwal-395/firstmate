"""A recorded spelling correction, enforced on regenerated displays.

The deterministic pass (`transcript_corrections.apply_to_document`)
guarantees the TRANSCRIPT. It does not move copies made before the
correction: a proposal written Sep 10 quoting speech the correction
respelt Sep 9 keeps the old spelling until something regenerates it -
and regeneration alone is not enough, because the model-authored half
(quotes the reader writes, reasons, verdict details) can misspell
against a clean transcript and a routed prompt. The prompt half tells;
it does not guarantee.

This is the post-pass PR 976 recommends (the 3.04 keep-exclusion
precedent: recorded decisions enforced on every regenerated proposal,
in the post-bridge, deterministically). After the model answers and
after measurements derive, before the step output persists, every
display string is respelled with the same whole-word rule the
transcript pass uses. No pins and this passes everything through
untouched: a project that recorded nothing regenerates byte for byte
what it always did.

What is respelled: quoted speech and model-authored copy about it -
`text`, `says`, `quote`, `reason`, `preview`, `detail`, `note` and
their neighbours. What is NEVER respelled (`SKIP_KEYS`): identity and
reference - slugs, labels, timeline names, file paths, decision
anchors (`anchor_phrase`, `from_phrase`: pins are decisions, not
displays, and their staleness reporting is the existing loud
mechanism), speaker attributions (a separate measurement from quoted
words), approvals, formats and versions. A whole-word match inside an
id would otherwise rename the thing the id names.

Where it runs (each guarded - a respell failure warns and keeps the
unrespelled text, and the coherence wording scan still flags it, so a
broken post-pass is loud rather than silent):
- 3.04 bridge (`reel_candidates` measurements),
- 3.04 post-bridge (`reel_selection`: moments, considered, dropped),
- 3.05 bridge (`reels_to_read` lines),
- 3.05 post-bridge (the reader's entry AND the reel words, before
  `read_one` checks quotes against words - both sides get the same
  transform, so grounding is preserved while spelling is corrected),
- the conformance verifier (the report, before it is written).
The live proposal inherits from the respelled selection on its next
rebuild; moments the captain already ruled on keep their text (the
proposal writer refuses to overwrite ruled moments), which is
"approved text stands" rather than a divergence to fix.

`tests/test_display_respell.py`.
"""

from __future__ import annotations

#: Keys whose values are identity or reference, never quoted speech.
#: A respell inside one renames the thing rather than fixing a quote.
SKIP_KEYS = frozenset({
    "slug", "slugs", "id", "ids", "uuid",
    "anchor_phrase", "from_phrase",
    "source_file", "asset", "audio_path", "overlay_path", "path",
    "timeline_name", "name", "label", "sfx_id",
    "speaker", "speakers",
    "approval", "format", "version", "key", "kind", "status",
    "position", "block_position", "spine_block_position",
    "source_start", "source_end", "timeline_start_frame",
    "timeline_end_frame",
})


def respell_display(obj, corrections: list):
    """Respell every display string in `obj`. In place.

    Returns `{"replacements": n, "fields": [...]}`: how many whole-word
    substitutions the recorded corrections made, and the dotted field
    paths they landed on (truncated to the first 20). Empty corrections
    returns zeros and touches nothing.
    """
    from library.tools import transcript_corrections as _tc

    report: dict = {"replacements": 0, "fields": []}
    if not corrections:
        return report

    def _walk(node, key: str, location: str) -> None:
        if isinstance(node, dict):
            for child_key, value in list(node.items()):
                child_location = (f"{location}/{child_key}"
                                  if location else str(child_key))
                if (isinstance(value, str)
                        and str(child_key) not in SKIP_KEYS
                        and "/" not in value):
                    # A path wearing a heard word as a directory
                    # ("lucy_takes/") is a reference, not a quote:
                    # respelling it breaks the build, while skipping it
                    # at worst leaves one substitution the coherence
                    # scan still flags. Quotes do not carry slashes;
                    # paths always do.
                    new_text, count = _tc.apply_spelling(
                        value, corrections)
                    if count:
                        node[child_key] = new_text
                        report["replacements"] += count
                        if len(report["fields"]) < 20:
                            report["fields"].append(child_location)
                else:
                    _walk(value, str(child_key), child_location)
        elif isinstance(node, list):
            for index, value in enumerate(node):
                child_location = f"{location}/{index}"
                if (isinstance(value, str) and key not in SKIP_KEYS
                        and "/" not in value):
                    new_text, count = _tc.apply_spelling(
                        value, corrections)
                    if count:
                        node[index] = new_text
                        report["replacements"] += count
                        if len(report["fields"]) < 20:
                            report["fields"].append(child_location)
                else:
                    _walk(value, key, child_location)

    _walk(obj, "", "")
    return report


def respell_for_project(obj, project_folder: str):
    """Respell `obj` with the project's active corrections. In place.

    Returns `(report, corrections)`: the `respell_display` report and
    the corrections it applied. No project folder, or none recorded,
    returns zeros - regeneration without a correction is byte-identical.
    """
    empty: dict = {"replacements": 0, "fields": []}
    if not project_folder:
        return empty, []
    from library.tools import transcript_corrections as _tc

    try:
        corrections = _tc.spelling_corrections(project_folder)
    except Exception:
        return empty, []
    if not corrections:
        return empty, []
    return respell_display(obj, corrections), corrections


def apply_post_pass(obj, project_folder: str, where: str):
    """The one call every regen point makes. In place, guarded.

    Respells `obj`, says what moved on stderr, and never raises: a
    post-pass failure warns and keeps the unrespelled text, and the
    layer-coherence wording scan still flags what stands - a broken
    pass is loud rather than silent. Returns the report.
    """
    import sys

    empty: dict = {"replacements": 0, "fields": []}
    try:
        report, _ = respell_for_project(obj, project_folder)
    except Exception as exc:  # noqa: BLE001 - a post-pass never refuses
        print(f"WARNING: display respell skipped at {where} ({exc}); "
              f"unrespelled text stands and the coherence scan still "
              f"flags it.", file=sys.stderr)
        return empty
    if report["replacements"]:
        print(f"  display respell at {where}: "
              f"{report['replacements']} substitution(s) - "
              f"{', '.join(report['fields'][:5])}"
              f"{'...' if len(report['fields']) > 5 else ''}",
              file=sys.stderr)
    return report
