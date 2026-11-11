"""Keep-alive placement: where a parked model's chunks should live so the next restore is fast.

park() solves one LP over the whole live graph. For each TP shard it decides how many bytes go to
each candidate vertex (s_v); for each restore scenario k (a probable set of destination GPUs) it
routes those bytes to the destinations over cached paths with per-resource loads <= residual*T_k.
Objective: sum_k p_k T_k + eps * sum_v tier_weight(v) s_v, so placement prefers DRAM over HBM and
local over remote only when restore time is equal. The durable copy (original files on some SSD)
is always available as an unlimited-capacity source; bytes left there are simply fetched slowly.
The continuous byte split is rounded to whole chunks by largest remaining quota.

evict() frees space on a vertex that another tenant needs: lowest-priority, least-recently-used
models lose their replica there first; the durable copy guarantees nothing is ever lost.
"""
import time
from collections import defaultdict

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

GB = 1e9
TIER_WEIGHT = {"hbm": 2.0, "dram": 1.0, "ssd": 0.2}


def park(snap, paths_fn, shards, durable, scenarios, budget_bytes, free_bytes, candidates,
         eps=1e-3, time_limit_s=2.0):
    """shards: {shard: [(chunk_id, nbytes)]}; durable: vertex holding the original files;
    scenarios: [(prob, {shard: dst_vertex})]; free_bytes: {vertex: bytes we may use now};
    candidates: vertices allowed to hold replicas. Returns ({chunk_id: [vertex]}, info)."""
    t0 = time.perf_counter()
    V = snap.topology.vertices
    total = {sh: sum(n for _, n in cs) for sh, cs in shards.items()}
    cand = [v for v in candidates if v != durable and free_bytes.get(v, 0) > 0]
    store = [(v, sh) for sh in shards for v in cand + [durable]]
    sidx = {k: i for i, k in enumerate(store)}
    var_flow, res_ids, path_ids = [], {}, {}
    for k, (_, dsts) in enumerate(scenarios):
        for v, sh in store:
            dst = dsts[sh]
            if v == dst:
                continue                                   # already resident in the target HBM
            for p in paths_fn(v, dst):
                var_flow.append((k, v, sh, p))
                path_ids.setdefault((k, p), len(path_ids))
                for r in p.resources:
                    res_ids.setdefault((k, r), len(res_ids))
    ns, nf, nk = len(store), len(var_flow), len(scenarios)
    n = ns + nf + nk                                       # s (GB) | x (GB) | T_k
    cost = np.zeros(n)
    for i, (v, _) in enumerate(store):
        cost[i] = eps * (0.0 if v == durable else TIER_WEIGHT[V[v].kind])
    for k, (prob, _) in enumerate(scenarios):
        cost[ns + nf + k] = prob

    # resource and path rows per scenario: sum x - cap * T_k <= 0
    ub_r, ub_c, ub_v = [], [], []
    res_row = {key: i for i, key in enumerate(res_ids)}
    path_row = {key: len(res_ids) + i for i, key in enumerate(path_ids)}
    for j, (k, v, sh, p) in enumerate(var_flow):
        for r in p.resources:
            ub_r.append(res_row[(k, r)]); ub_c.append(ns + j); ub_v.append(1.0)
        ub_r.append(path_row[(k, p)]); ub_c.append(ns + j); ub_v.append(1.0)
    for (k, r), i in res_row.items():
        ub_r.append(i); ub_c.append(ns + nf + k); ub_v.append(-snap.residual_GBps(r))
    for (k, p), i in path_row.items():
        ub_r.append(i); ub_c.append(ns + nf + k); ub_v.append(-p.single_stream_GBps)
    row = len(res_row) + len(path_row)
    b_ub = [0.0] * row
    # capacity per vertex and the keep-alive budget
    for v in cand:
        for sh in shards:
            ub_r.append(row); ub_c.append(sidx[(v, sh)]); ub_v.append(1.0)
        b_ub.append(free_bytes[v] / GB); row += 1
    for v in cand:
        for sh in shards:
            ub_r.append(row); ub_c.append(sidx[(v, sh)]); ub_v.append(1.0)
    b_ub.append(budget_bytes / GB); row += 1
    A_ub = coo_matrix((ub_v, (ub_r, ub_c)), shape=(row, n)).tocsr()

    eq_r, eq_c, eq_v, b_eq = [], [], [], []
    erow = 0
    for sh in shards:                                      # every shard fully stored somewhere
        for v in cand + [durable]:
            eq_r.append(erow); eq_c.append(sidx[(v, sh)]); eq_v.append(1.0)
        b_eq.append(total[sh] / GB); erow += 1
    flows_of = defaultdict(list)
    for j, (k, v, sh, p) in enumerate(var_flow):
        flows_of[(k, v, sh)].append(j)
    for k, (_, dsts) in enumerate(scenarios):              # stored bytes all flow to the target
        for v, sh in store:
            if v == dsts[sh]:
                continue
            for j in flows_of[(k, v, sh)]:
                eq_r.append(erow); eq_c.append(ns + j); eq_v.append(1.0)
            eq_r.append(erow); eq_c.append(sidx[(v, sh)]); eq_v.append(-1.0)
            b_eq.append(0.0); erow += 1
    A_eq = coo_matrix((eq_v, (eq_r, eq_c)), shape=(erow, n)).tocsr()
    sol = linprog(cost, A_ub=A_ub, b_ub=b_ub, A_eq=A_eq, b_eq=b_eq, bounds=(0, None),
                  method="highs", options={"time_limit": time_limit_s})
    if not sol.success:
        return {cid: [durable] for cs in shards.values() for cid, _ in cs}, {
            "status": sol.message, "planning_s": time.perf_counter() - t0}

    replicas, placed = {}, defaultdict(int)
    for sh, cs in shards.items():
        quota = {v: sol.x[sidx[(v, sh)]] * GB for v in cand + [durable]}
        for cid, nbytes in sorted(cs, key=lambda c: -c[1]):
            v = max(quota, key=quota.get)
            quota[v] -= nbytes
            replicas[cid] = [durable] if v == durable else [v, durable]
            if v != durable:
                placed[v] += nbytes
    T = [float(sol.x[ns + nf + k]) for k in range(nk)]
    return replicas, {"status": "optimal", "expected_restore_s": float(sum(p * t for (p, _), t in zip(scenarios, T))),
                      "scenario_restore_s": T, "placed_bytes": dict(placed),
                      "variables": n, "planning_s": time.perf_counter() - t0}


class KeepAlivePool:
    """Replica bookkeeping for several parked models sharing the graph."""

    def __init__(self):
        self.models = {}            # name -> {"replicas", "sizes", "priority", "last_used", "durable"}

    def add(self, name, replicas, sizes, durable, priority=0, now=0.0):
        self.models[name] = {"replicas": replicas, "sizes": sizes, "durable": durable,
                             "priority": priority, "last_used": now}

    def drop(self, name):
        self.models.pop(name, None)

    def held(self):
        """{vertex: bytes} held by all parked models (durable copies excluded)."""
        out = defaultdict(int)
        for m in self.models.values():
            for cid, vs in m["replicas"].items():
                for v in vs:
                    if v != m["durable"]:
                        out[v] += m["sizes"][cid]
        return out

    def evict(self, vertex, need_bytes):
        """Drop replicas on `vertex` until `need_bytes` are freed. Returns freed bytes and the
        (model, chunk) pairs that lost a replica. Durable copies are never touched."""
        freed, lost = 0, []
        order = sorted(self.models.items(), key=lambda kv: (kv[1]["priority"], kv[1]["last_used"]))
        for name, m in order:
            for cid, vs in m["replicas"].items():
                if freed >= need_bytes:
                    return freed, lost
                if vertex in vs and vertex != m["durable"]:
                    vs.remove(vertex)
                    freed += m["sizes"][cid]
                    lost.append((name, cid))
        return freed, lost
