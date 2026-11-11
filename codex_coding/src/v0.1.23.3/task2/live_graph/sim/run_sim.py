"""Run the keep-alive simulator for every policy x load level x seed and summarize.

Usage: python run_sim.py <result_dir> [--horizon S] [--seeds N] [--levels a,b] [--policies a,b]
Topology and the baseline foreign HBM occupancy come from graph_r02 (real four-node probes).
Writes records.json and summary.json (refuses to overwrite).
"""
import argparse
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
from livesim import Sim                                                    # noqa: E402

from fluidckpt.live.graph import LiveGraph, build_topology                 # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
GRAPH = os.path.join(HERE, "../../../../../results/v0.1.23.3/task2/live_graph/graph_r02")

# Durable copies follow where the real checkpoints live (/data/node28, /data/node29, /home on n27).
MODELS = [
    {"name": "qwen3-0.6b", "bytes": int(1.5e9), "tp": 1, "durable": "n27/ssd/nvme0n1", "home": ["n27/hbm/6"]},
    {"name": "llama-8b", "bytes": int(16e9), "tp": 1, "durable": "n28/ssd/nvme0n1", "home": ["n28/hbm/5"]},
    {"name": "qwen-32b", "bytes": int(65e9), "tp": 2, "durable": "n28/ssd/nvme0n1", "home": ["n27/hbm/4", "n27/hbm/5"]},
    {"name": "qwen3.6-35b-a3b", "bytes": int(70e9), "tp": 2, "durable": "n28/ssd/nvme0n1", "home": ["n30/hbm/0", "n30/hbm/1"]},
    {"name": "clover-35b", "bytes": int(70e9), "tp": 4, "durable": "n29/ssd/nvme0n1",
     "home": ["n29/hbm/4", "n29/hbm/5", "n29/hbm/6", "n29/hbm/7"]},
]


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rdir")
    ap.add_argument("--horizon", type=float, default=1200)
    ap.add_argument("--seeds", type=int, default=3)
    ap.add_argument("--levels", default="none,moderate,heavy")
    ap.add_argument("--policies", default="P0,P1,P2,P2f")
    a = ap.parse_args()
    os.makedirs(a.rdir, exist_ok=True)
    if os.path.exists(os.path.join(a.rdir, "summary.json")):
        raise SystemExit("refusing to overwrite")
    probes = [json.load(open(os.path.join(GRAPH, f"probe_n{n}.json"))) for n in (27, 28, 29, 30)]
    graph = LiveGraph(build_topology([p["inventory"] for p in probes]))
    base = {f"{p['inventory']['node']}/hbm/{g}": u for p in probes for g, u in p["fast"][-1]["hbm_used"].items()}
    records, summary = [], {}
    for level in a.levels.split(","):
        for seed in range(a.seeds):
            for pol in a.policies.split(","):
                t0 = time.time()
                sim = Sim(graph, MODELS, pol, level, seed, horizon=a.horizon, base_hbm_used=base)
                recs = sim.run()
                for r in recs:
                    r.update(level=level, seed=seed)
                records += recs
                fetch = [w for k, w in sim.plan_wall if k == "fetch"]
                park = [w for k, w in sim.plan_wall if k == "park"]
                print(f"{level:9s} seed={seed} {pol:4s} restores={len(recs):3d} "
                      f"mean={statistics.mean([r['restore_s'] for r in recs]) if recs else float('nan'):6.2f}s "
                      f"evicted={sim.evicted / 1e9:7.1f}GB unfinished={sim.unfinished} fetch_plan_p50={pct(fetch, .5) or 0:.3f}s "
                      f"park_plan_p50={pct(park, .5) or 0:.3f}s wall={time.time() - t0:.0f}s", flush=True)
                summary.setdefault(level, {}).setdefault(pol, {"evicted_GB": [], "fetch_plan_s": [], "park_plan_s": [],
                                                               "unfinished": []})
                s = summary[level][pol]
                s["unfinished"].append(sim.unfinished)
                s["evicted_GB"].append(sim.evicted / 1e9)
                s["fetch_plan_s"] += fetch
                s["park_plan_s"] += park
    table = {}
    for level, pols in summary.items():
        for pol, s in pols.items():
            rs = [r for r in records if r["level"] == level and r["policy"] == pol]
            big = [r for r in rs if r["bytes"] >= 60e9]
            table.setdefault(level, {})[pol] = {
                "restores": len(rs),
                "mean_s": statistics.mean(r["restore_s"] for r in rs) if rs else None,
                "p50_s": pct([r["restore_s"] for r in rs], .5), "p90_s": pct([r["restore_s"] for r in rs], .9),
                "big_mean_s": statistics.mean(r["restore_s"] for r in big) if big else None,
                "big_p90_s": pct([r["restore_s"] for r in big], .9),
                "cold_frac": sum(r["from_durable_frac"] > 0.99 for r in rs) / len(rs) if rs else None,
                "durable_bytes_frac": sum(r["from_durable_frac"] * r["bytes"] for r in rs) / sum(r["bytes"] for r in rs) if rs else None,
                "evicted_GB_mean": statistics.mean(s["evicted_GB"]), "unfinished_at_horizon": sum(s["unfinished"]),
                "fetch_plan_p50_s": pct(s["fetch_plan_s"], .5), "fetch_plan_p99_s": pct(s["fetch_plan_s"], .99),
                "park_plan_p50_s": pct(s["park_plan_s"], .5),
            }
    json.dump(records, open(os.path.join(a.rdir, "records.json"), "w"))
    json.dump({"args": vars(a), "models": MODELS, "table": table}, open(os.path.join(a.rdir, "summary.json"), "w"), indent=1)
    for level, pols in table.items():
        for pol, row in pols.items():
            print(level, pol, {k: (round(v, 3) if isinstance(v, float) else v) for k, v in row.items()})


if __name__ == "__main__":
    main()
