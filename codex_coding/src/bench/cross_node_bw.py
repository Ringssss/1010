"""Cross-node GPU-to-GPU bandwidth, measured with the collectives that actually work here.

A checkpoint crosses a node boundary as: HBM(src) -> host/IB -> HBM(dst). This measures that
movement with `broadcast` (one-way push, root -> peer), `all_reduce` (ring, both directions) and a
streamed variant (many chunks back to back, which is how a materialization really runs).

NOT measured with dist.send/dist.recv: RCCL 2.22.3 on this image fails point-to-point cross-node
("ncclSocketInit: connecting to address with family 0", proxy Accept EPERM) while collectives work.
See docs/findings_multinode.md.

  scripts/run_on_pair.sh scripts/cross_node_bw.py node27 node29 -- --out <json> --tag 27-29
"""
import argparse
import json
import os
import statistics
import time

import torch
import torch.distributed as dist

MiB = 1 << 20


def timed(fn, repeats, devices):
    """Median wall time of fn() over repeats, after one warm-up round (lazy RCCL channel setup)."""
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
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--sizes", default="16,64,256,1024", help="comma-separated MiB")
    ap.add_argument("--chunk-MiB", type=int, default=256, help="stream chunk size")
    a = ap.parse_args()
    sizes = [int(x) * MiB for x in a.sizes.split(",")]

    dist.init_process_group(backend="nccl", timeout=__import__("datetime").timedelta(seconds=180))
    rank, world = dist.get_rank(), dist.get_world_size()
    local = int(os.environ.get("LOCAL_RANK", 0))
    torch.cuda.set_device(local)
    dev = torch.device("cuda", local)
    root = 0

    rows = []
    for nbytes in sizes:
        buf = torch.empty(nbytes // 4, dtype=torch.float32, device=dev)
        chunk = torch.empty(min(a.chunk_MiB * MiB, nbytes) // 4, dtype=torch.float32, device=dev)
        nchunk = (nbytes + chunk.numel() * 4 - 1) // (chunk.numel() * 4)

        def bc_one():
            dist.broadcast(buf, src=root)

        def bc_stream():
            for _ in range(nchunk):
                dist.broadcast(chunk, src=root)

        def ar_one():
            dist.all_reduce(buf)

        t_bc = timed(bc_one, a.repeats, dev)
        t_bcs = timed(bc_stream, a.repeats, dev)
        t_ar = timed(ar_one, a.repeats, dev)
        # bytes on the wire: broadcast = N; ring all_reduce = 2N(world-1)/world
        row = {"bytes": nbytes, "MiB": nbytes >> 20, "chunks": nchunk,
               "broadcast_GBps": nbytes / t_bc / 1e9,
               "broadcast_stream_GBps": nbytes / t_bcs / 1e9,
               "allreduce_GBps": (2 * nbytes * (world - 1) / world) / t_ar / 1e9,
               "broadcast_s": t_bc, "broadcast_stream_s": t_bcs, "allreduce_s": t_ar}
        rows.append(row)
        if rank == 0:
            print(f"{nbytes>>20:5d} MiB  bcast {row['broadcast_GBps']:6.2f}  "
                  f"bcast-stream({nchunk}x) {row['broadcast_stream_GBps']:6.2f}  "
                  f"allreduce {row['allreduce_GBps']:6.2f} GB/s", flush=True)

    gathered = [None] * world
    dist.all_gather_object(gathered, None)
    info = {"rank": rank, "host": os.uname().nodename, "local_rank": local,
            "dev": torch.cuda.get_device_properties(local).name}
    allinfo = [None] * world
    dist.all_gather_object(allinfo, info)
    if rank == 0:
        out = {"tag": a.tag, "world": world, "peers": [i["host"] for i in allinfo],
               "devices": {i["host"]: i["dev"] for i in allinfo},
               "note": "RCCL send/recv cross-node is broken on this image; measured with collectives",
               "rows": rows}
        os.makedirs(os.path.dirname(a.out), exist_ok=True)
        json.dump(out, open(a.out, "w"), indent=1)
        print("wrote", a.out)
    dist.destroy_process_group()


if __name__ == "__main__":
    main()
