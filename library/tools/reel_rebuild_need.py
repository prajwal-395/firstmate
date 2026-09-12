"""Whether a reel needs a Resolve pass at all, answered from measurements.

The cost this closes
--------------------
A build stages EVERY reel it is asked for and pays the full Resolve
placement pass for each one, whether or not anything about that reel
changed.  What that pass costs is measured, in the tree, from the
composed-edit spike (`docs/RULE_EVIDENCE.md`, "what it costs"): a
rebuild of one reel is 19.4-67.1 s of Resolve time on that reel, of
which the Fusion comp pass is 17.0-63.7 s - and the comp pass is FIXED
overhead, not per-comp work, so *"a pass over a timeline whose every
comp was already banked and unchanged still took 25.9-73.4 s"*.

That settles which instrument saves it.  `composed_edit` composes a
sub-reel edit out of the verbs Resolve has, and it cannot avoid the
comp pass - its own measurement is *"on this reel the composed path is
not faster"*.  The only thing that avoids a per-reel fixed cost is not
paying it: **a reel nothing changed about is not placed again.**

What it came to, measured 2026-09-12 end to end against the running
Resolve on `Podcast (field test)`, three real reels (13, 23, 26) built
into probe containers off an APFS clone of the captain's project:

===========================================  =========  ==============
build                                          seconds  Resolve passes
===========================================  =========  ==============
three reels, none of them built before          405.82               3
three reels, one reel's declaration changed     110.40               1
three reels, nothing changed                     45.23               0
===========================================  =========  ==============

The per-reel Resolve pass on those three was 130 s, 151 s and 34 s
against 38 s, 17 s and 11 s of derivation - so the derivation the skip
still pays is a tenth of what it avoids.  The middle row is the case
the profile priced at *"~7 min per five-reel build"*: 3.7x here, and
what is saved scales with the reels that did not change.

**A rebuild is a control arm only with the entry timeline
controlled.**  While measuring this, three consecutive builds of Reel
23, each entered on a delivery-shaped reel, rendered byte-identical
(0.000000 mean, 180/180 identical frames, head and tail).  An earlier
pair built without that control differed on 63% of subpixels, every
picture Pan/Tilt exactly two apart
(`docs/READING_A_TRANSFORM.md`, which also
carries the four-way reading table this module's `carried_digest_live`
exists for).  Issue 999 and `transform_drift` own the positioning
question; leaving an unchanged reel alone is a PROTECTION as well as a
saving, because re-placing a reel the captain already approved is the
only way any such scaling reaches it.

Why this module and not a flag
------------------------------
`only=` already lets an operator name the reels to build, and that is
the same saving bought with a GUESS.  The operator does not know which
reels a changed declaration reaches - the whole reason
`reel_divergence` exists is that "every reel inherits it" was true of
the engine and false of the project.  So the decision is made from
state, here, and the decision is FAIL-CLOSED: every answer that is not
a positive match on both halves is REBUILD.

The two halves
--------------
A reel may be left alone exactly when both are true:

1. **The derivation is identical.**  Everything the build derived for
   this reel before it touched Resolve - its ranges and placements, its
   cards, its caption segments and their rendered bytes, its overlay
   placements, its explainer and semantic segments, its motion plan,
   its ending and the project's look - digests to what the build that
   placed the live timeline recorded.  Plus the engine code and the
   project-WIDE declarations, which are digested wholesale (below);
   the per-reel pin stores are not, which is what lets a pin on one
   reel cost one reel.
2. **The live timeline is still the one that build placed.**  The
   carried digest of the timeline as it reads now equals the one
   recorded straight after its promotion.  A reel that drifted - a hand
   edit, a reset, a composed edit - is rebuilt, because a rebuild
   restores the engine's own framing and a skip preserves the drift
   (`reel_divergence`, 2026-09-12: *"A reset is not a hand edit"*).

Both halves cost almost nothing.  Half 1 is CPU the build already
spends - the derivation runs either way, and the profile measured it as
seconds.  Half 2 is one `snapshot_timeline` per reel, MEASURED at
0.043-0.153 s on the captain's eight reels, and the divergence survey
already takes exactly those snapshots on every build.

Over-covering is the safe direction, and it is deliberate
--------------------------------------------------------
A digest that misses an input skips a reel that needed rebuilding.  A
digest that covers too much rebuilds a reel that did not.  The first is
a wrong reel; the second is a minute.  So:

* :data:`ENGINE_CODE_TREES` digests whole source trees, RECURSIVELY,
  rather than a closure computed from imports.  An import closure would
  miss every step this path reaches through the operation registry by
  NAME (`operations.get("subtitles.plan")`) - the under-cover trap in
  the shape that looks most rigorous.
* :data:`DECLARATION_SOURCES` digests each project-wide declaration
  file whole rather than the fields a reel happens to read.  A field
  added to `project.yaml` next week is covered the day it is added.
* An `external/` declaration that :data:`PER_REEL_DECLARATION_STEMS`
  does not claim is folded into the project-wide half, so a new
  declaration over-covers from the day it lands rather than being
  silently ignored.

The consequence is stated plainly: **during active engine work every
build invalidates every reel, and that is correct.**  The saving lands
where the profile said it does - a build of several reels where one
reel's plan, pin or declaration changed and the engine did not.

`built_with` was too narrow to be this answer
---------------------------------------------
`plan_provenance.REEL_BUILD_CODE_FILES` names three paths, and this
module does not reuse it: measured 2026-09-12, those three cover 3 of
the 131 source files reachable from the reel build by import alone, so
a change to `reel_ending.py`, `reel_look.py`, `tight_box.py` or
`overlay_placement.py` leaves the stamp identical.  That is reported
rather than repaired here: widening `built_with` changes what a stamp
already in the captain's version record means, and that is not this
module's call.  :func:`engine_code_digest` is this module's own reading
and it covers the trees.

`tests/test_reel_rebuild_need.py`.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence

from library.tools.stable_json import dumps_stable

# ── What a reel's identity is computed over ──────────────────────────

ENGINE_CODE_TREES = (
    "library/tools",
    "library/schemas",
    "library/steps/step_7_01_build_reels",
    "library/steps/step_7_02_verify_reels",
    "library/steps/step_4_01_plan_subtitles",
    "library/steps/step_4_05_render_subtitles",
    "library/steps/step_4_06_render_motion_graphics",
    "library/steps/step_1_02_catalog_footage",
)
"""Source trees whose content decides what a reel build places.

Digested RECURSIVELY and WHOLE.  `library/tools` carries more than the
reel path needs, and that over-coverage is the point: a tree is a
boundary a reader can check, while a per-module list is a claim about
reachability that goes stale the first time a module gains an import.

The step directories are NOT over-coverage - each is reached: 7.01 and
7.02 are the build's own nodes, 4.01 and 4.05 are the caption path
`reel_subtitle_segments` drives through the operation registry, 4.06 is
the motion-graphics renderer, and 1.02 is where `timeline_ingest` gets
`extract_metadata`. `test_reel_rebuild_need.py` walks the build's real
import closure and FAILS on a module no tree here contains, so the day
this path reaches a new one the list is corrected rather than quietly
under-covering.
"""

CODE_SUFFIXES = (".py",)
"""What counts as engine source.  Presets, templates and macros are
project declarations, not engine code, and are digested as such."""

DECLARATION_SOURCES = (
    "project.yaml",
    "brand_assets",
    "compositions",
)
"""PROJECT-WIDE declarations, relative to the project folder.

Each is digested WHOLE - a file by its bytes, a directory recursively -
because each is a decision about the project rather than about one
reel, and changing one legitimately means every reel may be different.
`project.yaml` is the sharpest case: `style.subtitle.caption_row`
decides where every caption card is PLACED, and the placement happens
inside `build_reel_timeline` rather than in anything the derivation
digest can see.  So it has to be here, and a project-wide change has to
cost a full round.
"""

PER_REEL_DECLARATION_STEMS = {
    "captain_edits": (
        "closer redraws and span_retime trims, keyed by reel - they "
        "move the RANGES, and the ranges plus the placements derived "
        "from them are in the derivation digest"),
    "caption_timing": (
        "caption-only timing pins, keyed by reel - they retime the "
        "rendered segments, and each segment's reel_start_frame is in "
        "the derivation digest"),
    "reel_ending": (
        "where a reel ends and what draws over its tail, keyed by reel "
        "- the resolved `ending` is passed to the derivation digest "
        "outright"),
}
"""Declarations under `external/` whose effect on ONE reel is already
carried by that reel's derivation digest.

This is what makes the saving reach the case the profile priced - *"a
five-reel build where one reel changed"*.  In practice the thing that
changes for one reel is a captain pin, and every pin store here is
keyed by reel: folding the whole file into the project-wide half would
mean a pin on Reel 13 rebuilding Reel 23 as well, which is the saving
gone in exactly the case it exists for.

**An `external/` declaration NOT listed here is folded into the
project-wide half**, so a new declaration file over-covers from the day
it lands rather than being silently ignored.  That is the fail-closed
direction and it needs no enumeration to stay true: the classification
happens at build time against what is really on disk.  Adding an entry
here is a claim that the derivation carries its per-reel effect, and
the value is where that claim is written down.

`transcript_corrections` (strikes and insistences) is not here because
it does not live under `external/`, and it does not need to be: the
build resolves it to `moment_cuts` and `moment_insisted` per reel and
those go into the derivation digest directly.
"""

DECLARATION_SKIP_NAMES = frozenset({
    ".DS_Store", "__pycache__", ".git",
})


# ── Digests ──────────────────────────────────────────────────────────

def _digest_file(digest: "hashlib._Hash", label: str, path: Path) -> bool:
    try:
        data = path.read_bytes()
    except OSError:
        return False
    digest.update(label.encode("utf-8"))
    digest.update(b"\0")
    digest.update(data)
    return True


def _digest_tree(digest: "hashlib._Hash", root: Path, base: Path,
                 suffixes: Optional[Sequence[str]] = None) -> int:
    """Fold every file under `root` into `digest`.  Returns the count."""
    found = 0
    if root.is_file():
        if _digest_file(digest, str(root.relative_to(base)), root):
            found += 1
        return found
    if not root.is_dir():
        return 0
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        if any(part in DECLARATION_SKIP_NAMES for part in path.parts):
            continue
        if suffixes is not None and path.suffix not in suffixes:
            continue
        if _digest_file(digest, str(path.relative_to(base)), path):
            found += 1
    return found


def engine_code_digest(repo_root=None) -> Optional[str]:
    """A digest of the engine source a reel build is decided by.

    `None` when no tree is present - a scratch root with no engine.  A
    caller treats that as "no identity to compare" and REBUILDS, never
    as one revision: a hollow digest that matches is the worst possible
    answer here.
    """
    base = Path(repo_root) if repo_root is not None \
        else Path(__file__).resolve().parents[2]
    digest = hashlib.sha256()
    found = 0
    for rel in ENGINE_CODE_TREES:
        found += _digest_tree(digest, base / rel, base, CODE_SUFFIXES)
    return digest.hexdigest() if found else None


def project_wide_digest(project_folder, brand_template=None,
                        asset_hashes=None) -> Optional[str]:
    """A digest of every declaration that speaks for the whole project.

    `None` when the project folder holds none of them, which a caller
    treats as REBUILD for the reason `engine_code_digest` states.

    `brand_template` is the project's resolved template content and
    `asset_hashes` the digests of the assets a declaration names by
    absolute path (`reel_divergence.asset_digests`).  Neither lives
    under the project folder, so neither would be covered by walking
    it - and both decide what reaches a reel.
    """
    base = Path(project_folder)
    digest = hashlib.sha256()
    found = 0
    for rel in DECLARATION_SOURCES:
        found += _digest_tree(digest, base / rel, base, None)
    # Every `external/` declaration this module does not claim is
    # per-reel. Unknown means project-wide, which over-covers.
    external = base / "external"
    if external.is_dir():
        for path in sorted(external.glob("*.json")):
            if path.stem in PER_REEL_DECLARATION_STEMS:
                continue
            if _digest_file(digest, str(path.relative_to(base)), path):
                found += 1
    if brand_template is not None:
        digest.update(b"brand_template\0")
        digest.update(dumps_stable(brand_template).encode("utf-8"))
        found += 1
    if asset_hashes is not None:
        digest.update(b"asset_hashes\0")
        digest.update(dumps_stable(dict(asset_hashes)).encode("utf-8"))
        found += 1
    return digest.hexdigest() if found else None


def _rounded(value, places: int = 6):
    """Floats to a fixed precision, everything else verbatim.

    Resolve reports transforms as floats and a digest must not turn a
    repr difference into a content change.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, float):
        return round(value, places)
    if isinstance(value, Mapping):
        return {str(k): _rounded(v, places) for k, v in sorted(
            value.items(), key=lambda kv: str(kv[0]))}
    if isinstance(value, (list, tuple)):
        return [_rounded(v, places) for v in value]
    return value


CARRIED_CLIP_KEYS = (
    "track_type", "track_index", "track_name", "speaker",
    "source_file", "source_in_frame", "source_out_frame", "source_frames",
    "timeline_start", "timeline_end", "name", "transform",
)
"""What the carried digest reads off each clip.

`resolve_item_id` is deliberately OUT: it identifies the object, not the
picture, and a re-placed item carrying identical content gets a new one
(`composed_edit`: *"a re-placed item is a new object"*).  The timeline's
own NAME is out for the same reason - promotion renames a staging to its
final name without touching a frame.  Markers are not in the snapshot at
all, which is what stops a captain's note forcing a rebuild of a reel
they have not asked to change.
"""


def carried_digest(snapshot) -> Optional[str]:
    """A digest of what a live reel timeline CARRIES, or None.

    `None` for anything that will not read as a snapshot - the caller
    rebuilds.  An unreadable reel is never a matching one.
    """
    if snapshot is None:
        return None
    try:
        clips = list(getattr(snapshot, "clips", ()) or ())
        body = {
            "fps": _rounded(float(getattr(snapshot, "fps", 0.0))),
            "width": int(getattr(snapshot, "width", 0) or 0),
            "height": int(getattr(snapshot, "height", 0) or 0),
            "start_frame": int(getattr(snapshot, "start_frame", 0) or 0),
            "end_frame": int(getattr(snapshot, "end_frame", 0) or 0),
            "clips": [
                {key: _rounded(getattr(clip, key, None))
                 for key in CARRIED_CLIP_KEYS}
                for clip in clips
            ],
        }
    except Exception:                                     # noqa: BLE001
        return None
    return hashlib.sha256(
        dumps_stable(body).encode("utf-8")).hexdigest()


def carried_digest_live(project, timeline) -> Optional[str]:
    """`carried_digest` of a live reel, read with THAT REEL CURRENT.

    A clip's transform does not read back the same way twice.  What
    Resolve returns depends on which timeline is CURRENT at the moment
    of the read, and the reel being read is not always that timeline.
    Measured 2026-09-12 on `Podcast (field test)`, three promoted reels
    read four times with nothing touched in between - only the current
    timeline changed:

        current timeline        Reel 13    Reel 23    Reel 26
        Reel 13   (1080x1920)   227d3096   6d9b3608   e6c2f02a
        Reel 23   (1080x1920)   2dc8a569   c3492434   e6c2f02a
        Reel 26   (1080x1920)   2dc8a569   6d9b3608   e8b42186
        the master (3840x2160)  bfc63ef8   15d616bf   a61fbe42

    Four readings of one untouched reel, four digests.  The master's
    row is the entry-unit conversion AGENTS.md 5 documents; the three
    reel rows are all 1080x1920 and still disagree.

    So a digest is only comparable with another taken the SAME way, and
    the one way available at both ends is the reel's own: at promotion
    the reel is there to be made current, and at the next build it is
    too.  The self-read is STABLE - measured over four rounds, each
    preceded by a different current timeline, all three reels returned
    the same digest every time.

    Without this the two ends disagree by construction: promotion
    closed every record with the LAST promoted reel current, and the
    next build compared them with the ENTRY timeline current, so every
    reel but the coincidentally-matching one read as drifted and was
    placed again.  That is the fail-closed direction - a needless
    Resolve pass, never a wrong skip - but it costs exactly the saving
    the decision exists for.

    Moving the cursor is a WRITE - it moves state every other writer
    is reading - so the read runs inside
    `resolve_lock.cursor_excursion`, which moves the cursor and puts it
    back through the guarded setter, refuses outside an instance lease,
    and READS THE CURSOR BACK.  The read-back is not ceremony here: a
    `SetCurrentTimeline` that silently did not land would digest a
    DIFFERENT timeline, and a digest that then happened to match the
    record is a WRONG SKIP - the one direction this whole module is
    built to avoid.  `UnguardedPlacementError` and `ResolveRaceError`
    therefore PROPAGATE; only a failure to read the timeline returns
    `None`, which the caller treats as a rebuild.

    Both call sites are already inside `@under_lease` sections
    (`rebuild_reels_in_project`, `promote_staged_reels`), so this takes
    no lease of its own and holds the instance no longer than the build
    already does.

    The caller's current timeline is RESTORED.
    """
    if project is None or timeline is None:
        return None
    from library.tools.resolve_lock import cursor_excursion
    from library.tools.timeline_ingest import snapshot_timeline
    with cursor_excursion(project, timeline, "carried read-back"):
        try:
            return carried_digest(snapshot_timeline(timeline,
                                                    project.GetName()))
        except Exception:                                 # noqa: BLE001
            return None


# ── The derivation half ──────────────────────────────────────────────

def _file_digest(path) -> str:
    """A digest of a rendered artefact's bytes, or a marker for absence.

    An artefact the derivation NAMES and disk does not have digests to
    `missing:<name>`, which cannot equal a recorded digest of real
    bytes - so a caption or card that vanished off disk rebuilds the
    reel rather than reading as unchanged.
    """
    try:
        return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError:
        return f"missing:{os.path.basename(str(path))}"


def _placement_rows(placements_list) -> list:
    """The placed spans, as plain comparable data."""
    rows = []
    for place in placements_list or ():
        clip = place.get("clip") if isinstance(place, Mapping) else None
        rows.append({
            "source_file": getattr(clip, "source_file", None),
            "track_type": getattr(clip, "track_type", None),
            "speaker": getattr(clip, "speaker", None),
            "source_in": _rounded(getattr(clip, "source_in", None)),
            "source_out": _rounded(getattr(clip, "source_out", None)),
            "reel_start": _rounded(
                place.get("reel_start") if isinstance(place, Mapping)
                else None),
            "reel_end": _rounded(
                place.get("reel_end") if isinstance(place, Mapping)
                else None),
            "source_in_seconds": _rounded(
                place.get("source_in") if isinstance(place, Mapping)
                else None),
            "source_out_seconds": _rounded(
                place.get("source_out") if isinstance(place, Mapping)
                else None),
        })
    return rows


def _card_rows(cards) -> list:
    rows = []
    for card in cards or ():
        path = getattr(card, "render_path", "") or getattr(
            card, "path", "") or ""
        rows.append({
            "placement": str(getattr(card, "placement", "")),
            "render_name": str(getattr(card, "render_name", "")),
            "reel_start_frame": getattr(card, "reel_start_frame", None),
            "duration_frames": getattr(card, "duration_frames", None),
            "bytes": _file_digest(path) if path else "no-path",
        })
    return rows


def _segment_rows(segments) -> list:
    """Rendered overlay segments - captions, explainer, semantic.

    Each contributes its recorded placement and the DIGEST OF ITS
    BYTES, so a re-render that changed pixels changes the derivation
    even where every number around it agrees.
    """
    rows = []
    for segment in segments or ():
        if not isinstance(segment, Mapping):
            rows.append({"unreadable": repr(segment)[:120]})
            continue
        path = segment.get("file") or segment.get("path") or ""
        rows.append({
            "segment_id": segment.get("segment_id"),
            "source_in_frame": segment.get("source_in_frame"),
            "source_out_frame": segment.get("source_out_frame"),
            "reel_start_frame": segment.get("reel_start_frame"),
            "frames": segment.get("frames"),
            "width": segment.get("width"),
            "height": segment.get("height"),
            "bytes": _file_digest(path) if path else "no-path",
        })
    return rows


def _as_data(entry):
    """A planned placement as plain data.

    `transition_overlay` hands these over as dataclasses carrying
    `as_dict`; a caller that has already flattened them hands dicts.
    Both must digest the same way or a refactor of the planner's
    return type would read as a changed reel.
    """
    render = getattr(entry, "as_dict", None)
    return render() if callable(render) else entry


def derivation_digest(
    *,
    reel_number,
    engine_code: Optional[str],
    project_wide: Optional[str],
    plan_content_hash: str,
    transcript_hash: str,
    master_digest: Optional[str],
    ranges,
    placements_list,
    cards,
    caption_segments,
    explainer_segments,
    semantic_segments,
    overlay_placements,
    motion_record,
    ending,
    look,
    grade_cdl,
    grade_look,
    power_grade,
    asset_hashes: Optional[Mapping] = None,
    extra: Optional[Mapping] = None,
    card_row_role: Optional[str] = None,
) -> Optional[str]:
    """A digest of everything this reel's Resolve pass would place.

    `None` when either wholesale half is missing (`engine_code`,
    `project_wide`) - the caller REBUILDS.  There is no partial answer:
    a derivation digest computed without knowing the engine is not a
    weaker match, it is not a match.

    `card_row_role` travels only where cards do: a project that
    declares no cards digests byte-identically with or without it (the
    same rule the lower-thirds `extra` keeps at its call site), while
    a reel whose closing card moves rows reads as changed - which is
    what a stale row IS.
    """
    if not engine_code or not project_wide:
        return None
    body = {
        "reel_number": int(reel_number),
        "engine_code": engine_code,
        "project_wide": project_wide,
        "plan": plan_content_hash,
        "transcript": transcript_hash,
        "master": master_digest,
        "ranges": _rounded(list(ranges or ())),
        "placements": _placement_rows(placements_list),
        "cards": _card_rows(cards),
        "captions": _segment_rows(caption_segments),
        "explainer": _segment_rows(explainer_segments),
        "semantic": _segment_rows(semantic_segments),
        "overlays": _rounded(
            [_as_data(entry) for entry in (overlay_placements or ())]),
        "motion": _rounded(dict(motion_record or {})),
        "ending": _rounded(dict(ending or {})),
        "look": _rounded(dict(look or {})),
        "grade_cdl": _rounded(dict(grade_cdl or {})),
        "grade_look": _rounded(dict(grade_look or {})),
        "power_grade": _rounded(dict(power_grade or {})),
        "assets": _rounded(dict(asset_hashes or {})),
        "extra": _rounded(dict(extra or {})),
        **({"card_row_role": card_row_role} if cards else {}),
    }
    return hashlib.sha256(
        dumps_stable(body).encode("utf-8")).hexdigest()


# ── The decision ─────────────────────────────────────────────────────

REBUILD = "rebuild"
LEAVE_ALONE = "leave_alone"


@dataclass(frozen=True)
class Decision:
    """What to do with one reel, and WHY - always both."""

    reel: str
    action: str
    reason: str
    derivation: Optional[str] = None
    carried: Optional[str] = None
    recorded: Mapping = field(default_factory=dict)

    @property
    def leave_alone(self) -> bool:
        return self.action == LEAVE_ALONE

    def as_dict(self) -> dict:
        return {
            "reel": self.reel,
            "action": self.action,
            "reason": self.reason,
            "derivation_digest": self.derivation,
            "carried_digest": self.carried,
            "recorded": dict(self.recorded or {}),
        }


def decide(reel: str, fresh_derivation: Optional[str],
           live_carried: Optional[str],
           recorded: Optional[Mapping]) -> Decision:
    """Whether this reel needs a Resolve pass.  FAIL-CLOSED.

    Six ways to answer REBUILD and one to answer LEAVE_ALONE, and the
    reason is always carried: an operator reading a build log must be
    able to see WHICH half disagreed, because a skip whose grounds are
    invisible is indistinguishable from a reel silently dropped.
    """
    recorded = dict(recorded or {})
    if fresh_derivation is None:
        return Decision(reel, REBUILD,
                        "this build could not digest its own derivation "
                        "(no engine or no project declarations read) - "
                        "an unknown derivation is never a matching one",
                        None, live_carried, recorded)
    if not recorded:
        return Decision(reel, REBUILD,
                        "no build signature on record for this reel - "
                        "nothing to compare, so it is placed",
                        fresh_derivation, live_carried, recorded)
    if not recorded.get("derivation"):
        return Decision(reel, REBUILD,
                        "the recorded signature carries no derivation "
                        "digest (built before this was recorded)",
                        fresh_derivation, live_carried, recorded)
    if not recorded.get("carried"):
        return Decision(reel, REBUILD,
                        "the recorded signature carries no carried "
                        "digest - what was placed was never read back, "
                        "so drift cannot be ruled out",
                        fresh_derivation, live_carried, recorded)
    if live_carried is None:
        return Decision(reel, REBUILD,
                        "the live timeline could not be read - an "
                        "unread reel is never an unchanged one",
                        fresh_derivation, None, recorded)
    if recorded.get("derivation") != fresh_derivation:
        return Decision(reel, REBUILD,
                        "the derivation changed since the build that "
                        "placed this timeline",
                        fresh_derivation, live_carried, recorded)
    if recorded.get("carried") != live_carried:
        return Decision(reel, REBUILD,
                        "the live timeline is not the one that build "
                        "placed - it drifted, and a rebuild restores "
                        "the engine's own framing where a skip would "
                        "preserve the drift",
                        fresh_derivation, live_carried, recorded)
    return Decision(reel, LEAVE_ALONE,
                    "derivation and carried timeline both match the "
                    "recorded build - placing it again would produce "
                    "the same frames",
                    fresh_derivation, live_carried, recorded)


def render_decisions(decisions: Sequence[Decision]) -> str:
    """The table a build prints before it places anything."""
    if not decisions:
        return "No reels considered."
    lines = ["REBUILD NEED (per reel, computed from state):"]
    for decision in decisions:
        mark = "leave alone" if decision.leave_alone else "REBUILD    "
        lines.append(f"  {mark}  {decision.reel}")
        lines.append(f"                {decision.reason}")
    left = sum(1 for d in decisions if d.leave_alone)
    lines.append(
        f"  {left} of {len(decisions)} reel(s) need no Resolve pass.")
    return "\n".join(lines)


def signature_for_record(derivation: Optional[str]) -> dict:
    """The record a build writes, with the carried half still open.

    The carried digest cannot be taken at build time: the build places
    a STAGING container and promotion renames it, so the only moment a
    reel's carried state means "what is approved" is after the
    promotion.  `promote_staged_reels` closes the record there.
    """
    return {"derivation": derivation or "", "carried": ""}


__all__ = [
    "CARRIED_CLIP_KEYS",
    "CODE_SUFFIXES",
    "DECLARATION_SOURCES",
    "Decision",
    "PER_REEL_DECLARATION_STEMS",
    "ENGINE_CODE_TREES",
    "LEAVE_ALONE",
    "REBUILD",
    "carried_digest",
    "carried_digest_live",
    "decide",
    "derivation_digest",
    "engine_code_digest",
    "project_wide_digest",
    "render_decisions",
    "signature_for_record",
]
