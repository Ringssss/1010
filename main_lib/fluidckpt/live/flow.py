"""Fetch planning: move chunks from wherever replicas live into destination HBM, as fast as the
live residual capacities allow.

Strategies (same inputs, same output type, so the simulator can swap them):
  "lp"       max-concurrent-flow over chunk *classes*: chunks with the same replica set and the
             same destination are interchangeable, so the LP has one variable per (class, path)
             regardless of chunk count. min T s.t. each class is fully delivered, every resource
             carries <= residual * T and every path <= its single-stream rate * T. The fractional
             split is then rounded to whole chunks per path (largest remaining quota first).
  "greedy"   load-aware list scheduling: biggest chunk first, each onto the path whose bottleneck
             finish time grows least. No solver; the fallback when the LP is slow or fails.
  "fastest"  load-oblivious: each chunk independently takes its single best path under the
             snapshot's residuals (the per-chunk-fastest baseline of the 10-25 decision study).

`predicted_s` of every plan is the bottleneck estimate of the integral assignment:
max over resources of load/residual and over paths of load/stream rate.
"""
import math
import time
from collections import defaultdict

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from .types import FetchPlan, Transfer

GB = 1e9


def _candidates(demands, replicas, paths_fn):
    """Per demand index: candidate paths; demands already satisfied in place have none."""
    out = []
    for cid, nbytes, dst in demands:
        cands = []
        for src in replicas.get(cid, ()):
            if src == dst:
                cands = None
                break
            cands += paths_fn(src, dst)
        if cands == []:
            raise ValueError(f"chunk {cid[:12]} has no route to {dst}")
        out.append(cands)
    return out


def _bottleneck(load_res, load_path, snap, path_cap):
    t = 0.0
    for r, b in load_res.items():
        t = max(t, b / (snap.residual_GBps(r) * GB))
    for p, b in load_path.items():
        t = max(t, b / (path_cap[p] * GB))
    return t


def _path_cap(path, snap):
    return min([path.single_stream_GBps] + [snap.residual_GBps(r) for r in path.resources])


def _finish(plan_rows, demands, snap, version, t0, lower=None, notes=None):
    load_res, load_path, cap = defaultdict(float), defaultdict(float), {}
    transfers, order = [], defaultdict(int)
    for i, path in plan_rows:
        cid, nbytes, _ = demands[i]
        cap[path] = path.single_stream_GBps
        load_path[path] += nbytes
        for r in path.resources:
            load_res[r] += nbytes
        transfers.append(Transfer(cid, path, nbytes, order[path]))
        order[path] += 1
    pred = _bottleneck(load_res, load_path, snap, cap)
    return FetchPlan(transfers, pred, pred if lower is None else lower, version,
                     time.perf_counter() - t0, notes or {})


def plan_fetch(snap, demands, replicas, paths_fn, strategy="lp", time_limit_s=0.5):
    """demands: [(chunk_id, nbytes, dst_vertex)]; replicas: {chunk_id: [vertex]}."""
    t0 = time.perf_counter()
    cands = _candidates(demands, replicas, paths_fn)
    todo = [i for i, c in enumerate(cands) if c is not None]
    if not todo:
        return FetchPlan([], 0.0, 0.0, snap.version, time.perf_counter() - t0, {"in_place": len(demands)})
    if strategy == "fastest":
        rows = [(i, max(cands[i], key=lambda p: _path_cap(p, snap))) for i in todo]
        return _finish(rows, demands, snap, snap.version, t0, notes={"strategy": "fastest"})
    if strategy == "greedy":
        return _greedy(snap, demands, cands, todo, t0)
    if strategy != "lp":
        raise ValueError(strategy)

    # ---- classes ----------------------------------------------------------------------------
    classes = defaultdict(list)                       # (replica set, dst) -> demand indices
    for i in todo:
        cid, _, dst = demands[i]
        classes[(tuple(sorted(replicas[cid])), dst)].append(i)
    keys = list(classes)
    var, path_ids, res_ids = [], {}, {}
    for c, key in enumerate(keys):
        seen = set()
        for p in cands[classes[key][0]]:
            if p not in seen:
                seen.add(p)
                path_ids.setdefault(p, len(path_ids))
                for r in p.resources:
                    res_ids.setdefault(r, len(res_ids))
                var.append((c, p))
    n = len(var) + 1                                   # x in GB per (class, path); last = T
    rows, cols, vals = [], [], []
    for k, (c, p) in enumerate(var):
        for r in p.resources:
            rows.append(res_ids[r]); cols.append(k); vals.append(1.0)
        rows.append(len(res_ids) + path_ids[p]); cols.append(k); vals.append(1.0)
    nr = len(res_ids) + len(path_ids)
    caps = np.zeros(nr)
    for r, j in res_ids.items():
        caps[j] = snap.residual_GBps(r)
    for p, j in path_ids.items():
        caps[len(res_ids) + j] = p.single_stream_GBps
    rows += list(range(nr)); cols += [n - 1] * nr; vals += list(-caps)
    A_ub = coo_matrix((vals, (rows, cols)), shape=(nr, n)).tocsr()
    eq_r, eq_c = [], []
    for k, (c, _) in enumerate(var):
        eq_r.append(c); eq_c.append(k)
    A_eq = coo_matrix((np.ones(len(eq_r)), (eq_r, eq_c)), shape=(len(keys), n)).tocsr()
    b_eq = np.array([sum(demands[i][1] for i in classes[key]) / GB for key in keys])
    cost = np.zeros(n); cost[-1] = 1.0
    sol = linprog(cost, A_ub=A_ub, b_ub=np.zeros(nr), A_eq=A_eq, b_eq=b_eq, bounds=(0, None),
                  method="highs", options={"time_limit": time_limit_s})
    if not sol.success:
        plan = _greedy(snap, demands, cands, todo, t0)
        plan.notes.update(fallback=sol.message)
        return plan

    # ---- round to whole chunks: biggest chunk to the path with the largest remaining quota ----
    quota = defaultdict(dict)
    for k, (c, p) in enumerate(var):
        if sol.x[k] > 1e-9:
            quota[c][p] = quota[c].get(p, 0.0) + sol.x[k] * GB
    rows_out = []
    for c, key in enumerate(keys):
        q = quota[c]
        for i in sorted(classes[key], key=lambda i: -demands[i][1]):
            p = max(q, key=q.get)
            q[p] -= demands[i][1]
            rows_out.append((i, p))
    return _finish(rows_out, demands, snap, snap.version, t0, lower=float(sol.x[-1]),
                   notes={"strategy": "lp", "classes": len(keys), "variables": len(var)})


def _greedy(snap, demands, cands, todo, t0):
    load_res, load_path = defaultdict(float), defaultdict(float)
    res_cap = {}
    rows = []
    for i in sorted(todo, key=lambda i: -demands[i][1]):
        b = demands[i][1]
        best, best_t = None, math.inf
        for p in cands[i]:
            t = (load_path[p] + b) / (p.single_stream_GBps * GB)
            for r in p.resources:
                if r not in res_cap:
                    res_cap[r] = snap.residual_GBps(r) * GB
                t = max(t, (load_res[r] + b) / res_cap[r])
            if t < best_t:
                best, best_t = p, t
        rows.append((i, best))
        load_path[best] += b
        for r in best.resources:
            load_res[r] += b
    return _finish(rows, demands, snap, snap.version, t0, notes={"strategy": "greedy"})

