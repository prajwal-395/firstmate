# Installed Resolve execution capabilities

Inventory measured on 2026-10-19 from the installed DaVinci Resolve 21.1.0
application and its bundled API reference at
`/Library/Application Support/Blackmagic Design/DaVinci Resolve/Developer/Scripting`.
The `.pyi` contains 13 public classes and 410 public methods. The API parser
records each signature, parameter type and requiredness, return type,
documentation presence, page/object context, source hash, and current release
changelog. `method_capability_records()` emits normalized, version-pinned
method records (`documented`, `observed`, `implemented`, `tested`,
`live_qualified`, `production_enabled`) for AXI registry joins. A callable
proxy probe can record `observed` per method when given the relevant live
object handles; lack of a live object is recorded as
unknown, not unavailable. Callable presence is not behavioral qualification.

## Blackmagic official MCP

The installed bundle advertises 14 tools. `native_mcp.capability_inventory()`
obtains the names, descriptions and input schemas directly from
`tools/list`; this table classifies the observed tool set:

| Capability group | Tools | AXI route and status |
|---|---|---|
| Documentation and status | `get_resolve_status`, `get_whats_new`, `get_scripting_api`, `search_scripting_api`, `get_scripting_docs` | Existing `resolve-axi status` and `resolve-axi api` routes. These are discovery/read tools and overlap the installed native API reference where applicable. |
| LUT catalog | `list_dctls`, `list_luts` | Existing `resolve-axi luts list`; reads the shared LUT shelf. |
| LUT shelf writes | `update_dctl`, `delete_dctl`, `delete_lut`, `generate_lut` | Existing `resolve-axi luts` routes; writes are constrained to the shared LUT/MCP shelf and are not timeline editing operations. |
| Application lifecycle | `launch_resolve` | Not routed through generic AXI edit transactions. |
| Script execution | `run_script`, `run_script_unsafe` | Not production-routed. The first executes arbitrary Resolve Python; the unsafe variant also has filesystem, network and subprocess access. Existing `resolve-axi run --unsafe` remains the diagnostic escape hatch. |

No unique timeline-edit capability in the official MCP is registered as a
production operation: its script tools expose arbitrary code and do not supply
the operation-specific authorization, read-back verification or recovery
contract. Existing AXI documentation and LUT routes remain the supported
surface. The MCP tool inventory is runtime-discovered so a Resolve update can
be compared without maintaining a second handwritten tool list.

## Generic typed invocation

`library/tools/resolve_native_invocation.py` is the internal typed native
backend primitive. It consumes the installed-reference metadata, validates
argument names and known scalar/container types, supports structured project
and timeline references, rejects undocumented calls and a conservative set of
destructive methods, and requires an AXI operation authorization plus an
operation-specific read-back verifier for writes. It does not auto-enable
methods. `resolve-axi run --unsafe` remains the separate diagnostic escape
hatch for exploratory scripts; typed invocation cannot use it to bypass
documented-method, destructive-method, argument, authorization, or read-back
guards.

The registry lane owns the canonical execution-capability registry format.
This inventory therefore exports installed method facts and invocation
constraints as source metadata; the existing `edit_algebra.OP_STATUS` remains
the authority for editorial operation meaning and backend qualification.
