"""Acceptance checks for cross-request single-flight execution (CI-13b)."""

from concurrent.futures import ThreadPoolExecutor
import threading

from library.tools.local_ci.request_dedup import RequestDeduplicator


def test_identical_verification_request_runs_once_for_both_consumers():
    deduplicator = RequestDeduplicator()
    started = threading.Event()
    release = threading.Event()
    executions = []

    def execute():
        executions.append("ran")
        started.set()
        assert release.wait(timeout=5)
        return {"passed": 3, "failed": 0}

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(deduplicator.run, "test.py:verification-key",
                            "check-a", execute)
        assert started.wait(timeout=5)
        second = pool.submit(deduplicator.run, "test.py:verification-key",
                             "check-b", execute)
        with deduplicator._lock:
            assert deduplicator._lock.wait_for(
                lambda: len(deduplicator._requests[
                    "test.py:verification-key"]["consumers"]) == 2,
                timeout=5)
        release.set()
        results = [first.result(timeout=5), second.result(timeout=5)]

    assert executions == ["ran"]
    assert all(item["result"] == {"passed": 3, "failed": 0}
               for item in results)
    assert all(item["consumers"] == ["check-a", "check-b"]
               for item in results)


def test_distinct_verification_keys_execute_independently():
    deduplicator = RequestDeduplicator()
    calls = []
    for key in ("test.py:key-a", "test.py:key-b"):
        deduplicator.run(
            key, key, lambda request_key=key:
            calls.append(request_key) or request_key)
    assert calls == ["test.py:key-a", "test.py:key-b"]
