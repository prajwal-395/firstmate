"""Re-attach the file-backed Loaders an imported comp lost, under lock.

`TimelineItem.ImportFusionComp` turns Loader nodes into MediaIn
placeholders (measured on Resolve Studio 21.1 across four file shapes
with and without FormatID - the merge wirings to them go unwired).
The .comp text stays the declaration; this module is the delivery:
for each loader spec, AddTool a Loader, SetMultiClip its first frame,
set its trims, ConnectInput it to the merge the spec names - all
inside `comp.Lock()` / `Unlock()`, which suppresses the file-browser
dialog an unlocked AddTool opens on the operator's screen.

Judged by read-back, never by returns alone: `ConnectInput` returns
True while the loader shows 0x0 dimensions and renders black
(measured), so a delivery whose loader did not decode REFUSES with
the file named rather than leaving a black composite on the timeline.
A false refusal (dims populating late on some build) is honest and
actionable; a false pass ships black.

No timeline path rides this today: the behind-subject composite it
was built for now reaches the timeline precomposited, as a normal
overlay clip (library/tools/behind_subject.py) - scripted Loaders
never decode on timeline comps in Resolve Studio 21.1 (measured
2026-09-24). What stays is the generic executor, covered by its
tests, for a future file-backed delivery that measures.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from library.tools.ren_refusal import RenRefusal


class LoaderDeliveryRefused(RenRefusal):
    """A file-backed Loader did not deliver onto the timeline comp."""


def _slot(value: Any):
    """The single slot of a `{slot: value}` clip attr, or the value."""
    if isinstance(value, Mapping):
        if len(value) == 1:
            return next(iter(value.values()))
        return None
    return value


def _clip_length(attrs: Mapping) -> int | None:
    """The clip's frame count, or None where no table answers at all.

    Read off both attribute tables: the integer table carries the
    length where the loader parsed its numbering, the string table
    where it decoded the file. Sanity (positive, not the 0xFFFFFFFF
    unreadable-file sentinel) is `_verify_decode`'s job, not this
    reader's - a 0 that reads back must refuse the same as an absent
    one, and the refusal names the value either way.
    """
    for key in ("TOOLST_Clip_Length", "TOOLIT_Clip_Length"):
        raw = _slot(attrs.get(key))
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            continue
        return int(raw)
    return None


def _clip_dims(attrs: Mapping) -> tuple | None:
    """The decoded frame size, or None.

    Same two-table read as the length: a loader that parsed its
    numbering but never decoded shows 0x0 on one table while the
    other may carry the file's real size. Both zero is the measured
    black-render state and refuses downstream.
    """
    for wkey, hkey in (("TOOLST_Clip_Width", "TOOLST_Clip_Height"),
                       ("TOOLIT_Clip_Width", "TOOLIT_Clip_Height")):
        raw_w = _slot(attrs.get(wkey))
        raw_h = _slot(attrs.get(hkey))
        if (isinstance(raw_w, (int, float)) and not isinstance(raw_w, bool)
                and isinstance(raw_h, (int, float))
                and not isinstance(raw_h, bool)
                and int(raw_w) > 0 and int(raw_h) > 0):
            return (int(raw_w), int(raw_h))
    return None


def _refuse(detail: str) -> None:
    raise LoaderDeliveryRefused(
        "a Fusion Loader did not deliver its file",
        detail,
        "Re-run the build step: the title and matte files are written "
        "fresh on every compile, so a transient decode failure clears. "
        "If it refuses again with the file named and on disk, Resolve "
        "did not decode that Loader on this build - see the delivery "
        "measurements in library/tools/execution/deliver_loaders.py.")


def _connected(merge: Any, input_id: str) -> bool:
    try:
        inputs = merge.GetInputList() or {}
    except Exception:  # noqa: BLE001 - read-back is best effort per input
        return False
    for handle in inputs.values():
        try:
            attrs = handle.GetAttrs() or {}
        except Exception:  # noqa: BLE001 - per-input best effort
            continue
        if attrs.get("INPS_ID") == input_id:
            return bool(attrs.get("INPB_Connected"))
    return False


def deliver_loaders(comp: Any, specs: Sequence[Mapping],
                    wiring: Sequence[tuple],
                    placeholder_names: Sequence[str] = ()) -> dict:
    """Deliver loader specs onto an imported timeline comp.

    `specs` are `{"role", "first_frame", "trim_in", "trim_out"}`
    dicts, one per file-backed node to re-attach; `wiring` is
    `(merge tool, merge input, loader role, loader output)` - which
    merge input each loader drives; `placeholder_names` are the
    comp-text Loader names the import turned into MediaIn
    placeholders. Returns `{role: loader tool name}`. Raises
    `LoaderDeliveryRefused` naming the first failure: no merge, no
    loader, no clip, no length, 0x0 dimensions, no trims where
    declared, or an unwired input afterwards. Placeholder MediaIns
    the import left behind are deleted once the real loaders are
    wired, so a later read cannot mistake one for the delivery.
    """
    by_role = {spec.get("role"): spec for spec in specs or []}
    delivered: dict[str, str] = {}
    comp.Lock()
    try:
        for role, spec in by_role.items():
            first = spec.get("first_frame", "")
            if not first:
                _refuse(f"Loader spec for role {role!r} names no file.")
            loader = comp.AddTool("Loader", -32768, -32768)
            if not loader:
                _refuse(f"AddTool Loader for role {role!r} was declined.")
            try:
                loader.SetMultiClip(first)
            except Exception as exc:  # noqa: BLE001 - refusal carries it
                _refuse(f"SetMultiClip({first!r}) for role {role!r} "
                        f"raised {exc!r}.")
            name = loader.GetAttrs().get("TOOLS_Name", role)
            delivered[role] = name
            _set_trims(loader, role, spec)
        for merge_name, input_id, role, _source in wiring:
            merge = comp.FindTool(merge_name)
            if not merge:
                _refuse(f"Merge {merge_name!r} is not on the comp - "
                        f"the import dropped more than the Loaders.")
            loader_name = delivered.get(role)
            loader = comp.FindTool(loader_name) if loader_name else None
            if not loader:
                _refuse(f"Loader for role {role!r} vanished after "
                        f"creation.")
            try:
                wired = merge.ConnectInput(input_id, loader)
            except Exception as exc:  # noqa: BLE001 - refusal carries it
                _refuse(f"ConnectInput({input_id!r}) on {merge_name!r} "
                        f"raised {exc!r}.")
            if not wired or not _connected(merge, input_id):
                _refuse(f"{merge_name!r}.{input_id} is not connected "
                        f"after wiring.")
        _remove_placeholders(comp, placeholder_names)
    finally:
        comp.Unlock()
    _verify_decode(comp, by_role, delivered)
    return delivered


def _set_trims(loader: Any, role: str, spec: Mapping) -> None:
    """The trims the comp text declares, or a refusal.

    A trim that does not take would misalign the file against its
    span in silence - so a mismatch refuses rather than rounding.
    """
    for key, attr in (("trim_in", "TOOLIT_Clip_TrimIn"),
                      ("trim_out", "TOOLIT_Clip_TrimOut")):
        want = spec.get(key)
        if want is None or want == 0 and key == "trim_in":
            continue
        try:
            loader.SetAttrs({attr: int(want)})
        except Exception as exc:  # noqa: BLE001 - refusal carries it
            _refuse(f"SetAttrs({attr}={want}) for role {role!r} "
                    f"raised {exc!r}.")
        have = _slot(loader.GetAttrs().get(attr))
        if have != int(want):
            _refuse(f"Loader trim {attr} reads back {have!r}, want "
                    f"{int(want)} - the file would play the wrong "
                    f"frames.")


def _remove_placeholders(comp: Any, names: Sequence[str]) -> None:
    """Delete the import's MediaIn placeholders for delivered loaders.

    The importer turns each Loader into a same-named MediaIn; once the
    real loader is wired those dangle - deleting them keeps a later
    read from mistaking a placeholder for the delivery. Only MediaIns
    are touched: a same-named Loader means the import one day kept it,
    and deleting a working loader would be the defect this module
    exists to remove.
    """
    for name in names:
        try:
            node = comp.FindTool(name)
        except Exception:  # noqa: BLE001 - per-node best effort
            continue
        if not node:
            continue
        try:
            reg = node.GetAttrs().get("TOOLS_RegID")
        except Exception:  # noqa: BLE001 - per-node best effort
            continue
        if reg == "MediaIn":
            node.Delete()


def _verify_decode(comp: Any, by_role: Mapping,
                   delivered: Mapping) -> None:
    """Every delivered loader decoded its file, or refuse naming it.

    Length alone is not evidence - a sequence parses its numbering
    while showing 0x0 and rendering black (measured) - so dims are
    required too. Length must also be sane: 0xFFFFFFFF is the
    unreadable-file sentinel.
    """
    for role in by_role:
        loader = comp.FindTool(delivered.get(role, ""))
        if not loader:
            _refuse(f"Loader for role {role!r} vanished before "
                    f"verification.")
        attrs = loader.GetAttrs() or {}
        first = by_role[role].get("first_frame", "")
        length = _clip_length(attrs)
        if length is None or length <= 0 or length == 0xFFFFFFFF:
            _refuse(f"Loader for role {role!r} shows length "
                    f"{length!r} for {first!r} - the file did not "
                    f"resolve.")
        dims = _clip_dims(attrs)
        if dims is None or dims[0] <= 0 or dims[1] <= 0:
            _refuse(f"Loader for role {role!r} shows dimensions "
                    f"{dims!r} for {first!r} - the file parsed but "
                    f"did not decode, and the composite would render "
                    f"black.")
