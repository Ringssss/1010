"""Plan materialization scenarios with every planner, predict the makespan with the fluid model,
execute each plan on the real node and compare.

  python -m fluidckpt.executor.run_plans --profile benchmarks/results/profile_node27.json \
      --fit benchmarks/results/model_fit_node27.json --variant V9_links_hswio_numa_rw_conflict_tied \
      --out benchmarks/results/plans_node27.json

Sizes are real Qwen3.6-35B-A3B shards (67 GiB bf16: TP2 = 33.5 GiB/GPU, TP4 = 16.75 GiB/GPU).
Executor streams are bandwidth-faithful, not content-faithful: an H2D/P2P stream re-copies one
256 MiB buffer until its byte count is done; NVMe streams really read (O_DIRECT) the checkpoint files.
Every plan runs on fresh workers that only see the GPUs of their own stream.
"""
import argparse
import json
import statistics

from fluidckpt.model.fit import Model
from fluidckpt.model.fluid import simulate
from fluidckpt.model.resources import SingleCaps
from fluidckpt.planner.plans import (DemandClass, Problem, plan_fastest_source, plan_lp, plan_lp_linear, plan_nvme_only,
                                     plan_resource_greedy, streams)
from fluidckpt.profiler.workers import Pool

GiB = 1 << 30
CKPT = 67 * GiB
OP_BYTES = {"h2d": 256 << 20, "p2p": 256 << 20, "nvme2gpu": 256 << 20}
NVME = {"tier": "nvme"}
GPU_NUMA = {0: 0, 1: 3, 2: 2, 3: 1, 4: 4, 5: 7, 6: 6, 7: 5}  # node27, from the profile


def hbm(g):
    return {"tier": "hbm", "gpu": g}


def dram(n):
    return {"tier": "dram", "numa": n}


def scenarios():
    tp2, tp4 = CKPT // 2, CKPT // 4
    return {
        # TP2 instance on GPUs 0,1. Shard 0 still lives in HBM of GPU 4 (a draining instance) and in DRAM
        # on NUMA 2; shard 1 was demoted to DRAM on the far socket only.
        "tp2_scattered": [
            DemandClass(0, tp2, [hbm(4), dram(2), NVME]),
            DemandClass(1, tp2, [dram(5), NVME]),
        ],
        # Scale-out of a TP4 instance onto GPUs 0-3 while a copy serves on GPUs 4-7; one DRAM copy on NUMA 0.
        "tp4_from_running_copy": [
            DemandClass(g, tp4, [hbm(g + 4), dram(0), NVME]) for g in range(4)
        ],
        # Four TP2 instances at once (all 8 GPUs) from two DRAM copies, one per socket, plus NVMe.
        "4xtp2_from_two_dram_copies": [
            DemandClass(g, tp2, [dram(0 if g % 2 == 0 else 1), dram(4 if g % 2 == 0 else 5), NVME])
            for g in range(8)
        ],
        # Only the NVMe EWI plus one DRAM copy on the far socket (NUMA 6); TP4 on socket A.
        "tp4_nvme_plus_far_dram": [
            DemandClass(g, tp4, [dram(6), NVME]) for g in range(4)
        ],
        # Two new TP2 instances on GPUs 0-3 cloned from one running TP2 on GPUs 4,5: each serving GPU's
        # HBM fans out to two targets (shared P2P send port); demotion left DRAM copies NUMA-local.
        "2xtp2_clone_from_one_running": [
            DemandClass(g, tp2, [hbm(4 if g % 2 == 0 else 5), dram(GPU_NUMA[g]), NVME]) for g in range(4)
        ],
        # Placement policy regime: each shard demoted to the DRAM of its target GPU's own NUMA node.
        "tp4_numa_local_dram": [
            DemandClass(g, tp4, [dram(GPU_NUMA[g]), NVME]) for g in range(4)
        ],
        # Whole node: four TP2 instances, every shard demoted to its target GPU's own NUMA node.
        "4xtp2_numa_local_dram": [
            DemandClass(g, tp2, [dram(GPU_NUMA[g]), NVME]) for g in range(8)
        ],
    }


def path_cost(gpu_numa):
    """Per-GB cost of a path for the min-cost stage: pulling from a serving GPU's HBM steals its HSW
    port from TP collectives; remote-NUMA reads load the shared host fabric."""
    def cost(spec):
        if spec["kind"] == "p2p":
            return 1.0
        if spec["kind"] == "h2d" and spec["numa"] != gpu_numa[spec["gpu"]]:
            return 0.3
        return 0.0
    return cost


def planners(prob, gpu_numa, only=None):
    if only == "linear":  # round 2: regime-restricted LP against the best round-1 baseline
        lin = plan_lp_linear(prob.classes, prob.model)
        return [plan_fastest_source(prob), lin, lin.paced()]
    lp = plan_lp(prob)
    cheap = plan_lp(prob, cost=path_cost(gpu_numa), slack=0.10)  # cheapest plan within 10% of T*
    return [plan_nvme_only(prob), plan_fastest_source(prob), plan_resource_greedy(prob),
            lp, lp.paced(), cheap, cheap.paced()]


def dynamic_plan(prob, lp, all_paths):
    """Work-stealing execution seeded by the LP: streams = LP-used paths (or every candidate path);
    each stream prefers the classes the LP gave it, then steals from any class it can serve."""
    lp_bytes = {}
    for (c, i), b in lp.assign.items():
        lp_bytes[(c, lp.paths[i]["_key"] if "_key" in lp.paths[i] else _k(lp.paths[i]))] = b
    keys = [_k(p) for p in prob.paths]
    use = range(len(prob.paths)) if all_paths else sorted({keys.index(k) for (_, k) in lp_bytes})
    paths, serves, chunk = [], [], []
    for p in use:
        cls = [c for c, cand in enumerate(prob.cands) if cand and p in cand]
        cls.sort(key=lambda c: -lp_bytes.get((c, keys[p]), 0))
        paths.append(prob.paths[p])
        serves.append(cls)
        chunk.append(64 << 20 if prob.paths[p]["kind"] == "nvme2gpu" else 256 << 20)  # small tail for slow paths
    return {"planner": "dynamic_" + ("all_paths" if all_paths else "lp_paths"), "paths": paths, "serves": serves,
            "chunk": chunk, "class_bytes": [c.nbytes if cand else 0 for c, cand in zip(prob.classes, prob.cands)],
            "lp_bound_s": lp.lp_bound_s}


def _k(spec):
    return tuple(sorted(spec.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", required=True)
    ap.add_argument("--fit", required=True)
    ap.add_argument("--variant", required=True)
    ap.add_argument("--out", required=True)
    ap.add_argument("--repeats", type=int, default=3)
    ap.add_argument("--dry", action="store_true", help="plan and predict only")
    ap.add_argument("--scenarios", default=None, help="comma-separated subset")
    ap.add_argument("--only", default=None, help="'linear': fastest_source vs regime-restricted LP only")
    a = ap.parse_args()
    prof, fit = json.load(open(a.profile)), json.load(open(a.fit))
    gpu_numa = {int(k): v for k, v in prof["gpu_numa"].items()}
    v = fit["variants"][a.variant]
    model = Model(SingleCaps(prof), v["variant"], gpu_numa, v["capacity_GBps"], tied=v["tied"],
                  family_default=v["family_capacity_GBps"])

    todo = []
    for sname, classes in scenarios().items():
        if a.scenarios and sname not in a.scenarios.split(","):
            continue
        prob = Problem(classes, model)
        plans = planners(prob, gpu_numa, a.only)
        for all_paths in (() if a.only else (False, True)):
            d = dynamic_plan(prob, plans[3], all_paths)
            todo.append(dict(d, scenario=sname, pred_makespan_s=None, bytes=None, rates=None, meta={}))
        for plan in plans:
            fin = simulate(*streams(plan, model))
            todo.append({"scenario": sname, "planner": plan.name, "paths": plan.paths, "rates": plan.rates,
                         "bytes": plan.bytes_per_path, "lp_bound_s": plan.lp_bound_s, "meta": plan.meta,
                         "pred_finish_s": fin, "pred_makespan_s": max(fin)})
    todo.sort(key=lambda r: r["scenario"])
    for r in todo:
        pred = "   dyn  " if r["pred_makespan_s"] is None else f"{r['pred_makespan_s']:.3f}s"
        print(f"{r['scenario']:26s} {r['planner']:26s} streams={len(r['paths']):2d} pred={pred}"
              + (f" lp_bound={r['lp_bound_s']:.3f}s" if r["lp_bound_s"] else ""))
    if not a.dry:
        for r in todo:
            pool = Pool.for_specs(r["paths"])
            try:
                if "serves" in r:
                    runs = pool.dynamic(r["paths"], r["serves"], r["class_bytes"], r["chunk"], OP_BYTES, a.repeats)
                else:
                    runs = pool.fixed(r["paths"], r["bytes"], OP_BYTES, a.repeats, r["rates"])
            finally:
                pool.close()
            r["runs"] = [{"finish_s": [x["t1"] - x["start"] for x in run],
                          "makespan_s": max(x["t1"] for x in run) - min(x["t0"] for x in run)} for run in runs]
            r["actual_makespan_s"] = statistics.median(x["makespan_s"] for x in r["runs"])
            if "serves" in r:
                r["took"] = [[x["took"] for x in run] for run in runs]
            base = r["pred_makespan_s"] or r["lp_bound_s"]
            r["pred_rel_err"] = (base - r["actual_makespan_s"]) / r["actual_makespan_s"]
            print(f"{r['scenario']:26s} {r['planner']:26s} pred={base:.3f}s actual={r['actual_makespan_s']:.3f}s "
                  f"({'vs LP bound' if r['pred_makespan_s'] is None else 'model err'} {r['pred_rel_err']:+.1%})",
                  flush=True)
    json.dump({"variant": a.variant, "capacity_GBps": model.capacity, "results": todo},
              open(a.out, "w"), indent=1, default=str)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
