"""One 8-GiB-per-rank transport diagnostic; contiguous targets isolate the bulk path."""
import argparse
import resource

from run_restore_profile import ProfileRun
from fluidckpt.cluster.control import request


class BulkProfileRun(ProfileRun):
    def fixture(self):
        size = (8 << 30)+17
        self.result["scope"] = "one bulk-path diagnostic; node28 pinned NUMA DRAM to node29 contiguous HBM; not a model-load benchmark"
        self.result["rank_bytes"] = size
        sources = []
        for rank in range(2):
            w = rank+4
            request(self.endpoints[w], "synth", {"snap": "profile", "tp": rank, "nbytes": size, "seed": 7200+rank})
            sources.append(request(self.endpoints[w], "adopt", {"snap": "profile", "tp": rank, "id": f"profile/source/{rank}"}))
        warm = [self.replica_target(s, f"profile/warm/{i}", i+6, n=32 << 20) for i, s in enumerate(sources)]
        self.cluster.transfer(self.cluster.plan_restore(warm, "resource_lp"))
        for t in warm: request(self.endpoints[t["worker"]], "retire", {"id": t["id"]})
        targets = [self.replica_target(s, f"profile/target/{i}", i+6) for i, s in enumerate(sources)]
        plan = self.cluster.plan_restore(targets, "resource_lp")
        plan["profile_execute"] = True
        self.result["profile_transfer"] = self.cluster.transfer(plan)
        if plan["strategy"] != "resource_lp" or plan["fallback_reason"]: raise AssertionError("LP fallback")
        self.event("bulk_profile_passed", bytes=2*size, execute_s=self.result["profile_transfer"]["execute_wall_s"])
        for t in targets+sources: request(self.endpoints[t["worker"]], "retire", {"id": t["id"]})


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--run", required=True)
    parser.add_argument("--port-base", type=int, default=46100)
    parser.set_defaults(worker_serialize=0, handoff_poll=.01, fixture_only=True,
                        model="/data/node28/shared/zhujianian/Qwen3.6-35B-A3B")
    BulkProfileRun(parser.parse_args()).run()
