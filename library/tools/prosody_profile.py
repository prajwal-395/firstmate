"""Whether one prosody profile MEASURED anything, in one place.

Step 1.05 writes a profile per clip and a profile is a FILE ON DISK, so
counting files is not the same as counting measurements (AGENTS.md 10.3).
The failure path used to write a profile like any other result -
``{"prosody": {"method": null, "error": "parselmouth not installed"}}`` -
and project 001 collected seventeen of them, reported success, and sent
4.2 KB of identical error records to the creative director as if they
were data.

Two readers, and they are the reason this is not a private helper of the
step: step 1.05 rejects a defective profile at write time, and
`context_views` keeps one that a PREVIOUS run already recorded in state
out of the prompt.  A single predicate means the two cannot disagree
about what counts as a measurement.
"""


def profile_defect(profile: dict) -> str:
    """Why this prosody profile measures nothing, or "" if it does."""
    if not isinstance(profile, dict):
        return "not a JSON object"
    prosody = profile.get("prosody")
    if not isinstance(prosody, dict):
        return "no prosody block"
    if prosody.get("error"):
        return str(prosody["error"])
    if not prosody.get("method"):
        return "no analysis method recorded"
    if not prosody.get("pitch_stats") and not prosody.get("intensity_contour_50ms"):
        return "neither pitch nor intensity was measured"
    return ""
