"""CPU-only tests of the restore scheduler and slab allocator in live/noded.py.
Run: PYTHONPATH=main_lib python3 -m pytest codex_coding/src/v0.1.23.3/task2/live_graph/tests/test_pool_sched.py -q
(or plain `python3 <file>`)."""
import threading
import time

from fluidckpt.live.noded import Sched, Slab

MiB = 1 << 20


def chunks(n, classes, plan):
    return [{"id": f"{i:064x}", "n": 64 * MiB, "classes": list(classes), "plan": plan(i)} for i in range(n)]


def drain(sched, rates, fail=None):
    """Run one worker thread per class; each 'fetch' takes n / rate. Returns bytes per class."""
    done = {c: 0 for c in rates}

    def work(cls):
        while True:
            i = sched.next(cls)
            if i is None:
                return
            if fail and fail(cls, i):
                sched.fail(cls, i, "gone")
                continue
            dt = sched.chunks[i]["n"] / rates[cls] / 1e9
            time.sleep(dt)
            sched.fetched(cls, 0, i, dt)
            done[cls] += 1
            sched.landed([i])
    th = [threading.Thread(target=work, args=(c,)) for c in rates]
    [x.start() for x in th]
    [x.join(20) for x in th]
    assert not any(x.is_alive() for x in th), "scheduler did not terminate"
    return done


def test_every_chunk_lands_once():
    s = Sched(chunks(40, ["a", "b"], lambda i: "a"), True, {"a": 10, "b": 10})
    done = drain(s, {"a": 10, "b": 10})
    assert sum(done.values()) == 40 and s.left == 0 and all(x == 2 for x in s.state)


def test_idle_path_steals_from_overloaded_one():
    s = Sched(chunks(40, ["a", "b"], lambda i: "a"), True, {"a": 5, "b": 5})
    done = drain(s, {"a": 5, "b": 5})
    assert done["b"] >= 10, done                     # everything was planned on a


def test_no_steal_keeps_the_plan():
    s = Sched(chunks(20, ["a", "b"], lambda i: "a"), False, {"a": 20, "b": 20})
    done = drain(s, {"a": 20, "b": 20})
    assert done == {"a": 20, "b": 0}


def test_slow_path_does_not_steal_what_the_fast_one_finishes_sooner():
    # b is 100x slower: taking a chunk would finish later than leaving it to a.
    s = Sched(chunks(8, ["a", "b"], lambda i: "a"), True, {"a": 50, "b": 0.5})
    done = drain(s, {"a": 50, "b": 0.5})
    assert done["b"] == 0, done


def test_failed_source_sheds_to_the_other():
    s = Sched(chunks(16, ["a", "b"], lambda i: "a" if i % 2 else "b"), True, {"a": 10, "b": 10})
    done = drain(s, {"a": 10, "b": 10}, fail=lambda cls, i: cls == "a")
    assert done == {"a": 0, "b": 16} and s.failed >= 1 and s.error is None


def test_last_source_lost_is_an_error_not_a_hang():
    s = Sched(chunks(4, ["a"], lambda i: "a"), True, {"a": 10})
    drain(s, {"a": 10}, fail=lambda cls, i: True)
    assert s.error and "no source left" in s.error


def test_revoke_moves_unclaimed_chunks_and_waits_for_inflight():
    s = Sched(chunks(12, ["a", "b"], lambda i: "a"), True, {"a": 10, "b": 10})
    i = s.next("a")                                  # one chunk is being read from a
    out = {}
    t = threading.Thread(target=lambda: out.update(s.revoke(["a"], timeout=5)))
    t.start()
    time.sleep(0.05)
    assert t.is_alive(), "revoke returned while a read from the revoked class was in flight"
    s.fetched("a", 0, i, 0.01)                       # the read ends -> revoke may return
    t.join(2)
    assert not t.is_alive() and out["moved"] == 11
    s.landed([i])
    done = drain(s, {"b": 10})
    assert done["b"] == 11 and all("a" not in c["classes"] for c in s.chunks)


def test_slab_first_fit_and_coalesce():
    class Fake(Slab):
        def __init__(self, n):
            self.nbytes, self.free, self.used = n, [(0, n)], 0
    s = Fake(1024)
    a, b, c = s.alloc(100), s.alloc(200), s.alloc(300)
    assert (a, b, c) == (0, 128, 384) and s.alloc(400) is None
    s.release(b, 200)
    assert s.alloc(150) == 128                       # the hole is reused
    s.release(128, 150); s.release(a, 100); s.release(c, 300)
    assert s.free == [(0, 1024)] and s.used == 0


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn()
            print("ok", name)
