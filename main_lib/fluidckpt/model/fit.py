"""Fit the shared-resource model on training experiments, score it on held-out random mixes.

  python -m fluidckpt.model.fit benchmarks/results/profile_node27.json --out benchmarks/results/model_fit_node27.json
"""
import argparse
import json
import math
import statistics

from fluidckpt.model.fluid import INF, maxmin_rates
from fluidckpt.model.resources import CONFLICT, VARIANTS, SingleCaps, conflicted, resources


def fit_alpha_beta(alpha_beta):
    """Least squares T = alpha + n / beta on per-size medians."""
    out = {}
    for name, row in alpha_beta.items():
        xs = [int(s) for s in row["times"]]
        ys = [statistics.median(v) for v in row["times"].values()]
        n = len(xs)
        mx, my = sum(xs) / n, sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs)
        slope = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx
        alpha = my - slope * mx
        ss_res = sum((y - alpha - slope * x) ** 2 for x, y in zip(xs, ys))
        ss_tot = sum((y - my) ** 2 for y in ys)
        # relative error of the fit at each size (small sizes matter for chunk sizing)
        rel = {str(x >> 20) + "MiB": (alpha + slope * x - y) / y for x, y in zip(xs, ys)}
        out[name] = {"alpha_ms": alpha * 1e3, "beta_GBps": 1 / slope / 1e9, "r2": 1 - ss_res / ss_tot,
                     "rel_err_by_size": rel}
    return out


def observed(exp):
    """Per-flow median GB/s over repeats."""
    n = len(exp["flows"])
    return [statistics.median(run[i]["GBps"] for run in exp["runs"]) for i in range(n)]


def repeat_noise(exp):
    aggs = [sum(f["GBps"] for f in run) for run in exp["runs"]]
    m = statistics.mean(aggs)
    return (max(aggs) - min(aggs)) / m if m else 0.0


class Model:
    def __init__(self, caps, variant, gpu_numa, capacity=None, tied=True, family_default=None):
        self.caps, self.fam, self.gpu_numa = caps, VARIANTS[variant], gpu_numa
        self.capacity = capacity or {}
        self.tied, self.family_default = tied, dict(family_default or {})

    def flows(self, specs, joint=True):
        """joint=True: `specs` run together, so interaction rules apply (conflict caps)."""
        out = [(self.caps.cap(s), resources(s, self.fam, self.gpu_numa)) for s in specs]
        if joint and "conflict" in self.fam:
            for i in conflicted(specs, self.gpu_numa):
                out[i] = (min(out[i][0], self.capacity.get(CONFLICT, INF)), out[i][1] + [CONFLICT + f":{i}"])
        return out

    def full_capacity(self, flows):
        """Capacity dict covering every resource the given flows use (family defaults filled in)."""
        return {r: c for _, res in flows for r in res if not r.startswith(CONFLICT + ":")
                for c in [self.cap_of(r)] if c < INF}

    def set_family(self, g, members, v):
        for r in members:
            self.capacity[r] = v
        if self.tied and g != CONFLICT:
            self.family_default[g] = v

    def cap_of(self, r):
        """Capacity of resource r; with tying, resources unseen in training inherit the family value."""
        if r in self.capacity:
            return self.capacity[r]
        return self.family_default.get(r.split(":")[0], INF) if self.tied else INF

    def predict(self, specs):
        fl = self.flows(specs)
        return maxmin_rates(fl, self.full_capacity(fl))


def loss(model, exps, per_flow=False):
    """Squared log error of the aggregate rate: this identifies the capacity region, which is all
    the planner LP needs. Per-flow splits inside a saturated resource are a sharing-policy question
    (often unfair on this hardware) and would otherwise force bogus capacities."""
    err = []
    for exp in exps:
        pred, obs = model.predict(exp["flows"]), observed(exp)
        if per_flow:
            err += [math.log(max(p, 1e-6) / o) ** 2 for p, o in zip(pred, obs)]
        err.append(math.log(max(sum(pred), 1e-6) / sum(obs)) ** 2)
    return sum(err) / len(err)


def family(r):
    return r.split(":")[0]


def fit_capacities(model, train, tied=True):
    """Coordinate descent; each capacity is either unbounded or an observed saturation value.
    tied=True: one capacity per resource family (all GPUs / all NUMA nodes are the same hardware);
    untied per-resource capacities overfit aggregates by crippling single links."""
    cand = {}
    for exp in train:
        obs = observed(exp)
        through = {}
        for (cap, res), o in zip(model.flows(exp["flows"]), obs):
            for r in res:
                through[r] = through.get(r, 0.0) + o
        for r, v in through.items():
            if r.startswith(CONFLICT):  # one shared per-flow cap, candidates are observed flow rates
                cand.setdefault(CONFLICT, set()).update(round(o, 3) for o in obs)
            else:
                cand.setdefault(r, set()).add(round(v, 3))
    groups = {}
    for r, vs in cand.items():
        g = family(r) if tied and r != CONFLICT else r
        groups.setdefault(g, [set(), []])
        groups[g][0].update(vs)
        groups[g][1].append(r)
    members = {g: m for g, (_, m) in groups.items()}
    if tied:  # members that never saturated in training still share the family value
        allres = {r for e in train for _, res in model.flows(e["flows"]) for r in res}
        for r in allres:
            if not r.startswith(CONFLICT) and family(r) in members and r not in members[family(r)]:
                members[family(r)].append(r)
    model.capacity = {}
    model.family_capacity = {}
    best = loss(model, train)
    for _ in range(4):
        improved = False
        for g in sorted(groups):
            cur = model.family_capacity.get(g, INF)
            for v in sorted(groups[g][0]) + [INF]:
                model.family_capacity[g] = v
                model.set_family(g, members[g], v)
                l = loss(model, train)
                if l < best - 1e-12:
                    best, cur, improved = l, v, True
            model.family_capacity[g] = cur
            model.set_family(g, members[g], cur)
        if not improved:
            break
    model.capacity = {r: v for r, v in model.capacity.items() if v < INF}
    model.family_capacity = {g: v for g, v in model.family_capacity.items() if v < INF}
    model.family_default = {g: v for g, v in model.family_default.items() if v < INF}
    return best


def score(model, exps):
    agg, flow = [], []
    for exp in exps:
        pred, obs = model.predict(exp["flows"]), observed(exp)
        agg.append(abs(sum(pred) - sum(obs)) / sum(obs))
        flow += [abs(p - o) / o for p, o in zip(pred, obs)]

    def q(xs, p):
        xs = sorted(xs)
        return xs[min(len(xs) - 1, int(p * len(xs)))]
    return {"n_exp": len(exps), "n_flows": len(flow),
            "aggregate_rel_err": {"median": q(agg, 0.5), "p90": q(agg, 0.9), "max": max(agg)},
            "flow_rel_err": {"median": q(flow, 0.5), "p90": q(flow, 0.9), "max": max(flow)}}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile", help="main profile (singles, alpha/beta, experiments)")
    ap.add_argument("extra", nargs="*", help="more profiles whose concurrent experiments are merged")
    ap.add_argument("--out", required=True)
    ap.add_argument("--variants", default="V0_additive,V2_links_numa,V7_links_numa_rw_conflict,V8_links_numa_rw_socket_conflict,V9_links_hswio_numa_rw_conflict")
    a = ap.parse_args()
    prof = json.load(open(a.profile))
    for path in a.extra:
        prof["concurrent"].update(json.load(open(path))["concurrent"])
    gpu_numa = {int(k): v for k, v in prof["gpu_numa"].items()}
    caps = SingleCaps(prof)
    train = [dict(name=k, **v) for k, v in prof["concurrent"].items() if v["split"] == "train"]
    val = [dict(name=k, **v) for k, v in prof["concurrent"].items() if v["split"] == "validation"]
    val2 = [dict(name=k, **v) for k, v in prof["concurrent"].items() if v["split"] == "validation2"]
    val3 = [dict(name=k, **v) for k, v in prof["concurrent"].items() if v["split"] == "validation3"]
    out = {"profiles": [a.profile] + a.extra, "alpha_beta": fit_alpha_beta(prof["alpha_beta"]),
           "repeat_noise": {"train_median": statistics.median(map(repeat_noise, train)),
                            "validation_median": statistics.median(map(repeat_noise, val))},
           "variants": {}}
    print("alpha/beta fit:")
    for k, v in out["alpha_beta"].items():
        print(f"  {k:22s} alpha={v['alpha_ms']:7.3f} ms  beta={v['beta_GBps']:6.1f} GB/s  R2={v['r2']:.5f}")
    print("repeat-to-repeat aggregate spread (noise floor):", {k: round(v, 3) for k, v in out["repeat_noise"].items()})
    def line(tag, sc):
        return (f"{tag} agg med {sc['aggregate_rel_err']['median']:5.1%} p90 {sc['aggregate_rel_err']['p90']:5.1%} "
                f"max {sc['aggregate_rel_err']['max']:5.1%} | flow med {sc['flow_rel_err']['median']:5.1%} "
                f"p90 {sc['flow_rel_err']['p90']:6.1%}")
    for variant in a.variants.split(","):
      for tied in (False, True):
        name = variant + ("_tied" if tied else "")
        m = Model(caps, variant, gpu_numa, tied=tied)
        train_loss = fit_capacities(m, train, tied=tied)
        res = {"variant": variant, "tied": tied, "train_loss": train_loss, "capacity_GBps": m.capacity,
               "family_capacity_GBps": m.family_default, "train": score(m, train)}
        print(f"{name}  {({k: round(v, 1) for k, v in m.family_default.items()} if tied else '')}\n"
              f"   {line('train      ', res['train'])}")
        for tag, exps in (("validation", val), ("validation2", val2), ("validation3", val3)):
            if exps:
                res[tag] = score(m, exps)
                res[tag + "_detail"] = [{"name": e["name"], "flows": e["flows"], "observed": observed(e),
                                         "predicted": m.predict(e["flows"])} for e in exps]
                print(f"   {line(f'{tag:11s}', res[tag])}")
        out["variants"][name] = res
    json.dump(out, open(a.out, "w"), indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
