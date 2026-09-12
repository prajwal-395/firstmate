"""The variant spec widened past the seam - and the boundary holds.

Seam-only was the limit that made `timeline_variants` never serve a real
question: `SPEC_KEYS = ("suffix", "j_cut", "cutaway", "cover", "watch")`
has no way to say "the same reel with the other ending", which is what a
creative A/B actually is.

Widening it is easy; widening it CORRECTLY is the whole job, because a
spec that can express any difference is a second builder - and then the
thing beside the approved reel is no longer the approved reel with one
change in it, and the comparison the captain is looking at is not the
one they asked for.

So both halves are pinned here:

- what a variant CAN express is DERIVED from
  `external_inputs.DECLARATIONS`, the engine's own enumeration of a
  per-project declaration, never listed a second time;
- what it CANNOT is named with the module that owns each near miss, and
  a spec that reaches for one is refused with that list in the message.

Each test fails if its mechanism is removed.
"""
import pytest

from library.tools import external_inputs
from library.tools import timeline_variants as variants

# ── What it CAN express, and where that list comes from ──────────

def test_the_declarable_set_is_derived_not_listed_again():
    """A store added to `external_inputs.DECLARATIONS` becomes
    variant-expressible with no second edit, and one removed stops
    being expressible at the same moment. Two lists would drift, and
    the drift would read as "that difference cannot be compared"."""
    assert set(variants.declarable()) == set(external_inputs.DECLARATIONS)
    # And it is not empty, or every `declares` below would be refused
    # for a reason that has nothing to do with the spec.
    assert len(variants.declarable()) >= 5


@pytest.mark.parametrize("store", sorted(external_inputs.DECLARATIONS))
def test_every_declaration_store_is_expressible(store):
    """The stores `edit_depth` names owners for - the ending, the
    caption timing, the overlay intent, the mix intent, the placed
    assets - are exactly the ones a variant may differ in."""
    assert variants.validate_variant_spec(
        {"suffix": " (other-ending)", "declares": [store]}) == []


def test_a_declaring_variant_needs_no_seam():
    """This is the widening itself. Before it, a variant with a
    different ending and the same seam was refused as "not a
    comparison" - which is precisely the comparison the captain
    makes."""
    assert variants.validate_variant_spec(
        {"suffix": " (cta-b)", "declares": ["reel_ending"],
         "watch": "the close"}) == []


def test_a_seam_and_a_declaration_can_ride_together():
    assert variants.validate_variant_spec({
        "suffix": " (reaction-cutaway)",
        "cutaway": {"hide_angle": "A", "window_seconds": [12.4, 12.7]},
        "declares": ["caption_timing"]}) == []


# ── What it CANNOT, and the boundary that is held ────────────────

def test_a_spec_with_no_difference_at_all_is_still_refused():
    """The widening did not weaken the original rule: a variation with
    nothing different in it is not a comparison."""
    errors = variants.validate_variant_spec({"suffix": " (same)"})
    assert errors
    assert "declares" in errors[0]


def test_a_store_nothing_owns_is_refused_and_the_boundary_is_quoted():
    """`series_look` is the near miss the report asked for by name, and
    it is deliberately OUT: a look resolves from project.yaml and the
    brand template, so it is a property of the SERIES. A variant that
    differed in it would compare two series, not two treatments of one
    moment."""
    errors = variants.validate_variant_spec(
        {"suffix": " (warm)", "declares": ["series_look"]})
    assert errors
    message = " ".join(errors)
    assert "series_look" in message
    # The refusal names what IS allowed and what is out of vocabulary,
    # rather than only saying no.
    assert "reel_ending" in message
    assert "a different look, grade or delivery format" in message


def test_the_out_of_vocabulary_roster_names_an_owner_for_every_entry():
    """`never` is not optional (AGENTS.md 16's shape): an entry that
    only says a thing is excluded teaches nobody where it belongs."""
    assert variants.OUT_OF_VOCABULARY
    for near_miss, owner in variants.OUT_OF_VOCABULARY.items():
        assert near_miss.strip()
        assert ".py" in owner or ".json" in owner, (
            f"{near_miss!r} is excluded without naming the module that "
            f"owns it - a boundary with no forwarding address is a dead "
            f"end rather than a rule.")


def test_out_of_vocabulary_and_declarable_cannot_overlap():
    """A store that is both expressible and excluded is a rule nobody
    can obey."""
    words = " ".join(variants.OUT_OF_VOCABULARY)
    for store in variants.declarable():
        assert store not in words


def test_declares_must_be_a_list_of_known_names():
    assert variants.validate_variant_spec(
        {"suffix": " (a)", "declares": "reel_ending"})
    assert variants.validate_variant_spec(
        {"suffix": " (a)", "declares": []})
    assert variants.validate_variant_spec(
        {"suffix": " (a)", "declares": ["reel_ending", "reel_ending"]})


def test_a_key_outside_the_spec_is_still_refused():
    """The spec widened; it did not become open. An unknown key is how
    a second builder arrives one field at a time."""
    errors = variants.validate_variant_spec(
        {"suffix": " (a)", "declares": ["reel_ending"],
         "transcript": "other.json"})
    assert errors and "unknown keys" in errors[0]


# ── The canonical spelling carries it ────────────────────────────

def test_declares_is_written_sorted(tmp_path):
    """Two branches naming the same stores in a different order must
    not read as a conflict - the same merge-friendliness the reels and
    suffixes are sorted for."""
    project = tmp_path / "p"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    variants.write_variant_specs(str(project), {"variants": {"9": [
        {"suffix": " (a)", "declares": ["reel_ending", "caption_timing"]}]}})
    written = variants.read_variant_specs(str(project))
    assert written["variants"]["9"][0]["declares"] == [
        "caption_timing", "reel_ending"]


def test_specs_for_reel_reads_back_what_was_declared(tmp_path):
    project = tmp_path / "p"
    (project / "pipeline_output" / "review").mkdir(parents=True)
    variants.write_variant_specs(str(project), {"variants": {"9": [
        {"suffix": " (b)", "declares": ["reel_ending"]},
        {"suffix": " (a)", "cutaway": {"hide_angle": "A",
                                       "window_seconds": [1, 2]}}]}})
    specs = variants.specs_for_reel(str(project), 9)
    assert [s["suffix"] for s in specs] == [" (a)", " (b)"]
    assert variants.spec_by_suffix(str(project), 9, " (b)")["declares"] \
        == ["reel_ending"]
    assert variants.spec_by_suffix(str(project), 9, " (z)") is None
