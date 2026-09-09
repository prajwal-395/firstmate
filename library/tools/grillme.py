"""grillme's deterministic half: what the project already answers.

The grillme skill (`.agents/skills/grillme/SKILL.md`) is an interactive
interview conducted WITH the captain, OUTSIDE any pipeline run. This
module is what it runs BEFORE it opens its mouth: coverage of each
topic off everything the project already has - the attached brief, the
context folder, the learned context - so it never asks something
already answered.

A topic is covered when any of its SIGNALS fires: a phrase in the
brief, a context file whose name or text speaks to it, an active
learning whose statement does. Signals are deliberately cheap
substring matches, not judgements: this module reports coverage, and
the interviewer (the skill, interactively) decides whether the covered
ground is ENOUGH. A covered topic the captain's one line barely touches
is still covered here and still worth one follow-up there - the skill
says so, and that is a question about depth, not a gap.

`GRILLME_TOPICS` is a coverage checklist, NOT a question list. The
questions are written per gap, per project, by the interviewer, from
the gap's `why_it_matters`. A fixed list would ask what the project
already answered; gaps ask what it does not know.

`tests/test_grillme.py`.
"""

from __future__ import annotations

import os
import re

#: topic -> what it needs and why the video fails without it. The `signals`
#: are substrings matched case-insensitively against the brief, every
#: context file's name and text, and every active learning's statement.
#: A signal firing means "spoken to", not "settled" - see the module
#: docstring.
GRILLME_TOPICS = {
    "music": {
        "about": "What the bed under the video sounds like.",
        "why_it_matters": (
            "Step 2.04 chooses the track the whole video sits on; without "
            "a direction it scores from the creative direction alone."),
        "signals": ("music", "sound philosophy", "soundtrack", "track",
                     "score", "song", "audio bed", "bed music"),
    },
    "series": {
        "about": "Which series this video belongs to, if any.",
        "why_it_matters": (
            "The channel brief names several series and the model has "
            "reached for the wrong one by name before (#258); an unnamed "
            "video needs the absence said out loud."),
        "signals": ("series", "belongs to", "weeknight", "4th wall",
                     "fourth wall"),
    },
    "pacing": {
        "about": "How the cut moves - hold lengths, cut density, energy.",
        "why_it_matters": (
            "mesh_spine times the spine from this; a mismatch here "
            "re-times every block downstream."),
        "signals": ("pac", "cutting rhythm", "hold", "energy", "tempo of "
                     "the cut", "breathe", "fast-cut", "slow"),
    },
    "grade": {
        "about": "How the picture looks - palette, contrast, warmth.",
        "why_it_matters": (
            "Step 5.01 grades from the declared look; without one the "
            "colourist decides alone and the captain meets it at the "
            "render."),
        "signals": ("grade", "palette", "colour", "color", "lut",
                     "warm", "contrast", "saturation", "cinematic"),
    },
    "captions": {
        "about": "How words on screen read - placement, typeface, voice.",
        "why_it_matters": (
            "Steps 4.01/4.05 plan and render every caption card; style "
            "settled late re-renders the whole layer."),
        "signals": ("caption", "subtitle", "lower third", "typeface",
                     "font", "on-screen text", "title card"),
    },
    "audience": {
        "about": "Who this is for and what it should do to them.",
        "why_it_matters": (
            "Creative direction aims the narrative at someone; an "
            "unaimed video aims at the channel average."),
        "signals": ("audience", "viewer", "for people who", "demographic",
                     "beginner", "home cook"),
    },
    "references": {
        "about": "What to look at - reference videos, images, links.",
        "why_it_matters": (
            "A look described in prose drifts; a look pointed at holds. "
            "The context folder exists to hold exactly these."),
        "signals": ("reference", "inspiration", "look at", "like this",
                     "http", "youtu", "vimeo", "moodboard", "mood board"),
    },
    "bookends": {
        "about": "How the video opens and closes - intro, outro, cards.",
        "why_it_matters": (
            "Bookends are declared per brand template and rendered per "
            "run; an undeclared one is invented late or missing."),
        "signals": ("intro", "outro", "bookend", "end card", "cold open",
                     "hook", "title sequence"),
    },
    "sound_design": {
        "about": "What the video sounds like beyond music - SFX, voice.",
        "why_it_matters": (
            "Step 4.04 plans every effect from this; silence here reads "
            "as 'no opinion' and the planner fills it."),
        "signals": ("sfx", "sound effect", "sound design", "foley",
                     "voiceover", "voice-over", "narration", "ambience",
                     "ambient"),
    },
    "delivery": {
        "about": "Where this plays - format, length, platform.",
        "why_it_matters": (
            "Length and shape decide the spine before the spine is "
            "meshed; a 90-second reel is not a 12-minute video cut "
            "short."),
        "signals": ("reel", "short", "format", "aspect", "9:16",
                     "16:9", "duration", "length", "platform", "youtube",
                     "instagram", "tiktok"),
    },
}


def _read_brief_text(project_folder: str) -> tuple:
    """`(attached_text, where)`. Unattached reads as no text - the gaps
    this leaves are real gaps, which is exactly what the interview is
    for."""
    try:
        from library.tools.brief_attachment import read_declaration
        attachment = read_declaration(project_folder)
    except Exception:  # noqa: BLE001 - no declaration, no brief
        return "", "no readable declaration"
    if not attachment.attached or not attachment.path:
        return "", attachment.reading
    path = attachment.path
    if not os.path.isabs(path):
        path = os.path.join(project_folder, path)
    try:
        with open(path, "r", encoding="utf-8") as handle:
            return handle.read(), os.path.abspath(path)
    except OSError:
        return "", f"declared but unreadable: {attachment.path}"


def _context_texts(project_folder: str) -> list:
    """`(name, text)` for every readable text file in context/."""
    from library.tools.project_context import context_dir, scan_context
    texts = []
    for entry in scan_context(project_folder):
        if entry["kind"] not in ("markdown", "text"):
            continue
        try:
            with open(entry["abspath"], "r", encoding="utf-8") as handle:
                texts.append((f"context/{entry['name']}",
                              handle.read()))
        except (OSError, UnicodeDecodeError):
            continue
    _ = context_dir  # the directory spelling stays owned by project_context
    return texts


def _learning_texts(project_folder: str) -> list:
    try:
        from library.tools.learned_context import _load
    except Exception:  # noqa: BLE001 - no learnings, no coverage from them
        return []
    try:
        learnings = _load(project_folder)
    except Exception:  # noqa: BLE001 - a broken store is grillme's
        # business only as a gap; never a crash
        return []
    return [(f"learning {l.get('id')}", str(l.get("statement", "")))
            for l in learnings
            if isinstance(l, dict) and l.get("status") == "active"
            and str(l.get("statement", "")).strip()]


def coverage(project_folder: str) -> dict:
    """Per topic: covered or not, and the evidence that decided it.

    Evidence is `(source, signal)` pairs - the file or learning and the
    phrase that fired - so the skill can quote what the project already
    said instead of asking it again."""
    brief_text, brief_where = _read_brief_text(project_folder)
    sources = []
    if brief_text.strip():
        sources.append((f"brief ({brief_where})", brief_text))
    sources.extend(_context_texts(project_folder))
    sources.extend(_learning_texts(project_folder))
    report = {}
    for topic, spec in GRILLME_TOPICS.items():
        evidence = []
        for source_name, text in sources:
            lowered = text.casefold()
            for signal in spec["signals"]:
                if signal.casefold() in lowered:
                    evidence.append({
                        "source": source_name,
                        "signal": signal,
                    })
                    break
            if source_name.startswith("context/"):
                stem = os.path.splitext(
                    os.path.basename(source_name))[0]
                if topic in stem.casefold().replace("-", " ").replace(
                        "_", " "):
                    evidence.append({
                        "source": source_name,
                        "signal": f"filename names {topic!r}",
                    })
        # Learnings speak in statements, not keywords: a settled
        # decision whose topic word never appears still covers it when
        # the statement is ABOUT it. The cheap version of that reading:
        # a learning whose statement shares a content word with the
        # topic's `about` line.
        report[topic] = {
            "covered": bool(evidence),
            "evidence": evidence,
            "about": spec["about"],
            "why_it_matters": spec["why_it_matters"],
        }
    # The learning half, read the other way: a learning whose statement
    # covers a topic even when no keyword fired. Match on the topic's
    # own content words (stop-words excluded) so "Pacing: hold food
    # close-ups" covers pacing without the word "pacing" having to be
    # a signal.
    stop = {"the", "a", "an", "what", "how", "this", "that", "and",
            "for", "its", "like", "per", "run", "from", "with"}
    for topic, row in report.items():
        if row["covered"]:
            continue
        about_words = {w.strip("-,.") for w in
                       GRILLME_TOPICS[topic]["about"].casefold().split()}
        about_words -= stop
        for source_name, text in sources:
            if not source_name.startswith("learning "):
                continue
            words = {w.strip("-,.:") for w in text.casefold().split()}
            if about_words & words and topic in text.casefold():
                row["covered"] = True
                row["evidence"].append({
                    "source": source_name,
                    "signal": f"settled in the learning's own words",
                })
                break
    return report


def gaps(project_folder: str) -> list:
    """The topics nothing answers, each with why it matters. This is
    what the skill asks about - and ONLY this. A topic the project
    answers never appears here, so a question already answered is never
    asked."""
    report = coverage(project_folder)
    return [
        {"topic": topic,
         "about": row["about"],
         "why_it_matters": row["why_it_matters"]}
        for topic, row in report.items()
        if not row["covered"]
    ]


def known_list(project_folder: str) -> list:
    """What the skill states up front, so the captain sees what it read
    before it asks anything. Each line names the topic and where the
    answer came from."""
    report = coverage(project_folder)
    lines = []
    for topic, row in report.items():
        if not row["covered"]:
            continue
        sources = ", ".join(sorted({e["source"] for e in row["evidence"]}))
        lines.append(f"- {topic}: answered in {sources}")
    return lines
