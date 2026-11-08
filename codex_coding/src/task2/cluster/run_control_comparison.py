"""One legacy and one compact control pass, holding model, byte plan and replicas fixed."""
import argparse
import hashlib
import json
import resource

from run_policy_comparison import PolicyRun


class ControlRun(PolicyRun):
    def probe_planning(self, targets):
        rows = []
        for strategy in ("nearest", "balanced", "resource_lp"):
            plans = {}
            for mode in ("legacy", "compact"):
                self.cluster.control_mode = mode
                p = self.cluster.plan_restore(targets, strategy)
                plans[mode] = p
            if plans["legacy"]["ops"] != plans["compact"]["ops"] or plans["legacy"]["targets"] != plans["compact"]["targets"]:
                raise AssertionError("wire format changed planning semantics")
            rows.append({"strategy": strategy, "ops_and_manifests_equal": True,
                "modes": {mode: {"timing": p["control_durations_s"], "planner_rpc": p["planner_rpc"],
                                 "inventory_rpc": p["inventory_rpc"]} for mode, p in plans.items()}})
        self.cluster.control_mode = "compact"
        return rows

    def real_model(self):
        sources, reference = self.prepare_model()
        future = [self.replica_target(s, f"future/{g}", g+6) for g, s in enumerate(sources)]
        candidates = [self.region_request(s, f"model/joint/{g}", g+6, "dram", (1 << 30)+17, 1 << 30)
                      for g, s in enumerate(sources)]
        self.result["model_joint_placement"] = self.cluster.place(
            candidates, "joint_greedy", [{"targets": future}], extra_budget=2 << 30)
        if len(self.result["model_joint_placement"]["placement"]["decisions"]) != 2:
            raise AssertionError("both local replicas required for fixed layout")
        self.result["comparison_layout"] = self.layout()
        self.result["control_planner_probes"] = self.probe_planning(future)
        self.result.update(scope="node29 TP2; identical primary-only byte plan and physical replicas; one run per control mode",
                           handoff_poll_s=self.args.handoff_poll, variant_order=["legacy", "compact"])
        self.event("control_comparison_ready")
        signatures = []
        for mode in self.result["variant_order"]:
            self.cluster.control_mode = mode
            if self.layout() != self.result["comparison_layout"]:
                raise AssertionError("replica layout changed")
            self.restore_model("node29", "control_"+mode, sources, [6, 7], "nearest",
                               [f"model/remote/{g}" for g in range(2)], reference)
            row = self.result["restores"][-1]
            row.update(control_mode=mode, layout_fingerprint=self.layout()["fingerprint"])
            fields = ("src_w", "dst_w", "tp", "identity", "off", "n", "replica_id", "generation", "source_offset", "kind")
            signature = [tuple(o[k] for k in fields) for o in row["flow"]["plan"]["ops"]]
            signatures.append(signature)
            row["byte_plan_sha256"] = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
            if self.layout() != self.result["comparison_layout"]:
                raise AssertionError("replica layout changed after restore")
            self.save()
        if signatures[0] != signatures[1]: raise AssertionError("comparison used different byte plans")
        self.result["stages"]["control_comparison"] = {"passed": True, "byte_plans_equal": True}


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--port-base", type=int, default=46100)
    parser.add_argument("--worker-serialize", type=int, default=0)
    parser.add_argument("--handoff-poll", type=float, default=0.01)
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    parser.set_defaults(fixture_only=False)
    ControlRun(parser.parse_args()).run()
