"""Two-rank driver for fluidckpt.live.mover: prove the ack-gated broadcast primitive.

Rank 1 holds N chunks in pinned DRAM and sends them; rank 0 receives, copies them off, and
acknowledges. The sender never waits on its own collective -- that is the whole point (see
mover.py). Correctness is a byte-for-byte checksum on the receiver, so a fast-but-wrong result
cannot pass.

Usage, one process per node:
  python3 test_mover.py --rank 0 --peer-rank 1 --world 2 --master 12.11.3.27:39650 \
      --peers '{"0":"12.11.3.27","1":"12.11.3.28"}' --ack-base 39700 --gib 1
"""
import argparse
import datetime
import hashlib
import json
import time

import torch
import torch.distributed as dist

import faulthandler, signal
faulthandler.register(signal.SIGUSR1)

from fluidckpt.live.mover import HostSink, MiB, Mover


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rank", type=int, required=True)
    ap.add_argument("--world", type=int, default=2)
    ap.add_argument("--master", required=True)
    ap.add_argument("--peers", required=True, help='json {"rank": "host"}, or a file holding it')
    ap.add_argument("--ack-base", type=int, default=39700)
    ap.add_argument("--gib", type=float, default=1.0)
    ap.add_argument("--chunks", type=int, default=4)
    a = ap.parse_args()

    peers_text = a.peers
    if peers_text and not peers_text.lstrip().startswith("{"):
        with open(peers_text) as fh:            # path form avoids nested-quote mangling over ssh
            peers_text = fh.read()
    peers = {int(k): v for k, v in json.loads(peers_text).items()}
    torch.cuda.set_device(0)
    dev = torch.device("cuda", 0)
    # Reserve the staging block BEFORE any collective: a first-touch cudaMalloc after a pending
    # collective makes the driver synchronise the device, which is where the two-rank mover hung.
    _staging = torch.empty(256 << 20, dtype=torch.uint8, device=dev)
    # Same rule for pinned host memory: cudaHostRegister on first touch also synchronises the
    # device, so the pinned chunk copies are reserved here, before any collective exists.
    _per = int(a.gib * 2**30) // a.chunks // MiB * MiB
    _pinned = [torch.empty(_per, dtype=torch.uint8, pin_memory=True) for _ in range(a.chunks)]
    # Receivers must own their landing buffers before any collective too: pinning memory
    # (cudaHostRegister) while a cross-node collective is pending deadlocks the same way a
    # first-touch cudaMalloc does.
    _sinks = [HostSink(_per) for _ in range(a.chunks)]
    torch.cuda.synchronize()
    dist.init_process_group("nccl", init_method=f"tcp://{a.master}", rank=a.rank,
                            world_size=a.world, timeout=datetime.timedelta(seconds=180))
    print(json.dumps({"ev": "pg_up", "rank": a.rank}), flush=True)
    g = dist.new_group(list(range(a.world)))
    print(json.dumps({"ev": "group", "rank": a.rank}), flush=True)
    warm = torch.ones(1, device=dev)
    dist.broadcast(warm, src=0, group=g)   # create the communicator before any timed transfer
    print(json.dumps({"ev": "warm", "rank": a.rank}), flush=True)
    mv = Mover(a.rank, [(0, 0), (1, 0)], {(0, 1): g, (1, 0): g}, a.ack_base, dev,
               staging=_staging)
    print(json.dumps({"ev": "mover", "rank": a.rank}), flush=True)
    mv.ready = peers                      # rank -> host for the ack channel
    mv._ack.ready = peers

    n = int(a.gib * 2**30)
    ids = [f"blk{i}" for i in range(a.chunks)]
    per = n // a.chunks
    per = per // MiB * MiB

    print(json.dumps({"ev": "init", "rank": a.rank}), flush=True)

    if a.rank == 1:
        for i, cid in enumerate(ids):
            with mv.lock:
                mv.buffers[cid] = _pinned[i]      # already allocated before the collectives
            b = mv.buffer(cid)
            b.fill_(7)                                   # fast: 256 MiB byte fill
            b[:1 << 20].copy_(torch.arange(1 << 20, dtype=torch.uint8))   # position-sensitive head
        torch.cuda.synchronize()
        print(json.dumps({"ev": "loaded", "GiB": round(per * len(ids) / 2**30, 3)}), flush=True)
        t0 = time.time()
        out = [mv.send(0, cid) for cid in ids]
        total = time.time() - t0
        print(json.dumps({"ev": "sent", "chunks": len(ids),
                          "GiB": round(per * len(ids) / 2**30, 3),
                          "total_s": round(total, 4),
                          "GBps": round(per * len(ids) / total / 1e9, 2),
                          "per_chunk": out}), flush=True)
    else:
        t0 = time.time()
        outs = []
        for i, cid in enumerate(ids):
            sink = _sinks[i]
            outs.append(mv.recv(1, cid, sink))
            outs[-1]["sha"] = hashlib.sha256(sink.buf.numpy().tobytes()).hexdigest()[:16]
        total = time.time() - t0
        print(json.dumps({"ev": "recv", "chunks": len(ids), "GiB": round(per * len(ids) / 2**30, 3),
                          "total_s": round(total, 4),
                          "GBps": round(per * len(ids) / total / 1e9, 2),
                          "per_chunk": outs}), flush=True)
    if a.rank == 0:
        dist.destroy_process_group()
    print(json.dumps({"ev": "done", "rank": a.rank}), flush=True)


if __name__ == "__main__":
    main()
