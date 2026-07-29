"""
Fusion .comp / .setting Parser (Layer 0).

Recursive descent parser for the Lua table subset used by Fusion
composition files (.comp) and macro files (.setting). Parses into
the engine's FusionComp / FusionNode / BezierSpline objects.

Handles:
- Tool definitions: Name = ToolType { Inputs = { ... }, }
- BezierSpline keyframes with LH/RH handles
- SourceOp wiring
- GroupOperator / MacroOperator (.setting macros)
- ordered() wrappers (treated same as plain tables)
- Quoted keys: ["MediaIn1.GlobalStart"]
- Point values: { 0.5, 0.49 }
- Expressions: Input { Expression = "..." }
- ViewInfo / OperatorInfo positions

NOT handled:
- Fuse plugins (.fuse) — actual Lua scripts with logic
- DCTL shaders (.dctl) — GPU code
- Standalone Lua scripts (.lua) — procedural code
"""

from __future__ import annotations

import re
from typing import Optional, Union

from .nodes import BezierSpline, FusionComp, FusionNode


def parse_comp(content: str) -> FusionComp:
    """Parse a .comp file string into a FusionComp object.

    Returns a fully populated FusionComp that can be inspected,
    modified, and re-serialized.
    """
    parser = _LuaTableParser(content)
    data = parser.parse()
    return _build_comp(data)


def parse_comp_file(path: str) -> FusionComp:
    """Read and parse a .comp file from disk."""
    with open(path, "r") as f:
        return parse_comp(f.read())


def parse_setting(content: str) -> FusionComp:
    """Parse a .setting file (Fusion macro) into a FusionComp.

    .setting files use the same Lua table format but wrap tools
    in GroupOperator or MacroOperator containers.
    """
    parser = _LuaTableParser(content)
    data = parser.parse()
    return _build_from_setting(data)


def parse_setting_file(path: str) -> FusionComp:
    """Read and parse a .setting file from disk."""
    with open(path, "r") as f:
        return parse_setting(f.read())


# ─── Lua Table Parser ────────────────────────────────────────


class _LuaTableParser:
    """Recursive descent parser for Fusion's Lua table subset.

    Tokens:
        {  }  =  ,  [  ]  (  )
        identifier  number  string  boolean
        "ordered"  "Input"  "BezierSpline"  etc.
    """

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.length = len(text)

    def parse(self) -> dict:
        """Parse the top-level structure."""
        self._skip_ws()
        # .comp files start with "Composition {"
        # .setting files start with "{"
        token = self._peek_identifier()
        if token == "Composition":
            self._consume_identifier()
            return self._parse_table()
        elif self._peek_char() == "{":
            return self._parse_table()
        else:
            # Try to consume an identifier then table
            self._consume_identifier()
            return self._parse_table()

    def _parse_value(self) -> object:
        """Parse a value: table, string, number, boolean, or identifier call."""
        self._skip_ws()
        ch = self._peek_char()

        if ch == "{":
            return self._parse_table()
        elif ch == '"':
            return self._parse_string()
        elif ch == "-" or ch.isdigit() or ch == ".":
            return self._parse_number()
        elif ch == "":
            return None
        else:
            # Identifier — could be:
            # - boolean (true/false)
            # - nil
            # - A type constructor like Input { ... }
            # - A standalone identifier reference
            ident = self._consume_identifier()

            if ident == "true":
                return True
            elif ident == "false":
                return False
            elif ident == "nil":
                return None
            elif ident == "ordered":
                # ordered() { ... } — just parse the table inside
                self._skip_ws()
                if self._peek_char() == "(":
                    self._consume_char()  # (
                    self._skip_ws()
                    self._consume_char()  # )
                self._skip_ws()
                if self._peek_char() == "{":
                    return self._parse_table()
                return {}
            else:
                # Check if followed by { — type constructor
                self._skip_ws()
                if self._peek_char() == "{":
                    table = self._parse_table()
                    table["_type"] = ident
                    return table
                else:
                    return ident

    def _parse_table(self) -> dict:
        """Parse a Lua table { ... } into a dict."""
        self._expect("{")
        result = {}
        array_index = 0

        while True:
            self._skip_ws()
            if self._peek_char() == "}":
                self._consume_char()
                return result
            if self._peek_char() == "":
                return result  # EOF

            # Check for [key] = value  (bracketed key)
            if self._peek_char() == "[":
                self._consume_char()
                self._skip_ws()

                if self._peek_char() == '"':
                    # ["quoted.key"] = value
                    key = self._parse_string()
                else:
                    # [123] = value  (integer key, e.g., keyframes)
                    key = self._parse_number()

                self._skip_ws()
                self._expect("]")
                self._skip_ws()
                self._expect("=")
                value = self._parse_value()
                result[key] = value
            elif self._peek_char() == '"':
                # Could be a string value in an array-like table
                # or a quoted key followed by =
                saved = self.pos
                s = self._parse_string()
                self._skip_ws()
                if self._peek_char() == "=":
                    self._consume_char()
                    value = self._parse_value()
                    result[s] = value
                else:
                    result[array_index] = s
                    array_index += 1
            elif self._peek_char() == "-" or self._peek_char().isdigit() or self._peek_char() == ".":
                # Numeric value in an array-like table (e.g., { 1.0, 0.5 })
                num = self._parse_number()
                result[array_index] = num
                array_index += 1
            else:
                # identifier = value  OR  identifier  (standalone)
                ident = self._consume_identifier()
                if not ident:
                    # Skip unknown character
                    self._consume_char()
                    continue

                self._skip_ws()
                if self._peek_char() == "=":
                    self._consume_char()
                    value = self._parse_value()
                    result[ident] = value
                else:
                    # Standalone identifier (e.g., in Transitions = { [0] = "DFTDissolve" })
                    result[array_index] = ident
                    array_index += 1

            # Skip trailing comma
            self._skip_ws()
            if self._peek_char() == ",":
                self._consume_char()

    def _parse_string(self) -> str:
        """Parse a quoted string."""
        self._expect('"')
        start = self.pos
        while self.pos < self.length:
            if self.text[self.pos] == "\\":
                self.pos += 2  # skip escaped char
            elif self.text[self.pos] == '"':
                s = self.text[start : self.pos]
                self.pos += 1
                return s
            else:
                self.pos += 1
        return self.text[start:]

    def _parse_number(self) -> Union[int, float]:
        """Parse a numeric literal."""
        start = self.pos
        if self._peek_char() == "-":
            self.pos += 1
        while self.pos < self.length and (
            self.text[self.pos].isdigit()
            or self.text[self.pos] == "."
            or self.text[self.pos] in "eE+-"
        ):
            self.pos += 1
        s = self.text[start : self.pos]
        if not s or s == "-":
            return 0
        if "." in s or "e" in s.lower():
            return float(s)
        return int(s)

    def _consume_identifier(self) -> str:
        """Consume an identifier [a-zA-Z_][a-zA-Z0-9_]*."""
        start = self.pos
        while self.pos < self.length and (
            self.text[self.pos].isalnum() or self.text[self.pos] == "_"
        ):
            self.pos += 1
        return self.text[start : self.pos]

    def _peek_identifier(self) -> str:
        """Peek at the next identifier without consuming."""
        saved = self.pos
        ident = self._consume_identifier()
        self.pos = saved
        return ident

    def _peek_char(self) -> str:
        """Peek at the next non-whitespace character."""
        self._skip_ws()
        if self.pos >= self.length:
            return ""
        return self.text[self.pos]

    def _consume_char(self) -> str:
        """Consume and return the next character."""
        if self.pos >= self.length:
            return ""
        ch = self.text[self.pos]
        self.pos += 1
        return ch

    def _expect(self, ch: str):
        """Expect and consume a specific character."""
        self._skip_ws()
        if self.pos < self.length and self.text[self.pos] == ch:
            self.pos += 1
        # Silently skip if not found — fault tolerance

    def _skip_ws(self):
        """Skip whitespace and Lua comments (-- ...)."""
        while self.pos < self.length:
            if self.text[self.pos] in " \t\n\r":
                self.pos += 1
            elif (
                self.pos + 1 < self.length
                and self.text[self.pos] == "-"
                and self.text[self.pos + 1] == "-"
            ):
                # Skip to end of line
                while self.pos < self.length and self.text[self.pos] != "\n":
                    self.pos += 1
            else:
                break


# ─── AST → Object Model ─────────────────────────────────────


def _build_comp(data: dict) -> FusionComp:
    """Convert parsed Lua table dict into a FusionComp."""
    # Extract duration from RenderRange
    render_range = data.get("RenderRange", {})
    if isinstance(render_range, dict):
        last_frame = render_range.get(1, render_range.get(0, 75))
        duration = last_frame + 1
    else:
        duration = 76

    comp = FusionComp(duration=duration)

    # Parse Tools
    tools = data.get("Tools", {})
    _parse_tools_into_comp(tools, comp)

    return comp


def _build_from_setting(data: dict) -> FusionComp:
    """Convert a .setting file's parsed data into a FusionComp."""
    tools = data.get("Tools", {})

    # .setting files typically have one top-level GroupOperator/MacroOperator
    # with nested Tools inside
    comp = FusionComp(duration=100)  # default; no RenderRange in .setting

    for name, value in tools.items():
        if name.startswith("_"):
            continue
        if not isinstance(value, dict):
            continue

        tool_type = value.get("_type", "")

        if tool_type in ("GroupOperator", "MacroOperator"):
            # Parse the inner tools
            inner_tools = value.get("Tools", {})
            _parse_tools_into_comp(inner_tools, comp)
        else:
            _add_tool_to_comp(name, value, comp)

    return comp


def _parse_tools_into_comp(tools: dict, comp: FusionComp):
    """Parse a Tools dict and add nodes to comp."""
    for name, value in tools.items():
        if name.startswith("_"):
            continue
        if not isinstance(value, dict):
            continue
        _add_tool_to_comp(name, value, comp)


def _add_tool_to_comp(name: str, value: dict, comp: FusionComp):
    """Add a single tool definition to the comp."""
    tool_type = value.get("_type", "Unknown")

    if tool_type == "BezierSpline":
        _add_spline(name, value, comp)
    else:
        _add_node(name, tool_type, value, comp)


def _add_spline(name: str, data: dict, comp: FusionComp):
    """Parse a BezierSpline entry."""
    # SplineColor
    sc = data.get("SplineColor", {})
    color = (
        int(sc.get("Red", 233)),
        int(sc.get("Green", 217)),
        int(sc.get("Blue", 11)),
    )

    spline = BezierSpline(name, color=color)

    # KeyFrames
    keyframes = data.get("KeyFrames", {})
    for frame_key, kf_data in sorted(keyframes.items(), key=lambda x: _to_num(x[0])):
        if isinstance(frame_key, str) and frame_key.startswith("_"):
            continue
        frame = int(_to_num(frame_key))

        if isinstance(kf_data, dict):
            value = kf_data.get(0, 0.0)
            lh_data = kf_data.get("LH")
            rh_data = kf_data.get("RH")
            flags_data = kf_data.get("Flags")

            lh = None
            if isinstance(lh_data, dict):
                lh = (_to_num(lh_data.get(0, 0)), _to_num(lh_data.get(1, 0)))

            rh = None
            if isinstance(rh_data, dict):
                rh = (_to_num(rh_data.get(0, 0)), _to_num(rh_data.get(1, 0)))

            flags = None
            if isinstance(flags_data, dict):
                flags = {k: v for k, v in flags_data.items() if not str(k).startswith("_")}

            spline.add_key(frame, float(value), lh=lh, rh=rh, flags=flags)

    comp.add_node(spline)


def _add_node(name: str, tool_type: str, data: dict, comp: FusionComp):
    """Parse a tool node entry."""
    node = FusionNode(name, tool_type)

    # Top-level attributes
    for attr in ("CtrlWZoom", "CtrlWShown", "NameSet"):
        if attr in data:
            node.set_attr(attr, data[attr])

    # Inputs
    inputs = data.get("Inputs", {})
    for inp_name, inp_data in inputs.items():
        if inp_name.startswith("_"):
            continue
        _set_parsed_input(node, inp_name, inp_data)

    # ViewInfo → position
    view_info = data.get("ViewInfo", {})
    pos = view_info.get("Pos", {})
    if isinstance(pos, dict):
        node.pos = (
            int(_to_num(pos.get(0, 0))),
            int(_to_num(pos.get(1, 0))),
        )

    comp.add_node(node)


def _set_parsed_input(node: FusionNode, name: str, data):
    """Set a parsed input on a node."""
    if not isinstance(data, dict):
        # Simple value
        node.inputs[name] = {"_type": "value", "value": data}
        return

    inp_type = data.get("_type", "")

    if "SourceOp" in data:
        # Wired connection
        node.inputs[name] = {
            "_type": "sourceop",
            "SourceOp": data["SourceOp"],
            "Source": data.get("Source", "Output"),
        }
    elif "Expression" in data:
        # Expression input
        node.inputs[name] = {
            "_type": "raw",
            "Expression": data["Expression"],
        }
        if "Value" in data:
            node.inputs[name]["Value"] = data["Value"]
    elif "Value" in data:
        val = data["Value"]
        if isinstance(val, dict):
            # Point value { 0.5, 0.49 }
            if 0 in val and 1 in val:
                node.inputs[name] = {
                    "_type": "point",
                    "value": (_to_num(val[0]), _to_num(val[1])),
                }
            else:
                node.inputs[name] = {"_type": "value", "value": val}
        else:
            node.inputs[name] = {"_type": "value", "value": val}
    else:
        # Store as raw dict
        clean = {k: v for k, v in data.items() if not str(k).startswith("_")}
        if clean:
            node.inputs[name] = clean


def _to_num(v) -> float:
    """Safely convert to number."""
    if isinstance(v, (int, float)):
        return v
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0
