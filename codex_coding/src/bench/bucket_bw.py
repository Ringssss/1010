"""Does bucketing many small tensors into few large broadcasts actually win?

SGLang's remote weight send does one `dist.broadcast` per named_parameter. GLM-5.3 has 118,629
of them (59,487 weights + 59,044 fp8 weight_scale_inv + 98 others), so the fixed per-collective
cost is paid 118,629 times. This measures that cost directly and compares layouts:

  per_tensor  N broadcasts of S bytes each      <- SGLang's shape
  bucketed    M broadcasts of (N/M)*S bytes     <- what we want to do

Reported as GB/s so the two are comparable, plus the implied time for the real GLM-5.3 layout.

  NPROC=8 scripts/run_on_pair.sh scripts/bucket_bw.py node27 node29 \
      NCCL_NET_PLUGIN=librccl-net-shca.so -- --out <json> --tag bucket
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
    fn(); torch.cuda.synchronize(); dist.barrier()
    ts = []
    for _ in range(repeats):
        dist.barrier(); t0 = time.perf_counter(); fn(); torch.cuda.synchronize()
        ts.append(time.perf_counter() - t0)
    return statistics.median(ts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--tag", default="")
    ap.add_argument("--nproc", type=int, default=int(os.environ.get("NPROC", "8")))
    ap.add_argument("--repeats", type=int, default=3)
    # GLM-5.3-shaped: many median-sized tensors, small total per rank
    ap.add_argument("--n-small", type=int, default=256, help="number of small tensors to broadcast")
    ap.add_argument("--small-KiB", type=int, default=128, help="size of each small tensor")
    ap.add_argument("--bucket-MiB", type=int, default=32, help="target bucket size")
    a = ap.parse_args()

    dist.init_process_group(backend="nccl", timeout=__import__("datetime").timedelta(seconds=300))
    rank, world = dist.get_rank(), dist.get_world_size()
    local = int(os.environ.get("LOCAL_RANK", 0))
    torch.cuda.set_device(local)
    dev = torch.device("cuda", local)
    n = a.nproc

    pair_peer = (rank + n) % world
    pairs = [dist.new_group([i, i + n]) for i in range(n)]
    mypair = pairs[rank % n]

    small_elems = a.small_KiB * 1024 // 2
    tensors = [torch.empty(small_elems, dtype=torch.float16, device=dev) for _ in range(a.n_small)]
    total = sum(t.numel() * t.element_size() for t in tensors)
    nper = max(1, (a.bucket_MiB * MiB) // (a.small_KiB * 1024))
    buckets = [tensors[i:i + nper] for i in range(0, len(tensors), nper)]
    buckets = [torch.cat(b) if len(b) > 1 else b[0].clone() for b in buckets]

    def per_tensor():
        for t in tensors:
            dist.broadcast(t, src=rank % n, group=mypair)

    def bucketed():
        for b in buckets:
            dist.broadcast(b, src=rank % n, group=mypair)

    t_pt = timed(per_tensor, a.repeats)
    t_bk = timed(bucketed, a.repeats)
    rows = [{"mode": "per_tensor", "n_bcasts": len(tensors), "GiB": total / 2**30,
             "seconds": t_pt, "GBps": total / t_pt / 1e9, "us_per_bcast": t_pt / len(tensors) * 1e6},
            {"mode": "bucketed", "n_bcasts": len(buckets), "GiB": total / 2**30,
             "seconds": t_bk, "GBps": total / t_bk / 1e9, "us_per_bcast": t_bk / len(buckets) * 1e6}]
    if rank == 0:
        for r in rows:
            print(f"  {r['mode']:11s} {r['n_bcasts']:6d} bcasts  {r['GiB']:.2f} GiB  "
                  f"{r['seconds']*1e3:9.2f} ms  {r['GBps']:7.2f} GB/s  {r['us_per_bcast']:8.1f} us/bcast",
                  flush=True)
        speedup = t_pt / t_bk
        print(f"  bucketing speedup: {speedup:.1f}x", flush=True)
        # extrapolate to the real GLM-5.3 send: 118,629 tensors, 704 GB over 16 ranks
        glm_tensors, glm_bytes, glm_ranks = 118629, 704e9, 16
        per_rank_bytes = glm_bytes / glm_ranks
        print(f"  --- extrapolation to GLM-5.3 (1 broadcast per parameter, {glm_tensors} params) ---",
              flush=True)
        print(f"  total fixed cost alone: {glm_tensors * rows[0]['us_per_bcast']/1e6:.1f} s "
              f"(per_tensor) vs {glm_tensors * rows[1]['us_per_bcast']/1e6:.1f} s (bucketed)", flush=True)
        out = {"tag": a.tag, "world": world, "nproc_per_node": n, "rows": rows,
               "bucket_speedup": speedup, "glm_tensors": glm_tensors}
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(out, open(a.out, "w"), indent=1)
        print("wrote", a.out, flush=True)
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
