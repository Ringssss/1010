"""Cluster-side live graph loop: pull every node's publisher, keep one LiveGraph current, and
record snapshots as JSON lines so later experiments replay real background load.

Usage:
  python3 run_live_cluster.py --out <dir> [--seconds N] [--period 0.2] [--ports ...]

Also prints, every --report seconds, a compact view of what the graph sees: per-node free HBM /
DRAM / SSD and the measured IB + NVMe rates, so a human can tell at a glance whether the picture
is live or stale.
"""
import argparse
import json
import os
import sys
import time
import urllib.request

from fluidckpt.live.agent import Aggregator
from fluidckpt.live.graph import LiveGraph, build_topology

IB = {"node27": "12.11.3.27", "node28": "12.11.3.28", "node29": "12.11.3.29", "node30": "12.11.3.30"}
GiB = 1 << 30


def fetch_inventory(base, timeout=2.0):
    with urllib.request.urlopen(f"{base}/inventory", timeout=timeout) as r:
        return json.loads(r.read())


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--nodes", default="node27,node28,node29,node30")
    ap.add_argument("--port", type=int, default=18740)
    ap.add_argument("--period", type=float, default=0.2)
    ap.add_argument("--seconds", type=float, default=0, help="0 = until interrupted")
    ap.add_argument("--report", type=float, default=10.0)
    a = ap.parse_args(argv)

    os.makedirs(a.out, exist_ok=True)
    nodes = a.nodes.split(",")
    endpoints = {n: f"http://{IB[n]}:{a.port}" for n in nodes}
    inv = {}
    for n, base in endpoints.items():
        for _ in range(50):
            try:
                inv[n] = fetch_inventory(base)
                break
            except Exception as e:
                last = e
                time.sleep(0.2)
        else:
            raise SystemExit(f"{n}: inventory unreachable: {last}")

    topo = build_topology(list(inv.values()))
    g = LiveGraph(topo)
    agg = Aggregator(g, endpoints)

    t0 = time.time()
    t_report, n_pull, pull_ms = t0, 0, []
    with open(os.path.join(a.out, "snapshots.jsonl"), "w") as fh:
        fh.write(json.dumps({"event": "topology", "vertices": len(topo.vertices),
                             "edges": len(topo.edges), "resources": len(topo.resources),
                             "t": time.time()}) + "\n")
        while True:
            now = time.time()
            if a.seconds and now - t0 >= a.seconds:
                break
            lat = agg.poll()
            n_pull += 1
            pull_ms.append(max(lat.values()) * 1e3)
            snap = g.snapshot()
            row = {"t": now, "version": snap.version, "taken_at": snap.taken_at,
                   "err": dict(agg.errors), "vertices": {}, "resources": {}}
            for vid, st in snap.vertices.items():
                row["vertices"][vid] = {"free": st.free_bytes, "age": round(now - st.observed_at, 3),
                                        "stale": st.stale}
            for rid, rs in snap.resources.items():
                row["resources"][rid] = {"bg_GBps": round(rs.background_GBps, 4),
                                         "stale": rs.stale}
            fh.write(json.dumps(row) + "\n")

            if now - t_report >= a.report:
                _report(g, snap, inv, pull_ms, time.time() - t0, n_pull, agg.errors)
                t_report, pull_ms, n_pull = now, [], 0
            time.sleep(max(0.0, a.period - (time.time() - now)))
    return 0


def _free(snap, prefix, kind):
    out = {}
    for vid, st in snap.vertices.items():
        parts = vid.split("/")
        if parts[0] == prefix and parts[1] == kind:
            out["/".join(parts[2:]) or "all"] = getattr(st, "free_bytes", None)
    return out


def _report(g, snap, inv, pull_ms, elapsed, n_pull, errors):
    p50 = sorted(pull_ms)[len(pull_ms) // 2] if pull_ms else 0.0
    print(f"[{elapsed:7.1f}s] v={snap.version} pulls={n_pull} p50max={p50:.1f}ms "
          f"p95max={sorted(pull_ms)[int(len(pull_ms) * .95)] if pull_ms else 0:.1f}ms "
          f"err={list(errors)}", flush=True)
    for node in sorted(inv):
        hbm = _free(snap, node, "hbm")
        dram = _free(snap, node, "dram")
        ssd = _free(snap, node, "ssd")
        free_hbm = [hbm[k] for k in sorted(hbm, key=lambda x: int(x) if x.isdigit() else 0)]
        free_gib = [round((v or 0) / GiB, 1) for v in free_hbm]
        d = sum(v or 0 for v in dram.values()) / GiB
        s = sum(v or 0 for v in ssd.values()) / GiB
        print(f"   {node}: hbm_free_GiB={free_gib} dram_free={d:.0f} ssd_free={s:.0f}", flush=True)


if __name__ == "__main__":
    sys.exit(main())
