"""Materialization planners (pure functions): demand + replicas + resource model -> stream plan.

A *class* is a set of bytes destined for one target GPU that share the same replica set.
A *path* is one executor stream: (source replica, target) realised as a transfer spec.
Plans are {path_index: bytes}; `streams()` turns them into simulator/executor input.
"""
from dataclasses import dataclass, field

import numpy as np

from fluidckpt.model.fluid import simulate
from scipy.optimize import linprog

GB = 1e9
INF = float("inf")


@dataclass
class DemandClass:
    target: int                 # target GPU
    nbytes: int
    sources: list               # [{"tier": "hbm", "gpu": i} | {"tier": "dram", "numa": n} | {"tier": "nvme"}]


@dataclass
class Plan:
    name: str
    paths: list                 # transfer specs, one executor stream each
    bytes_per_path: list        # total bytes carried by each stream
    assign: dict = field(default_factory=dict)  # (class_idx, path_idx) -> bytes
    lp_bound_s: float = None
    meta: dict = field(default_factory=dict)
    rates: list = None          # per-stream pacing (GB/s); None = unpaced, share by hardware policy

    def paced(self, T=None):
        """Same byte split, each stream capped at bytes / T so the LP rate vector is the schedule."""
        T = T or self.meta.get("T_stage2_s") or self.lp_bound_s
        return Plan(self.name + "_paced", self.paths, self.bytes_per_path, self.assign, self.lp_bound_s,
                    dict(self.meta, pace_T_s=T), [b / GB / T for b in self.bytes_per_path])


def path_spec(source, target, gpu_numa):
    t = source["tier"]
    if t == "hbm":
        return None if source["gpu"] == target else {"kind": "p2p", "src": source["gpu"], "dst": target}
    if t == "dram":
        return {"kind": "h2d", "numa": source["numa"], "gpu": target}
    if t == "nvme":  # staged through a pinned buffer on the target's own NUMA node
        return {"kind": "nvme2gpu", "numa": gpu_numa[target], "gpu": target, "file_idx": target}
    raise ValueError(t)


def _spec_key(s):
    return tuple(sorted(s.items()))


NVME_NUMA = 0  # node27: the NVMe hangs off NUMA 0 and shares GPU 0's root port


def linear_regime(spec, gpu_numa):
    """Paths the fluid model predicts within a few percent when combined (scripts/purity_split.py):
    every root port touches one NUMA node and no NUMA node feeds two root ports. Since GPU->NUMA is
    one-to-one on node27, that means: P2P, DRAM from the target GPU's own node, and NVMe only into the
    GPU that shares its root port and node (staged on NUMA 0). Mixed-node ports collapse (7-30 GB/s)."""
    if spec["kind"] == "p2p":
        return True
    if spec["kind"] == "h2d":
        return spec["numa"] == gpu_numa[spec["gpu"]]
    if spec["kind"] == "nvme2gpu":
        return spec["numa"] == NVME_NUMA and gpu_numa[spec["gpu"]] == NVME_NUMA
    return False


class Problem:
    def __init__(self, classes, model, admit=None, drop=()):
        """admit(spec) -> bool restricts each class to admissible paths; a class left without a
        non-NVMe path keeps its single fastest inadmissible one, pinned (no splitting across it).
        drop: path keys removed (used to prune streams too small to amortise their startup)."""
        self.classes, self.model = classes, model
        self.paths, index = [], {}
        self.cands = []  # per class: list of path indices
        for c in classes:
            specs = []
            for src in c.sources:
                s = path_spec(src, c.target, model.gpu_numa)
                if s is None:
                    specs = None  # already resident on the target GPU: IPC map, no transfer
                    break
                if _spec_key(s) not in drop:
                    specs.append(s)
            if specs and admit:
                ok = [s for s in specs if admit(s)]
                if not any(s["kind"] != "nvme2gpu" for s in ok):
                    fast = [s for s in specs if s["kind"] != "nvme2gpu" and not admit(s)]
                    if fast:
                        ok = [max(fast, key=model.caps.cap)]
                specs = ok or specs
            if specs is None:
                self.cands.append(None)
                continue
            idx = []
            for s in specs:
                k = _spec_key(s)
                if k not in index:
                    index[k] = len(self.paths)
                    self.paths.append(s)
                idx.append(index[k])
            self.cands.append(idx)
        self.admit, self.drop = admit, set(drop)
        flows = model.flows(self.paths, joint=False)  # candidates, not a concurrent set
        self.cap = [f[0] for f in flows]
        self.res = [f[1] for f in flows]
        self.rcap = {r: model.cap_of(r) for rs in self.res for r in rs}
        self.rcap = {r: c for r, c in self.rcap.items() if c < INF}

    def plan_from_assign(self, name, assign, **meta):
        per = [0] * len(self.paths)
        for (_, p), b in assign.items():
            per[p] += b
        used = [p for p in range(len(self.paths)) if per[p] > 0]
        remap = {p: i for i, p in enumerate(used)}
        return Plan(name, [self.paths[p] for p in used], [per[p] for p in used],
                    {(c, remap[p]): b for (c, p), b in assign.items() if b > 0}, meta=meta)


def streams(plan, model):
    """Simulator input for executing the plan: (cap, resources, bytes) per stream + capacities."""
    flows = model.flows(plan.paths)
    rates = plan.rates or [None] * len(flows)
    return ([(min(cap, r) if r else cap, res, b) for (cap, res), b, r in zip(flows, plan.bytes_per_path, rates)],
            model.full_capacity(flows))


def predict_makespan(plan, model):
    st, cap = streams(plan, model)
    return max(simulate(st, cap)) if st else 0.0


def _round(assign, classes, quantum):
    """Round byte splits to the quantum while keeping each class total exact."""
    out, by_class = {}, {}
    for (c, p), b in assign.items():
        by_class.setdefault(c, []).append((p, b))
    for c, parts in by_class.items():
        total = classes[c].nbytes
        parts.sort(key=lambda t: -t[1])
        acc = 0
        for p, b in parts[1:]:
            r = int(round(b / quantum)) * quantum
            r = min(r, total - acc)
            if r > 0:
                out[(c, p)] = r
                acc += r
        out[(c, parts[0][0])] = total - acc
    return out


def plan_single_source(prob, name, choose):
    assign = {}
    for c, (cls, cand) in enumerate(zip(prob.classes, prob.cands)):
        if cand:
            assign[(c, choose(prob, cand))] = cls.nbytes
    return prob.plan_from_assign(name, assign)


def plan_nvme_only(prob):
    def choose(p, cand):
        for i in cand:
            if p.paths[i]["kind"] == "nvme2gpu":
                return i
        raise ValueError("class has no NVMe replica")
    return plan_single_source(prob, "nvme_only", choose)


def plan_fastest_source(prob):
    """Per class, the source with the highest *uncontended* bandwidth (nearest/fastest greedy)."""
    return plan_single_source(prob, "fastest_source", lambda p, cand: max(cand, key=lambda i: p.cap[i]))


def plan_resource_greedy(prob, chunk=256 << 20):
    """List scheduling: each chunk goes to the path that minimises the estimated makespan so far."""
    cap_r = prob.rcap
    load_p = [0.0] * len(prob.paths)
    load_r = {}
    assign = {}
    items = []
    for c, (cls, cand) in enumerate(zip(prob.classes, prob.cands)):
        if cand:
            items += [(c, min(chunk, cls.nbytes - o)) for o in range(0, cls.nbytes, chunk)]
    items.sort(key=lambda t: -t[1])
    for c, b in items:
        def est(i):
            t = (load_p[i] + b) / GB / prob.cap[i]
            for r in prob.res[i]:
                if r in cap_r:
                    t = max(t, (load_r.get(r, 0) + b) / GB / cap_r[r])
            return t
        i = min(prob.cands[c], key=est)
        load_p[i] += b
        for r in prob.res[i]:
            load_r[r] = load_r.get(r, 0) + b
        assign[(c, i)] = assign.get((c, i), 0) + b
    return prob.plan_from_assign("resource_greedy", assign)


def plan_lp(prob, quantum=16 << 20, cost=None, slack=0.02):
    """Quickest fluid materialization LP; optional second stage minimises sum_p cost(path_p) * bytes_p
    subject to T <= (1 + slack) * T*.  Variables: x[c,p] in GB, then T.  `cost`: spec -> cost per GB."""
    var = [(c, p) for c, cand in enumerate(prob.cands) if cand for p in cand]
    nv = len(var) + 1
    iT = nv - 1
    A_eq, b_eq, A_ub, b_ub = [], [], [], []
    for c, cand in enumerate(prob.cands):
        if cand:
            row = np.zeros(nv)
            for k, (cc, p) in enumerate(var):
                if cc == c:
                    row[k] = 1
            A_eq.append(row)
            b_eq.append(prob.classes[c].nbytes / GB)
    for p in range(len(prob.paths)):  # single-stream cap of each path
        row = np.zeros(nv)
        for k, (_, pp) in enumerate(var):
            if pp == p:
                row[k] = 1
        row[iT] = -prob.cap[p]
        A_ub.append(row); b_ub.append(0)
    rnames = sorted(prob.rcap)
    for r in rnames:  # shared resource capacities
        row = np.zeros(nv)
        for k, (_, p) in enumerate(var):
            if r in prob.res[p]:
                row[k] = 1
        row[iT] = -prob.rcap[r]
        A_ub.append(row); b_ub.append(0)
    obj = np.zeros(nv); obj[iT] = 1
    s1 = linprog(obj, A_ub=np.array(A_ub), b_ub=b_ub, A_eq=np.array(A_eq), b_eq=b_eq, method="highs")
    if s1.status != 0:
        raise RuntimeError(s1.message)
    T = s1.x[iT]
    x, name, meta = s1.x, "lp_quickest", {"T_star_s": T}
    if cost:
        c2 = np.zeros(nv)
        for k, (_, p) in enumerate(var):
            c2[k] = cost(prob.paths[p])
        bound = np.zeros(nv); bound[iT] = 1
        s2 = linprog(c2, A_ub=np.vstack([A_ub, bound]), b_ub=list(b_ub) + [T * (1 + slack)],
                     A_eq=np.array(A_eq), b_eq=b_eq, method="highs")
        if s2.status != 0:
            raise RuntimeError(s2.message)
        x, name = s2.x, "lp_quickest_mincost"
        meta.update(slack=slack, T_stage2_s=s2.x[iT])
    assign = {v: x[k] * GB for k, v in enumerate(var) if x[k] * GB > quantum / 2}
    plan = prob.plan_from_assign(name, _round(assign, prob.classes, quantum), **meta)
    plan.lp_bound_s = T
    return plan


def plan_lp_linear(classes, model, min_stream=1 << 30, **kw):
    """Quickest-flow LP restricted to the linear regime, then pruned: a stream carrying less than
    min_stream bytes costs more in startup (~0.1 s for NVMe) than it saves, so drop it and re-solve."""
    admit = lambda s: linear_regime(s, model.gpu_numa)
    drop = set()
    while True:
        prob = Problem(classes, model, admit, drop)
        plan = plan_lp(prob, **kw)
        small = {_spec_key(p) for p, b in zip(plan.paths, plan.bytes_per_path) if b < min_stream}
        # never drop a class's last path
        small = {k for k in small if all(c is None or any(_spec_key(prob.paths[i]) not in small for i in c)
                                         for c in prob.cands)}
        if not small:
            plan.name = plan.name.replace("lp_quickest", "lp_linear")
            return plan
        drop |= small
