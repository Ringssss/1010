"""Does a worker that has done P2P over the full 8x8 mesh degrade concurrent P2P? Which flows suffer?"""
import json, sys
from fluidckpt.profiler.workers import Pool
P = lambda i, j: {"kind": "p2p", "src": i, "dst": j}
PAIRS = [P(0, 1), P(2, 3), P(4, 5), P(6, 7)]
NB = {"p2p": 256 << 20}


def age(pool, w):
    for i in range(8):
        for j in range(8):
            if i != j:
                pool._all([w], "setup", [{"spec": P(i, j), "nbytes": 64 << 20}])
    pool._all([w], "teardown", [None])


def trial(pool, tag, specs, out):
    runs = pool.concurrent(specs, NB, 0.4, 3)
    r = [[round(x["GBps"], 1) for x in run] for run in runs]
    print(tag, [f"{s['src']}->{s['dst']}" for s in specs], r, flush=True)
    out[tag] = {"flows": specs, "GBps": r}


def main():
    out = {}
    pool = Pool(4)
    age(pool, 3)  # the aged worker is slot 3
    trial(pool, "aged_w3__w3_runs_6->7", PAIRS, out)
    trial(pool, "aged_w3__w3_runs_0->1", PAIRS[1:] + PAIRS[:1], out)
    trial(pool, "aged_w3__repeat_6->7", PAIRS, out)
    trial(pool, "aged_w3__w3_idle", PAIRS[:3], out)
    pool.close()
    json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
