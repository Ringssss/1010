"""Real Qwen weights from full and partial NUMA replicas, with unchanged verification."""
import argparse
import json
from pathlib import Path
import resource
import socket
import time

from run_loading_matrix import Matrix, ROOT, memory, used_mib
from fluidckpt.planner.regions import plan_regions
from fluidckpt.serving.replicas import identity


class ReplicaMatrix(Matrix):
    def make_plan(self, indexes, strategy):
        started = time.monotonic()
        inventories = self.keepers_call("replica_inventory", [{"snap": "task2"} for _ in range(2)])
        replicas = [r for inv in inventories for r in inv["replicas"]]
        selected = [r for r in replicas if r["build"].get("primary")] if strategy == "single_source" else replicas
        demands = []
        for gpu, data in enumerate(indexes):
            node = next(n for n in self.topology["nodes"] if n["node"] == self.near[gpu])
            distances = {str(i): int(v) for i, v in enumerate(node["distance"].split())}
            demands.append({"tp": gpu, "gpu": gpu, "dst_w": gpu,
                            "identity": identity(data["meta"], data["index"]),
                            "nbytes": max(o+n for o, n in data["index"].values()), "distances": distances})
        result = plan_regions(demands, selected, self.profile,
                              "nearest" if strategy == "single_source" else strategy,
                              quantum=self.config.get("chunk_bytes", 64 << 20))
        result.update(policy=strategy, catalog_and_plan_s=time.monotonic()-started,
                      inventories=inventories, assumptions=self.profile.get("unmodeled", []))
        return result

    def restore_flow(self, indexes, planning, phase, executor):
        if executor not in ("serial", "parallel"):
            raise ValueError("regional executor must be serial or parallel")
        body = {"ops": planning["ops"], "tag": phase, "snap": "task2", "parallel": executor == "parallel",
                "paced": self.config.get("paced", False), "trace_enabled": False,
                "chunk_bytes": self.config.get("chunk_bytes", 64 << 20), "max_inflight": 2,
                "index": {str(g): indexes[g]["index"] for g in range(2)}}
        return self.keepers_call("region_flow", [body, body])

    def execute(self):
        try:
            for port in (self.a.port, self.a.keeper_port, self.a.keeper_port+1):
                with socket.socket() as s:
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1); s.bind(("127.0.0.1", port))
            if used_mib(memory()) > 2048: raise RuntimeError("selected GPUs are busy")
            self.sample("warmup")
            self.keepers_start("numa_registered")
            self.sample("capture_numa", "capture", backend="numa_registered")
            self.result["replica_builds"] = self.keepers_call("create_replica", [
                {"snap": "task2", "tp": g, "replica_id": f"task2/{g}/partial",
                 "off": self.config["replica_offset"], "n": self.config["replica_bytes_per_rank"],
                 "numa": self.near[1-g]} for g in range(2)])
            self.result["memory_with_replicas"] = self.host_memory(); self.save()
            for repeat in range(self.config["repeats"]):
                variants = list(self.config["variants"])
                if repeat % 2: variants.reverse()
                for variant in variants:
                    phase = f"restore_{variant['planner']}_{variant['executor']}_{repeat}"
                    self.sample(phase, "restore", backend="numa_registered", **variant)
            self.keepers_stop()
            self.result["status"] = "passed"
        except BaseException as exc:
            self.result.update(status="failed", error=repr(exc), failed_sample=self.result.get("active_sample"))
            self.record("failed", error=repr(exc)); raise
        finally:
            for child in list(reversed(self.children)): self.stop(child)
            self.result["memory_final"] = memory(); self.save()


if __name__ == "__main__":
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    parser = argparse.ArgumentParser(); parser.add_argument("--config", required=True); parser.add_argument("--run", required=True)
    args = parser.parse_args()
    ReplicaMatrix(json.loads(Path(args.config).read_text()), args.run).execute()
