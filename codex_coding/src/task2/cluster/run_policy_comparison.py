"""One observation per policy on the same target and unchanged physical replica layout."""
import argparse
import hashlib
import json
from pathlib import Path
import resource

from run_system import SystemRun


class PolicyRun(SystemRun):
    def layout(self):
        workers, replicas = self.cluster.inventory()
        keys = ("id", "worker", "identity", "epoch", "generation", "tier", "numa", "off", "n", "allocated_bytes")
        rows = sorted([{k: r[k] for k in keys} for r in replicas], key=lambda r: r["id"])
        if any(r["leases"] for r in replicas) or any(w["transactions"] for w in workers):
            raise RuntimeError("layout snapshot contains active transactions")
        return {"replicas": rows, "worker_used_bytes": [w["used_bytes"] for w in workers],
                "fingerprint": hashlib.sha256(json.dumps(rows, sort_keys=True).encode()).hexdigest()}

    def real_model(self):
        sources, reference = self.prepare_model()
        future = [self.replica_target(s, f"future/{g}", g+6) for g, s in enumerate(sources)]
        candidates = [self.region_request(s, f"model/joint/{g}", g+6, "dram", (1 << 30)+17, 1 << 30)
                      for g, s in enumerate(sources)]
        for candidate in candidates:
            candidate["locations"].append({"worker": candidate["tp"]+4, "tier": "dram"})
        self.result["model_joint_placement"] = self.cluster.place(
            candidates, "joint_greedy", [{"targets": future}], extra_budget=2 << 30)
        if len(self.result["model_joint_placement"]["placement"]["decisions"]) != 2:
            raise AssertionError("comparison requires both planned local replicas")
        self.result["comparison_layout"] = self.layout()
        self.result["scope"] = "node29 TP2; same retained 8GiB HBM + 2GiB extra DRAM; one sample per policy; no statistical claim"
        self.result["handoff_poll_s"] = self.args.handoff_poll
        # Explicit fixed order; first-instance effects remain visible in the recorded breakdown.
        variants = [("primary_only", "nearest", [f"model/remote/{g}" for g in range(2)]),
                    ("nearest", "nearest", None), ("balanced", "balanced", None),
                    ("resource_lp", "resource_lp", None)]
        self.result["variant_order"] = [name for name, _, _ in variants]
        self.event("comparison_layout_ready", fingerprint=self.result["comparison_layout"]["fingerprint"])
        for name, strategy, source_ids in variants:
            if self.layout() != self.result["comparison_layout"]:
                raise AssertionError("replica layout changed before comparison")
            self.restore_model("node29", "policy_"+name, sources, [6, 7], strategy, source_ids, reference)
            row = self.result["restores"][-1]
            row["policy"] = name
            row["layout_fingerprint"] = self.layout()["fingerprint"]
            if self.layout() != self.result["comparison_layout"]:
                raise AssertionError("replica layout changed after restore")
            self.save()
        self.result["stages"]["policy_comparison"] = {"passed": True, "samples": len(variants)}


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--port-base", type=int, default=46100)
    parser.add_argument("--worker-serialize", type=int, default=0)
    parser.add_argument("--handoff-poll", type=float, default=0.01)
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    parser.set_defaults(fixture_only=False)
    PolicyRun(parser.parse_args()).run()
