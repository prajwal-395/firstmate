"""Person and object profiles: stable cross-clip identity for WHO/WHAT is on screen.

What already exists, and what was genuinely missing:

- `vision_pipeline_v3` writes per-clip `objects[]` with a free-text `label`
  ("young man in black baseball cap and light grey shirt"), a `category`
  (person/vehicle/object/text/structure), a `role` and clip-local
  `appearances`. Every clip is described from scratch, so the same speaker
  in fourteen clips is fourteen unrelated strings. There is no identity.
- `compute_face_presence` (step 1.04) answers "is a face in frame" per
  5 Hz sample, never whose face it is.
- Object segmentation (step 1.06, UNWIRED) tracks `auto_object_N` masks
  within one clip, with no appearance embedding and no cross-clip link -
  a profile built on it would duplicate per-clip blobs, so this module
  does not read it.
- Nothing stores a name. The captain's names for their people live
  nowhere in the pipeline.

What this module adds is the missing link only: `build_profiles` groups
the ALREADY RECORDED v3 mentions across clips into durable profiles with
stable ids (`person_01`, `object_01`), the appearance evidence that earned
the link, where each appears with times, and a `name` slot that starts
unset - names are the captain's to give, via `attach_name`.

The match is deliberately textual and deterministic (token Jaccard over
the labels, transitive closure), because the recorded detections carry no
face embedding and no ReID vector - there is nothing else to match on
without running a new vision pass, which is out of scope. The thresholds
below are measured on project 001's seventeen v3 profiles, not tuned to
them: persons link at 0.30 because clothing/attribute phrases overlap
even when the shirt colour changes; non-persons link only at 0.60 AND
with three or more content tokens, because generic background labels
("white car", "brick building") recur across clips that share no actual
car or building. A generic hub label ("dark grey car") that is a strict
subset of longer descriptions never links - subset matching is how one
parking lot collapses into one invented car. Two mentions from the SAME clip never link - the pass
lists one entry per entity, so same-clip co-occurrence is distinctness,
not identity. A generic label that cannot prove identity stays a
singleton profile rather than becoming an invented one.

Deliberately NOT wired into the DAG: adding a step would change existing
contracts, which is out of scope. Readers call `build_profiles` on the
recorded v3 documents; `save_profiles`/`load_profiles` persist the store
as `person_object_profiles.json` beside the analysis it was built from.
"""

import json
import re
from typing import Dict, List

DEFAULT_FILENAME = "person_object_profiles.json"

PERSON_LINK_THRESHOLD = 0.30
OBJECT_LINK_THRESHOLD = 0.60
MIN_DISTINCTIVE_TOKENS = 3

_TOKEN_RE = re.compile(r"[a-z0-9]+")

# Words that carry no identity: grammar, and the generic "a person" phrasing
# the vision pass uses for every human. Dropping them is what lets "young
# man in black baseball cap" meet "man in black cap" instead of meeting
# every other person through the shared word "man".
STOPWORDS = frozenset({
    "a", "an", "the", "and", "or", "with", "in", "on", "of", "at", "to",
    "wearing", "young", "old", "older", "man", "woman", "boy", "girl",
    "person", "persons", "people", "child",
})

# One spelling for the colour the pass spells both ways, so "grey shirt"
# and "gray shirt" are one token, not a missed link.
CANONICAL_TOKENS = {"grey": "gray"}


def mention_tokens(label: str) -> frozenset:
    """Content tokens of one `objects[]` label, normalised for matching."""
    tokens = set()
    for raw in _TOKEN_RE.findall(label.lower()):
        token = CANONICAL_TOKENS.get(raw, raw)
        if token and token not in STOPWORDS:
            tokens.add(token)
    return frozenset(tokens)


def _jaccard(a: frozenset, b: frozenset) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _span_seconds(ranges) -> float:
    total = 0.0
    for span in ranges or []:
        try:
            total += max(float(span[1]) - float(span[0]), 0.0)
        except (TypeError, ValueError, IndexError):
            continue
    return total


def build_profiles(clips: List[Dict]) -> List[Dict]:
    """Group recorded v3 object mentions into cross-clip profiles.

    `clips` is a list of `{"clip_id": ..., "objects": [...]}` where each
    object carries the v3 keys `label`, `category`, `role`, `appearances`.
    Returns profiles ordered by screen time (longest first), with stable
    per-kind ids. Pure function of its input: no IO, no model, no Resolve.
    """
    mentions = []
    for clip in clips:
        clip_id = clip.get("clip_id", "")
        for obj in clip.get("objects") or []:
            label = obj.get("label") or ""
            tokens = mention_tokens(label)
            if not tokens:
                continue
            mentions.append({
                "clip_id": clip_id,
                "label": label,
                "category": obj.get("category") or "object",
                "role": obj.get("role") or "background",
                "ranges": [list(s) for s in (obj.get("appearances") or [])],
                "tokens": tokens,
            })

    # Union-find over mentions: persons link on shared appearance
    # attributes, objects only on distinctive multi-token labels.
    parent = list(range(len(mentions)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    def union(i, j):
        ri, rj = find(i), find(j)
        if ri != rj:
            parent[max(ri, rj)] = min(ri, rj)

    for i in range(len(mentions)):
        for j in range(i + 1, len(mentions)):
            a, b = mentions[i], mentions[j]
            a_person = a["category"] == "person"
            b_person = b["category"] == "person"
            if a_person != b_person:
                continue
            # Two mentions in the SAME clip are distinct by construction:
            # the pass lists one entry per entity, so a clip that names
            # two cars holds two cars. Linking them would un-count what
            # the pass counted. Identity is only ever claimed ACROSS clips.
            if a["clip_id"] == b["clip_id"]:
                continue
            if a_person:
                if _jaccard(a["tokens"], b["tokens"]) >= PERSON_LINK_THRESHOLD:
                    union(i, j)
            elif a["category"] == b["category"]:
                if (len(a["tokens"]) >= MIN_DISTINCTIVE_TOKENS
                        and len(b["tokens"]) >= MIN_DISTINCTIVE_TOKENS
                        and _jaccard(a["tokens"], b["tokens"])
                        >= OBJECT_LINK_THRESHOLD
                        # Neither description may be a strict subset of the
                        # other: a bare "dark grey car" sits inside every
                        # longer car description, so subset links turn one
                        # generic hub into a chain that merges a parking lot
                        # (001: an interior close-up became two street cars).
                        # Equal labels still link - repetition IS evidence.
                        and not (a["tokens"] < b["tokens"]
                                 or b["tokens"] < a["tokens"])):
                    union(i, j)

    groups: Dict[int, List[int]] = {}
    for i in range(len(mentions)):
        groups.setdefault(find(i), []).append(i)

    profiles = []
    for members in groups.values():
        first = mentions[members[0]]
        kind = "person" if first["category"] == "person" else "object"
        labels = sorted({mentions[m]["label"] for m in members})
        # The longest label is the most descriptive one the pass wrote.
        display = max(labels, key=lambda s: (len(s), s))
        appearances = [{
            "clip_id": mentions[m]["clip_id"],
            "ranges": mentions[m]["ranges"],
            "label": mentions[m]["label"],
        } for m in sorted(members,
                           key=lambda m: mentions[m]["clip_id"])]
        clip_ids = {mentions[m]["clip_id"] for m in members}
        total = round(sum(_span_seconds(mentions[m]["ranges"])
                          for m in members), 3)
        profiles.append({
            "profile_id": "",
            "kind": kind,
            "display_label": display,
            "name": None,
            "appearances": appearances,
            "evidence_labels": labels,
            "clip_count": len(clip_ids),
            "total_seconds": total,
            "linked": len(clip_ids) > 1,
        })

    # Longest screen time first, with total tie-breaks, so ids are stable
    # however the input clips arrive.
    profiles.sort(key=lambda p: (-p["total_seconds"], p["kind"],
                                 p["display_label"],
                                 p["appearances"][0]["clip_id"]
                                 if p["appearances"] else ""))
    counters: Dict[str, int] = {}
    for profile in profiles:
        counters[profile["kind"]] = counters.get(profile["kind"], 0) + 1
        profile["profile_id"] = (
            f"{profile['kind']}_{counters[profile['kind']]:02d}")
    return profiles


def attach_name(profiles: List[Dict], profile_id: str, name: str) -> Dict:
    """Give one profile the captain's name for it. Names start unset."""
    clean = (name or "").strip()
    if not clean:
        raise ValueError("name must be a non-empty string")
    for profile in profiles:
        if profile.get("profile_id") == profile_id:
            profile["name"] = clean
            return profile
    raise KeyError(f"unknown profile id: {profile_id}")


def save_profiles(profiles: List[Dict], path: str) -> None:
    """Persist the store as JSON. UTF-8: labels carry real punctuation."""
    with open(path, "w", encoding="utf-8") as f:
        json.dump({"profiles": profiles}, f, indent=2, ensure_ascii=False)
        f.write("\n")


def load_profiles(path: str) -> List[Dict]:
    """Read back what `save_profiles` wrote."""
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data.get("profiles", [])
