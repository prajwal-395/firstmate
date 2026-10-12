"""Refusals and failures as data: the `REN_RESULT` line (LE-03).

With `REN_OUTPUT=json`, every refusal catch site prints `render()` as
today and then one final stderr line `REN_RESULT {...}`; an uncaught
error prints its traceback and then `REN_RESULT` with code
`REN_UNEXPECTED`. Humans (no flag) see exactly what they saw before:
prose, or a bare traceback. These tests pin the payload shapes, the
gating, and the one end-to-end refusal the acceptance names (`ren
search` with no index).
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from library.tools.ren_refusal import (
    REFUSAL_EXIT_CODE,
    RenRefusal,
    emit_refusal_result,
    refusal_result_dict,
    report_unexpected,
    unexpected_result_dict,
)

ROOT = Path(__file__).resolve().parents[3]


def _refusal():
    return RenRefusal("no footage index at <tmp>",
                      "search reads the built index first",
                      "build it first: ren search-index '<tmp>'")


def test_refusal_payload_carries_exit_code_class_and_fix():
    payload = refusal_result_dict(_refusal())
    assert payload == {"ok": False, "exit": 4, **_refusal().to_dict()}
    assert payload["exit"] == REFUSAL_EXIT_CODE == 4
    assert payload["code"] == "REN_REFUSAL"
    assert payload["recovery_class"] == "blocked"
    assert "ren search-index" in payload["fix"]


def test_refusal_line_prints_only_with_the_flag(capsys, monkeypatch):
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    emit_refusal_result(_refusal())
    assert capsys.readouterr().err == ""

    monkeypatch.setenv("REN_OUTPUT", "json")
    emit_refusal_result(_refusal())
    lines = [line for line in capsys.readouterr().err.splitlines()
             if line.startswith("REN_RESULT ")]
    assert len(lines) == 1
    assert json.loads(lines[0][len("REN_RESULT "):])["exit"] == 4


def test_unexpected_payload_names_type_message_and_where():
    try:
        raise ValueError("forced boom")
    except ValueError as exc:
        payload = unexpected_result_dict(exc)
    assert payload["ok"] is False
    assert payload["exit"] == 1
    assert payload["code"] == "REN_UNEXPECTED"
    assert payload["exception"] == "ValueError: forced boom"
    path, _, lineno = payload["where"].rpartition(":")
    assert Path(path) == Path(__file__)
    assert lineno.isdigit()


def test_report_unexpected_prints_traceback_then_the_line(capsys, monkeypatch):
    monkeypatch.setenv("REN_OUTPUT", "json")
    try:
        raise ValueError("forced boom")
    except ValueError as exc:
        assert report_unexpected(exc) == 1
    err = capsys.readouterr().err
    assert "Traceback (most recent call last):" in err
    assert "ValueError: forced boom" in err
    result_lines = [line for line in err.splitlines()
                    if line.startswith("REN_RESULT ")]
    assert len(result_lines) == 1
    assert json.loads(result_lines[0][len("REN_RESULT "):])["code"] == \
        "REN_UNEXPECTED"


def test_report_unexpected_without_the_flag_prints_only_the_traceback(
        capsys, monkeypatch):
    monkeypatch.delenv("REN_OUTPUT", raising=False)
    try:
        raise ValueError("forced boom")
    except ValueError as exc:
        assert report_unexpected(exc) == 1
    err = capsys.readouterr().err
    assert "Traceback (most recent call last):" in err
    assert "REN_RESULT " not in err


def _search_no_index(tmp_path, with_flag):
    env = dict(os.environ)
    if with_flag:
        env["REN_OUTPUT"] = "json"
    else:
        # A plain human machine: the flag truly unset AND no harness
        # on PATH, so neither an explicit choice nor the runtime
        # default can fire.
        env.pop("REN_OUTPUT", None)
        empty_bin = tmp_path / "emptybin"
        empty_bin.mkdir(exist_ok=True)
        env["PATH"] = str(empty_bin)
    return subprocess.run(
        [sys.executable, "-m", "library.tools.analysis.footage_query",
         "search", str(tmp_path), "x"],
        capture_output=True, encoding="utf-8", cwd=str(ROOT),
        env=env, check=False)


def test_search_with_no_index_emits_exactly_one_parseable_line(tmp_path):
    done = _search_no_index(tmp_path, with_flag=True)
    assert done.returncode == 4
    lines = [line for line in done.stderr.splitlines()
             if line.startswith("REN_RESULT ")]
    assert len(lines) == 1
    payload = json.loads(lines[0][len("REN_RESULT "):])
    assert payload["ok"] is False
    assert payload["exit"] == 4
    assert payload["code"]
    assert payload["recovery_class"]
    assert payload["fix"]
    assert "ren: refused - " in done.stderr


def test_search_without_the_flag_emits_no_result_line(tmp_path):
    done = _search_no_index(tmp_path, with_flag=False)
    assert done.returncode == 4
    assert "REN_RESULT " not in done.stderr
    assert "ren: refused - " in done.stderr
