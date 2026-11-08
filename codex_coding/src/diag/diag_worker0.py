"""Is the starved flow tied to worker slot 0 (process history) or to the path?"""
import json, sys
from fluidckpt.profiler.workers import Pool
P = lambda i, j: {"kind": "p2p", "src": i, "dst": j}
pairs = [P(0, 1), P(2, 3), P(4, 5), P(6, 7)]
NB = {"p2p": 256 << 20, "h2d": 256 << 20}
out = {}


def run(pool, tag, specs):
    runs = pool.concurrent(specs, NB, 0.4, 3)
    r = [[round(x["GBps"], 1) for x in run] for run in runs]
    print(tag, [f"{s['src']}->{s['dst']}" for s in specs], r, flush=True)
    out[tag] = {"flows": specs, "GBps": r}
def main():
    pool = Pool(4)
    run(pool, "fresh_order0", pairs)
    run(pool, "fresh_rot1", pairs[1:] + pairs[:1])
    run(pool, "fresh_rot2", pairs[2:] + pairs[:2])
    # give worker 0 history like the profiler did (many setups on all devices)
    for i in range(8):
        for j in range(8):
            if i != j:
                pool.sizes(P(i, j), 64 << 20, [64 << 20], 1)
    run(pool, "aged_order0", pairs)
    run(pool, "aged_rot1", pairs[1:] + pairs[:1])
    pool.close()
    json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
