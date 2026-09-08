"""
Fusion Node Graph Object Model (Layer 1).

Python objects representing Fusion compositions, tools (nodes), and
animated parameters (BezierSplines). These serialize to the Lua table
syntax that .comp files use.

Design principles:
- Each object validates itself on construction and serialization
- Safety rules from fusion_gotchas.md are enforced in the serializer
- Objects can be loaded from a parser (Layer 0) or built programmatically
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Optional, Union


# ─── Easing Functions (match DaVinci LUTLookup curves) ────────


def ease_linear(t: float) -> float:
    return t


def ease_sine(t: float) -> float:
    """Sine ease-in-out: slow start and end, fast middle."""
    return 0.5 * (1.0 - math.cos(math.pi * t))


def ease_quad(t: float) -> float:
    """Quadratic ease-in-out."""
    if t < 0.5:
        return 2.0 * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 2 / 2.0


def ease_cubic(t: float) -> float:
    """Cubic ease-in-out."""
    if t < 0.5:
        return 4.0 * t * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 3 / 2.0


def ease_quart(t: float) -> float:
    """Quartic ease-in-out."""
    if t < 0.5:
        return 8.0 * t * t * t * t
    return 1.0 - (-2.0 * t + 2.0) ** 4 / 2.0


EASING_FUNCTIONS = {
    "Linear": ease_linear,
    "Sine": ease_sine,
    "Quad": ease_quad,
    "Cubic": ease_cubic,
    "Quart": ease_quart,
}


# ─── BezierSpline ────────────────────────────────────────────


@dataclass
class Keyframe:
    """A single keyframe in a BezierSpline."""
    frame: int
    value: float
    lh: Optional[tuple[float, float]] = None  # left handle (frame, value)
    rh: Optional[tuple[float, float]] = None  # right handle (frame, value)
    flags: Optional[dict] = None  # e.g., {"Linear": True}

    def serialize(self, indent: str = "\t\t\t\t") -> str:
        """Serialize to Lua keyframe entry."""
        parts = [f"{self.value}"]

        if self.lh:
            parts.append(f"LH = {{ {self.lh[0]}, {self.lh[1]} }}")
        if self.rh:
            parts.append(f"RH = {{ {self.rh[0]}, {self.rh[1]} }}")
        if self.flags:
            flag_items = ", ".join(
                f"{k} = {_lua_value(v)}" for k, v in self.flags.items()
            )
            parts.append(f"Flags = {{ {flag_items} }}")

        inner = ", ".join(parts)
        return f"{indent}[{self.frame}] = {{ {inner} }},"


class BezierSpline:
    """An animated parameter represented as a BezierSpline with keyframes.

    Serializes to:
        Name = BezierSpline {
            SplineColor = { Red = 233, Green = 217, Blue = 11 },
            KeyFrames = {
                [0] = { 1.0, RH = { 25, 1.013 } },
                [75] = { 1.03, LH = { 50, 1.033 } },
            }
        }
    """

    def __init__(
        self,
        name: str,
        *,
        color: Optional[tuple[int, int, int]] = None,
    ):
        self.name = name
        self.color = color or (233, 217, 11)  # default yellow
        self.keyframes: list[Keyframe] = []

    def add_key(
        self,
        frame: int,
        value: float,
        *,
        lh: Optional[tuple[float, float]] = None,
        rh: Optional[tuple[float, float]] = None,
        flags: Optional[dict] = None,
    ) -> "BezierSpline":
        """Add a keyframe. Returns self for chaining."""
        self.keyframes.append(Keyframe(frame, value, lh, rh, flags))
        return self

    @classmethod
    def sampled(
        cls,
        name: str,
        *,
        start_frame: int,
        end_frame: int,
        easing: str = "Sine",
        mirror: bool = False,
        scale: float = 1.0,
        offset: float = 0.0,
        reverse: bool = False,
        hold_before: Optional[float] = None,
        hold_after: Optional[float] = None,
        color: Optional[tuple[int, int, int]] = None,
    ) -> "BezierSpline":
        """Create a BezierSpline by sampling an easing curve at every frame.

        Replicates DaVinci's LUTLookup behavior as pre-baked keyframes.

        Args:
            name: Spline name.
            start_frame: First frame of the transition region.
            end_frame: Last frame of the transition region.
            easing: One of "Linear", "Sine", "Quad", "Cubic", "Quart".
            mirror: If True, curve goes 0→1→0 (peaks at midpoint).
            scale: Multiply the eased value by this.
            offset: Add this to the scaled value.
            reverse: If True, goes 1→0 instead of 0→1.
            hold_before: If set, add a keyframe at frame 0 with this value.
            hold_after: If set, add a keyframe at end_frame+N with this value.
            color: Spline color.

        Returns:
            BezierSpline with one keyframe per frame, all linearized.
        """
        ease_fn = EASING_FUNCTIONS.get(easing, ease_sine)
        dur = end_frame - start_frame
        if dur <= 0:
            dur = 1

        spline = cls(name, color=color or (255, 200, 50))

        # Optional hold before transition
        if hold_before is not None and start_frame > 0:
            spline.add_key(0, hold_before, flags={"Linear": True})

        for f in range(start_frame, end_frame + 1):
            t = (f - start_frame) / dur  # 0→1

            if mirror:
                # Peak at midpoint: 0→1→0
                if t <= 0.5:
                    eased = ease_fn(t * 2.0)
                else:
                    eased = ease_fn((1.0 - t) * 2.0)
            else:
                eased = ease_fn(t)

            if reverse:
                eased = 1.0 - eased

            value = eased * scale + offset
            spline.add_key(f, round(value, 6), flags={"Linear": True})

        # Optional hold after transition
        if hold_after is not None:
            last_f = spline.keyframes[-1].frame
            if hold_after > last_f:
                spline.add_key(int(hold_after), spline.keyframes[-1].value,
                               flags={"Linear": True})

        return spline

    def linearize(self) -> "BezierSpline":
        """Compute explicit LH/RH handles for linear interpolation.

        Resolve ignores ``Flags = { Linear = true }`` during
        ImportFusionComp and auto-generates smooth Bezier handles.
        This method sets explicit handle coordinates that force the
        spline to interpolate linearly between each pair of keyframes.

        For a linear segment from keyframe A (f_a, v_a) to B (f_b, v_b):
          A.RH = ( (f_b - f_a) / 3,  (v_b - v_a) / 3 )
          B.LH = ( -(f_b - f_a) / 3, -(v_b - v_a) / 3 )

        Call after all add_key() calls, before serialize().
        Returns self for chaining.
        """
        kfs = self.keyframes
        for i in range(len(kfs)):
            kf = kfs[i]
            # Left handle — based on segment from previous keyframe
            if i > 0:
                prev = kfs[i - 1]
                df = kf.frame - prev.frame
                dv = kf.value - prev.value
                kf.lh = (-df / 3.0, -dv / 3.0)
            # Right handle — based on segment to next keyframe
            if i < len(kfs) - 1:
                nxt = kfs[i + 1]
                df = nxt.frame - kf.frame
                dv = nxt.value - kf.value
                kf.rh = (df / 3.0, dv / 3.0)
        return self

    def serialize(self, indent: str = "\t\t") -> str:
        """Serialize to Lua BezierSpline block."""
        i1 = indent
        i2 = indent + "\t"
        i3 = indent + "\t\t"

        kf_lines = "\n".join(kf.serialize(i3) for kf in self.keyframes)

        r, g, b = self.color
        return (
            f"{i1}{self.name} = BezierSpline {{\n"
            f"{i2}SplineColor = {{ Red = {r}, Green = {g}, Blue = {b} }},\n"
            f"{i2}KeyFrames = {{\n"
            f"{kf_lines}\n"
            f"{i2}}}\n"
            f"{i1}}},"
        )

    def __repr__(self) -> str:
        return f"BezierSpline({self.name!r}, {len(self.keyframes)} keys)"


# ─── FusionNode ──────────────────────────────────────────────


# Sentinel for "no value" vs None
_UNSET = object()


class FusionNode:
    """A Fusion tool (Transform, Merge, BrightnessContrast, etc.).

    Represents a single node in the Fusion node graph with:
    - A name (e.g., "Transform1")
    - A tool type (e.g., "Transform")
    - Attributes (e.g., CtrlWZoom = false)
    - Inputs (parameters, wiring to other nodes, animated values)
    - Position on the Fusion canvas

    Serializes to:
        Transform1 = Transform {
            CtrlWZoom = false,
            Inputs = {
                Size = Input {
                    SourceOp = "Transform1Size",
                    Source = "Value",
                },
                Input = Input {
                    SourceOp = "MediaIn1",
                    Source = "Output",
                },
            },
            ViewInfo = OperatorInfo { Pos = { 110, 0 } },
        },
    """

    def __init__(self, name: str, tool_type: str):
        self.name = name
        self.tool_type = tool_type
        self.attrs: dict[str, object] = {}
        self.inputs: dict[str, object] = {}
        self.clips: list[dict] = []
        self.pos: tuple[int, int] = (0, 0)

    def set_attr(self, key: str, value: object) -> "FusionNode":
        """Set a top-level attribute (e.g., CtrlWZoom)."""
        self.attrs[key] = value
        return self

    def set_input(
        self,
        name: str,
        value: object,
        *,
        source: str = "Output",
    ) -> "FusionNode":
        """Set an input parameter.

        Args:
            name: Input name (e.g., "Size", "Gain", "Input")
            value: One of:
                - str: Wire to another node (SourceOp reference)
                - BezierSpline: Animated parameter reference
                - float/int: Static numeric value
                - tuple: Static coordinate value (x, y)
                - dict: Raw input dict (for special cases)
            source: Source type for wired inputs (default "Output",
                    use "Value" for BezierSpline, "Mask" for mask inputs)
        """
        if isinstance(value, BezierSpline):
            # Wire to a BezierSpline by name
            self.inputs[name] = {
                "_type": "sourceop",
                "SourceOp": value.name,
                "Source": "Value",
            }
        elif isinstance(value, str):
            # Wire to another node's output
            self.inputs[name] = {
                "_type": "sourceop",
                "SourceOp": value,
                "Source": source,
            }
        elif isinstance(value, (int, float)):
            self.inputs[name] = {"_type": "value", "value": value}
        elif isinstance(value, tuple) and len(value) == 2:
            self.inputs[name] = {"_type": "point", "value": value}
        elif isinstance(value, dict):
            self.inputs[name] = value
        else:
            raise TypeError(
                f"Unsupported input type for {name}: {type(value)}"
            )
        return self

    def add_clip(self, filename: str, *, start_frame: int = 0,
                 length: Optional[int] = None) -> "FusionNode":
        """Point a Loader at the first frame of a numbered image sequence.

        A matte is frames of pixels, and a `.comp` is text: the Loader is
        the node that turns one into the other. `filename` is the first
        frame's path; Resolve follows the numbering from it. `length` trims
        the sequence (TrimOut); without it the Loader reads to the last
        frame on disk.
        """
        clip: dict[str, object] = {
            "ID": "Clip1",
            "Filename": filename,
            "StartFrame": start_frame,
            "LengthSetManually": True,
            "TrimIn": 0,
            "ExtendFirst": False,
            "ExtendLast": False,
            "Loop": 1,
            "GlobalStart": start_frame,
        }
        if length is not None:
            clip["TrimOut"] = max(int(length) - 1, 0)
            clip["GlobalEnd"] = start_frame + max(int(length) - 1, 0)
        self.clips.append(clip)
        return self

    def _validate(self) -> None:
        """Enforce safety rules from AGENTS.md / fusion_gotchas.md.

        These are AUTHORSHIP rules - they describe comps this pipeline
        writes. They are not a well-formedness check, so they must not be
        pointed at a file someone else authored; see FusionComp.authored.
        """
        # NEVER use ApplyMode on Merge → SIGSEGV crash
        if self.tool_type == "Merge" and "ApplyMode" in self.inputs:
            raise ValueError(
                f"{self.name}: ApplyMode on Merge CRASHES Resolve (SIGSEGV). "
                "Use Normal mode (default) at reduced Blend opacity."
            )

        # NEVER use BlendClone — silently ignored, use Blend
        if "BlendClone" in self.inputs:
            raise ValueError(
                f"{self.name}: BlendClone is silently ignored. Use 'Blend'."
            )

        # Background nodes MUST have GlobalOut
        if self.tool_type == "Background":
            has_global_out = any(
                k == "GlobalOut" for k in self.inputs
            )
            if not has_global_out:
                raise ValueError(
                    f"{self.name}: Background node missing GlobalOut. "
                    "This causes rendering to stop mid-clip."
                )

        # EllipseMask must have Inverted, MaskWidth, MaskHeight, PixelAspect
        if self.tool_type == "EllipseMask":
            required = {"Inverted", "MaskWidth", "MaskHeight", "PixelAspect"}
            missing = required - set(self.inputs.keys())
            if missing:
                raise ValueError(
                    f"{self.name}: EllipseMask missing required inputs: "
                    f"{missing}. These are needed for correct vignette rendering."
                )

        # DirectionalBlur Length should not exceed 5
        if self.tool_type == "DirectionalBlur" and "Length" in self.inputs:
            inp = self.inputs["Length"]
            if (
                isinstance(inp, dict)
                and inp.get("_type") == "value"
                and inp["value"] > 5
            ):
                raise ValueError(
                    f"{self.name}: DirectionalBlur Length {inp['value']} > 5. "
                    "Creates artifacts and edge tiling."
                )

        # Defocus XDefocusSize should not exceed 3.0
        if self.tool_type == "Defocus" and "XDefocusSize" in self.inputs:
            inp = self.inputs["XDefocusSize"]
            if (
                isinstance(inp, dict)
                and inp.get("_type") == "value"
                and inp["value"] > 3.0
            ):
                raise ValueError(
                    f"{self.name}: Defocus XDefocusSize {inp['value']} > 3.0. "
                    "Creates excessive blur artifacts."
                )

    def serialize(self, indent: str = "\t\t", *, validate: bool = True) -> str:
        """Serialize to Lua tool definition.

        ``validate=False`` skips the authorship rules. Only a comp that
        came from the parser passes it - see FusionComp.authored.
        """
        if validate:
            self._validate()

        i1 = indent
        i2 = indent + "\t"
        i3 = indent + "\t\t"

        lines = [f"{i1}{self.name} = {self.tool_type} {{"]

        # Attributes (top-level, before Inputs)
        for key, val in self.attrs.items():
            lines.append(f"{i2}{key} = {_lua_value(val)},")

        # Clips table (Loader image sequences)
        if self.clips:
            lines.append(f"{i2}Clips = {{")
            for clip in self.clips:
                lines.append(f"{i2}\tClip {{")
                for key, val in clip.items():
                    lines.append(f"{i2}\t\t{key} = {_lua_value(val)},")
                lines.append(f"{i2}\t}},")
            lines.append(f"{i2}}},")

        # Inputs block
        if self.inputs:
            lines.append(f"{i2}Inputs = {{")
            for inp_name, inp_val in self.inputs.items():
                lines.append(_serialize_input(inp_name, inp_val, i3))
            lines.append(f"{i2}}},")

        # ViewInfo
        px, py = self.pos
        lines.append(
            f"{i2}ViewInfo = OperatorInfo {{ Pos = {{ {px}, {py} }} }},"
        )

        lines.append(f"{i1}}},")
        return "\n".join(lines)

    def __repr__(self) -> str:
        return (
            f"FusionNode({self.name!r}, {self.tool_type!r}, "
            f"inputs={list(self.inputs.keys())})"
        )


# ─── FusionComp ──────────────────────────────────────────────


class FusionComp:
    """A complete Fusion composition — container of nodes + serializer.

    Serializes to a complete .comp file:
        Composition {
            CurrentTime = 0,
            RenderRange = { 0, 75 },
            ...
            Tools = {
                MediaIn1 = MediaIn { ... },
                Transform1 = Transform { ... },
                ...
                MediaOut1 = MediaOut { ... },
            },
        }
    """

    def __init__(self, duration: int, *, authored: bool = True):
        """
        Args:
            duration: Composition length in frames.
            authored: True when this pipeline built the comp, which is
                when the gotcha rules in FusionNode._validate and
                _validate_global apply. The parser passes False: those
                rules describe comps we write, and DaVinci's own shipped
                macros legitimately break them - 20 of the 143 built-in
                presets carry a Background with no GlobalOut, 14 an
                EllipseMask with no Inverted, 4 an ApplyMode on Merge.
                Auditing a foreign file against them reported those
                presets as crashes when nothing was wrong with them.
        """
        self.duration = duration
        self.authored = authored
        self.nodes: list[Union[FusionNode, BezierSpline]] = []

    @property
    def last_frame(self) -> int:
        return self.duration - 1

    def add_node(self, node: Union[FusionNode, BezierSpline]) -> "FusionComp":
        """Add a node or spline to the composition."""
        self.nodes.append(node)
        return self

    def add_nodes(
        self, nodes: list[Union[FusionNode, BezierSpline]]
    ) -> "FusionComp":
        """Add multiple nodes at once."""
        self.nodes.extend(nodes)
        return self

    def find_node(self, name: str) -> Optional[FusionNode]:
        """Find a node by name."""
        for n in self.nodes:
            if hasattr(n, "name") and n.name == name:
                return n
        return None

    def find_nodes_by_type(self, tool_type: str) -> list[FusionNode]:
        """Find all nodes of a given tool type."""
        return [
            n
            for n in self.nodes
            if isinstance(n, FusionNode) and n.tool_type == tool_type
        ]

    def _validate_global(self) -> None:
        """Validate cross-node rules.

        NEVER use Path {} when a Merge node exists in the same comp.
        """
        has_merge = any(
            isinstance(n, FusionNode) and n.tool_type == "Merge"
            for n in self.nodes
        )
        if has_merge:
            for n in self.nodes:
                if isinstance(n, BezierSpline):
                    continue
                if not isinstance(n, FusionNode):
                    continue
                for inp_name, inp_val in n.inputs.items():
                    if (
                        isinstance(inp_val, dict)
                        and inp_val.get("_type") == "path"
                    ):
                        raise ValueError(
                            f"{n.name}.{inp_name}: Path type used with Merge "
                            "in same comp → causes black output. "
                            "Use static Center value instead."
                        )

        # Build spline lookup
        splines = {
            n.name: n
            for n in self.nodes
            if isinstance(n, BezierSpline)
        }

        # Check safety limits on animated values
        for n in self.nodes:
            if not isinstance(n, FusionNode):
                continue
                
            # DirectionalBlur Length <= 5.0
            if n.tool_type == "DirectionalBlur" and "Length" in n.inputs:
                inp = n.inputs["Length"]
                if isinstance(inp, dict) and inp.get("_type") == "sourceop":
                    spline_name = inp.get("SourceOp")
                    if spline_name in splines:
                        spline = splines[spline_name]
                        if spline.keyframes:
                            max_val = max(kf.value for kf in spline.keyframes)
                            if max_val > 5.0:
                                raise ValueError(
                                    f"{n.name}: DirectionalBlur Length animated peak {max_val} > 5. "
                                    "Creates artifacts and edge tiling."
                                )

            # Transform zoom (Size) <= 1.04
            if n.tool_type == "Transform" and "Size" in n.inputs:
                inp = n.inputs["Size"]
                if isinstance(inp, dict) and inp.get("_type") == "sourceop":
                    spline_name = inp.get("SourceOp")
                    if spline_name in splines:
                        spline = splines[spline_name]
                        if spline.keyframes:
                            max_val = max(kf.value for kf in spline.keyframes)
                            if max_val > 1.04:
                                raise ValueError(
                                    f"{n.name}: Transform zoom (Size) animated peak {max_val} > 1.04. "
                                    "Too aggressive, breaks immersion."
                                )

    def serialize(self) -> str:
        """Serialize to a complete .comp file string."""
        if self.authored:
            self._validate_global()

        last = self.last_frame
        lines = [
            f"Composition {{",
            f"\tCurrentTime = 0,",
            f"\tRenderRange = {{ 0, {last} }},",
            f"\tGlobalRange = {{ 0, {last} }},",
            f"\tCurrentID = 10,",
            f"\tHiQ = true,",
            f"\tPlaybackUpdateMode = 0,",
            f'\tVersion = "Generated by pipeline",',
            f"\tTools = {{",
        ]

        for node in self.nodes:
            if isinstance(node, FusionNode):
                lines.append(node.serialize(validate=self.authored))
            else:
                lines.append(node.serialize())

        lines.append("\t},")
        lines.append("}")

        return "\n".join(lines)

    def dump(self) -> str:
        """Human-readable summary of the composition for debugging."""
        parts = [f"FusionComp(duration={self.duration}, nodes={len(self.nodes)})"]
        for n in self.nodes:
            if isinstance(n, BezierSpline):
                parts.append(f"  ├─ {n!r}")
            elif isinstance(n, FusionNode):
                parts.append(f"  ├─ {n!r}")
        return "\n".join(parts)

    def __repr__(self) -> str:
        return f"FusionComp(duration={self.duration}, {len(self.nodes)} nodes)"


# ─── Serialization Helpers ───────────────────────────────────


def _lua_value(val: object) -> str:
    """Convert a Python value to Lua syntax."""
    if isinstance(val, bool):
        return "true" if val else "false"
    elif isinstance(val, (int, float)):
        return str(val)
    elif isinstance(val, str):
        return f'"{val}"'
    elif isinstance(val, tuple) and len(val) == 2:
        return f"{{ {val[0]}, {val[1]} }}"
    elif val is None:
        return "nil"
    else:
        return str(val)


def _serialize_input(name: str, val: dict, indent: str) -> str:
    """Serialize a single Input entry to Lua."""
    i2 = indent + "\t"

    if not isinstance(val, dict):
        raise TypeError(f"Input {name} has unexpected type: {type(val)}")

    inp_type = val.get("_type", "raw")

    if inp_type == "sourceop":
        # Wired to another node/spline
        src_op = val["SourceOp"]
        src = val["Source"]
        return (
            f"{indent}{name} = Input {{\n"
            f"{i2}SourceOp = \"{src_op}\",\n"
            f"{i2}Source = \"{src}\",\n"
            f"{indent}}},"
        )
    elif inp_type == "value":
        # Through _lua_value, not str(): a Python bool renders as "True",
        # which Lua reads as an undefined global (nil), and a bare string
        # renders unquoted. Numbers are unchanged either way.
        # A dict payload still falls through to repr() inside _lua_value -
        # only a parsed foreign comp can produce one, and those are no
        # longer re-serialized by the pipeline (see builtin_effect_loader).
        v = val["value"]
        return f"{indent}{name} = Input {{ Value = {_lua_value(v)}, }},"
    elif inp_type == "point":
        x, y = val["value"]
        return f"{indent}{name} = Input {{ Value = {{ {x}, {y} }}, }},"
    elif inp_type == "quoted_key":
        # For keys like ["MediaIn1.GlobalStart"]
        inner = val.get("inner", "")
        v = val["value"]
        return f'{indent}["{name}"] = Input {{ Value = {v}, }},'
    else:
        # Raw passthrough — serialize dict as-is
        # This handles edge cases the caller manages directly
        parts = []
        for k, v in val.items():
            if k.startswith("_"):
                continue
            parts.append(f"{i2}{k} = {_lua_value(v)},")
        inner = "\n".join(parts)
        return (
            f"{indent}{name} = Input {{\n"
            f"{inner}\n"
            f"{indent}}},"
        )
