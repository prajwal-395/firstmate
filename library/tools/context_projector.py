"""The prompt projection: what a step DECLARES, and what that leaves.

`project_fields` is the projection.  `declared_context_fields` is the
one place that answers WHERE a manifest declares it, because a
declaration nothing reads is not a declaration.

Rules relocated from AGENTS.md 10.1
-----------------------------------
**A `context_fields` declaration lives at the manifest's TOP LEVEL, and
one written anywhere else is REFUSED rather than ignored.**
`declared_context_fields` is the only reader of that location, and
`run_pipeline.project_step_context`, `input_contract._reaches_prompt`
and `replay_bench.reconstruct` all ask it rather than indexing the
manifest themselves - three spellings of "where is it" are three places
for it to be somewhere else.
- Step 3.04 declared its allow-list under `interface`, which the
  JSON Schema described and nothing read, so the projection never ran
  and 817,317 characters of raw transcript - 92% of the request, with
  8,509 per-word timings in it - reached the model that chooses which
  passages become reels. It ran that way on every reel selection ever
  made. [why](docs/RULE_EVIDENCE.md#the-declaration-nothing-read)
- Raising is the point.  An unread declaration reads exactly like a
  step that deliberately declares none, and the two must not look the
  same from any caller.
- `declared_context_fields` below is the only enforcer of that location:
  a dead `library/schema/manifest.schema.json` once described it too, but
  nothing ever loaded that file, so it was deleted rather than left as a
  second spelling of the rule.
- `tests/contracts/test_context_contracts.py`.

`project_fields` is documented on the function.
"""

import copy

def _project_single_path(source, path_parts):
    if not path_parts:
        return copy.deepcopy(source)
    
    part = path_parts[0]
    
    if part == '*':
        if isinstance(source, list):
            result = []
            for item in source:
                projected = _project_single_path(item, path_parts[1:])
                if projected is not None:
                    result.append(projected)
                else:
                    if isinstance(item, dict):
                        result.append({})
                    elif isinstance(item, list):
                        result.append([])
                    else:
                        result.append(None)
            return result
        return None
    else:
        if isinstance(source, dict) and part in source:
            projected = _project_single_path(source[part], path_parts[1:])
            if projected is not None:
                return {part: projected}
        return None

def _merge(target, source):
    if isinstance(target, dict) and isinstance(source, dict):
        for k, v in source.items():
            if k in target:
                target[k] = _merge(target[k], v)
            else:
                target[k] = copy.deepcopy(v)
        return target
    elif isinstance(target, list) and isinstance(source, list):
        result = []
        for i in range(max(len(target), len(source))):
            t_item = target[i] if i < len(target) else None
            s_item = source[i] if i < len(source) else None
            
            if t_item is None:
                result.append(copy.deepcopy(s_item))
            elif s_item is None:
                result.append(copy.deepcopy(t_item))
            else:
                result.append(_merge(t_item, s_item))
        return result
    else:
        return copy.deepcopy(source)

def _drop_single_path(target, path_parts) -> None:
    """Delete one dot-path, `*` included, from an already-projected tree."""
    if not path_parts:
        return

    part, rest = path_parts[0], path_parts[1:]

    if part == '*':
        if isinstance(target, list):
            for item in target:
                _drop_single_path(item, rest)
        return

    if not isinstance(target, dict) or part not in target:
        return
    if rest:
        _drop_single_path(target[part], rest)
    else:
        del target[part]


def project_fields(data: dict, dot_paths: list[str]) -> dict:
    """Extract only specified dot-notation paths from data.

    Example:
        data = {"temporal_index": {"transcripts": [...], "energy_curve": {"values": [0.1, 0.2, ...]}}}
        project_fields(data, ["temporal_index.transcripts"]) 
        -> {"temporal_index": {"transcripts": [...]}}

    A path may be prefixed with `-` to DROP it from what the paths above
    it selected: `["timed_spine", "-timed_spine.structure.*.word_timestamps"]`
    is the whole spine without the per-word timings.  Enumerating the
    other twenty keys instead would have worked once and then quietly
    stopped delivering the twenty-first, which is the key-name failure
    AGENTS.md section 10.1 calls the dominant bug class; naming the one
    field to remove cannot go stale that way.  A `-` path that matches
    nothing is a no-op, because "not present" is what it asked for.

    An entry may instead be `view:<name>`, naming a reading of a routed
    input rather than a path into it - see `library/tools/context_views.py`
    for the enumeration and for why the transcript needs one.  Views are
    merged alongside the selected paths and before the drops, so a `-`
    path may narrow a view the same way it narrows anything else.  An
    unknown view name raises.

    A declaration of NOTHING BUT `-` paths means "everything, minus
    these".  That is the only way to remove one field from a step that is
    deliberately unprojected: `render` and `validate` are handed their
    whole input set on a standing decision, and an allow-list written to
    take one key out of them would silently become the decision it was
    avoiding.

    Raises RuntimeError if a projection produces all-empty dicts from a
    non-empty list, which indicates a schema mismatch in context_fields.
    """
    import sys

    from library.tools.context_views import build_view, is_view, view_name

    view_paths = [p for p in dot_paths if is_view(p)]
    keep_paths = [p for p in dot_paths
                  if not p.startswith('-') and not is_view(p)]
    drop_paths = [p[1:] for p in dot_paths if p.startswith('-')]

    # Nothing but `-` paths: everything, minus these.
    if drop_paths and not keep_paths and not view_paths:
        result = copy.deepcopy(data)
        for path in drop_paths:
            _drop_single_path(result, path.split('.'))
        return result

    result = {}
    missed_paths = []
    for path in keep_paths:
        parts = path.split('.')
        projected = _project_single_path(data, parts)
        if projected is not None:
            result = _merge(result, projected)
        else:
            root = parts[0]
            if root in data and data[root] is not None:
                missed_paths.append(path)

    for path in view_paths:
        result = _merge(result, build_view(view_name(path), data))

    for path in drop_paths:
        _drop_single_path(result, path.split('.'))

    if missed_paths:
        print(f"  [context_projector] WARNING: {len(missed_paths)} context_fields "
              f"resolved to nothing: {missed_paths}", file=sys.stderr)

    # Guard: detect when a list was projected to all-empty dicts
    for key, val in result.items():
        if isinstance(val, list) and len(val) > 0:
            source = data.get(key, [])
            if isinstance(source, list) and len(source) > 0:
                non_empty = sum(1 for item in val if item and item != {} and item != [])
                if non_empty == 0:
                    raise RuntimeError(
                        f"context_fields schema mismatch: '{key}' has {len(val)} items "
                        f"but all projected to empty dicts. The context_fields paths for "
                        f"'{key}' don't match the actual data schema. "
                        f"Actual item keys: {sorted(source[0].keys()) if isinstance(source[0], dict) else 'not a dict'}"
                    )

    return result


CONTEXT_FIELDS_KEY = "context_fields"


class MisplacedContextFields(ValueError):
    """A manifest declared its allow-list where nothing reads it."""


def declared_context_fields(manifest, step_id: str = ""):
    """The prompt allow-list this manifest declares, or None for "all".

    ONE location - the manifest's top level.  A declaration under
    `interface`, beside `inputs` and `outputs` where it reads as though
    it belongs, is REFUSED: that is exactly how step 3.04 shipped an
    allow-list the projection never applied, and a silently-ignored
    declaration is indistinguishable from a step that declares none.

    `None` means the step declares nothing and is handed every byte it
    was routed (AGENTS.md 10.1), which is a real and deliberate state -
    `render` and `validate` are in it by a standing decision.  That is
    why the misplaced case raises instead of returning `None`.
    """
    if not isinstance(manifest, dict):
        return None

    interface = manifest.get("interface")
    if isinstance(interface, dict) and CONTEXT_FIELDS_KEY in interface:
        where = f"{step_id}: " if step_id else ""
        raise MisplacedContextFields(
            f"{where}`{CONTEXT_FIELDS_KEY}` is declared under `interface`, "
            f"where nothing reads it, so the prompt projection would never "
            f"run and the model would be handed every byte the step was "
            f"routed. Move it to the manifest's top level - see "
            f"library/tools/context_projector.declared_context_fields."
        )

    return manifest.get(CONTEXT_FIELDS_KEY)
