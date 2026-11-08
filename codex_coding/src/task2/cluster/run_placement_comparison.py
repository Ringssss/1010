"""Two real restores: existing versus optimized placement under identical tier budgets."""
import argparse
import copy
import json
import resource
import time

from run_policy_comparison import PolicyRun
from fluidckpt.cluster.control import request

GIB = 1 << 30


class PlacementRun(PolicyRun):
    def fixture(self):
        self.event("comparison_scope", real_restores=2, repeats_per_layout=1, extra_hbm_gib=8, extra_dram_gib=2)

    def build(self, sources, prefix, tier, size, offset=GIB+17):
        targets = [self.replica_target(s, f"{prefix}/{g}", g+6, tier, offset, size) for g, s in enumerate(sources)]
        result = self.cluster.transfer(self.cluster.plan_restore(targets, "nearest", [f"model/remote/{g}" for g in range(2)]))
        return targets, result

    def calibrate(self, sources):
        dram, dbuild = self.build(sources, "model/joint", "dram", GIB)
        hbm, hbuild = self.build(sources, "calibration/hbm", "hbm", GIB)
        self.result["local_source_build"] = {"dram": dbuild, "hbm": hbuild}
        rows = []
        variants = [("network:dram", [f"model/remote/{g}" for g in range(2)]),
                    ("network:hbm", [f"model/gpu/{g}" for g in range(2)]),
                    ("local_dram:dram", [t["id"] for t in dram]),
                    ("local_hbm:hbm", [t["id"] for t in hbm])]
        for name, ids in variants:
            measurements = []
            for label, n in (("warm", 32 << 20), ("measure", GIB)):
                targets = [self.replica_target(s, f"cal/{name}/{label}/{g}", g+6, "hbm", GIB+17, n)
                           for g, s in enumerate(sources)]
                flow = self.cluster.transfer(self.cluster.plan_restore(targets, "nearest", ids))
                if label == "measure":
                    durations = [sum(o["elapsed_s"] for o in w["records"] if o["role"] in ("target", "both"))
                                 for w in flow["execution"] if w["worker"] in (6, 7)]
                    measurements = durations
                    rows.append({"path": name, "rank_bytes": n, "target_seconds": durations,
                                 "GBps": n/1e9/max(durations), "flow": flow})
                for t in targets: request(self.endpoints[t["worker"]], "retire", {"id": t["id"]})
            self.event("path_calibrated", path=name, rank_seconds=measurements)
        for t in hbm: request(self.endpoints[t["worker"]], "retire", {"id": t["id"]})
        rates = {r["path"]: r["GBps"] for r in rows}
        profile = copy.deepcopy(self.cluster.profile)
        profile.update(path_rates=rates, serial_target_execution=True,
                       network_node_GBps=2*rates["network:dram"],
                       provenance="one 1GiB observation per path with two concurrent ranks and contiguous targets; executor-aware provisional model")
        self.result["calibration"] = rows
        self.result["comparison_profile"] = profile
        self.cluster.profile = profile
        self.save()

    def real_model(self):
        sources, reference = self.prepare_model()
        self.calibrate(sources)
        workers, replicas = self.cluster.inventory(compact=True)
        base = [r for r in replicas if r["id"].startswith("model/remote/")]
        existing = [r for r in replicas if r not in base]
        targets = [self.replica_target(s, f"future/{g}", g+6) for g, s in enumerate(sources)]
        candidates = []
        for rank, source in enumerate(base):
            for block in range(6):
                for kind, w, tier in (("remote_hbm", rank+2, "hbm"), ("local_hbm", rank+6, "hbm"),
                                      ("local_dram", rank+6, "dram")):
                    c = dict(source, id=f"candidate/{rank}/{block}/{kind}", worker=w, node=workers[w]["node"],
                             numa=workers[w]["numa"], tier=tier, off=GIB+17+block*GIB, n=GIB,
                             block=block, placement_kind=kind, generation=1, state="VALID")
                    candidates.append(c)
        case = {"base": base, "existing": existing, "candidates": candidates, "workers": workers,
                "targets": [{k: t[k] for k in ("key", "worker", "identity", "tp", "off", "n")} for t in targets],
                "profile": self.cluster.profile, "tier_budgets": {"dram": 2*GIB, "hbm": 8*GIB},
                "location_budgets": {**{f"{w}:hbm": 4*GIB for w in (2, 3, 6, 7)}, **{f"{w}:dram": GIB for w in (6, 7)}}}
        (self.out/"placement_case.json").write_text(json.dumps(case, indent=2))
        self.remote("node27", ["python3", "codex_coding/src/task2/cluster/solve_placement_case.py",
            "--input", str(self.out/"placement_case.json"), "--output", str(self.out/"placement_solutions.json")])
        solutions = json.loads((self.out/"placement_solutions.json").read_text())
        self.result["placement_solutions"] = solutions
        self.result["scope"] = "known node29 TP2 destination; identical complete node28 DRAM base plus at most 8GiB HBM and 2GiB DRAM; one restore per layout"
        self.result["layouts"] = {"existing": self.layout()}
        self.event("placements_solved", selected=solutions["selected_policy"], predictions=
                   {k: r["objective_s"] for k, r in solutions["policies"].items()})
        self.restore_model("node29", "placement_existing", sources, [6, 7], "resource_lp", None, reference)
        for r in existing: request(self.endpoints[r["worker"]], "retire", {"id": r["id"]})
        selected = solutions["policies"][solutions["selected_policy"]]["selected"]
        # Coalesce adjacent selections with the same owner/tier to avoid artificial tiny operations.
        groups = []
        for c in sorted(selected, key=lambda c: (c["tp"], c["worker"], c["tier"], c["off"])):
            if groups and all(groups[-1][k] == c[k] for k in ("tp", "worker", "tier")) and groups[-1]["off"]+groups[-1]["n"] == c["off"]:
                groups[-1]["n"] += c["n"]
            else: groups.append(dict(c))
        built, stamp = [], time.monotonic()
        for i, c in enumerate(groups):
            target = self.replica_target(sources[c["tp"]], f"optimized/{i}", c["worker"], c["tier"], c["off"], c["n"])
            built.append(self.cluster.transfer(self.cluster.plan_restore([target], "resource_lp", [r["id"] for r in base])))
        self.result["optimized_build"] = {"wall_s": time.monotonic()-stamp, "flows": built}
        self.result["layouts"]["optimized"] = self.layout()
        self.event("optimized_layout_ready", selected=solutions["selected_policy"])
        self.restore_model("node29", "placement_optimized", sources, [6, 7], "resource_lp", None, reference)
        for row in self.result["restores"]:
            p = row["flow"]["plan"]
            if p["strategy"] != "resource_lp" or p["fallback_reason"]: raise AssertionError("restore LP fallback")
        self.result["stages"]["placement_comparison"] = {"passed": True, "samples": 2}


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--port-base", type=int, default=46100)
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=False,
                        model="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    PlacementRun(parser.parse_args()).run()
