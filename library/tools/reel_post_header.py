"""A social-post header above a vertical reel's picture. One enumeration.

The captain, 2026-09-25: "for all of the videos create a graphic that
makes it like a twitter post by adding something like this with the
profile picture, the blue check, and a text hook regarding the video in
the black space above the video" - and, on the identity: "it should
include the lucie logo as well as @luciecontent handle and the blue check
mark with the hook text as well".

Two halves, and neither lives in engine code:

* **The account** - avatar, display name, handle, verified, and every
  size and position the header draws at - is ARTWORK the project owns
  (AGENTS.md 14), declared at ``effect.post_header`` in its
  ``project.yaml``.  A project that declares none gets no header and
  the build is the build it was before this module.
* **The hook** is one line written FOR THAT REEL, from its own words, by
  the model - never a template and never a summary the engine composes
  (AGENTS.md 10.5).  It is stored per reel in
  ``external/reel_post_header.json`` under the reel's number, with the
  basis it was written from.  A declared header on a reel with no hook
  draws NOTHING and says so: a header with an invented or empty hook is
  a post nobody wrote.

How it reaches the picture: ``remotion-subtitles``' ``PostHeader``
composition is rendered ONCE per reel as a transparent still, carried as
a looped ProRes 4444 movie over the reel's picture runs - the TV frame's
own carriage (``reel_look.frame_overlay_segments``), because a still
cannot be placed for an arbitrary length through Resolve's API - and
placed full canvas on its own ``post_header`` row.

Where it sits is MEASURED after the render, not asserted: the drawn ink
is read off the still and checked against every platform's UI band
(``platform_safe_zones.intrusions``) and against the picture window.
Those are REPORTED on the run, never refused - the zones are a guide the
captain holds the reel up against, and a header he placed inside one
knowingly is his call.
"""

from __future__ import annotations

import base64
import hashlib
import json
import mimetypes
import os
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass, field

from library.tools.timeline_layout import POST_HEADER_NAME

DECLARATION_KEY = "post_header"

#: Everything the declaration may carry.  Checked on read: a key nothing
#: reads is a look the editor believes shipped.
DECLARATION_KEYS = (
    "avatar", "avatar_shape", "avatar_background", "name", "handle",
    "verified", "top", "name_size", "handle_size", "hook_size", "font",
    "reason",
)

#: Required, because each one is taste or identity and the engine
#: supplies neither (AGENTS.md 10.5, 14).
REQUIRED_KEYS = ("avatar", "name", "handle", "verified", "top",
                 "name_size", "handle_size", "hook_size")

AVATAR_SHAPES = ("square", "round")

#: Where the per-reel hooks live, beside the project's other per-reel
#: declarations (``reel_cta.json``, ``reel_ending.json``).
HOOKS_FILE = "reel_post_header.json"

COMPOSITION = "PostHeader"
RENDER_TIMEOUT_SECONDS = 300

# ── What kind of nothing a reel got ──────────────────────────────────
NOT_DECLARED = "not_declared"
NO_HOOK_WRITTEN = "no_hook_written"
HEADER_PLACED = "header_placed"


class PostHeaderError(ValueError):
    """A declaration or a hook that cannot be read as written - RAISED."""


@dataclass
class HeaderPlan:
    """One reel's header, including the empty case and what it measured."""

    reel_name: str
    reel_number: int | None
    basis: str = NOT_DECLARED
    hook: str = ""
    hook_basis: str = ""
    still_path: str = ""
    ink_box: tuple[int, int, int, int] | None = None
    intrusions: list[dict] = field(default_factory=list)
    over_picture: tuple[int, int, int, int] | None = None
    segments: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "reel": self.reel_name,
            "number": self.reel_number,
            "basis": self.basis,
            "hook": self.hook,
            "hook_basis": self.hook_basis,
            "still_path": self.still_path,
            "ink_box": list(self.ink_box) if self.ink_box else None,
            "zone_intrusions": [dict(i) for i in self.intrusions],
            "over_picture": (list(self.over_picture)
                             if self.over_picture else None),
            "segments": [{"overlay_path": s.get("overlay_path"),
                          "timeline_start": s.get("timeline_start"),
                          "total_frames": s.get("total_frames")}
                         for s in self.segments],
        }


# ── The declaration ──────────────────────────────────────────────────

def project_declaration(project_folder: str | None) -> dict | None:
    """``effect.post_header`` off the project's yaml, normalised, or None.

    Anything malformed RAISES naming the key, the same choice
    ``speaker_identity.declared_speakers`` makes.
    """
    if not project_folder:
        return None
    path = os.path.join(project_folder, "project.yaml")
    if not os.path.isfile(path):
        return None
    import yaml

    with open(path, "r", encoding="utf-8") as handle:
        config = yaml.safe_load(handle) or {}
    raw = (config.get("effect") or {}).get(DECLARATION_KEY)
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise PostHeaderError(
            f"effect.{DECLARATION_KEY} must be a mapping, got "
            f"{type(raw).__name__}")
    unknown = sorted(set(raw) - set(DECLARATION_KEYS))
    if unknown:
        raise PostHeaderError(
            f"effect.{DECLARATION_KEY} declares {unknown}, which nothing "
            f"reads. It takes {list(DECLARATION_KEYS)}.")
    missing = [k for k in REQUIRED_KEYS if raw.get(k) in (None, "")]
    if missing:
        raise PostHeaderError(
            f"effect.{DECLARATION_KEY} declares no {missing}. The account "
            f"and every size are the project's; the engine supplies none "
            f"(AGENTS.md 10.5, 14).")

    avatar = str(raw["avatar"])
    if not os.path.isabs(avatar):
        avatar = os.path.join(project_folder, avatar)
    if not os.path.isfile(avatar):
        raise PostHeaderError(
            f"effect.{DECLARATION_KEY}.avatar is {raw['avatar']!r}, which "
            f"is not a file")
    shape = str(raw.get("avatar_shape") or "square").strip().lower()
    if shape not in AVATAR_SHAPES:
        raise PostHeaderError(
            f"effect.{DECLARATION_KEY}.avatar_shape is {shape!r}; it takes "
            f"{list(AVATAR_SHAPES)}")
    try:
        top = float(raw["top"])
    except (TypeError, ValueError):
        raise PostHeaderError(
            f"effect.{DECLARATION_KEY}.top must be a fraction of the frame "
            f"height, got {raw['top']!r}") from None
    if not 0.0 <= top < 1.0:
        raise PostHeaderError(
            f"effect.{DECLARATION_KEY}.top is {top}; it is a fraction of "
            f"the frame height, 0 <= top < 1")
    sizes = {}
    for key in ("name_size", "handle_size", "hook_size"):
        try:
            sizes[key] = int(raw[key])
        except (TypeError, ValueError):
            raise PostHeaderError(
                f"effect.{DECLARATION_KEY}.{key} must be pixels, got "
                f"{raw[key]!r}") from None
        if sizes[key] <= 0:
            raise PostHeaderError(
                f"effect.{DECLARATION_KEY}.{key} must be positive")
    return {
        "avatar": avatar,
        "avatar_shape": shape,
        "avatar_background": str(raw.get("avatar_background")
                                 or "transparent"),
        "name": str(raw["name"]).strip(),
        "handle": str(raw["handle"]).strip().lstrip("@"),
        "verified": bool(raw["verified"]),
        "top": top,
        "font": str(raw.get("font") or "").strip(),
        **sizes,
    }


# ── The hooks ────────────────────────────────────────────────────────

def hooks_path(project_folder: str) -> str:
    return os.path.join(project_folder, "external", HOOKS_FILE)


def read_hooks(project_folder: str) -> dict[str, dict]:
    """``{reel_number: {"hook", "basis"}}`` as written, or ``{}``."""
    path = hooks_path(project_folder)
    if not os.path.isfile(path):
        return {}
    with open(path, "r", encoding="utf-8") as handle:
        payload = json.load(handle) or {}
    hooks = payload.get("hooks")
    if not isinstance(hooks, dict):
        raise PostHeaderError(f"{path}: `hooks` must be a mapping of reel "
                              f"number to {{hook, basis}}")
    return {str(k): v for k, v in hooks.items()}


def hook_for(project_folder: str, reel_number) -> dict | None:
    """This reel's hook, or None where nobody wrote one."""
    if reel_number is None:
        return None
    entry = read_hooks(project_folder).get(str(int(reel_number)))
    if entry is None:
        return None
    if not isinstance(entry, dict) or not str(entry.get("hook") or "").strip():
        raise PostHeaderError(
            f"{hooks_path(project_folder)}: reel {reel_number}'s entry "
            f"carries no `hook`")
    return {"hook": str(entry["hook"]).strip(),
            "basis": str(entry.get("basis") or "").strip()}


# ── Geometry ─────────────────────────────────────────────────────────

def layout_box(declared: dict, width: int, height: int) -> dict[str, int]:
    """Where the header lays out: the declared row, between the
    combined platform safe columns.

    The horizontal span is not chosen here - it is the columns clear of
    every platform's side strips (``platform_safe_zones``), so no phone
    crops the hook off.
    """
    from library.tools import platform_safe_zones as psz

    x0, x1 = _combined_safe_columns()
    sx = width / psz.REFERENCE_SIZE[0]
    left = round(x0 * sx)
    right = round(x1 * sx)
    return {"left": left, "top": round(declared["top"] * height),
            "width": right - left}


def _combined_safe_columns() -> tuple[int, int]:
    from library.tools import platform_safe_zones as psz

    columns = [p.safe_columns() for p in psz.PLATFORMS.values()]
    return (max(c[0] for c in columns), min(c[1] for c in columns))


def ink_box(png_path: str) -> tuple[int, int, int, int] | None:
    """The drawn ink of a transparent still as (x0, y0, x1, y1), or None."""
    from PIL import Image

    with Image.open(png_path) as image:
        alpha = image.convert("RGBA").getchannel("A")
        return alpha.point(lambda a: 255 if a > 8 else 0).getbbox()


# ── Rendering ────────────────────────────────────────────────────────

def _data_uri(path: str) -> str:
    mime = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if path.lower().endswith(".svg"):
        mime = "image/svg+xml"
    with open(path, "rb") as handle:
        return f"data:{mime};base64,{base64.b64encode(handle.read()).decode()}"


def props_for(declared: dict, hook: str, width: int, height: int,
              fps: float) -> dict:
    return {
        "avatarSrc": _data_uri(declared["avatar"]),
        "avatarShape": declared["avatar_shape"],
        "avatarBackground": declared["avatar_background"],
        "displayName": declared["name"],
        "handle": declared["handle"],
        "verified": declared["verified"],
        "hook": hook,
        "fontFamily": declared["font"] or "Montserrat",
        "nameSize": declared["name_size"],
        "handleSize": declared["handle_size"],
        "hookSize": declared["hook_size"],
        "box": layout_box(declared, width, height),
        "fps": fps,
        "width": int(width),
        "height": int(height),
        "durationInFrames": 1,
    }


def render_still(props: dict, out_png: str,
                 remotion_dir: str | None = None) -> str:
    """One transparent still of ``PostHeader``, judged by its result."""
    from library.tools.paths import REMOTION_DIR

    remotion_dir = str(remotion_dir or REMOTION_DIR)
    props_path = out_png[:-4] + "_props.json"
    with open(props_path, "w", encoding="utf-8") as handle:
        json.dump(props, handle, indent=2, sort_keys=True)
    command = ["npx", "remotion", "still", COMPOSITION, out_png,
               "--props", props_path, "--image-format", "png", "--frame", "0"]
    try:
        result = subprocess.run(
            command, cwd=remotion_dir, capture_output=True, check=False,
            text=True, encoding="utf-8", errors="replace",
            timeout=RENDER_TIMEOUT_SECONDS)
    except subprocess.TimeoutExpired as exc:
        raise PostHeaderError(
            f"PostHeader still timed out after {RENDER_TIMEOUT_SECONDS}s"
        ) from exc
    if result.returncode != 0 or not os.path.isfile(out_png):
        raise PostHeaderError(
            f"PostHeader still failed ({result.returncode}): "
            f"{(result.stderr or '')[-500:]}")
    if ink_box(out_png) is None:
        raise PostHeaderError(f"PostHeader still {out_png} drew nothing")
    return out_png


def carry_still(png: str, frames: int, fps: float, out_mov: str) -> str:
    """The still as a looped ProRes 4444 movie of at least ``frames``.

    The TV frame's carriage (``reel_look.frame_overlay_segments``): ONE
    file at the longest run, shorter runs trim it at placement.
    """
    from library.tools.reel_look import _rendered_frame_count

    if _rendered_frame_count(out_mov) >= frames:
        return out_mov
    result = subprocess.run([
        "ffmpeg", "-y", "-loop", "1", "-i", png,
        "-t", f"{frames / fps:.5f}", "-r", f"{fps:.6f}",
        "-c:v", "prores_ks", "-profile:v", "4444",
        "-pix_fmt", "yuva444p10le", out_mov,
    ], capture_output=True, encoding="utf-8", check=False)
    if result.returncode != 0 or not os.path.isfile(out_mov):
        raise PostHeaderError(
            f"the post header could not be carried to {frames} frames: "
            f"ffmpeg exited {result.returncode}. "
            f"{(result.stderr or '').strip()[-400:]}")
    return out_mov


def _still_for(declared: dict, hook: str, width: int, height: int,
               fps: float, project_folder: str, render=render_still) -> str:
    """The reel's header still, rendered once and keyed by its props."""
    from library.tools.project_layout import Area, ProjectLayout

    props = props_for(declared, hook, width, height, fps)
    stamp = hashlib.sha1(json.dumps(props, sort_keys=True).encode(
        "utf-8")).hexdigest()[:10]
    out_dir = str(ProjectLayout(project_folder).write_dir(
        Area.REEL_POST_HEADERS))
    os.makedirs(out_dir, exist_ok=True)
    png = os.path.join(out_dir, f"post_header_{stamp}.png")
    if not os.path.isfile(png):
        render(props, png)
    return png


def header_floor(project_folder: str | None, reel_number,
                 width: int, height: int, fps: float,
                 render=render_still) -> int | None:
    """The first row below this reel's header that another graphic may use.

    The header's MEASURED ink bottom plus one hook line of clearance
    (the declaration's own ``hook_size``, the same gap the composition
    leaves under the account row), so a top-anchored graphic sits under
    the header instead of on it. None where the reel draws no header.
    """
    declared = project_declaration(project_folder)
    if declared is None:
        return None
    hook = hook_for(project_folder, reel_number)
    if hook is None:
        return None
    box = ink_box(_still_for(declared, hook["hook"], width, height, fps,
                             project_folder, render=render))
    if box is None:
        return None
    return int(box[3]) + int(declared["hook_size"])


def plan_for_reel(reel_name: str, reel_number, runs: Sequence[tuple[int, int]],
                  fps: float, width: int, height: int,
                  project_folder: str,
                  picture_window: tuple[int, int, int, int] | None = None,
                  render=render_still) -> HeaderPlan:
    """One reel's header: render, measure, carry.  Empty plans say why."""
    from library.tools import platform_safe_zones as psz

    plan = HeaderPlan(reel_name=reel_name,
                      reel_number=(int(reel_number)
                                   if reel_number is not None else None))
    declared = project_declaration(project_folder)
    if declared is None:
        return plan
    hook = hook_for(project_folder, reel_number)
    if hook is None:
        plan.basis = NO_HOOK_WRITTEN
        print(f"  {reel_name}: NO POST HEADER - no hook written for reel "
              f"{reel_number} in external/{HOOKS_FILE}; the header is "
              f"declared, and a hook is the model's to write, never the "
              f"engine's", file=sys.stderr)
        return plan
    plan.hook, plan.hook_basis = hook["hook"], hook["basis"]
    runs = [(int(a), int(b)) for a, b in runs or () if int(b) > int(a)]
    if not runs:
        plan.basis = NO_HOOK_WRITTEN
        return plan

    png = _still_for(declared, plan.hook, width, height, fps,
                     project_folder, render=render)
    plan.still_path = png

    plan.ink_box = ink_box(png)
    if plan.ink_box is not None:
        plan.intrusions = psz.intrusions(plan.ink_box, psz.COMBINED,
                                         frame=(width, height))
        for hit in plan.intrusions:
            print(f"  {reel_name}: post header inside {hit['platform']} "
                  f"{hit['band']} zone ({hit['ui']}) at {hit['overlap']}",
                  file=sys.stderr)
        if picture_window is not None:
            x0 = max(plan.ink_box[0], picture_window[0])
            y0 = max(plan.ink_box[1], picture_window[1])
            x1 = min(plan.ink_box[2], picture_window[2])
            y1 = min(plan.ink_box[3], picture_window[3])
            if x1 > x0 and y1 > y0:
                plan.over_picture = (x0, y0, x1, y1)
                print(f"  {reel_name}: post header draws over the picture "
                      f"at {plan.over_picture}", file=sys.stderr)

    longest = max(b - a for a, b in runs)
    mov = carry_still(png, longest, fps, png[:-4] + ".mov")
    plan.segments = [{"overlay_path": mov, "timeline_start": a / fps,
                      "total_frames": b - a} for a, b in runs]
    plan.basis = HEADER_PLACED
    print(f"  {reel_name}: post header {plan.hook!r} ink {plan.ink_box}",
          file=sys.stderr)
    return plan


def header_rows(plan: HeaderPlan | None) -> list:
    """What the header contributes to the rebuild digest."""
    if plan is None or not plan.segments:
        return []
    return [{"still": os.path.basename(plan.still_path),
             "segments": [(s["timeline_start"], s["total_frames"])
                          for s in plan.segments]}]


# ── The record ───────────────────────────────────────────────────────
#
# What each reel's header really was, written where the verifier reads
# it (F25), renamed at promotion and dropped with a refused staging -
# the same lifecycle `speaker_identity`'s lower-third record keeps, for
# the same reason: a record left under a staging name is one F25 cannot
# find, and it would then report the header it placed as out of band.

PLANS_FILE = "post_headers.json"
PLANS_FORMAT = "post_headers/1"

#: What the reel build calls the header's row, and what the verifier
#: files items by. One spelling, `timeline_layout`'s.
TRACK_NAME = POST_HEADER_NAME

#: What a rendered header file is called, as a pattern.
FILE_SHAPE = r"^post_header_[0-9a-f]{10}$"


def plans_path(project_folder: str) -> str:
    from library.tools.project_layout import Area, ProjectLayout

    return os.path.join(
        str(ProjectLayout(project_folder).write_dir(Area.REVIEW)), PLANS_FILE)


def read_plans(project_folder: str) -> dict:
    """What the build recorded, or ``{}`` when it recorded nothing."""
    path = plans_path(project_folder)
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle) or {}
    except (OSError, ValueError):
        return {}


def _rewrite(project_folder: str, edit) -> None:
    path = plans_path(project_folder)
    stored = read_plans(project_folder) or {"format": PLANS_FORMAT,
                                            "plans": []}
    stored["format"] = PLANS_FORMAT
    stored["plans"] = edit(list(stored.get("plans") or []))
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(stored, handle, indent=2)


def write_plans(project_folder: str, records: Sequence[dict]) -> None:
    """MERGE these reels' records over the stored ones, per reel."""
    records = [r for r in records or () if r]
    if not records:
        return
    touched = {str(r.get("reel")) for r in records}
    _rewrite(project_folder, lambda plans: [
        p for p in plans if str(p.get("reel")) not in touched] + records)


def rename_plan_reels(project_folder: str, mapping: dict) -> None:
    """Rename staged records to their final names; a stale final goes."""
    if not mapping or not os.path.isfile(plans_path(project_folder)):
        return
    finals = set(mapping.values())

    def edit(plans):
        kept = [p for p in plans if p.get("reel") not in finals]
        for p in kept:
            if p.get("reel") in mapping:
                p["reel"] = mapping[p["reel"]]
        return kept
    _rewrite(project_folder, edit)


def drop_plan_reels(project_folder: str, names) -> None:
    """Remove records for containers about to be deleted."""
    drop = set(names or ())
    if not drop or not os.path.isfile(plans_path(project_folder)):
        return
    _rewrite(project_folder,
             lambda plans: [p for p in plans if p.get("reel") not in drop])


def plan_for(plans: dict | None, reel_name: str) -> dict | None:
    """The recorded header for one reel BY NAME, or None."""
    for record in (plans or {}).get("plans") or []:
        if str(record.get("reel")) == str(reel_name):
            return record
    return None


# ── Applying to built reels, without a rebuild ───────────────────────
#
# The captain ruled 2026-09-24 against full rebuilds of his accepted
# reels (they can undo hand values), so a header reaches an existing
# final as a `touch-reel` change: a new "Post Header" row, the header
# over the picture, and - where he asked for the graphics to go - every
# Semantic-row item switched OFF, never deleted. One journaled act per
# reel under one batch, so `ren undo` reverses the whole thing.

#: The rows whose items are the model-planned top graphics.
GRAPHIC_ROW_BASE = "Semantic"


def picture_runs(tracks) -> list[tuple[int, int]]:
    """Contiguous (start, end) record-frame runs of picture on V1/V2."""
    spans = sorted(
        (int(c["record_in"]), int(c["record_out"]))
        for t in tracks or ()
        if str(t.get("type", "")).lower().startswith("v")
        and int(t["index"]) in (1, 2)
        for c in (t.get("clips") or ()))
    runs: list[list[int]] = []
    for start, end in spans:
        if runs and start <= runs[-1][1]:
            runs[-1][1] = max(runs[-1][1], end)
        else:
            runs.append([start, end])
    return [(a, b) for a, b in runs]


def touch_spec(project_folder: str, reel_number: int, tracks, *,
               fps: float, width: int = 1080, height: int = 1920,
               disable_graphics: bool = True,
               render=render_still) -> dict:
    """The `touch-reel` spec that puts this reel's header on its final."""
    declared = project_declaration(project_folder)
    if declared is None:
        raise PostHeaderError(f"effect.{DECLARATION_KEY} is not declared")
    hook = hook_for(project_folder, reel_number)
    if hook is None:
        raise PostHeaderError(
            f"no hook written for reel {reel_number} in external/"
            f"{HOOKS_FILE}; the hook is the model's to write")
    runs = picture_runs(tracks)
    if not runs:
        raise PostHeaderError(f"reel {reel_number} plays no picture on V1/V2")
    png = _still_for(declared, hook["hook"], width, height, fps,
                     project_folder, render=render)
    mov = carry_still(png, max(b - a for a, b in runs), fps,
                      png[:-4] + ".mov")
    video = [t for t in tracks if str(t.get("type", "")).lower()
             .startswith("v")]
    row = f"V{len(video) + 1}"
    edits: list[dict] = [{"op": "add_row", "name": TRACK_NAME}]
    edits += [{"op": "add_overlay", "row": row, "media": mov,
               "record": a, "duration": b - a, "properties": {},
               "name": os.path.basename(mov)} for a, b in runs]
    if disable_graphics:
        for t in video:
            name = str(t.get("name") or "")
            if name != GRAPHIC_ROW_BASE and not (
                    name.startswith(GRAPHIC_ROW_BASE + " ")
                    and name[len(GRAPHIC_ROW_BASE) + 1:].isdigit()):
                continue
            for index, clip in enumerate(t.get("clips") or ()):
                if clip.get("enabled", True):
                    edits.append({"op": "set_enabled",
                                  "row": f"V{int(t['index'])}",
                                  "item": index, "enabled": False})
    origin = min(int(c["record_in"]) for t in video
                 for c in (t.get("clips") or ()))
    return {"reel": int(reel_number), "edits": edits,
            "post_header": {
                "hook": hook["hook"], "hook_basis": hook["basis"],
                "still_path": png, "ink_box": list(ink_box(png) or ()),
                "segments": [{"overlay_path": mov,
                              "timeline_start": (a - origin) / fps,
                              "total_frames": b - a} for a, b in runs]}}
