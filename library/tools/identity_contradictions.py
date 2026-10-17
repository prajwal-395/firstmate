"""Lane vep-mu-reid AUD-049: detect and record identity contradictions.

Pure functions over a resolve_person_tracks-shaped roster dict; they
record contradictions, never resolve them. Three kinds:

- same_frame_copresence: two face spans of one person overlapping in
  time within one content digest. One body cannot be in two places in
  one frame, so this is the strongest contradiction.
- face_voice_disagreement: a speech-face link attaches a face owned by
  person A to a voice track the cross-source join gave to person B.
- voice_group_multi_owner: a voice_match_conflicts entry, surfaced
  verbatim. An ECAPA group touching two persons joins nobody.
"""

from __future__ import annotations

from typing import Any


def _persons(roster: dict) -> list[dict[str, Any]]:
    persons = roster.get("persons") if isinstance(roster, dict) else None
    return [p for p in persons or [] if isinstance(p, dict)]


def _overlaps(a_start: float, a_end: float, b_start: float, b_end: float) -> bool:
    return max(a_start, b_start) < min(a_end, b_end)


def detect_same_frame_copresence(roster: dict) -> list[dict[str, Any]]:
    """Same-person face spans overlapping in time within one digest."""
    found: list[dict[str, Any]] = []
    for person in _persons(roster):
        spans = [s for s in person.get("face_spans") or [] if isinstance(s, dict)]
        by_digest: dict[str, list[dict]] = {}
        for span in spans:
            digest = span.get("content_digest")
            if isinstance(digest, str):
                by_digest.setdefault(digest, []).append(span)
        for digest, group in by_digest.items():
            for i in range(len(group)):
                for j in range(i + 1, len(group)):
                    a, b = group[i], group[j]
                    try:
                        overlap = _overlaps(
                            float(a["start"]),
                            float(a["end"]),
                            float(b["start"]),
                            float(b["end"]),
                        )
                    except (KeyError, TypeError, ValueError):
                        continue
                    if not overlap:
                        continue
                    found.append(
                        {
                            "kind": "same_frame_copresence",
                            "person_id": person.get("person_id"),
                            "digest": digest,
                            "span_a": a,
                            "span_b": b,
                            "basis": (
                                f"two face spans of "
                                f"{person.get('person_id')} overlap in time "
                                f"within {digest} "
                                f"({a.get('start')}-{a.get('end')} vs "
                                f"{b.get('start')}-{b.get('end')}); one body "
                                f"cannot be in two places in one frame"
                            ),
                        }
                    )
    return found


def _face_owner(roster: dict) -> dict[str, str]:
    """Face track_id -> owning person_id via face_tracks membership."""
    owner: dict[str, str] = {}
    for person in _persons(roster):
        pid = person.get("person_id")
        for track in person.get("face_tracks") or []:
            if isinstance(track, dict) and track.get("track_id"):
                owner.setdefault(str(track["track_id"]), pid)
    return owner


def _face_digest(person: dict, track_id: str) -> Any:
    for track in person.get("face_tracks") or []:
        if isinstance(track, dict) and str(track.get("track_id")) == track_id:
            return track.get("content_digest")
    return None


def _voice_owner(roster: dict) -> dict[tuple, str]:
    """(content_digest, voice_track) -> person_id via voice_matches."""
    owner: dict[tuple, str] = {}
    for person in _persons(roster):
        pid = person.get("person_id")
        for match in person.get("voice_matches") or []:
            if not isinstance(match, dict):
                continue
            digest = match.get("content_digest")
            track = match.get("voice_track")
            if digest and track:
                owner.setdefault((digest, track), pid)
    return owner


def detect_face_voice_disagreement(roster: dict) -> list[dict[str, Any]]:
    """Speech-face links whose voice belongs to another person.

    Face ownership comes from face_tracks membership; voice ownership
    from voice_matches (the cross-source join). A link whose voice no
    other person claims is consistent and yields nothing.
    """
    persons = {p.get("person_id"): p for p in _persons(roster)}
    face_owner = _face_owner(roster)
    voice_owner = _voice_owner(roster)
    found: list[dict[str, Any]] = []
    for pid, person in persons.items():
        for link in person.get("speech_face_links") or []:
            if not isinstance(link, dict):
                continue
            face_track = link.get("face_track")
            voice_track = link.get("voice_track")
            if face_track is None or voice_track is None:
                continue
            face_track = str(face_track)
            owner_a = face_owner.get(face_track, pid)
            digest = link.get("content_digest")
            if digest is None:
                digest = _face_digest(persons.get(owner_a, person), face_track)
            if digest is None:
                continue
            owner_b = voice_owner.get((digest, str(voice_track)))
            if owner_b is not None and owner_b != owner_a:
                found.append(
                    {
                        "kind": "face_voice_disagreement",
                        "person_id": owner_a,
                        "other_person_id": owner_b,
                        "digest": digest,
                        "face_track": face_track,
                        "voice_track": str(voice_track),
                        "basis": (
                            f"face track {face_track} owned by {owner_a} is "
                            f"linked to voice track {voice_track}, which the "
                            f"cross-source join gave to {owner_b}"
                        ),
                    }
                )
    return found


def surface_voice_conflicts(roster: dict) -> list[dict[str, Any]]:
    """Map each voice_match_conflicts entry to a contradiction record."""
    found: list[dict[str, Any]] = []
    conflicts = (
        roster.get("voice_match_conflicts") if isinstance(roster, dict) else None
    )
    for entry in conflicts or []:
        if not isinstance(entry, dict):
            continue
        found.append(
            {
                "kind": "voice_group_multi_owner",
                "voice_tracks": entry.get("voice_tracks") or [],
                "persons": entry.get("persons") or [],
                "basis": ("ECAPA group touches >1 person; joins nobody"),
            }
        )
    return found


def detect_all(roster: dict) -> dict[str, Any]:
    """Run all three detectors; empty roster yields zero, never an error."""
    copresence = detect_same_frame_copresence(roster)
    disagreement = detect_face_voice_disagreement(roster)
    conflicts = surface_voice_conflicts(roster)
    links = sum(len(p.get("speech_face_links") or []) for p in _persons(roster))
    pairs = 0
    for person in _persons(roster):
        spans = [
            s
            for s in person.get("face_spans") or []
            if isinstance(s, dict) and isinstance(s.get("content_digest"), str)
        ]
        pairs += sum(
            1
            for i in range(len(spans))
            for j in range(i + 1, len(spans))
            if spans[i].get("content_digest") == spans[j].get("content_digest")
        )
    return {
        "contradictions": copresence + disagreement + conflicts,
        "checked": {
            "copresence": pairs,
            "face_voice_disagreement": links,
            "voice_group_multi_owner": len(conflicts),
        },
    }
