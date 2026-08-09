import re
import csv
import io

def _parse_csv_line(line: str) -> list[str]:
    reader = csv.reader([line], quotechar="'", escapechar="\\")
    return next(reader)

def _format_csv_row(vals: list[str]) -> str:
    writer_file = io.StringIO()
    writer = csv.writer(writer_file, quotechar="'", escapechar="\\", quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow(vals)
    return writer_file.getvalue()

def _is_uniform_dict_list(data: list) -> tuple[bool, list[str]]:
    if not data:
        return False, []
    if not all(isinstance(x, dict) for x in data):
        return False, []
    
    # Require that they share at least 50% of the possible keys on average,
    # or just strictly the same keys. Let's do: all dicts must have at least one common key
    # if len > 1, or just same keys.
    all_keys = set()
    for item in data:
        all_keys.update(item.keys())
        
    if len(all_keys) == 0:
        return True, []
        
    # Check if they are "mostly same"
    total_keys = len(all_keys)
    sum_keys = sum(len(item) for item in data)
    avg_keys = sum_keys / len(data)
    
    if avg_keys / total_keys < 0.51:
        return False, []
    
    keys = list(all_keys)
    keys.sort()
    return True, keys

def _format_scalar(val: any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        # We handle newlines and commas via csv module later if it's in a table,
        # but for inline key-value pairs we also need to escape it if it has newlines.
        val = val.replace('\n', '\\n')
        return val
    return str(val)

def _parse_scalar(val_str: str) -> any:
    if val_str == "":
        return None
    if val_str == "true":
        return True
    if val_str == "false":
        return False
    # try int
    try:
        return int(val_str)
    except ValueError:
        pass
    # try float
    try:
        return float(val_str)
    except ValueError:
        pass
    # unescape newlines
    return val_str.replace('\\n', '\n')

def json_to_toon(data: any, indent: int = 0) -> str:
    """Convert JSON-compatible data to TOON format."""
    ind = " " * indent
    if isinstance(data, dict):
        if not data:
            return f"{ind}{{}}"
        lines = []
        for k, v in data.items():
            if isinstance(v, (dict, list)):
                lines.append(f"{ind}{k}:")
                sub = json_to_toon(v, indent + 2)
                if sub:
                    lines.append(sub)
            else:
                scalar_val = _format_scalar(v)
                # If scalar_val has commas or something, no big deal for KV, but let's just emit it
                lines.append(f"{ind}{k}: {scalar_val}")
        return "\n".join(lines)
        
    elif isinstance(data, list):
        if not data:
            return f"{ind}[]"
            
        is_uniform, keys = _is_uniform_dict_list(data)
        if is_uniform:
            lines = []
            header = f"{ind}[{len(data)}]{{{','.join(keys)}}}"
            lines.append(header)
            for item in data:
                row_vals = []
                for k in keys:
                    v = item.get(k)
                    if isinstance(v, (dict, list)):
                        # TOON tabular doesn't naturally support nested objects in cells.
                        # We'll JSON-serialize them and treat as string.
                        import json
                        row_vals.append(json.dumps(v))
                    else:
                        row_vals.append(_format_scalar(v))
                lines.append(ind + _format_csv_row(row_vals))
            return "\n".join(lines)
        else:
            lines = []
            for i, item in enumerate(data):
                if isinstance(item, (dict, list)):
                    lines.append(f"{ind}[{i}]")
                    sub = json_to_toon(item, indent + 2)
                    if sub:
                        lines.append(sub)
                else:
                    lines.append(f"{ind}[{i}] {_format_scalar(item)}")
            return "\n".join(lines)
    else:
        return f"{ind}{_format_scalar(data)}"

def toon_to_json(toon_str: str) -> any:
    """Parse TOON format back to JSON. (Best effort for tests)"""
    lines = toon_str.split('\n')
    
    def parse_block(start_idx, current_indent):
        if start_idx >= len(lines):
            return None, start_idx
            
        first_line = lines[start_idx]
        actual_indent = len(first_line) - len(first_line.lstrip(' '))
        
        if actual_indent < current_indent:
            return None, start_idx
            
        # Is it a dict?
        if first_line.lstrip().startswith('{}'):
            return {}, start_idx + 1
            
        # Is it an empty list?
        if first_line.lstrip().startswith('[]'):
            return [], start_idx + 1
            
        # Is it a tabular list?
        table_match = re.match(r'^ *\[(\d+)\]\{(.*)\}$', first_line)
        if table_match:
            count = int(table_match.group(1))
            keys = table_match.group(2).split(',') if table_match.group(2) else []
            result = []
            idx = start_idx + 1
            for _ in range(count):
                if idx >= len(lines):
                    break
                row_line = lines[idx].lstrip(' ')
                vals = _parse_csv_line(row_line)
                row_dict = {}
                for k, v in zip(keys, vals):
                    # parse nested JSON if it looks like it
                    if v.startswith('{') or v.startswith('['):
                        import json
                        try:
                            v = json.loads(v)
                        except json.JSONDecodeError:
                            v = _parse_scalar(v)
                    else:
                        v = _parse_scalar(v)
                    row_dict[k] = v
                result.append(row_dict)
                idx += 1
            return result, idx
            
        # Is it an indexed list?
        idx_match = re.match(r'^ *\[(\d+)\](.*)$', first_line)
        if idx_match:
            result = []
            idx = start_idx
            while idx < len(lines):
                line = lines[idx]
                line_indent = len(line) - len(line.lstrip(' '))
                if line_indent < current_indent:
                    break
                m = re.match(r'^ *\[(\d+)\](.*)$', line)
                if not m:
                    break
                val_str = m.group(2).strip()
                if val_str:
                    result.append(_parse_scalar(val_str))
                    idx += 1
                else:
                    sub_val, next_idx = parse_block(idx + 1, current_indent + 2)
                    result.append(sub_val)
                    idx = next_idx
            return result, idx
            
        # Is it a dict block?
        if ':' in first_line:
            result = {}
            idx = start_idx
            while idx < len(lines):
                line = lines[idx]
                line_indent = len(line) - len(line.lstrip(' '))
                if line_indent < current_indent:
                    break
                if ':' not in line:
                    break
                k_str, v_str = line.split(':', 1)
                k = k_str.strip()
                v_str = v_str.strip()
                if v_str:
                    result[k] = _parse_scalar(v_str)
                    idx += 1
                else:
                    sub_val, next_idx = parse_block(idx + 1, current_indent + 2)
                    result[k] = sub_val
                    idx = next_idx
            return result, idx
            
        # scalar
        return _parse_scalar(first_line.strip()), start_idx + 1
        
    res, _ = parse_block(0, 0)
    return res
