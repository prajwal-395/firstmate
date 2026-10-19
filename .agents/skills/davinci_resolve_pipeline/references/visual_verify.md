# Rendered-frame inspection

For a Resolve capture or rendered file, use the current AXI operation and
its verification contract. Do not drive Resolve render APIs from this
reference; native writes and render jobs run under AXI.

Inspect exported pixels at the relevant frame or interval. File size is
not a correctness threshold. Use `verify_render` for deterministic checks
and a visual inspection method when the requested property cannot be
proved structurally. A successful render job is not itself a passing
verification result.
