# Fusion composition authoring

`library/tools/fusion/comp_builder.py` owns the valid Fusion composition
shape and the constraints enforced by the renderer. Read that source when
changing the builder; do not maintain a second list of node behavior,
parameter defaults or crash workarounds here.

Generate a composition through the owning builder. Any Resolve import,
inspection or mutation uses [resolve-axi](../../resolve-axi/SKILL.md).
Effect parameters come from the approved plan and its vocabulary; this
reference supplies no creative magnitudes or fallback values.
