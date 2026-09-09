"""The single owner of timeline layout. AGENTS.md 10.2 / docs/TIMELINE_SOP.md.

One module takes the material - which angles exist, which speakers, what
the plan asks for - and returns the track plan: for each track its
index, its media type, its role, its name, and what will occupy it.
Nothing else in the codebase may decide a track index or a track name;
the reel builder asks it too, passing its reel-specific rows
(transitions, explainer, semantic, and the TV-frame set row) as roles
rather than hardcoded indices beside it.

What is fixed and what is derived
---------------------------------
The row ORDER is the standard and it is fixed: picture rows first (one
a-roll row per angle, then b-roll), then the frame row where the reel
TV-frame look dresses the picture, then the caption row, then the
reel's additive rows (transitions, explainer, semantic), then
decorative picture (motion-graphics rows, generator effects, timed
text); then speech rows (one per angle), then the music bed, then SFX.
Role names are vocabulary from that standard, not taste.

Picture rows follow angles under every look, the way speech rows
already do (captain's ruling on Reel 09, 2026-09-09): one row per
speaker, named for them. There is no collapse of two angles onto one
row - the TV-frame row sits above the picture rows it dresses rather
than taking one's place.

Every COUNT comes from the material, never a constant: a row exists
because something goes on it. Two angles mean two a-roll rows and two
speech rows; no b-roll asked for means no b-roll row; SFX stacking is
decided by overlap, mechanically. This is what makes "two speakers
collapsed onto one row" and "blank rows with nothing on them"
structurally impossible rather than fixed once.

Names come from the material too: an a-roll row carries its angle's
label, a speech row its angle plus its program stream (the captain's
own "Akshita CH1"). Singleton roles carry the standard name.
"""

from dataclasses import dataclass, field


# ── Roles ──────────────────────────────────────────────────────────
# The vocabulary of what a row can be. A row's NAME is derived from its
# role plus its occupant (angle label, stream label, layer number).

A_ROLL = "a_roll"
B_ROLL = "b_roll"
FRAME = "frame"
CAPTIONS = "captions"
TRANSITIONS = "transitions"
EXPLAINER = "explainer"
SEMANTIC = "semantic"
MOTION_GRAPHICS = "motion_graphics"
GENERATORS = "generators"
TIMED_TEXT = "timed_text"
SPEECH = "speech"
MUSIC = "music"
SFX = "sfx"

VIDEO = "video"
AUDIO = "audio"

#: Roles of which a timeline holds at most one row. Two rows carrying
#: one of these names is two rows doing one row's job.
SINGLETON_ROLES = frozenset({B_ROLL, FRAME, CAPTIONS, TRANSITIONS,
                              EXPLAINER, SEMANTIC, GENERATORS, TIMED_TEXT,
                              MUSIC})

#: The standard NAMES those singleton rows carry. A master row carrying
#: one is a layer, not a camera - which is how the reel builder tells
#: its angles apart from decoration without a second list of its own.
#: "Captions" is the reel caption row's retired name; it still denotes
#: a layer wherever it survives.
SINGLETON_NAMES = frozenset({
    "B-Roll", "Frame", "Subtitles", "Captions", "Transitions",
    "Explainer", "Semantic", "Generator Effects", "Timed Text",
    "Music", "SFX",
})

#: Names Resolve itself gives tracks nobody named. A row carrying one
#: was never organised.
DEFAULT_NAMES = frozenset({"Video", "Audio", "Subtitle"})

#: The angle key used when the manifest declares none. A manifest that
#: declares no angles builds exactly one a-roll row and one speech row,
#: which is the single-camera shape every run before this one had.
DEFAULT_ANGLE_KEY = "main"
DEFAULT_ANGLE_LABEL = "A-Roll"
DEFAULT_SPEECH_NAME = "Speech"


@dataclass
class Angle:
    """One camera angle: its key in the manifest, its row label, and the
    name and program channel of the speech row that carries its sound."""
    key: str
    label: str
    speech_name: str
    program_channel: int = 1


@dataclass
class TrackSpec:
    """One row of the plan: where it sits, what it is, what goes on it."""
    index: int
    media_type: str  # "video" | "audio"
    role: str
    name: str
    occupant: str = ""  # angle key, or a description of the layer


@dataclass
class TrackPlan:
    """The whole layout. `material` is echoed back so a verifier can
    rebuild the plan from a recorded build result."""
    video_tracks: list = field(default_factory=list)
    audio_tracks: list = field(default_factory=list)
    material: dict = field(default_factory=dict)

    def video_row_for_angle(self, key: str):
        for track in self.video_tracks:
            if track.role == A_ROLL and track.occupant == key:
                return track
        return None

    def speech_row_for_angle(self, key: str):
        for track in self.audio_tracks:
            if track.role == SPEECH and track.occupant == key:
                return track
        return None

    def caption_row(self):
        for track in self.video_tracks:
            if track.role == CAPTIONS:
                return track
        return None

    def speech_rows(self):
        return [t for t in self.audio_tracks if t.role == SPEECH]

    def aroll_rows(self):
        return [t for t in self.video_tracks if t.role == A_ROLL]

    def role_of(self, media_type: str, index: int):
        for track in (self.video_tracks if media_type == VIDEO
                      else self.audio_tracks):
            if track.index == index:
                return track.role
        return None

    def row_for_role(self, role: str):
        """The plan row carrying `role`, or None when the material asked
        for no such row. The reel builder places its overlay rows
        through this rather than a hardcoded index beside the plan."""
        for track in self.video_tracks + self.audio_tracks:
            if track.role == role:
                return track
        return None

    def program_channels(self):
        """Expected source channel per speech row, by audio row index."""
        channels = {}
        for track in self.audio_tracks:
            if track.role != SPEECH:
                continue
            for angle in self.material.get("angles", []):
                if angle.get("key") == track.occupant:
                    channels[track.index] = int(
                        angle.get("program_channel", 1))
        return channels

    def serializable(self):
        return {
            "video_tracks": [vars(t) for t in self.video_tracks],
            "audio_tracks": [vars(t) for t in self.audio_tracks],
            "material": self.material,
        }


def allocate_non_overlapping_rows(spans, base_index: int):
    """Pack (span, ...) intervals across rows so nothing on one row
    overlaps in time. Returns [(span, row_index)] with rows counted up
    from `base_index`.

    Mechanical, not taste: overlap is a fact about the plan, and the
    smallest row count that holds it is forced. This is what makes SFX
    "one row or many depending on how layered the sound is" - and the
    same packing decides motion-graphics and timed-text rows.
    """
    spans = list(spans)
    if not spans:
        return []
    ordered = sorted(range(len(spans)), key=lambda i: spans[i][0])
    row_ends: dict = {}
    allocations = [None] * len(spans)
    for i in ordered:
        start, end = spans[i][0], spans[i][1]
        assigned = None
        for row in sorted(row_ends):
            if row_ends[row] <= start:
                assigned = row
                break
        if assigned is None:
            assigned = max(row_ends) + 1 if row_ends else base_index
        row_ends[assigned] = end
        allocations[i] = (spans[i], assigned)
    return allocations


def _layered_names(role: str, base: str, count: int):
    """First row carries the bare role name; further rows number up."""
    if count <= 1:
        return [base]
    return [base] + [f"{base} {n}" for n in range(2, count + 1)]


def plan_layout(material: dict) -> TrackPlan:
    """The track plan for the material. `material` carries:

    - angles: [{key, label, speech_name, program_channel}] in row order.
      Empty means the manifest declares none: one legacy-shaped row pair.
      Two angles mean two a-roll rows under every look, including the
      reel TV-frame look - the frame row sits above them (captain's
      ruling on Reel 09, 2026-09-09; the `collapse_picture` rule that
      used to sit here was removed with it, and a stale
      `collapse_picture` key in the material is ignored rather than
      honoured).
    - has_broll: bool.
    - has_frame: bool. The TV-frame look's set row, directly above the
      picture rows it dresses. (`tv_frame.LAYER_TRACKS` reads footage
      V1, frame V2, captions V3 - that is the MASTER path's shape,
      which never collapsed; the reel's frame row follows its own
      picture rows.)
    - caption_spans / mg_spans / timed_text_spans: [(start, end)] in
      frames. A row exists per overlapping layer, not per segment.
    - has_transitions / has_explainer / has_semantic: bool. The reel's
      own additive rows - transition elements over the reel's cuts, the
      animated explainer, the model-planned semantic visuals - one row
      each, only when the reel places something on it.
    - has_generators: bool.
    - music_spans / sfx_spans: [(start, end)] in frames, packed the
      same way.
    """
    raw_angles = material.get("angles") or []
    if raw_angles:
        angles = [Angle(key=a["key"], label=a["label"],
                        speech_name=a.get("speech_name",
                                          f"{a['label']} CH{a.get('program_channel', 1)}"),
                        program_channel=int(a.get("program_channel", 1)))
                  for a in raw_angles]
    else:
        angles = [Angle(key=DEFAULT_ANGLE_KEY, label=DEFAULT_ANGLE_LABEL,
                        speech_name=DEFAULT_SPEECH_NAME, program_channel=1)]

    video = []
    audio = []

    for angle in angles:
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=A_ROLL, name=angle.label,
                               occupant=angle.key))
    if material.get("has_broll"):
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=B_ROLL, name="B-Roll",
                               occupant="b_roll"))

    if material.get("has_frame"):
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=FRAME, name="Frame",
                               occupant="frame"))

    caption_spans = [tuple(s) for s in material.get("caption_spans", [])]
    if caption_spans:
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=CAPTIONS, name="Subtitles",
                               occupant="captions"))

    if material.get("has_transitions"):
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=TRANSITIONS, name="Transitions",
                               occupant="transitions"))

    if material.get("has_explainer"):
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=EXPLAINER, name="Explainer",
                               occupant="explainer"))

    if material.get("has_semantic"):
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=SEMANTIC, name="Semantic",
                               occupant="semantic"))

    mg_rows = allocate_non_overlapping_rows(
        [tuple(s) for s in material.get("mg_spans", [])],
        base_index=len(video) + 1)
    mg_names = _layered_names(MOTION_GRAPHICS, "Motion Graphics",
                              len({r for _, r in mg_rows}))
    for name in mg_names if mg_rows else []:
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=MOTION_GRAPHICS, name=name,
                               occupant="motion_graphics"))

    if material.get("has_generators"):
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=GENERATORS, name="Generator Effects",
                               occupant="generators"))

    tt_rows = allocate_non_overlapping_rows(
        [tuple(s) for s in material.get("timed_text_spans", [])],
        base_index=len(video) + 1)
    tt_names = _layered_names(TIMED_TEXT, "Timed Text",
                              len({r for _, r in tt_rows}))
    for name in tt_names if tt_rows else []:
        video.append(TrackSpec(index=len(video) + 1, media_type=VIDEO,
                               role=TIMED_TEXT, name=name,
                               occupant="timed_text"))

    for angle in angles:
        audio.append(TrackSpec(index=len(audio) + 1, media_type=AUDIO,
                               role=SPEECH, name=angle.speech_name,
                               occupant=angle.key))

    music_rows = allocate_non_overlapping_rows(
        [tuple(s) for s in material.get("music_spans", [])],
        base_index=len(audio) + 1)
    music_names = _layered_names(MUSIC, "Music",
                                 len({r for _, r in music_rows}))
    for name in music_names if music_rows else []:
        audio.append(TrackSpec(index=len(audio) + 1, media_type=AUDIO,
                               role=MUSIC, name=name, occupant="music"))

    sfx_rows = allocate_non_overlapping_rows(
        [tuple(s) for s in material.get("sfx_spans", [])],
        base_index=len(audio) + 1)
    sfx_names = _layered_names(SFX, "SFX", len({r for _, r in sfx_rows}))
    for name in sfx_names if sfx_rows else []:
        audio.append(TrackSpec(index=len(audio) + 1, media_type=AUDIO,
                               role=SFX, name=name, occupant="sfx"))

    return TrackPlan(video_tracks=video, audio_tracks=audio,
                     material=material)
