#!/usr/bin/env python3
"""Create a standard macOS local account and its home directory."""

from __future__ import annotations

import argparse
import subprocess
from collections.abc import Callable, Sequence


CommandRunner = Callable[..., subprocess.CompletedProcess]


def create_standard_account(username: str, password: str, home: str,
                            *, run: CommandRunner = subprocess.run) -> None:
    """Create the user without admin membership, then materialize its home."""
    run([
        "/usr/sbin/sysadminctl", "-addUser", username,
        "-fullName", "Ren Qualification", "-password", password,
        "-home", home,
    ], check=True)
    run(["/usr/sbin/createhomedir", "-c", "-u", username], check=True)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("username")
    parser.add_argument("password")
    parser.add_argument("home")
    args = parser.parse_args(argv)
    create_standard_account(args.username, args.password, args.home)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
