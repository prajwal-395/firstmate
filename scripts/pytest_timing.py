"""Use the last complete gate's file timings to order xdist work."""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Iterable
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


def timing_path() -> Path:
    configured = os.environ.get("VEP_TEST_TIMING_FILE")
    if configured:
        return Path(configured).expanduser()
    cache_root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return cache_root / "ren" / "pytest-file-durations.json"


def read_timings(path: Path | None = None) -> dict[str, float]:
    """Read usable file-duration telemetry; malformed cache is a cold start."""
    source = path or timing_path()
    try:
        data = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if (not isinstance(data, dict) or data.get("version") != 1
            or not isinstance(data.get("files"), dict)):
        return {}
    result = {}
    for name, value in data["files"].items():
        if not isinstance(name, str) or Path(name).is_absolute():
            continue
        try:
            seconds = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(seconds) and seconds >= 0:
            result[Path(name).as_posix()] = seconds
    return result


def ordered_items(items: Iterable, durations: dict[str, float], root: Path) -> list:
    """Keep each test file together and put historically longer files first."""
    grouped: dict[str, list] = {}
    for item in items:
        try:
            path = Path(item.path).resolve().relative_to(root.resolve()).as_posix()
        except (AttributeError, OSError, ValueError):
            path = str(item.nodeid).split("::", 1)[0]
        grouped.setdefault(path, []).append(item)
    files = sorted(grouped, key=lambda name: (-durations.get(name, 0.0), name))
    return [item for name in files for item in grouped[name]]


def pytest_addoption(parser) -> None:
    group = parser.getgroup("Ren test timing")
    group.addoption(
        "--timing-shard", action="store_true", default=False,
        help="order test files by duration from the last full gate run",
    )


def pytest_collection_modifyitems(config, items) -> None:
    if not config.getoption("--timing-shard"):
        return
    durations = read_timings()
    if not durations:
        return
    items[:] = ordered_items(items, durations, Path(config.rootpath))


def _relative_test_file(value: str) -> str | None:
    path = Path(value)
    if not path.is_absolute():
        path = REPO_ROOT / path
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except (OSError, ValueError):
        return None


def _relative_test_classname(classname: str) -> str | None:
    """Resolve pytest's JUnit classname to its test module path."""
    if not classname:
        return None
    parts = classname.split(".")
    for end in range(len(parts), 0, -1):
        candidate = REPO_ROOT.joinpath(*parts[:end]).with_suffix(".py")
        try:
            relative = candidate.resolve().relative_to(REPO_ROOT)
        except (OSError, ValueError):
            continue
        if relative.name.startswith("test_") and candidate.is_file():
            return relative.as_posix()
    return None


def write_timings(reports: Iterable[Path], destination: Path | None = None) -> int:
    """Replace timing telemetry with measured, non-skipped files in reports."""
    totals: dict[str, float] = defaultdict(float)
    observed: set[str] = set()
    for report in reports:
        root = ET.parse(report).getroot()
        for case in root.iter("testcase"):
            if case.find("skipped") is not None:
                continue
            filename = case.get("file")
            relative = _relative_test_file(filename) if filename else None
            if relative is None:
                relative = _relative_test_classname(case.get("classname", ""))
            if relative is None:
                continue
            try:
                seconds = float(case.get("time", "0"))
            except (TypeError, ValueError):
                continue
            if not math.isfinite(seconds) or seconds < 0:
                continue
            totals[relative] += seconds
            observed.add(relative)

    if not observed:
        raise ValueError("JUnit reports did not identify any repository test files")

    target = destination or timing_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": 1,
        "files": {name: round(totals[name], 6) for name in sorted(observed)},
    }
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(temporary, target)
    except BaseException:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise
    return len(observed)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    update = subparsers.add_parser("update", help="record one full gate run")
    update.add_argument("reports", nargs="+", type=Path)
    args = parser.parse_args(argv)
    if args.command == "update":
        try:
            count = write_timings(args.reports)
        except (OSError, ValueError, ET.ParseError) as exc:
            print(f"could not record test timings: {exc}", file=sys.stderr)
            return 1
        print(f"recorded timings for {count} test files in {timing_path()}")
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
