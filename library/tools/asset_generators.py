"""The generative tier: a provider interface with no provider hardcoded.

The routing layer's last resort: nothing held or transformable can
answer the need, so a provider is asked to make it. This module is the
INTERFACE - what a provider must be able to do - and the registry that
holds the configured providers. No provider is hardcoded and none
ships: a machine with no provider configured refuses by name when the
generative tier is consulted, naming what is missing.

A provider is an object with:

- ``name``: the provider's name, recorded in every decision it answers;
- ``kinds``: the need kinds it can attempt (a set of ``NEED_KINDS``);
- ``generate(need, context, count)``: make up to ``count`` candidates,
  each a dict ``{"path", "score", "provenance"}`` where ``score`` is
  the provider's own quality reading of the candidate.

The handler chooses among the configured providers that can attempt
the need, generates candidates, evaluates them, and returns the best.
The evaluation is a highest-score-wins ranking for now - the
evaluation/QC track (spec capability 15) replaces it when it lands, and
the ranking is a named function so the swap is one line.
"""
from __future__ import annotations

from library.tools import asset_routing
from library.tools.ren_refusal import RenRefusal

#: How many candidates a provider is asked for when the need does not
#: state one. The routing concept is candidates -> evaluate -> best, so
#: the default is more than one; a need that only needs one sets it.
DEFAULT_CANDIDATES = 3


class GeneratorRefused(RenRefusal):
    """The generative tier cannot answer the need."""


class GeneratorProvider:
    """What a generative provider implements. Subclass and override.

    The registry holds instances; the router consults them by name and
    records the name in the decision, so an asset a provider made is
    attributed to that provider and to no other.
    """

    name: str = ""

    def kinds(self) -> set:
        """The need kinds this provider can attempt."""
        return set()

    def generate(self, need: asset_routing.Need, context: dict,
                 count: int = 1) -> list:
        """Make up to ``count`` candidates for ``need``.

        Each candidate is ``{"path", "score", "provenance"}``. A
        provider that cannot make a candidate refuses with
        ``GeneratorRefused`` - a refusal the router records, never a
        default asset.
        """
        raise GeneratorRefused(
            f"the {self.name!r} provider does not implement generate",
            "a provider that cannot generate has no candidates to "
            "offer, and an empty candidate list would look like a "
            "provider that tried and found nothing.",
            "implement generate, or remove the provider from the "
            "registry.",
        )


#: provider name -> provider instance. No provider is hardcoded: the
#: registry starts empty and a machine configures what it has.
_PROVIDERS: dict[str, GeneratorProvider] = {}


def register_provider(provider: GeneratorProvider) -> None:
    """Register a provider under its ``name``.

    A provider with no name, or a name already registered, refuses: an
    anonymous provider could not be attributed, and a silent overwrite
    would leave the previous provider's assets pointing at a name that
    now means something else.
    """
    name = getattr(provider, "name", "")
    if not name or not isinstance(name, str) or not name.strip():
        raise GeneratorRefused(
            "a provider was registered with no name",
            "every asset a provider makes is attributed to its name; an "
            "anonymous provider's assets could not be traced to it.",
            "give the provider a name.",
        )
    if name in _PROVIDERS:
        raise GeneratorRefused(
            f"a provider named {name!r} is already registered",
            "two providers under one name would make every decision "
            "ambiguous about which one answered.",
            "register the second provider under a different name.",
        )
    _PROVIDERS[name] = provider


def configured_providers() -> dict:
    """The registered providers, name -> instance."""
    return dict(_PROVIDERS)


def _providers_that_can_attempt(need: asset_routing.Need) -> list:
    """The registered providers that declare they can attempt `need`."""
    able = []
    for name, provider in _PROVIDERS.items():
        try:
            kinds = provider.kinds()
        except GeneratorRefused:
            continue
        if need.kind in kinds:
            able.append(provider)
    return able


def _evaluate(candidates: list) -> dict:
    """The best candidate, by the provider's own score.

    Highest score wins; ties break on the candidate that was generated
    first, so the ranking is deterministic. The evaluation/QC track
    (spec capability 15) replaces this function when it lands.
    """
    best = None
    for candidate in candidates:
        if not isinstance(candidate, dict) or "path" not in candidate:
            continue
        if best is None or float(candidate.get("score", 0.0)) > \
                float(best.get("score", 0.0)):
            best = candidate
    return best


def generative_fill(need: asset_routing.Need, context: dict) -> dict:
    """Answer a need from a configured provider.

    Chooses among the providers that can attempt the need, generates
    candidates, evaluates them, and returns the best. Refuses by name
    when no provider is configured, or when none can attempt the need -
    the refusal reaches the decision record instead of aborting the
    routing.
    """
    able = _providers_that_can_attempt(need)
    if not able:
        configured = ", ".join(sorted(_PROVIDERS)) or "none"
        if not _PROVIDERS:
            raise GeneratorRefused(
                f"no generative provider is configured, so the need "
                f"{need.kind!r} has nothing to ask",
                "the generative tier is the routing layer's last resort; "
                "with no provider registered there is no generator to "
                "choose and nothing to generate.",
                "register a provider with "
                "asset_generators.register_provider, or route the need "
                "to a lower tier.",
            )
        raise GeneratorRefused(
            f"no configured provider can attempt {need.kind!r}",
            f"configured providers: {configured}; none declares it can "
            f"attempt this need kind.",
            "register a provider that can attempt this kind, or route "
            "the need to a lower tier.",
        )
    provider = able[0]
    count = need.params.get("candidates", DEFAULT_CANDIDATES)
    try:
        candidates = provider.generate(need, context, count)
    except GeneratorRefused:
        raise
    if not candidates:
        raise GeneratorRefused(
            f"the {provider.name!r} provider returned no candidates for "
            f"{need.kind!r}",
            "a provider that generates nothing has answered nothing; "
            "recording it as solved would insert an asset that does not "
            "exist.",
            "fix the provider to return candidates, or route the need "
            "to a lower tier.",
        )
    best = _evaluate(candidates)
    if best is None:
        raise GeneratorRefused(
            f"the {provider.name!r} provider returned candidates with "
            f"no usable path for {need.kind!r}",
            "a candidate without a path cannot be inserted, and picking "
            "one at random would insert an asset nobody chose.",
            "fix the provider to return path-carrying candidates, or "
            "route the need to a lower tier.",
        )
    provenance = dict(best.get("provenance") or {})
    provenance.update({
        "producer": provider.name,
        "method": asset_routing.METHOD_DECLARED,
        "derived_from": provenance.get("derived_from", []),
        "detail": (f"candidate 1 of {len(candidates)} from "
                   f"{provider.name}, score {best.get('score', 0.0)}"),
    })
    return {
        "handler": "generative_fill",
        "solved": True,
        "artifact": {"path": best.get("path"),
                     "media_type": need.params.get("media_type",
                                                   "video"),
                     "score": best.get("score", 0.0)},
        "provenance": provenance,
        "reason": None,
        "refusal": None,
        "candidates": candidates,
    }


def register_generators() -> None:
    """Register the generative tier's handler into the router."""
    asset_routing.register_handler("generative", "generative_fill",
                                   generative_fill)


register_generators()
