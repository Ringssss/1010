"""Score frozen fitted models (no refitting) on held-out profiles.

  python -m fluidckpt.model.score_frozen benchmarks/results/profile_node27.json \
      benchmarks/results/model_fit_node27_frozen.json benchmarks/results/profile_node27_heldout.json \
      --out benchmarks/results/model_heldout_node27.json
"""
import argparse
import json
import statistics

from fluidckpt.model.fit import Model, observed, repeat_noise, score
from fluidckpt.model.resources import SingleCaps


def load_model(prof, v):
    gpu_numa = {int(k): x for k, x in prof["gpu_numa"].items()}
    return Model(SingleCaps(prof), v["variant"], gpu_numa, v["capacity_GBps"], tied=v["tied"],
                 family_default=v["family_capacity_GBps"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("profile")
    ap.add_argument("frozen_fit")
    ap.add_argument("heldout", nargs="+")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    prof, fit = json.load(open(a.profile)), json.load(open(a.frozen_fit))
    exps = {}
    for path in a.heldout:
        for k, v in json.load(open(path))["concurrent"].items():
            exps.setdefault(v["split"], []).append(dict(name=k, **v))
    out = {"frozen_fit": a.frozen_fit, "heldout": a.heldout,
           "noise": {s: statistics.median(map(repeat_noise, e)) for s, e in exps.items()}, "variants": {}}
    print("repeat noise (median aggregate spread):", {s: f"{v:.2%}" for s, v in out["noise"].items()})
    for name, v in fit["variants"].items():
        m = load_model(prof, v)
        out["variants"][name] = res = {}
        row = []
        for split, e in sorted(exps.items()):
            sc = score(m, e)
            res[split] = sc
            res[split + "_detail"] = [{"name": x["name"], "flows": x["flows"], "observed": observed(x),
                                       "predicted": m.predict(x["flows"])} for x in e]
            row.append(f"{split}: agg med {sc['aggregate_rel_err']['median']:5.1%} p90 {sc['aggregate_rel_err']['p90']:5.1%} "
                       f"max {sc['aggregate_rel_err']['max']:5.1%} flow med {sc['flow_rel_err']['median']:5.1%}")
        print(f"{name:42s} " + " | ".join(row))
    json.dump(out, open(a.out, "w"), indent=1)
    print("wrote", a.out)


if __name__ == "__main__":
    main()
