"""Check stream dependencies on real transfers, then one model restore per executor with LP enabled."""
import argparse
import hashlib
import json
import resource

from run_policy_comparison import PolicyRun
from fluidckpt.cluster.control import request


class PipelineRun(PolicyRun):
    def fixture(self):
        # Exercise local, peer, network, DRAM and HBM destinations on the pipelined path.
        self.cluster.executor = "double_buffer"
        super().fixture()
        size = (self.args.fixture_gib << 30)+17
        sources = []
        for g in range(2):
            w = g+4
            request(self.endpoints[w], "synth", {"snap": "pipe", "tp": g, "nbytes": size, "seed": 4100+g})
            sources.append(request(self.endpoints[w], "adopt", {"snap": "pipe", "tp": g, "id": f"pipe/source/{g}"}))
        targets = [self.replica_target(s, f"pipe/failed/{g}", g+6, off=3, n=(96 << 20)+11)
                   for g, s in enumerate(sources)]
        failed_plan = self.cluster.plan_restore(targets[:1], "resource_lp")
        failed_plan.update(executor="double_buffer", fail_after_chunks=2)
        try:
            self.cluster.transfer(failed_plan)
            raise AssertionError("injected stream failure was accepted")
        except RuntimeError as exc:
            if "injected failure" not in str(exc): raise
        workers, rs = self.cluster.inventory()
        if any(w["transactions"] for w in workers) or any(r["leases"] for r in rs):
            raise AssertionError("failure retained transactions or source leases")
        if any(r["id"] == targets[0]["id"] for r in rs):
            raise AssertionError("failed target was published")
        retry = self.cluster.plan_restore(targets[:1], "resource_lp")
        retry.update(executor="double_buffer", trace_pipeline=True)
        diagnostic = self.cluster.transfer(retry)
        workers, rs = self.cluster.inventory()
        r = next(r for r in rs if r["id"] == targets[0]["id"])
        if r["generation"] != 2: raise AssertionError("aborted allocation generation was not advanced")
        request(self.endpoints[r["worker"]], "retire", {"id": r["id"]})
        self.result["pipeline_failure_check"] = {"passed": True, "retry": diagnostic}
        rows = []
        for executor in ("serial", "double_buffer"):
            targets = [self.replica_target(s, f"pipe/{executor}/{g}", g+6) for g, s in enumerate(sources)]
            plan = self.cluster.plan_restore(targets, "resource_lp")
            plan["executor"] = executor
            result = self.cluster.transfer(plan)
            rows.append({"executor": executor, "result": result})
            for t in targets: request(self.endpoints[t["worker"]], "retire", {"id": t["id"]})
        self.result["pipeline_fixture"] = {"scope": "preallocated-target execute interval; allocation and SHA256 verification reported separately",
            "rank_bytes": size, "rows": rows}
        for s in sources: request(self.endpoints[s["worker"]], "retire", {"id": s["id"]})
        workers, rs = self.cluster.inventory()
        if rs or any(any(w["used_bytes"].values()) for w in workers): raise AssertionError("fixture allocations remain")
        self.cluster.executor = "serial"
        self.event("pipeline_fixture_passed", times_s={r["executor"]: r["result"]["execute_wall_s"] for r in rows})

    def real_model(self):
        sources, reference = self.prepare_model()
        future = [self.replica_target(s, f"future/{g}", g+6) for g, s in enumerate(sources)]
        candidates = [self.region_request(s, f"model/joint/{g}", g+6, "dram", (1 << 30)+17, 1 << 30)
                      for g, s in enumerate(sources)]
        self.result["model_joint_placement"] = self.cluster.place(candidates, "joint_greedy", [{"targets": future}], extra_budget=2 << 30)
        self.result["comparison_layout"] = self.layout()
        self.result.update(scope="node29 TP2; LP-selected fixed replicas; one observation per executor",
                           variant_order=["serial", "double_buffer"], requested_strategy=self.args.planner)
        signatures = []
        for executor in self.result["variant_order"]:
            self.cluster.executor = executor
            if self.layout() != self.result["comparison_layout"]: raise AssertionError("layout changed")
            self.restore_model("node29", "pipeline_"+executor, sources, [6, 7], self.args.planner, None, reference)
            row = self.result["restores"][-1]
            row.update(executor=executor, layout_fingerprint=self.layout()["fingerprint"])
            p = row["flow"]["plan"]
            if p["strategy"] != self.args.planner or p["fallback_reason"]:
                raise AssertionError("requested planner did not execute successfully")
            fields = ("src_w", "dst_w", "tp", "identity", "off", "n", "replica_id", "generation", "source_offset", "kind")
            signature = [tuple(o[k] for k in fields) for o in p["ops"]]
            signatures.append(signature)
            row["byte_plan_sha256"] = hashlib.sha256(json.dumps(signature).encode()).hexdigest()
            if self.layout() != self.result["comparison_layout"]: raise AssertionError("layout changed after restore")
            self.save()
        if signatures[0] != signatures[1]: raise AssertionError("executor comparison used different byte plans")
        self.result["stages"]["pipeline_model"] = {"passed": True, "planner": self.args.planner}


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--fixture-only", action="store_true")
    parser.add_argument("--fixture-gib", type=int, default=2)
    parser.add_argument("--planner", choices=["nearest", "balanced", "resource_lp"], default="resource_lp")
    parser.add_argument("--port-base", type=int, default=46100)
    parser.add_argument("--worker-serialize", type=int, default=0)
    parser.add_argument("--handoff-poll", type=float, default=0.01)
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    PipelineRun(parser.parse_args()).run()
