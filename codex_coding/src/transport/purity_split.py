"""Split every measured mix by root-port purity and score the frozen V9 model on each side.
A root port is *pure* when all host traffic through it touches a single NUMA node.
node27: GPU g has its own root port, except GPU 0 which shares one with the checkpoint NVMe."""
import json
import statistics
import sys

from fluidckpt.model.score_frozen import load_model
from fluidckpt.model.fit import observed

R = "benchmarks/results/"
PORT = {g: g for g in range(8)}  # GPU -> root port id; the NVMe sits on port 0


def port_nodes(flows):
    nodes = {}
    for f in flows:
        k = f["kind"]
        if k in ("h2d", "d2h"):
            nodes.setdefault(PORT[f["gpu"]], set()).add(f["numa"])
        elif k == "nvme":
            nodes.setdefault(0, set()).add(f["numa"])
        elif k == "nvme2gpu":  # NVMe -> staging node, then staging node -> GPU
            nodes.setdefault(0, set()).add(f["numa"])
            nodes.setdefault(PORT[f["gpu"]], set()).add(f["numa"])
    return nodes


def pure(flows):
    return all(len(n) == 1 for n in port_nodes(flows).values())


def numa_fanout(flows):
    """Some NUMA node serves more than one root port."""
    ports = {}
    for p, ns in port_nodes(flows).items():
        for n in ns:
            ports.setdefault(n, set()).add(p)
    return any(len(v) > 1 for v in ports.values())


def main():
    prof = json.load(open(R + "profile_node27.json"))
    fit = json.load(open(R + "model_fit_node27_frozen.json"))
    m = load_model(prof, fit["variants"]["V9_links_hswio_numa_rw_conflict_tied"])
    rows = []
    for f in ["profile_node27.json", "profile_node27_train2.json", "profile_node27_heldout.json",
              "profile_node27_policy.json", "profile_node27_train3.json"]:
        for k, e in json.load(open(R + f))["concurrent"].items():
            o, p = sum(observed(e)), sum(m.predict(e["flows"]))
            rows.append(dict(name=k, split=e["split"], err=abs(p - o) / o, signed=(p - o) / o,
                             pure=pure(e["flows"]), fanout=numa_fanout(e["flows"]),
                             host=any(x["kind"] != "p2p" for x in e["flows"])))

    def line(tag, xs):
        if not xs:
            return f"{tag:44s} n= 0"
        es = sorted(x["err"] for x in xs)
        return (f"{tag:44s} n={len(es):3d} median {statistics.median(es):5.1%} p90 {es[int(0.9 * (len(es) - 1))]:5.1%} "
                f"max {es[-1]:5.1%}")
    out = {}
    for scope, sel in (("all splits", lambda r: True),
                       ("held-out only (validation2/3/4)", lambda r: r["split"] in ("validation2", "validation3", "validation4"))):
        rs = [r for r in rows if sel(r)]
        print(f"== {scope}")
        for tag, f in (("pure ports", lambda r: r["pure"]),
                       ("pure ports, no NUMA fan-out", lambda r: r["pure"] and not r["fanout"]),
                       ("pure ports, NUMA fan-out", lambda r: r["pure"] and r["fanout"]),
                       ("impure (some port touches >=2 NUMA nodes)", lambda r: not r["pure"])):
            xs = [r for r in rs if f(r)]
            print("  " + line(tag, xs))
            out[f"{scope}|{tag}"] = xs
    print("worst pure-port mixes:", [(r["name"], f"{r['signed']:+.0%}") for r in
                                     sorted((r for r in rows if r["pure"]), key=lambda r: -r["err"])[:6]])
    json.dump(out, open(R + "purity_split_node27.json", "w"), indent=1)


if __name__ == "__main__":
    sys.exit(main())
