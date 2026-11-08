"""Node-local path profiler: raw measurements only, no fitting.

  python -m fluidckpt.profiler.run --out benchmarks/results/profile_node27.json
"""
import argparse
import json
import os
import random
import socket
import subprocess
import time

from fluidckpt.profiler.workers import Pool

# hy-smi --showtopo on node27 (HCU -> NUMA node)
GPU_NUMA = {0: 0, 1: 3, 2: 2, 3: 1, 4: 4, 5: 7, 6: 6, 7: 5}
NUMA_NODES = list(range(8))
NBYTES = {"h2d": 256 << 20, "d2h": 256 << 20, "p2p": 256 << 20, "nvme": 64 << 20, "nvme2gpu": 256 << 20}
MiB = 1 << 20


def h2d(n, g): return {"kind": "h2d", "numa": n, "gpu": g}
def d2h(g, n): return {"kind": "d2h", "gpu": g, "numa": n}
def p2p(i, j): return {"kind": "p2p", "src": i, "dst": j}
def nvme(n, k=0): return {"kind": "nvme", "numa": n, "file_idx": k}
def nvme2gpu(n, g, k=0): return {"kind": "nvme2gpu", "numa": n, "gpu": g, "file_idx": k}


def local(g): return h2d(GPU_NUMA[g], g)


def train_experiments():
    e = {}
    for k in (2, 4, 8):
        e[f"h2d_local_k{k}"] = [local(g) for g in range(k)]
        e[f"h2d_numa0_to_k{k}"] = [h2d(0, g) for g in range(k)]
    e["h2d_local_socketB"] = [local(g) for g in (4, 5, 6, 7)]
    for k in (1, 2, 4):
        e[f"h2d_numa0_to_socketB_k{k}"] = [h2d(0, g) for g in (4, 5, 6, 7)[:k]]
    e["h2d_numa4_to_socketA_k4"] = [h2d(4, g) for g in (0, 1, 2, 3)]
    e["d2h_local_k8"] = [d2h(g, GPU_NUMA[g]) for g in range(8)]
    e["d2h_to_numa0_k4"] = [d2h(g, 0) for g in range(4)]
    for k in (2, 4):
        e[f"p2p_fanout_from4_k{k}"] = [p2p(4, j) for j in range(k)]
        e[f"p2p_fanin_to0_k{k}"] = [p2p(i, 0) for i in range(1, k + 1)]
    e["p2p_fanin_to0_k7"] = [p2p(i, 0) for i in range(1, 8)]
    e["p2p_fanout_from0_k7"] = [p2p(0, j) for j in range(1, 8)]
    e["p2p_pairs_disjoint"] = [p2p(0, 1), p2p(2, 3), p2p(4, 5), p2p(6, 7)]
    e["mix_gpu0_ingress_h2d_p2p"] = [local(0), p2p(1, 0)]
    e["mix_gpu0_ingress_h2d_p2p2"] = [local(0), p2p(1, 0), p2p(2, 0)]
    e["mix_gpu4_egress_d2h_p2p"] = [d2h(4, 4), p2p(4, 0)]
    for k in (1, 2, 4, 8):
        e[f"nvme_numa0_k{k}"] = [nvme(0, i) for i in range(k)]
    e["nvme_numa4_k4"] = [nvme(4, i) for i in range(4)]
    e["nvme2gpu_single"] = [nvme2gpu(0, 0)]
    e["nvme2gpu_k4"] = [nvme2gpu(GPU_NUMA[g], g, g) for g in range(4)]
    e["nvme_k4_plus_h2d_local_k4"] = [nvme(0, i) for i in range(4)] + [local(g) for g in range(4)]
    return e


def train2_experiments():
    """Added after validation set 0 exposed H2D+D2H collapse on one GPU and many-to-one NUMA reads."""
    e = {}
    e["bidir_g0_local"] = [local(0), d2h(0, 0)]
    e["bidir_g4_remote"] = [h2d(3, 4), d2h(4, 2)]
    e["bidir_g1_cross"] = [h2d(4, 1), d2h(1, 1)]
    e["bidir_k2_local"] = [local(0), local(1), d2h(0, 0), d2h(1, GPU_NUMA[1])]
    e["h2d_g0_d2h_g1_control"] = [local(0), d2h(1, GPU_NUMA[1])]
    e["numa0_read_and_write"] = [h2d(0, 1), d2h(2, 0)]
    e["numa0_rw_k4"] = [h2d(0, 1), h2d(0, 3), d2h(2, 0), d2h(4, 0)]
    for gs in ((2, 3), (0, 2), (1, 3), (6, 7), (2, 7)):
        e[f"numa0_to_g{gs[0]}g{gs[1]}"] = [h2d(0, g) for g in gs]
    e["numa4_to_socketB_k4"] = [h2d(4, g) for g in (4, 5, 6, 7)]
    e["h2d_two_numa_to_socketA"] = [h2d(0, 0), h2d(0, 1), h2d(4, 2), h2d(4, 3)]
    e["p2p_ring8"] = [p2p(i, (i + 1) % 8) for i in range(8)]
    e["p2p_bidir_pair"] = [p2p(0, 1), p2p(1, 0)]
    e["p2p_plus_bidir_pcie_g0"] = [p2p(1, 0), local(0), d2h(0, 0)]
    return e


def validation_experiments(count=16, seed=0):
    """Random mixes never used for fitting."""
    rng = random.Random(seed)
    out = {}
    for m in range(count):
        flows, n_nvme = [], 0
        for _ in range(rng.randint(3, 7)):
            kind = rng.choices(["h2d", "d2h", "p2p", "nvme"], weights=[4, 2, 3, 1])[0]
            if kind == "nvme":
                if n_nvme >= 3:
                    continue
                flows.append(nvme(rng.choice(NUMA_NODES), n_nvme)); n_nvme += 1
            elif kind == "h2d":
                flows.append(h2d(rng.choice(NUMA_NODES), rng.randrange(8)))
            elif kind == "d2h":
                flows.append(d2h(rng.randrange(8), rng.choice(NUMA_NODES)))
            else:
                i, j = rng.sample(range(8), 2)
                flows.append(p2p(i, j))
        out[f"val_{m:02d}"] = flows
    return out


def materialization_experiments(count=16, seed=2):
    """Random mixes shaped like planner output: DRAM->HBM (any NUMA), HBM->HBM, NVMe->HBM; no D2H."""
    rng = random.Random(seed)
    out = {}
    for m in range(count):
        flows, n_nvme = [], 0
        for _ in range(rng.randint(3, 8)):
            kind = rng.choices(["h2d", "p2p", "nvme2gpu"], weights=[4, 3, 1])[0]
            g = rng.randrange(8)
            if kind == "nvme2gpu":
                if n_nvme >= 2:
                    continue
                flows.append(nvme2gpu(GPU_NUMA[g], g, n_nvme)); n_nvme += 1
            elif kind == "h2d":
                flows.append(h2d(rng.choice(NUMA_NODES), g))
            else:
                flows.append(p2p(*rng.sample(range(8), 2)))
        out[f"mat_{m:02d}"] = flows
    return out


def train3_experiments():
    """Added after the plan runs: GPU 0 and the checkpoint NVMe share one PCIe switch / root port.
    Tests "switch bandwidth" (rejected: h2d0 + NVMe add up) against "one root port touching two NUMA
    nodes" (confirmed: h2d n0->g0 falls to 29.5 only when the NVMe writes to another node)."""
    return {
        "sw_h2d0_nvme1": [local(0), nvme(0)],
        "sw_h2d0_nvme2": [local(0), nvme(0, 0), nvme(0, 1)],
        "sw_h2d0_nvme2gpu_g2": [local(0), nvme2gpu(2, 2)],
        "sw_h2d0_nvme2gpu_g0": [local(0), nvme2gpu(0, 0)],
        "sw_h2d0_remote_nvme_n2": [h2d(2, 0), nvme(2)],
        "sw_d2h0_nvme1": [d2h(0, 0), nvme(0)],
        "sw_p2p40_nvme1": [p2p(4, 0), nvme(0)],
        "sw_ctrl_h2d1_nvme2gpu_g2": [local(1), nvme2gpu(2, 2)],
        "sw_ctrl_h2d4_nvme1": [local(4), nvme(0)],
    }


def policy_experiments(count=16, seed=3):
    """Random mixes inside the placement-policy regime: every DRAM read comes from the target GPU's
    own NUMA node, NVMe is staged on that node too, P2P has no fan-in/fan-out (one peer per GPU)."""
    rng = random.Random(seed)
    out = {}
    while len(out) < count:
        flows, n_nvme, srcs = [], 0, set()
        targets = rng.sample(range(8), rng.randint(2, 8))
        for g in targets:
            if rng.random() < 0.8:
                flows.append(h2d(GPU_NUMA[g], g))
            if n_nvme < 2 and rng.random() < 0.25:
                flows.append(nvme2gpu(GPU_NUMA[g], g, n_nvme)); n_nvme += 1
            free = [s for s in range(8) if s not in targets and s not in srcs]
            if free and rng.random() < 0.5:
                s = rng.choice(free); srcs.add(s)
                flows.append(p2p(s, g))
        if len(flows) >= 3:
            out[f"pol_{len(out):02d}"] = flows
    return out


def snapshot():
    try:
        use = subprocess.run(["/opt/hyhal/bin/hy-smi", "--showuse", "--showmeminfo", "vram", "--json"],
                             capture_output=True, text=True, timeout=30).stdout
    except Exception as exc:
        use = repr(exc)
    return {"time": time.time(), "loadavg": open("/proc/loadavg").read().split()[:3], "hy_smi": use}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", required=True)
    ap.add_argument("--window", type=float, default=0.5)
    ap.add_argument("--repeats", type=int, default=5)
    ap.add_argument("--only", default="alpha_beta,matrices,train,validation")
    a = ap.parse_args()
    groups = a.only.split(",")
    res = {"host": socket.gethostname(), "gpu_numa": GPU_NUMA, "nbytes": NBYTES,
           "window_s": a.window, "repeats": a.repeats, "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
           "snapshots": [snapshot()], "alpha_beta": {}, "single": [], "concurrent": {}}
    # Every measurement uses workers that have never run anything else and only see the GPUs of
    # their own flow: a long-lived worker that had cycled through ~200 setups cut P2P out of GPU 0
    # from ~155 to ~20 GB/s (benchmarks/results/diag_*_node27.json), poisoning the first profile.
    if "alpha_beta" in groups:
        sizes = [4 * MiB, 16 * MiB, 64 * MiB, 256 * MiB, 1024 * MiB]
        for name, spec in [("h2d_numa0_gpu0", h2d(0, 0)), ("h2d_numa4_gpu0", h2d(4, 0)),
                           ("d2h_gpu0_numa0", d2h(0, 0)), ("p2p_0_1", p2p(0, 1)),
                           ("nvme_numa0", nvme(0)), ("nvme2gpu_numa0_gpu0", nvme2gpu(0, 0))]:
            pool = Pool.for_specs([spec])
            try:
                t = pool.sizes(spec, sizes[-1], sizes, 5)
            finally:
                pool.close()
            res["alpha_beta"][name] = {"spec": spec, "times": {str(k): v for k, v in t.items()}}
            print("alpha_beta", name, {k // MiB: round(min(v) * 1e3, 3) for k, v in t.items()}, flush=True)
    if "matrices" in groups:
        singles = [h2d(n, g) for n in NUMA_NODES for g in range(8)] + \
                  [d2h(g, n) for g in range(8) for n in NUMA_NODES] + \
                  [p2p(i, j) for i in range(8) for j in range(8) if i != j]
        for b in range(0, len(singles), 8):  # 8 fresh workers per batch, each used for one flow, alone
            batch = singles[b:b + 8]
            pool = Pool.for_specs(batch)
            try:
                for i, s in enumerate(batch):
                    runs = pool.concurrent([s], NBYTES, 0.4, 3, idxs=[i])
                    res["single"].append({"spec": s, "GBps": [r[0]["GBps"] for r in runs]})
            finally:
                pool.close()
        print("single flows:", len(res["single"]), flush=True)
        res["snapshots"].append(snapshot())
    exps = {}
    if "train" in groups:
        exps.update({k: ("train", v) for k, v in train_experiments().items()})
    if "train2" in groups:
        exps.update({k: ("train", v) for k, v in train2_experiments().items()})
    if "validation" in groups:
        exps.update({k: ("validation", v) for k, v in validation_experiments().items()})
    if "validation2" in groups:  # fresh held-out set, drawn after the model structure was revised
        exps.update({f"v2_{k}": ("validation2", v) for k, v in validation_experiments(seed=1).items()})
    if "validation3" in groups:
        exps.update({k: ("validation3", v) for k, v in materialization_experiments().items()})
    if "train3" in groups:
        exps.update({k: ("train", v) for k, v in train3_experiments().items()})
    if "validation4" in groups:
        exps.update({k: ("validation4", v) for k, v in policy_experiments().items()})
    for name, (split, flows) in exps.items():
        pool = Pool.for_specs(flows)
        try:
            runs = pool.concurrent(flows, NBYTES, a.window, a.repeats)
        finally:
            pool.close()
        res["concurrent"][name] = {"split": split, "flows": flows, "runs": runs}
        agg = [sum(f["GBps"] for f in r) for r in runs]
        print(f"{split:10s} {name:32s} n={len(flows)} aggregate GB/s {[round(x, 1) for x in agg]}", flush=True)
    res["snapshots"].append(snapshot())
    res["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    os.makedirs(os.path.dirname(os.path.abspath(a.out)), exist_ok=True)
    with open(a.out, "w") as f:
        json.dump(res, f, indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
