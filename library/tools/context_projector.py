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

def project_fields(data: dict, dot_paths: list[str]) -> dict:
    """Extract only specified dot-notation paths from data.
    
    Example:
        data = {"temporal_index": {"transcripts": [...], "energy_curve": {"values": [0.1, 0.2, ...]}}}
        project_fields(data, ["temporal_index.transcripts"]) 
        -> {"temporal_index": {"transcripts": [...]}}
    """
    result = {}
    for path in dot_paths:
        parts = path.split('.')
        projected = _project_single_path(data, parts)
        if projected is not None:
            result = _merge(result, projected)
    return result
