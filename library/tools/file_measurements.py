"""Reuse measurements of unchanged files across Ren processes.

The cache is an optimization only: entries are addressed by canonical path,
device, inode, size, and nanosecond mtime/ctime. A changed filesystem identity
gets a fresh measurement. SQLite stores data in the user's cache directory,
never in a project or the checkout.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import subprocess
from collections.abc import Sequence
from pathlib import Path


def _identity(path: str | os.PathLike) -> tuple[str, int, int, int, int, int]:
    resolved = str(Path(path).expanduser().resolve(strict=True))
    stat = os.stat(resolved)
    return (
        resolved,
        stat.st_dev,
        stat.st_ino,
        stat.st_size,
        stat.st_mtime_ns,
        stat.st_ctime_ns,
    )


def _database() -> Path:
    root = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return root / "ren" / "file-measurements.sqlite3"


def _connect() -> sqlite3.Connection:
    path = _database()
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=10)
    db.execute("PRAGMA busy_timeout=10000")
    db.execute("""CREATE TABLE IF NOT EXISTS measurements (
        kind TEXT NOT NULL, path TEXT NOT NULL, dev INTEGER NOT NULL,
        ino INTEGER NOT NULL, size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
        ctime_ns INTEGER NOT NULL,
        signature TEXT NOT NULL, value BLOB NOT NULL,
        PRIMARY KEY(kind, path, dev, ino, size, mtime_ns, signature))""")
    return db


def cached_sha256(path: str | os.PathLike) -> str:
    """Return a whole-file SHA-256, reusing an unchanged file's digest."""
    identity = _identity(path)
    key = ("sha256", *identity, "sha256")
    with _connect() as db:
        row = db.execute(
            "SELECT value FROM measurements WHERE kind=? AND path=? AND dev=? "
            "AND ino=? AND size=? AND mtime_ns=? AND ctime_ns=? AND signature=?",
            key,
        ).fetchone()
        if row:
            return bytes(row[0]).decode("ascii")
    digest = hashlib.sha256()
    with open(identity[0], "rb") as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            digest.update(chunk)
    value = digest.hexdigest()
    if _identity(identity[0]) != identity:
        raise OSError(f"file changed while hashing: {identity[0]}")
    with _connect() as db:
        db.execute(
            "DELETE FROM measurements WHERE kind='sha256' AND path=?", (identity[0],)
        )
        db.execute(
            "INSERT OR REPLACE INTO measurements VALUES(?,?,?,?,?,?,?,?,?)",
            ("sha256", *identity, "sha256", value.encode("ascii")),
        )
    return value


def cached_ffprobe(
    path: str | os.PathLike, args: Sequence[str], *, timeout: float = 30
) -> subprocess.CompletedProcess:
    """Run/cache a successful JSON ffprobe query for a file identity.

    ``args`` are ffprobe arguments excluding the executable and final input
    path. Query options are part of the key, so narrow and broad probes never
    alias one another.
    """
    try:
        identity = _identity(path)
    except FileNotFoundError:
        return subprocess.run(
            ["ffprobe", *args, str(path)],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            check=False,
        )
    signature = json.dumps(list(args), separators=(",", ":"))
    key = (*identity, signature)
    with _connect() as db:
        row = db.execute(
            "SELECT value FROM measurements WHERE kind=? AND path=? AND dev=? "
            "AND ino=? AND size=? AND mtime_ns=? AND ctime_ns=? AND signature=?",
            ("ffprobe", *key),
        ).fetchone()
        if row:
            stdout = bytes(row[0]).decode("utf-8")
            return subprocess.CompletedProcess(
                ["ffprobe", *args, identity[0]], 0, stdout, ""
            )
    result = subprocess.run(
        ["ffprobe", *args, identity[0]],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    if result.returncode == 0 and _identity(identity[0]) == identity:
        with _connect() as db:
            db.execute(
                "DELETE FROM measurements WHERE kind='ffprobe' AND path=?",
                (identity[0],),
            )
            db.execute(
                "INSERT OR REPLACE INTO measurements VALUES(?,?,?,?,?,?,?,?,?)",
                ("ffprobe", *identity, signature, result.stdout.encode("utf-8")),
            )
    return result
