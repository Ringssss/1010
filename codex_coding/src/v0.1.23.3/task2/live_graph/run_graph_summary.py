"""Build the global live graph from recorded per-node probe JSONs and time its operations.

Usage: python run_graph_summary.py <result_dir> [out_name]   (expects probe_n*.json from live/probe.py)
Writes <result_dir>/<out_name> (default graph_summary.json); refuses to overwrite.
"""
import glob
import json
import os
import sys
import time
from collections import Counter

from fluidckpt.live.graph import NOMINAL, LiveGraph, build_topology

GiB = 1 << 30


def main(rdir, out_name="graph_summary.json"):
    out_path = os.path.join(rdir, out_name)
    if os.path.exists(out_path):
        raise SystemExit(f"refusing to overwrite {out_path}")
    probes = {}
    for path in sorted(glob.glob(os.path.join(rdir, "probe_n*.json"))):
        d = json.load(open(path))
        probes[d["inventory"]["node"]] = d
    t = time.perf_counter()
    topo = build_topology([p["inventory"] for p in probes.values()])
    build_s = time.perf_counter() - t
    g = LiveGraph(topo)
    t = time.perf_counter()
    for node, p in probes.items():
        g.ingest(node, fast=p["fast"][-1], pcie=p["pcie"])
    snap = g.snapshot(now=max(p["fast"][-1]["t"] for p in probes.values()))
    ingest_s = time.perf_counter() - t

    # steady-state update cost: re-ingest one node's sample and take a snapshot
    reps, t = 200, time.perf_counter()
    for i in range(reps):
        node = list(probes)[i % len(probes)]
        g.ingest(node, fast=probes[node]["fast"][-1])
        g.snapshot()
    update_s = (time.perf_counter() - t) / reps

    dst = next(v for v in topo.vertices if v.endswith("/hbm/4") and v.startswith(sorted(probes)[0]))
    t = time.perf_counter()
    n_paths = sum(len(g.paths(v, dst)) for v in topo.vertices if v != dst)
    enum_s = time.perf_counter() - t
    t = time.perf_counter()
    for v in topo.vertices:
        if v != dst:
            g.paths(v, dst)
    cached_s = time.perf_counter() - t
    samples = {}
    for src in [f"{dst.split('/')[0]}/dram/4", "n28/dram/4", "n28/hbm/4", "n29/ssd/nvme0n1"]:
        if src in topo.vertices:
            samples[src] = ["+".join(e.kind for e in p.edges) + " via " + ",".join(e.dst for e in p.edges[:-1])
                            for p in g.paths(src, dst)]

    free = {}
    for vid, s in snap.vertices.items():
        free[vid] = round(s.free_bytes / GiB, 2)
    tiers = {k: round(sum(x for v, x in free.items() if f"/{k}/" in v), 1) for k in ("hbm", "dram", "ssd")}
    busy = {r: round(s.background_GBps, 4) for r, s in snap.resources.items() if s.background_GBps > 0.01}
    out = {
        "nodes": sorted(probes),
        "counts": {"vertices": dict(Counter(v.kind for v in topo.vertices.values())),
                   "edges": dict(Counter(e.kind for e in topo.edges)), "resources": len(topo.resources)},
        "timing_ms": {"build_topology": build_s * 1e3, "ingest_4_nodes_and_snapshot": ingest_s * 1e3,
                      "steady_update_plus_snapshot": update_s * 1e3,
                      "enumerate_all_paths_to_one_gpu": enum_s * 1e3, "cached_all_paths_to_one_gpu": cached_s * 1e3,
                      "probe_fast_mean": {n: sum(x["cost_s"] for x in p["fast"]) / len(p["fast"]) * 1e3
                                          for n, p in probes.items()},
                      "probe_pcie": {n: p["pcie"]["cost_s"] * 1e3 for n, p in probes.items() if p["pcie"]}},
        "paths_to": dst, "n_paths": n_paths, "path_samples": samples,
        "free_GiB_by_tier": tiers, "free_GiB": free, "background_GBps_nonzero": busy,
        "nominal": {k: {"GBps": v[0], "source": v[1]} for k, v in NOMINAL.items()},
        "hbm_used_GiB": {n: {g: round(u / GiB, 1) for g, u in p["fast"][-1]["hbm_used"].items()}
                         for n, p in probes.items()},
    }
    json.dump(out, open(out_path, "w"), indent=1)
    print(json.dumps({k: out[k] for k in ("counts", "timing_ms", "n_paths", "free_GiB_by_tier", "path_samples")}, indent=1))
    print("background (GB/s, nonzero):", busy)


if __name__ == "__main__":
    main(*sys.argv[1:3])
