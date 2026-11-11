"""Calibrate what rsmi_dev_pci_throughput_get actually reports.

The SMI header says bytes/s; the amdgpu driver exposes packet counts that only become bytes after
multiplying by max_pkt_sz. Both readings are taken around one known-size H2D copy, so the ratio
(data moved / counter delta) tells which one the driver means and what its unit is.

Usage (inside a GPU container): python3 pcie_calibrate.py [--mb 512] [--reps 3] [--gpu 0]
Prints one JSON line per rep plus a summary.
"""
import argparse
import json
import time

import torch

from fluidckpt.live.probe import Smi


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mb", type=int, default=512)
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--gpu", type=int, default=0)
    a = ap.parse_args()

    smi = Smi()
    if smi.lib is None:
        raise SystemExit(f"smi unavailable: {smi.error}")
    n = a.mb << 20
    torch.cuda.set_device(a.gpu)
    dev = torch.device("cuda", a.gpu)

    host = torch.empty(n, dtype=torch.uint8, pin_memory=True)
    host.fill_(1)
    gpu = torch.empty(n, dtype=torch.uint8, device=dev)

    # warm-up: first touch of pageable/pinned memory and the copy engine
    gpu.copy_(host, non_blocking=True)
    torch.cuda.synchronize()
    time.sleep(1.2)                      # let the 1 s counter window drain

    rows = []
    for rep in range(a.reps):
        before = smi.pcie(a.gpu)         # (sent, received, max_pkt_sz), blocks ~1 s
        torch.cuda.synchronize()
        t0 = time.perf_counter()
        gpu.copy_(host, non_blocking=True)
        torch.cuda.synchronize()
        t1 = time.perf_counter()
        after = smi.pcie(a.gpu)
        if not before or not after:
            raise SystemExit("pcie counters unavailable")
        ds, dr, pkt = after[0] - before[0], after[1] - before[1], after[2]
        dt = t1 - t0
        rows.append({
            "rep": rep, "MiB": a.mb, "copy_s": round(dt, 6),
            "GBps_time": round(n / dt / 1e9, 3),
            "sent_delta": ds, "recv_delta": dr, "max_pkt_sz": pkt,
            "counter_kind": "host_to_dev" if dr >= ds else "dev_to_host",
            "GBps_counter_raw": round(max(ds, dr) / dt / 1e9, 6),
            "GBps_counter_x_pkt": round(max(ds, dr) * pkt / dt / 1e9, 6),
            "ratio_raw_to_bytes": round(n / max(ds, dr), 4) if max(ds, dr) else None,
            "gpu_alloc_GiB": round(torch.cuda.memory_allocated(dev) / 2**30, 3),
        })
        print(json.dumps(rows[-1]), flush=True)
        time.sleep(1.2)
    ok_time = sorted(r["GBps_time"] for r in rows)[len(rows) // 2]
    print(json.dumps({"summary": True, "median_GBps_time": ok_time,
                      "rows": [{"raw": r["GBps_counter_raw"], "xpkt": r["GBps_counter_x_pkt"]}
                               for r in rows]}), flush=True)


if __name__ == "__main__":
    main()
