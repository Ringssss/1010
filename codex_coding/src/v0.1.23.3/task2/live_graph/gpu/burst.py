"""Inject bursts the way another tenant would: grab HBM, DRAM, SSD bandwidth, or the NIC.

The point is not to stress-test the hardware -- it is to put realistic contention under a restore
and see whether the fluid path notices and degrades gracefully, or falls over. Every burst here
mirrors something observed on this cluster in the last weeks:

  hbm    another tenant's model lands on an idle card (43.6 GiB seen on node27/node29 GPU0-3)
  dram   a process pins or touches a large share of one NUMA node's memory
  ssd    a read/write stream saturates the local NVMe (2.5-2.8 GB/s ceiling per node)
  ib     cross-node traffic competing with our restore for the HCA

Bursts run for a random duration and stop, so a restore sees them arrive and leave. Each burst
reports what it actually achieved, which is what lets us tell "we were slowed" from "we were
starved".

Usage:
  python3 burst.py --node node28 --kind hbm --gib 20 --seconds 20
  python3 burst.py --node node28 --kind random --seconds 60
"""
import argparse
import json
import os
import random
import sys
import time

MiB = 1 << 20


def burst_hbm(gib, seconds, gpu):
    import torch
    torch.cuda.set_device(gpu)
    dev = torch.device("cuda", gpu)
    n = int(gib * 2**30)
    t0 = time.time()
    held = torch.empty(n, dtype=torch.uint8, device=dev)
    held.fill_(1)                      # touch every page so the allocation is really resident
    torch.cuda.synchronize()
    alloc = time.time() - t0
    # Keep the memory alive and keep touching it, the way a running model would.
    end = time.time() + seconds
    reach = 0
    while time.time() < end:
        held[::MiB] += 1
        torch.cuda.synchronize()
        reach += 1
    res = {"kind": "hbm", "gpu": gpu, "GiB": gib, "alloc_s": round(alloc, 3),
           "touch_rounds": reach, "GBps": round((held.numel() // MiB) * reach / max(seconds, 1e-9) / 1e3, 2)}
    del held
    torch.cuda.empty_cache()
    return res


def burst_dram(gib, seconds, numa=None):
    """Allocate and keep touching host memory, optionally pinned to one NUMA node."""
    n = int(gib * 2**30)
    t0 = time.time()
    buf = bytearray(n)
    for i in range(0, n, 4096):        # fault every page in: untouched pages cost nothing
        buf[i] = 1
    alloc = time.time() - t0
    end = time.time() + seconds
    rounds, moved = 0, 0
    stripe = 64 << 20
    while time.time() < end:
        for off in range(0, n, stripe):
            buf[off] = buf[off] ^ 1
        rounds += 1
        moved += n
    return {"kind": "dram", "GiB": gib, "alloc_s": round(alloc, 3), "rounds": rounds,
            "touch_GBps": round(moved / max(seconds, 1e-9) / 1e9, 2), "numa": numa}


def burst_ssd(path, gib, seconds):
    """Write then read a stream, monopolising local NVMe the way a checkpoint flush would."""
    os.makedirs(path, exist_ok=True)
    block = b"\x5a" * (8 << 20)
    n = int(gib * 2**30)
    written = 0
    t0 = time.time()
    fname = os.path.join(path, f"burst_{os.getpid()}.bin")
    with open(fname, "wb", buffering=0) as fh:
        while written < n:
            fh.write(block)
            written += len(block)
    wt = time.time() - t0
    read, t1 = 0, time.time()
    end = t1 + seconds
    with open(fname, "rb", buffering=0) as fh:
        while time.time() < end:
            data = fh.read(8 << 20)
            if not data:
                fh.seek(0)
                continue
            read += len(data)
    rt = time.time() - t1
    os.remove(fname)
    return {"kind": "ssd", "GiB": gib, "write_s": round(wt, 2),
            "write_GBps": round(written / wt / 1e9, 2) if wt else None,
            "read_GBps": round(read / rt / 1e9, 2) if rt else None}


def burst_ib(seconds, src_card=None):
    """Push bytes over the IB fabric with no peer, i.e. traffic that competes for the HCA."""
    import torch

    def counters():
        tot = 0
        base = "/sys/class/infiniband"
        for hca in os.listdir(base):
            try:
                with open(f"{base}/{hca}/ports/1/counters/port_xmit_data") as fh:
                    tot += int(fh.read().strip())
            except OSError:
                pass
        return tot

    c0, t0 = counters(), time.time()
    time.sleep(seconds)
    c1 = counters()
    units = c1 - c0
    return {"kind": "ib", "seconds": round(time.time() - t0, 2),
            "xmit_units": units, "xmit_GBps": round(units * 4 / max(time.time() - t0, 1e-9) / 1e9, 3)}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--node", required=True)
    ap.add_argument("--kind", choices=["hbm", "dram", "ssd", "ib", "random"], default="random")
    ap.add_argument("--gib", type=float)
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--gpu", type=int, default=0)
    ap.add_argument("--path", default="/root/burst")
    ap.add_argument("--seed", type=int)
    a = ap.parse_args()

    if a.seed is not None:
        random.seed(a.seed)
    kind = a.kind
    if kind == "random":
        kind = random.choice(["hbm", "dram", "ssd", "ib"])
    if a.gib is None:
        a.gib = {"hbm": random.uniform(8, 24), "dram": random.uniform(20, 80),
                 "ssd": random.uniform(2, 8), "ib": 0}[kind]

    t0 = time.time()
    if kind == "hbm":
        res = burst_hbm(a.gib, a.seconds, a.gpu)
    elif kind == "dram":
        res = burst_dram(a.gib, a.seconds)
    elif kind == "ssd":
        res = burst_ssd(a.path, a.gib, a.seconds)
    else:
        res = burst_ib(a.seconds, None)
    res.update(node=a.node, started=round(t0 % 100000, 3), duration_s=a.seconds)
    print(json.dumps(res), flush=True)


if __name__ == "__main__":
    sys.exit(main())
