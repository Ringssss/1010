"""Multi-rank cross-node bandwidth: does per-node cross-node traffic add up, or share one link?

A multi-node materialization pulls shards from remote replicas; the question the planner needs
answered is whether N ranks per node each get ~48 GB/s (per-rank links) or whether they share one
400 Gb/s port. Three modes:

  fanout  - one root (rank 0) broadcasts to every other rank: the "one source feeds all targets"
            pattern, which is how a warm replica would seed several new instances.
  pair    - R independent pairs (i <-> NPROC+i), each a broadcast on its own subgroup, running
            concurrently: the aggregate of N independent cross-node streams.
  ring    - all_reduce over the whole world: aggregate bidirectional rate.

  scripts/run_on_pair.sh scripts/multi_rank_bw.py node27 node29 NPROC=8 \
      NCCL_NET_PLUGIN=librccl-net-shca.so -- --out <json> --tag 27-29-r8
"""
import argparse
import json
import os
import statistics
import time

import torch
import torch.distributed as dist

MiB = 1 << 20


def timed(fn, repeats):
    fn()
    torch.cuda.synchronize()
    dist.barrier()
    ts = []
    for _ in range(repeats):
        dist.barrier()
        t0 = time.perf_counter()
        fn()
        torch.cuda.synchronize()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--nproc", type=int, default=int(os.environ.get("NPROC", "8")))
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--sizes", default="64,512")
    a = ap.parse_args()
    sizes = [int(x) * MiB for x in a.sizes.split(",")]

    dist.init_process_group(backend="nccl", timeout=__import__("datetime").timedelta(seconds=300))
    rank, world = dist.get_rank(), dist.get_world_size()
    local = int(os.environ.get("LOCAL_RANK", 0))
    torch.cuda.set_device(local)
    dev = torch.device("cuda", local)
    n = a.nproc
    node = rank // n                       # 0 = node A, 1 = node B (world == 2*n)

    # subgroup {i, n+i} for the pair mode
    pair_peer = (rank + n) % world
    pairs = [dist.new_group([i, i + n]) for i in range(n)]
    mypair = pairs[rank % n]

    rows = []
    for nbytes in sizes:
        buf = torch.empty(nbytes // 4, dtype=torch.float32, device=dev)

        def fanout():
            dist.broadcast(buf, src=0)

        def pair():
            dist.broadcast(buf, src=rank % n, group=mypair)

        def ring():
            dist.all_reduce(buf)

        t_f = timed(fanout, a.repeats)
        t_p = timed(pair, a.repeats)
        t_r = timed(ring, a.repeats)
        # fanout moves S to (world-1) ranks; pair moves S on each of n pairs; ring moves 2S(1-1/world)
        rows.append({"bytes": nbytes, "MiB": nbytes >> 20,
                     "fanout_GBps": nbytes * (world - 1) / t_f / 1e9,
                     "fanout_root_GBps": nbytes / t_f / 1e9,
                     "pair_GBps": nbytes * n / t_p / 1e9,
                     "pair_per_stream_GBps": nbytes / t_p / 1e9,
                     "ring_GBps": 2 * nbytes * (world - 1) / world / t_r / 1e9,
                     "fanout_s": t_f, "pair_s": t_p, "ring_s": t_r})
        if rank == 0:
            r = rows[-1]
            print(f"{nbytes>>20:5d} MiB  fanout {r['fanout_GBps']:7.2f} (root {r['fanout_root_GBps']:6.2f})  "
                  f"pair-agg {r['pair_GBps']:7.2f} ({r['pair_per_stream_GBps']:5.2f}/stream)  "
                  f"ring {r['ring_GBps']:7.2f} GB/s", flush=True)

    if rank == 0:
        out = {"tag": a.tag, "world": world, "nproc_per_node": n,
               "hosts": [os.uname().nodename] if node == 0 else None,
               "rows": rows}
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(out, open(a.out, "w"), indent=1)
        print("wrote", a.out)
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
