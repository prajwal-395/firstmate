"""
TOON serialisation - how a step's context is rendered for the prompt.

Rules relocated from AGENTS.md 10.1
-----------------------------------
These are the engine's rules for this module.  They lived in AGENTS.md
until it was split by subsystem; the wording is unchanged, so each rule
is findable by its own words, and AGENTS.md 10.1 keeps the headline
and points here.

**A TOON table cell is quoted with a BACKTICK, and a multi-line value under a KEY is a `|` block.**
`library/tools/toon_serializer.py`. [why](docs/RULE_EVIDENCE.md#the-apostrophe-was-doubled-in-every-prompt)
- **Columns come out in the order the DATA declares them, never sorted.** [why](docs/RULE_EVIDENCE.md#alphabetical-columns-put-end-before-start)
- The quote character must be one neither content class contains - this pipeline sends prose full of apostrophes AND `json.dumps`'d dicts full of double quotes, so `'` and `"` are both out.
- **A lossless round trip is NOT the test.** Nothing downstream calls `toon_to_json` - the model reads the characters. Assert the emitted FORM.
- A nested object in a table cell is `json.dumps`'d. **Do not "fix" it by demoting the table to indexed blocks** - measured, it makes them 19% bigger. [why](docs/RULE_EVIDENCE.md#embedded-json-is-where-the-content-is)
"""

import re
import csv
import io

# ── How a table cell is quoted ────────────────────────────────────────
#
# A cell is quoted only when it needs to be, and the quote character is
# a BACKTICK.  That choice is the whole point.
#
# Two kinds of value travel in these cells and this pipeline sends both:
#
#   * English prose - every transcript line, every scene description.
#     It is full of apostrophes.
#   * A JSON document - the nested-object fallback below serialises a
#     dict or list into one cell with `json.dumps`.  It is full of double
#     quotes, and of backslashes wherever json.dumps escaped something.
#
# Any CSV dialect has to represent the quote character inside a quoted
# field somehow, and there are only two ways: double it, or escape it
# with a backslash.  Backslash-escaping means backslashes themselves get
# escaped, which double-escapes every `\"` and `\n` inside an embedded
# JSON document.  Doubling touches nothing but the quote character.
#
# So: double, and pick a quote character neither content class contains.
# `'` was the old choice and it corrupted the first - `we're` reached
# every prompt as `we''re`, 180 times in one context on project 001,
# because the cell was quoted for its comma and the apostrophe was then
# doubled.  Nothing downstream un-doubles it: the model reads the text
# and never calls `toon_to_json`.  `"` would corrupt the second the same
# way.  A backtick appears in neither, so neither is altered.
_QUOTECHAR = "`"

def _parse_csv_line(line: str) -> list[str]:
    reader = csv.reader([line], quotechar=_QUOTECHAR, doublequote=True)
    return next(reader)

def _format_csv_row(vals: list[str]) -> str:
    writer_file = io.StringIO()
    writer = csv.writer(writer_file, quotechar=_QUOTECHAR, doublequote=True,
                        quoting=csv.QUOTE_MINIMAL, lineterminator="")
    writer.writerow(vals)
    return writer_file.getvalue()

# ── The order the columns come out in ─────────────────────────────────
#
# A table's columns are emitted in the order the DATA declares them -
# first-seen across the rows - and never sorted.
#
# Sorting them alphabetically was the default here and it is wrong twice
# over.  It puts the END of a range before its START: the 110-row
# transcript reached two prompts as `clip_id,end,start,text`, so its
# first row read `clip_006,16.085,14.68,...`, which under the
# conventional reading is a range that finishes before it begins.  And it
# destroys reading priority: the 18-column spine table led with
# `alignment_method` and `block_type` and put `content` - the actual line
# of dialogue - in column four.
#
# The order in the data is not arbitrary.  A spine block is written
# `position, block_type, duration_seconds, ... content, clip_id,
# source_start, source_end, ...`; a music section is written `type,
# start, end, duration, ...`; the transcript view is built `clip_id,
# start, end, text`.  Every one of those is a deliberate reading order
# that alphabetising threw away.  A projected tree carries the order of
# the manifest's own `context_fields`, which is the same kind of
# statement.
#
# This is what the hand-built tables (`cuts_toon`, `transcripts_toon`,
# `broll_candidates_toon`) already did by passing their headers
# explicitly, and they were the only tables in the pipeline that were not
# alphabetical.  Deliberate order is now the rule rather than the
# exception, so a new table gets it without a hand-written header.


def _column_order(data: list) -> list[str]:
    """Every key across the rows, in the order the rows first present it."""
    keys = {}
    for item in data:
        for k in item:
            keys[k] = None
    return list(keys)


def _is_uniform_dict_list(data: list) -> tuple[bool, list[str]]:
    if not data:
        return False, []
    if not all(isinstance(x, dict) for x in data):
        return False, []
    
    # Require that they share at least 50% of the possible keys on average,
    # or just strictly the same keys. Let's do: all dicts must have at least one common key
    # if len > 1, or just same keys.
    keys = _column_order(data)

    if not keys:
        return True, []
        
    # Check if they are "mostly same"
    total_keys = len(keys)
    sum_keys = sum(len(item) for item in data)
    avg_keys = sum_keys / len(data)
    
    if avg_keys / total_keys < 0.51:
        return False, []
    
    return True, keys

# A multi-line string under a dict key is emitted as an indented BLOCK,
# introduced by `|`, rather than escaped onto one line.
#
# The escape is right inside a table cell, where a row is a line and a
# real newline would end it.  Under a key there is no such constraint,
# and the values that travel there are documents: the captain's creative
# brief is 47,903 bytes of markdown, and `\n`-escaping it delivered the
# whole thing as ONE line carrying 700-odd literal `\n`.  A brief the
# model has to unescape before it can read it is the same defect as a
# transcript arriving with doubled apostrophes.
BLOCK_MARKER = "|"


def _format_scalar(val: any) -> str:
    if val is None:
        return ""
    if isinstance(val, bool):
        return "true" if val else "false"
    if isinstance(val, (int, float)):
        return str(val)
    if isinstance(val, str):
        # Inside a table cell a row IS a line, so a newline has to be
        # escaped.  Under a dict key it is emitted as a block instead -
        # see `_format_block` and BLOCK_MARKER above.
        val = val.replace('\n', '\\n')
        return val
    return str(val)


def _format_block(val: str, indent: int) -> list[str]:
    """The lines of a `|` block, indented under the key that introduces it."""
    ind = " " * indent
    # A blank source line is emitted blank rather than as trailing
    # whitespace; the reader below takes an under-indented BLANK line as
    # part of the block, and only a non-blank one ends it.
    return [ind + line if line else "" for line in val.split('\n')]

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
            elif isinstance(v, str) and ('\n' in v
                                         or v.strip() == BLOCK_MARKER):
                # The bare marker goes through the block route too, or a
                # value that IS `|` would read back as an empty block
                # that swallowed the keys under it.
                lines.append(f"{ind}{k}: {BLOCK_MARKER}")
                lines.extend(_format_block(v, indent + 2))
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

def _read_block(lines: list[str], start_idx: int, indent: int):
    """Read back a `|` block. Returns (value, index of the line after it)."""
    collected = []
    idx = start_idx
    while idx < len(lines):
        line = lines[idx]
        line_indent = len(line) - len(line.lstrip(' '))
        if line_indent >= indent:
            collected.append(line[indent:])
        elif not line.strip():
            # A blank line inside the block. Only a non-blank line at a
            # shallower indent ends it - the writer emits blanks bare, so
            # a blank can never belong to the key that follows.
            collected.append("")
        else:
            break
        idx += 1
    return "\n".join(collected), idx


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
            # Exactly the header's indent, never `lstrip`: a row is
            # written at the header's indent, and the first cell may
            # itself begin with a space.  Stripping greedily ate those,
            # which only showed once a column with leading whitespace
            # could be the first one.
            row_indent = len(first_line) - len(first_line.lstrip(' '))
            for _ in range(count):
                if idx >= len(lines):
                    break
                row_line = lines[idx]
                eaten = len(row_line) - len(row_line.lstrip(' '))
                row_line = row_line[min(row_indent, eaten):]
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
                if v_str == BLOCK_MARKER:
                    result[k], idx = _read_block(lines, idx + 1,
                                                 line_indent + 2)
                elif v_str:
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
