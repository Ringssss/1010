"""Which kind of worker history degrades P2P: touching all devices (H2D only) or P2P to all peers?"""
import json, sys
from fluidckpt.profiler.workers import Pool
P = lambda i, j: {"kind": "p2p", "src": i, "dst": j}
H = lambda n, g: {"kind": "h2d", "numa": n, "gpu": g}
PAIRS = [P(0, 1), P(2, 3), P(4, 5), P(6, 7)]
NB = {"p2p": 256 << 20, "h2d": 256 << 20}


def trial(pool, tag, out):
    runs = pool.concurrent(PAIRS, NB, 0.4, 3)
    r = [[round(x["GBps"], 1) for x in run] for run in runs]
    print(tag, r, flush=True)
    out[tag] = r


def main():
    out = {}
    pool = Pool(4)
    trial(pool, "fresh", out)
    for g in range(8):  # history A: allocations + H2D on every device, no peer access
        pool.sizes(H(0, g), 64 << 20, [64 << 20], 1)
    trial(pool, "after_h2d_all_devices", out)
    for j in range(1, 8):  # history B: P2P from GPU 0 to every peer
        pool.sizes(P(0, j), 64 << 20, [64 << 20], 1)
    trial(pool, "after_p2p_0_to_all", out)
    for i in range(1, 8):  # history C: P2P into GPU 0 from every peer
        pool.sizes(P(i, 0), 64 << 20, [64 << 20], 1)
    trial(pool, "after_p2p_all_to_0", out)
    pool.close()
    pool = Pool(4)
    for j in (2, 3, 4, 5, 6, 7):  # history D: only pairs not involving GPU 0/1 in the test flow
        pool.sizes(P(2, j) if j != 2 else P(3, 2), 64 << 20, [64 << 20], 1)
    trial(pool, "fresh_then_p2p_2_to_others", out)
    pool.close()
    json.dump(out, open(sys.argv[1], "w"), indent=1)


if __name__ == "__main__":
    main()
