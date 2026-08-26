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

    Raises RuntimeError if a projection produces all-empty dicts from a
    non-empty list, which indicates a schema mismatch in context_fields.
    """
    import sys

    keep_paths = [p for p in dot_paths if not p.startswith('-')]
    drop_paths = [p[1:] for p in dot_paths if p.startswith('-')]

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
