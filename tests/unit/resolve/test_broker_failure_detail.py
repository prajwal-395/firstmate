"""A broker that dies mid-request says WHICH job and WHAT it died of.

The defect this names: `project scratch create` failed with one line -
"ren-resolved closed the connection" - and the broker died with it.  A
lane could not tell a transient refusal from a crash, and the one line
named neither the job nor the cause.  The broker already leaves its job
table and its log beside its socket; the refusal now reads both.
"""
from __future__ import annotations

import sqlite3

import pytest

from library.tools.resolved import broker_paths, client, failure_reader


@pytest.fixture
def broker_dir(tmp_path, monkeypatch):
    """A private broker directory: the machine's own is never touched."""
    monkeypatch.setenv(broker_paths.BROKER_DIR_ENV, str(tmp_path))
    return tmp_path


def _write_job(directory, job_id, state="queued", error=""):
    path = directory / broker_paths.DB_NAME
    connection = sqlite3.connect(str(path))
    connection.execute(
        "CREATE TABLE jobs (id TEXT PRIMARY KEY, kind TEXT, state TEXT, "
        "error TEXT, owner TEXT, submitted REAL, started REAL, "
        "finished REAL, result TEXT)")
    connection.execute(
        "INSERT INTO jobs VALUES (?, 'resolve_axi', ?, ?, 'pid-1', 0, "
        "NULL, NULL, NULL)", (job_id, state, error))
    connection.commit()
    connection.close()


def _write_log(directory, line):
    (directory / broker_paths.LOG_NAME).write_text(line + "\n",
                                                   encoding="utf-8")


def test_the_job_table_and_the_log_are_read_without_writing_to_them(
        broker_dir):
    _write_job(broker_dir, "job-1", state="failed", error="Resolve died")
    _write_log(broker_dir, "ren-resolved: stopping (pid 1)")

    job = failure_reader.read_job("job-1")

    assert job["state"] == "failed"
    assert job["error"] == "Resolve died"
    assert failure_reader.read_log_tail() == ["ren-resolved: stopping (pid 1)"]


def test_an_unknown_job_reads_as_unknown_and_never_as_a_pass(broker_dir):
    assert failure_reader.read_job("no-such-job") is None


def test_a_closed_connection_names_the_job_and_the_brokers_last_error(
        broker_dir):
    _write_job(broker_dir, "job-42", state="queued")
    _write_log(broker_dir, "ren-resolved: retiring the idle broker (pid 9)")

    class DeadSocket:
        def settimeout(self, timeout):
            pass

        def sendall(self, data):
            pass

    class DeadStream:
        def readline(self):
            return b""

    with pytest.raises(client.BrokerError) as raised:
        client._exchange(DeadSocket(), DeadStream(),
                         {"op": "result", "id": "job-42"}, 1.0)

    message = str(raised.value)
    assert "closed the connection" in message
    assert "job-42" in message
    assert "queued" in message
    assert "retiring the idle broker" in message
    # The refusal stays ACTIONABLE: a closed connection is a resource
    # the caller does not have, and it says so.
    assert raised.value.blocker == "resource_contention"
    assert "wait_for_the_resource" in raised.value.next_action
