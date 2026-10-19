"""Typed, fail-closed invocation of the installed Resolve scripting API.

This is a backend primitive for Resolve AXI, not another public control
surface. Writes require an AXI authorization decision tied to an operation;
the caller cannot opt an undocumented or destructive method into production.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from library.tools.resolve_api_capabilities import (
    callable_method,
    capability_map_for_version,
    has_documented_method,
)


class NativeInvocationError(ValueError):
    """A native call could not be validated or safely authorized."""


@dataclass(frozen=True)
class ObjectReference:
    kind: str
    name: str
    stable_id: str | None = None


# These operations can destroy or replace user data even when their names
# look like ordinary writes. They remain diagnostic-only and are not exposed
# through generic invocation.
_NEVER_INVOKE = {
    "DeleteProject", "DeleteTimelines", "DeleteClips", "DeleteTrack",
    "DeleteFusionCompByName", "ImportProject", "SaveProject",
    "SaveProjectAs", "CloseProject",
}
_OBJECT_TYPES = {
    "Project": "project", "Timeline": "timeline",
    "MediaPoolItem": "media_pool_item", "TimelineItem": "timeline_item",
    "Folder": "folder", "ColorGroup": "color_group",
    "Gallery": "gallery", "GalleryStillAlbum": "gallery_still_album",
    "Graph": "graph", "MediaPool": "media_pool", "ProjectManager": "project_manager",
}

# Effect is an AXI contract, not something inferred from an English verb.
# In particular, Resolve has setters that are context-sensitive, methods
# called `Add*` that return a query-like object, and destructive methods
# whose names do not consistently advertise their reach. The AXI registry
# supplies this per operation/method decision after qualification review.
SIDE_EFFECTS = frozenset({
    "read_only", "reversible_mutation", "mutation", "destructive", "unknown",
})

# Semantic operation to exact native method bindings. An API method is never
# made executable by its spelling or by being present in the installed stub;
# each binding is reviewed against one AXI operation. Extend only with a typed
# operation contract and read-back implementation. Qualification remains in
# edit_algebra and is independent of this implementation map.
NATIVE_OPERATION_METHODS = {
    ("marker.add", "Timeline", "AddMarker"): "mutation",
}


def classify_operation_method(operation_id: str | None, class_name: str,
                              method_name: str, _metadata: dict) -> str:
    """Return the side effect for an explicitly bound AXI operation/method."""
    if operation_id is None:
        return "unknown"
    return NATIVE_OPERATION_METHODS.get(
        (operation_id, class_name, method_name), "unknown")


def _resolve_reference(resolve: Any, reference: ObjectReference,
                       project: Any,
                       resolver: Callable[[ObjectReference], Any] | None = None) -> Any:
    if reference.stable_id is not None:
        if resolver is None:
            raise NativeInvocationError(
                "stable object references require AXI's identity resolver")
        found = resolver(reference)
        if found is None:
            raise NativeInvocationError("stable object reference did not resolve uniquely")
        return found
    if reference.kind == "project":
        manager = resolve.GetProjectManager()
        found = manager.GetCurrentProject()
        if found is None or found.GetName() != reference.name:
            raise NativeInvocationError("project reference is not the exact open project")
        return found
    if reference.kind == "timeline":
        if project is None:
            raise NativeInvocationError("timeline references require an exact project")
        found = project.GetTimelineByName(reference.name)
        if found is None or found.GetName() != reference.name:
            raise NativeInvocationError("timeline reference did not resolve exactly")
        return found
    raise NativeInvocationError(
        f"object reference kind {reference.kind!r} has no stable resolver")


def _check_type(name: str, parameter: dict, value: Any,
                resolve: Any, project: Any,
                resolver: Callable[[ObjectReference], Any] | None,
                *, typed_dict_types: dict | None = None,
                literal_types: dict | None = None) -> Any:
    expected = parameter["type"]
    expected = expected.strip("'").strip('"')
    allowed = parameter.get("allowed_values")
    if allowed is not None and value not in allowed:
        raise NativeInvocationError(
            f"{name} must be one of {allowed}; got {value!r}")
    if expected in _OBJECT_TYPES:
        if not isinstance(value, ObjectReference):
            raise NativeInvocationError(
                f"{name} requires a structured {expected} reference")
        if value.kind != _OBJECT_TYPES[expected]:
            raise NativeInvocationError(f"{name} reference kind does not match {expected}")
        return _resolve_reference(resolve, value, project, resolver)
    typed_dict = (typed_dict_types or {}).get(expected)
    if typed_dict is not None:
        if not isinstance(value, dict):
            raise NativeInvocationError(f"{name} requires {expected} object")
        fields = typed_dict["fields"]
        unknown = set(value) - fields.keys()
        missing = {key for key, field in fields.items()
                   if field["required"] and key not in value}
        if unknown or missing:
            raise NativeInvocationError(
                f"{name} {expected} field mismatch: unknown={sorted(unknown)} "
                f"missing={sorted(missing)}")
        return {
            key: _check_type(
                f"{name}.{key}",
                {"type": fields[key]["type"], "required": fields[key]["required"],
                 **({"allowed_values": literal_types[fields[key]["type"]]}
                    if literal_types and fields[key]["type"] in literal_types else {})},
                member, resolve, project, resolver,
                typed_dict_types=typed_dict_types,
                literal_types=literal_types)
            for key, member in value.items()
        }
    expected = expected.replace("typing.", "")
    checks = {
        "str": lambda v: isinstance(v, str),
        "int": lambda v: isinstance(v, int) and not isinstance(v, bool),
        "float": lambda v: isinstance(v, (int, float)) and not isinstance(v, bool),
        "bool": lambda v: isinstance(v, bool),
    }
    alternatives = [part.strip().strip("'").strip('"')
                    for part in expected.split("|")]
    base = expected.split(".")[-1]
    checked = [(part.split(".")[-1], checks.get(part.split(".")[-1]))
               for part in alternatives]
    known = [(type_name, check) for type_name, check in checked if check]
    if "None" in alternatives and value is None:
        return value
    if known and not any(check(value) for _type_name, check in known):
        raise NativeInvocationError(f"{name} requires {expected}; got {type(value).__name__}")
    if base in {"list", "dict", "tuple"} and not isinstance(value, {
            "list": list, "dict": dict, "tuple": tuple}[base]):
        raise NativeInvocationError(f"{name} requires {base}")
    supported = set(checks) | {"None", "Any", "list", "dict", "tuple"}
    if allowed is None and not known and not any(part.split(".")[-1] in supported
                             or part.split(".")[-1] in _OBJECT_TYPES
                             for part in alternatives):
        raise NativeInvocationError(
            f"{name} has unsupported API type {expected}; refusing unvalidated value")
    if base == "Any" and not isinstance(value, (str, int, float, bool, list, dict, type(None))):
        raise NativeInvocationError(f"{name} requires a JSON-compatible value")
    return value


def is_mutation(method_name: str) -> bool:
    """Deprecated name heuristic retained for callers; never used for policy.

    Production invocation uses the AXI operation-specific classifier. This
    helper remains for compatibility with diagnostics and must not be used
    to authorize or route a write.
    """
    return method_name.startswith((
        "Add", "Apply", "Append", "Create", "Delete", "Import", "Insert",
        "Load", "Move", "Remove", "Rename", "Set", "Update", "Write",
    ))


def invoke(*, resolve: Any, target: Any, class_name: str, method_name: str,
           arguments: dict[str, Any], capability_map: dict,
           project: Any = None, operation_id: str | None = None,
           authorize: Callable[[str, str], bool] | None = None,
           classify_side_effect: Callable[[str | None, str, str, dict], str] | None = None,
           resolve_object: Callable[[ObjectReference], Any] | None = None,
           verify: Callable[[Any], bool] | None = None) -> dict[str, Any]:
    """Validate, authorize, invoke, and compactly report one API call.

    `classify_side_effect(operation_id, class_name, method_name, metadata)`
    is AXI's per-method contract lookup. Missing or unknown classifications
    fail closed; method spelling is not used to decide whether a call writes.
    `authorize(operation_id, qualified_method)` is AXI's production policy
    hook; the qualified name is ``Class.method`` to avoid same-name collisions.
    This API is never a substitute for the existing `run --unsafe` diagnostic
    escape hatch, which remains implemented by Resolve AXI itself.
    """
    release = capability_map_for_version(capability_map)
    method_meta = release["classes"].get(class_name, {}).get(method_name)
    if method_meta is None or not has_documented_method(capability_map, class_name, method_name):
        raise NativeInvocationError(f"{class_name}.{method_name} is undocumented; refusing")
    if method_name in _NEVER_INVOKE:
        raise NativeInvocationError(f"{method_name} is destructive and unavailable generically")

    if classify_side_effect is None:
        effect = classify_operation_method(
            operation_id, class_name, method_name, method_meta)
        if effect == "unknown":
            raise NativeInvocationError(
                "native invocation requires AXI's operation-specific side-effect classification")
    else:
        effect = classify_side_effect(
            operation_id, class_name, method_name, method_meta)
    if effect not in SIDE_EFFECTS:
        raise NativeInvocationError(
            f"{class_name}.{method_name} has invalid AXI side-effect classification {effect!r}")
    if effect in {"unknown", "destructive"}:
        raise NativeInvocationError(
            f"{class_name}.{method_name} is {effect}; generic invocation is unavailable")
    mutating = effect != "read_only"

    if isinstance(target, ObjectReference):
        expected_kind = _OBJECT_TYPES.get(class_name)
        if expected_kind is None or target.kind != expected_kind:
            raise NativeInvocationError(
                f"target reference kind does not match API class {class_name}")
        target = _resolve_reference(resolve, target, project, resolve_object)

    params = {row["name"]: row for row in method_meta["parameters"]}
    unknown = set(arguments) - params.keys()
    missing = {key for key, row in params.items()
               if row["required"] and key not in arguments}
    if unknown or missing:
        raise NativeInvocationError(
            f"argument mismatch: unknown={sorted(unknown)} missing={sorted(missing)}")

    if mutating:
        allowed = bool(
            operation_id and authorize
            and authorize(operation_id, f"{class_name}.{method_name}"))
        if not allowed:
            raise NativeInvocationError(
                "mutation requires AXI operation authorization; use the existing run --unsafe diagnostic path for exploratory scripts")
        if verify is None:
            raise NativeInvocationError(
                f"{method_name} requires an operation-specific read-back verifier")

    converted = {
        key: _check_type(
            key, params[key], value, resolve, project, resolve_object,
            typed_dict_types=release.get("typed_dict_types", {}),
            literal_types=release.get("literal_types", {}))
        for key, value in arguments.items()
    }
    method = callable_method(target, method_name)
    if method is None:
        raise NativeInvocationError(f"{class_name}.{method_name} is not callable on this Resolve proxy")
    result = method(**converted)
    if mutating:
        if result is False:
            raise NativeInvocationError(f"{method_name} returned false")
        verified = bool(verify(result))
        if not verified:
            raise NativeInvocationError(f"{method_name} read-back verification failed")
    else:
        verified = True
    if isinstance(result, str):
        compact_result: Any = result[:256] + ("…" if len(result) > 256 else "")
    elif isinstance(result, (int, float, bool, type(None))):
        compact_result = result
    else:
        compact_result = type(result).__name__
    return {
        "method": f"{class_name}.{method_name}",
        "operation_id": operation_id,
        "documented": True,
        "mutating": mutating,
        "side_effect": effect,
        "returned": compact_result,
        "verified": verified,
    }
