from __future__ import annotations

import subprocess

import pytest

from scripts.qualification_account import create_standard_account


def test_create_standard_account_sets_password_and_creates_home_without_admin(tmp_path):
    calls = []
    home = str(tmp_path / "renqualify")

    def run(command, *, check):
        calls.append((command, check))

    create_standard_account(
        "renqualify", "a-random-password", home, run=run
    )

    assert calls == [
        ([
            "/usr/sbin/sysadminctl", "-addUser", "renqualify",
            "-fullName", "Ren Qualification", "-password", "a-random-password",
            "-home", home,
        ], True),
        (["/usr/sbin/createhomedir", "-c", "-u", "renqualify"], True),
    ]
    assert not any("-admin" in command or "admin" == command[-1]
                   for command, _ in calls)


def test_home_is_not_created_if_account_creation_fails(tmp_path):
    calls = []
    home = str(tmp_path / "renqualify")

    def run(command, *, check):
        calls.append(command)
        raise subprocess.CalledProcessError(1, command)

    with pytest.raises(subprocess.CalledProcessError):
        create_standard_account("renqualify", "password", home, run=run)

    assert len(calls) == 1
    assert calls[0][0] == "/usr/sbin/sysadminctl"
