"""Cross-node max-flow planner for materializing TP shards from wherever copies live.

Each demand is one shard (tp) that must land on one destination keeper worker (the GPU that runs
TP rank tp). Each shard may have several copies: pinned DRAM on some worker of some node, or a
file on some node's SSD. The planner splits every shard's bytes across its copies so that the
slowest resource finishes last as late as possible -- min T such that every resource carries at
most cap * T bytes (a max-concurrent-flow LP) -- and emits byte-range ops for the keeper mesh.

Resources and their capacities (GB/s) come from measurements on this cluster:
  h2d:<node>:<gpu>      pinned DRAM -> that GPU (staging for IB, or the restore itself)   ~46
  hostrd:<node>         node DRAM read aggregate across GPUs (8 GPUs pulling at once)     measured
  ssd:<node>            local NVMe read                                                   2.5
  ibout:<node>, ibin:<node>   cross-node aggregate, 8 concurrent pair streams              205.8
  pair:<a>-><b>         one pair stream (a single broadcast on a 2-rank group)            48.5
  gpuin:<node>:<gpu>    the destination GPU's PCIe ingress, shared by H2D and IB RDMA     46
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
from scipy.optimize import linprog

GB = 1e9

DEFAULT_CAPS = {
    "h2d": 46.0,
    "hostrd": 180.0,
    "ssd": 2.5,
    "ib_node": 205.8,
    "pair": 48.5,
    "gpuin": 46.0,
}


@dataclass
class Copy:
    kind: str                  # "dram" | "ssd"
    node: str
    gpu: int = None            # dram: worker holding the pinned copy
    path: str = None           # ssd: file holding the packed shard


@dataclass
class Demand:
    tp: int
    node: str
    gpu: int                   # destination worker
    nbytes: int
    copies: list = field(default_factory=list)


def route(copy: Copy, d: Demand):
    """Resources used when `copy` feeds demand `d`, and the op kind."""
    if copy.kind == "ssd":
        if copy.node != d.node:
            raise ValueError("remote SSD must be staged into DRAM first")
        return "ssd", [f"ssd:{copy.node}", f"h2d:{d.node}:{d.gpu}", f"gpuin:{d.node}:{d.gpu}"]
    if copy.node == d.node:
        if copy.gpu != d.gpu:
            # pinned memory belongs to the keeper worker that allocated it; no cross-process H2D.
            return None, None
        return "h2d", [f"hostrd:{d.node}", f"h2d:{d.node}:{d.gpu}", f"gpuin:{d.node}:{d.gpu}"]
    return "ib", [f"hostrd:{copy.node}", f"h2d:{copy.node}:{copy.gpu}", f"ibout:{copy.node}",
                  f"ibin:{d.node}", f"pair:{copy.node}:{copy.gpu}->{d.node}:{d.gpu}",
                  f"gpuin:{d.node}:{d.gpu}"]


def cap_of(res, caps):
    kind = res.split(":")[0]
    return {"h2d": caps["h2d"], "hostrd": caps["hostrd"], "ssd": caps["ssd"],
            "ibout": caps["ib_node"], "ibin": caps["ib_node"], "pair": caps["pair"],
            "gpuin": caps["gpuin"]}[kind]


def solve(demands, caps=None):
    """Returns (T_seconds, [(demand_idx, copy_idx, bytes)], per-resource load)."""
    caps = dict(DEFAULT_CAPS, **(caps or {}))
    var = []                   # (demand_idx, copy_idx, kind, resources)
    for i, d in enumerate(demands):
        for j, c in enumerate(d.copies):
            kind, res = route(c, d)
            if kind is not None:
                var.append((i, j, kind, res))
    n = len(var) + 1           # last variable is T (seconds); x in GB
    cost = np.zeros(n); cost[-1] = 1.0
    A_eq, b_eq = [], []
    for i, d in enumerate(demands):
        row = np.zeros(n)
        for k, v in enumerate(var):
            if v[0] == i:
                row[k] = 1.0
        A_eq.append(row); b_eq.append(d.nbytes / GB)
    resources = sorted({r for v in var for r in v[3]})
    A_ub, b_ub = [], []
    for r in resources:
        row = np.zeros(n)
        for k, v in enumerate(var):
            if r in v[3]:
                row[k] = 1.0
        row[-1] = -cap_of(r, caps)
        A_ub.append(row); b_ub.append(0.0)
    sol = linprog(cost, A_ub=np.array(A_ub), b_ub=b_ub, A_eq=np.array(A_eq), b_eq=b_eq,
                  bounds=[(0, None)] * n, method="highs")
    if not sol.success:
        raise RuntimeError(sol.message)
    T = sol.x[-1]
    split = [(v[0], v[1], sol.x[k] * GB) for k, v in enumerate(var) if sol.x[k] * GB > 1]
    load = {r: sum(sol.x[k] for k, v in enumerate(var) if r in v[3]) / T for r in resources}
    return T, split, load


def to_ops(demands, split, wid, align=1 << 20):
    """Turn a byte split into contiguous byte-range ops. `wid(node, gpu)` -> keeper rank.

    Within a shard, ranges are given to copies in order; within the op list, IB ops are
    interleaved round-robin across destinations so every pair group starts streaming at once.
    """
    per_demand = {}
    for i, j, b in split:
        per_demand.setdefault(i, []).append((j, b))
    ops_by_dst = {}
    for i, parts in per_demand.items():
        d = demands[i]
        off = 0
        for idx, (j, b) in enumerate(parts):
            n = d.nbytes - off if idx == len(parts) - 1 else min(int(b // align * align), d.nbytes - off)
            if n <= 0:
                continue
            c = d.copies[j]
            kind, _ = route(c, d)
            op = {"kind": kind, "tp": d.tp, "off": off, "n": n, "dst_w": wid(d.node, d.gpu)}
            if kind == "ib":
                op["src_w"] = wid(c.node, c.gpu)
            elif kind == "h2d":
                op["src_w"] = wid(c.node, c.gpu)
            elif kind == "ssd":
                op["path"] = c.path
            ops_by_dst.setdefault(i, []).append(op)
            off += n
    # round-robin interleave across destinations (keeps all pair groups busy from t=0)
    ops, queues = [], [q for _, q in sorted(ops_by_dst.items())]
    while any(queues):
        for q in queues:
            if q:
                ops.append(q.pop(0))
    return ops
