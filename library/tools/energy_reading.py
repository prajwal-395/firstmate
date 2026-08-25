"""How a free-text `target_energy` is bucketed. One enumeration.

`creative_direction.target_energy` is prose, not a tidy enum - a model
writes "building", or "start observational -> build to a peak" - so every
reader has to bucket it, and two readers used to bucket it differently.
On project 001 the direction chose **"building"** deliberately, its own
rationale saying that cutting the piece as high-energy would "fight the
source":

* `transition_selector` did not match it, so scene-change cuts stayed
  hard cuts. The intended outcome.
* `step_5_03_creative_cohesion.map_energy` substring-matched it to
  **"high"**, then demanded transitions under 500 ms and >=10 SFX per
  minute - and cut three `defocus` transitions from 500 ms to 333 ms.

**"building" is not "high", and this module is why.** It names a
TRAJECTORY, and reading a trajectory as its endpoint discards the
distinction the direction was making; the word was chosen over "high",
not as a synonym for it. The vocabulary below is the transition
selector's, adopted whole for two reasons: it is the reading whose result
reaches the picture (which transition is drawn), and it is the reading
that was right on the one project anyone has measured.

`WITHDRAWN_HIGH_WORDS` records what the cohesion step used to add to that
list and why each is out. Widening the high bucket is a creative change -
it decides which transitions get drawn on every project whose direction
uses the word - so it is a decision to be taken deliberately, here, and
not by two lists drifting apart again.

Note the one thing this does NOT do: a phrase like "build to a peak"
still reads high, because "peak" is in the list. That is the transition
selector's long-standing behaviour and changing it would change the
picture; the trajectory reading is about the WORD "building", which names
no level at all.
"""

# Most-to-least obvious. A substring test, because the field is prose.
HIGH_ENERGY_WORDS = (
    "high", "frantic", "intense", "peak", "explosive", "energetic",
)

CALM_ENERGY_WORDS = ("low", "calm", "slow", "reflective")

# Words `map_energy` used to read as high, with the reason each is out.
WITHDRAWN_HIGH_WORDS = {
    "building": (
        "names a trajectory, not a level, and project 001's direction "
        "chose it explicitly over 'high' - reading it as its endpoint "
        "discards the choice"
    ),
    "dynamic": (
        "travelled with 'building' in the cohesion step's list and was "
        "never the reading that reached the picture; adding it to the "
        "shared vocabulary would newly make transitions faster on every "
        "project whose direction uses the word, which is a creative "
        "change nobody has ruled on"
    ),
    "fast": (
        "same: it may well belong here, but it belongs here by a "
        "decision rather than by inheritance from the reader that was "
        "wrong about 'building'"
    ),
}

MODERATE = "moderate"
HIGH = "high"
CALM = "calm"


def read_energy(target_energy) -> str:
    """Bucket a free-text energy into "high", "calm" or "moderate".

    High wins a tie: a direction that says "calm but building to
    something intense" is asking for the peak to be cut for.
    """
    text = str(target_energy or "").lower()
    if any(word in text for word in HIGH_ENERGY_WORDS):
        return HIGH
    if any(word in text for word in CALM_ENERGY_WORDS):
        return CALM
    return MODERATE


def is_high_energy(creative_direction: dict) -> bool:
    """The same reading, off a creative_direction dict.

    `energy`/`mood` never existed on creative_direction; the real keys are
    `target_energy`/`target_mood`, which is why the energy-driven branch
    in `transition_selector` was unreachable for the whole life of the
    step.
    """
    return read_energy((creative_direction or {}).get("target_energy")) == HIGH
