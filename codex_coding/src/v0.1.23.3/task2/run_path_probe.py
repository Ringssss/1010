"""Verify explicitly enumerated network paths before admitting them to placement search."""
import argparse
import json
from pathlib import Path
import resource
import sys

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT/"codex_coding/src/task2/cluster"))
from run_system import SystemRun
from fluidckpt.cluster.control import request


class PathProbe(SystemRun):
    def fixture(self):
        size = 32 << 20
        self.result["scope"] = "32MiB synthetic path availability probes, one sample each; not a model restore or bandwidth ranking"
        origins, base = [], []
        for rank in range(2):
            for owner, prefix, group in ((rank, "origin", origins), (rank+4, "base", base)):
                request(self.endpoints[owner], "synth", {"snap": prefix, "tp": rank, "nbytes": size, "seed": 13000+owner})
                group.append(request(self.endpoints[owner], "adopt", {"snap": prefix, "tp": rank, "id": f"probe/{prefix}/{rank}"}))
        flows, pairs = [], []
        def move(sources, destinations, prefix, keep=False):
            targets = [self.replica_target(s, f"probe/{prefix}/{g}", destinations[g]) for g, s in enumerate(sources)]
            flow = self.cluster.transfer(self.cluster.plan_restore(targets, "nearest", [s["id"] for s in sources]))
            if not flow["verification"]["passed"]: raise AssertionError("path probe content mismatch")
            flows.append(flow)
            pairs.extend([[op["src_w"], op["dst_w"]] for op in flow["plan"]["ops"] if op["kind"] == "network"])
            self.result["path_probes"] = flows
            self.event("network_paths_verified", label=prefix, pairs=pairs[-2:], bytes=flow["verification"]["checked_bytes"])
            if not keep:
                for target in targets: self.cluster.retire(target["id"])
            return targets
        move(origins, [4, 5], "origin_to_primary")
        hbm = move(base, [2, 3], "primary_to_keeper", keep=True)
        # Obtain real published descriptors, including their actual owner metadata.
        _, inventory = self.cluster.inventory()
        hbm = [next(r for r in inventory if r["id"] == t["id"]) for t in hbm]
        move(base, [6, 7], "primary_to_target")
        move(hbm, [6, 7], "keeper_to_target")
        report = {"passed": True, "members": [list(m) for m in self.members], "network_pairs": sorted(pairs),
                  "bytes_per_pair": size, "scope": "verified availability only, not bandwidth calibration; other pairs remain unverified",
                  "failed_observation": "cluster03: worker5 -> worker6 broadcast timed out; excluded, permanent hardware root cause unproven"}
        self.result["network_paths"] = report
        _, inventory = self.cluster.inventory()
        for r in inventory: request(self.endpoints[r["worker"]], "retire", {"id": r["id"]})
        self.event("path_probe_passed", pairs=len(pairs))

    def run(self):
        super().run()
        if self.result["status"] == "passed" and not self.result["cleanup_errors"]:
            (self.out/"network_pairs.json").write_text(json.dumps(self.result["network_paths"], indent=2))


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--port-base", type=int, default=28100)
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=True, development="v0.1.23.3",
        result_root=str(ROOT/"codex_coding/results/v0.1.23.3/task2"), source_directories=[str(Path(__file__).parent)])
    PathProbe(parser.parse_args()).run()
