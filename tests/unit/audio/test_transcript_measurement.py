"""The transcript-measurement store under the read-only lanes.

Two clips cut from the same samples share one audio key, and the lanes
can store their hearings at once. The store used a per-process temp
name, so two threads shared ONE temp file: one thread's rename moved
it away while the other was still writing, and the other's rename
then raised `FileNotFoundError` - a lane crashed on a hearing it had
already made. Temp names are unique per call now, so every concurrent
store lands intact.
"""
import threading

from library.tools import transcript_measurement as tm


def test_concurrent_same_key_stores_each_land_intact(tmp_path):
    key = "aa" * 32
    hearings = [
        ({"segments": [{"text": f"hearing {i}", "words": []}]},
         {"arm": "test", "aligner": "test"})
        for i in range(8)
    ]
    barrier = threading.Barrier(len(hearings))
    failures = []

    def hammer(pair):
        aligned, record = pair
        barrier.wait(timeout=10)
        for _ in range(25):
            try:
                tm.store(key, aligned, record, heard="test", root=tmp_path)
            except Exception as e:  # noqa: BLE001 - collected, asserted
                failures.append(repr(e))

    threads = [threading.Thread(target=hammer, args=(pair,))
               for pair in hearings]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=60)
    assert not any(t.is_alive() for t in threads)
    assert failures == [], f"lanes crashed storing: {failures[:3]}"

    loaded = tm.load(key, root=tmp_path)
    assert loaded is not None, "the store holds a mixture that will not load"
    assert loaded in hearings, f"the store holds a mixture: {loaded!r}"
    debris = [p for p in tmp_path.iterdir() if p.suffix == ".tmp"]
    assert debris == [], f"temp debris left behind: {debris}"
