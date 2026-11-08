"""One real TP2 restore with vendor HIP traces, unchanged LP/layout/serial executor."""
import argparse
import hashlib
import json
import os
import resource

from run_policy_comparison import PolicyRun


class ProfileRun(PolicyRun):
    def stop(self, handle):
        node, path = handle
        if path.name.startswith("worker_"):
            try:
                self.remote(node, ["python3", "codex_coding/src/task2/cluster/stop_profile_worker.py",
                                   "--spec", str(path)], timeout=45)
            finally:
                super().stop(handle)
            return
        return super().stop(handle)

    def start(self, node, name, command, env=None):
        if name.startswith("worker_"):
            directory = self.out / "profiles"
            directory.mkdir(exist_ok=True)
            os.chmod(directory, 0o777)
            command = ["/opt/dtk/bin/hipprof", "--hip-trace", "--hiptx-trace", "--trace-args",
                       "--trace-off", "--no-export", "-o", str(directory/name)] + command
            env = dict(env or {}, FLUIDCKPT_EXECUTE_PROFILE_DIR=str(directory))
        return super().start(node, name, command, env)

    def fixture(self):
        # Existing correctness suite was already run; no repeated benchmark matrix here.
        self.event("profiling_scope", executor="serial", planner="resource_lp", samples=1)

    def real_model(self):
        sources, reference = self.prepare_model()
        future = [self.replica_target(s, f"future/{g}", g+6) for g, s in enumerate(sources)]
        candidates = [self.region_request(s, f"model/joint/{g}", g+6, "dram", (1 << 30)+17, 1 << 30)
                      for g, s in enumerate(sources)]
        for candidate in candidates:
            candidate["locations"].append({"worker": candidate["tp"]+4, "tier": "dram"})
        self.result["model_joint_placement"] = self.cluster.place(
            candidates, "joint_greedy", [{"targets": future}], extra_budget=2 << 30)
        self.result["comparison_layout"] = self.layout()
        self.result["scope"] = "one instrumented serial restore; diagnostic timing, not benchmark latency"
        self.restore_model("node29", "profile_serial", sources, [6, 7], "resource_lp", None, reference)
        row = self.result["restores"][-1]
        plan = row["flow"]["plan"]
        if plan["strategy"] != "resource_lp" or plan["fallback_reason"]:
            raise AssertionError("profiling did not execute requested LP")
        fields = ("src_w", "dst_w", "tp", "identity", "off", "n", "replica_id", "generation", "source_offset", "kind")
        ops = [tuple(o[k] for k in fields) for o in plan["ops"]]
        row["byte_plan_sha256"] = hashlib.sha256(json.dumps(ops).encode()).hexdigest()
        self.result["stages"]["profiling"] = {"passed": True, "samples": 1}
        self.save()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser()
    parser.add_argument("--run", required=True)
    parser.add_argument("--port-base", type=int, default=46100)
    parser.add_argument("--worker-serialize", type=int, default=0)
    parser.add_argument("--handoff-poll", type=float, default=0.01)
    parser.add_argument("--model", default="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    parser.set_defaults(fixture_only=False)
    ProfileRun(parser.parse_args()).run()
